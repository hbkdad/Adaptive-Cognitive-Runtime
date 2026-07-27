from __future__ import annotations

import io
import json
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from acr_runtime.cli import main
from acr_runtime.content_security import (
    ContentAssessmentRequest,
    ContentSecurityController,
)
from acr_runtime.db import RuntimeDB
from acr_runtime.memory import MemoryCreate, MemoryType
from acr_runtime.permissions import CapabilityGrantRequest, PermissionController
from acr_runtime.providers.base import ChatMessage, EmbeddingRequest
from acr_runtime.secret_management import (
    EnvironmentSecretProvider,
    ExternalSecretProvider,
    KeyringSecretProvider,
    SecretBoundaryError,
    SecretLease,
    SecretManager,
    SecretReference,
    detect_secret_material,
    redact_secret_text,
    redact_secret_value,
    sanitize_secret_json,
    scan_staged_git_secrets,
)


def _openai_like_secret() -> str:
    return "sk-" + "A1b2C3d4E5f6G7h8I9j0K1"


class SecretManagementTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "acr.db"
        self.db = RuntimeDB(self.path)
        self.permissions = PermissionController(self.db.connection)
        self.reference = SecretReference("env", "ACR_TEST_TOKEN")

    def tearDown(self) -> None:
        self.db.close()
        self.temp.cleanup()

    @staticmethod
    def future() -> str:
        return (
            datetime.now(timezone.utc) + timedelta(hours=1)
        ).isoformat()

    def grant(self, reference: SecretReference | None = None) -> None:
        target = reference or self.reference
        self.permissions.grant(CapabilityGrantRequest(
            subject_type="task",
            subject_id="task-secret",
            capability="credential.use",
            resource_scope=target.resource_scope,
            expires_at=self.future(),
            delegable=False,
            grantor_type="trusted_workflow",
            grantor_id="operator-secret",
            reason="Bounded integration test",
            evidence=("test:secret-management",),
        ))

    def test_reference_is_opaque_bounded_and_stably_scoped(self):
        parsed = SecretReference.parse("env:ACR_TEST_TOKEN")
        self.assertEqual(parsed, self.reference)
        summary = parsed.public_summary()
        self.assertNotIn(parsed.key, json.dumps(summary))
        self.assertEqual(len(parsed.reference_hash), 64)
        self.assertEqual(
            parsed.resource_scope, f"secret:{parsed.reference_hash}"
        )
        with self.assertRaises(ValueError):
            SecretReference.parse("ACR_TEST_TOKEN")
        with self.assertRaises(ValueError):
            SecretReference("env", "lowercase")
        with self.assertRaises(ValueError):
            SecretReference.parse("unknown:item")

    def test_resolution_is_default_deny_and_provider_is_not_called(self):
        calls: list[str] = []
        manager = SecretManager(
            self.db.connection,
            self.permissions,
            providers=(ExternalSecretProvider(
                lambda key: calls.append(key) or "resolved-value"
            ),),
        )
        reference = SecretReference("external", "vault/item")
        with self.assertRaises(PermissionError):
            manager.resolve(
                reference,
                subject_type="task",
                subject_id="task-secret",
            )
        self.assertEqual(calls, [])
        event = self.db.connection.execute(
            "SELECT * FROM secret_access_events"
        ).fetchone()
        self.assertEqual(event["decision"], "denied")
        self.assertNotIn(reference.key, json.dumps(dict(event)))

    def test_exact_grant_returns_one_use_zeroized_lease_and_value_free_audit(self):
        secret = "ephemeral-resolved-value"
        self.grant()
        manager = SecretManager(
            self.db.connection,
            self.permissions,
            providers=(EnvironmentSecretProvider({
                self.reference.key: secret
            }),),
        )
        lease = manager.resolve(
            self.reference,
            subject_type="task",
            subject_id="task-secret",
        )
        self.assertNotIn(secret, repr(lease))
        self.assertEqual(lease.use(lambda value: len(value)), len(secret))
        self.assertTrue(lease.closed)
        with self.assertRaises(RuntimeError):
            lease.use(lambda value: len(value))
        event = manager.inspect(lease.audit_id)
        self.assertEqual(event["decision"], "granted")
        self.assertNotIn(secret, json.dumps(event))
        self.assertNotIn(self.reference.key, json.dumps(event))

    def test_lease_rejects_returning_secret_and_closes(self):
        lease = SecretLease(
            "do-not-return",
            audit_id="audit-test",
            provider="env",
            reference_hash="a" * 64,
        )
        with self.assertRaises(SecretBoundaryError):
            lease.use(lambda value: {"copied": [value]})
        self.assertTrue(lease.closed)

    def test_missing_and_provider_errors_are_value_free_and_fail_closed(self):
        self.grant()
        missing = SecretManager(
            self.db.connection,
            self.permissions,
            providers=(EnvironmentSecretProvider({}),),
        )
        with self.assertRaisesRegex(LookupError, "audit="):
            missing.resolve(
                self.reference,
                subject_type="task",
                subject_id="task-secret",
            )
        failing_reference = SecretReference("external", "vault/failure")
        self.grant(failing_reference)

        def fail(_: str) -> str:
            raise ValueError("provider-sensitive-detail")

        failing = SecretManager(
            self.db.connection,
            self.permissions,
            providers=(ExternalSecretProvider(fail),),
        )
        with self.assertRaisesRegex(RuntimeError, "Secret provider failed"):
            failing.resolve(
                failing_reference,
                subject_type="task",
                subject_id="task-secret",
            )
        rows = self.db.connection.execute(
            "SELECT decision FROM secret_access_events ORDER BY created_at"
        ).fetchall()
        self.assertEqual(
            [row["decision"] for row in rows], ["missing", "provider_error"]
        )

    def test_optional_keyring_dependency_fails_closed_without_value(self):
        with mock.patch.dict("sys.modules", {"keyring": None}):
            with self.assertRaisesRegex(
                RuntimeError, "optional dependency"
            ):
                KeyringSecretProvider().get("bounded-item")

    def test_cli_resolve_prints_only_opaque_metadata(self):
        secret = "cli-ephemeral-resolved-value"
        self.grant()
        output = io.StringIO()
        with mock.patch.dict(
            "os.environ", {self.reference.key: secret}, clear=False
        ):
            with redirect_stdout(output):
                result = main([
                    "--db", str(self.path),
                    "secrets", "resolve", self.reference.canonical,
                    "--subject-type", "task",
                    "--subject-id", "task-secret",
                ])
        self.assertEqual(result, 0)
        payload = json.loads(output.getvalue())
        self.assertTrue(payload["resolved"])
        self.assertNotIn(secret, output.getvalue())
        self.assertNotIn(self.reference.key, output.getvalue())

    def test_detection_and_recursive_redaction_cover_common_formats(self):
        token = _openai_like_secret()
        bearer = "Bearer " + "abcdefghijklmnop"
        text = f"Authorization: {bearer} {token}"
        self.assertIn("openai_api_key", detect_secret_material(text))
        self.assertIn("authorization_bearer", detect_secret_material(text))
        redacted = redact_secret_text(text)
        self.assertNotIn(token, redacted)
        self.assertNotIn("abcdefghijklmnop", redacted)
        nested = redact_secret_value({
            "api_key": "short-sensitive-value",
            "nested": [f"token={token}"],
        })
        serialized = json.dumps(nested)
        self.assertNotIn("short-sensitive-value", serialized)
        self.assertNotIn(token, serialized)
        self.assertNotIn(
            token, sanitize_secret_json(json.dumps({"message": token}))
        )

    def test_memory_prompt_embedding_and_content_boundaries_reject_or_quarantine(self):
        token = _openai_like_secret()
        with self.assertRaises(SecretBoundaryError):
            MemoryCreate(
                type=MemoryType.SEMANTIC,
                content=f"api_key={token}",
            )
        with self.assertRaises(SecretBoundaryError):
            ChatMessage("user", f"use {token}")
        with self.assertRaises(SecretBoundaryError):
            EmbeddingRequest("embedding-test", (f"index {token}",))
        security = ContentSecurityController(self.db.connection)
        assessment = security.assess(ContentAssessmentRequest(
            origin="web_content",
            source_id="https://example.test/secret",
            content=f"api_key={token}",
            provenance=("url:https://example.test/secret",),
        ))
        self.assertEqual(assessment["disposition"], "quarantine")
        self.assertIn(
            "secret_material:openai_api_key",
            assessment["suspicious_signals"],
        )
        stored = self.db.connection.execute(
            "SELECT * FROM content_security_assessments WHERE id=?",
            (assessment["id"],),
        ).fetchone()
        self.assertNotIn(token, json.dumps(dict(stored)))

    def test_staged_scanner_and_cli_report_metadata_not_secret_values(self):
        root = Path(self.temp.name) / "repository"
        root.mkdir()
        subprocess.run(
            ["git", "init", "-q"],
            cwd=root,
            check=True,
            stdin=subprocess.DEVNULL,
        )
        token = _openai_like_secret()
        (root / "settings.txt").write_text(
            f"service token: {token}\n", encoding="utf-8"
        )
        subprocess.run(
            ["git", "add", "settings.txt"],
            cwd=root,
            check=True,
            stdin=subprocess.DEVNULL,
        )
        findings = scan_staged_git_secrets(root)
        self.assertEqual(findings[0]["path"], "settings.txt")
        self.assertNotIn(token, json.dumps(findings))
        output = io.StringIO()
        with redirect_stdout(output):
            result = main([
                "secrets", "scan-staged", "--repository", str(root)
            ])
        self.assertEqual(result, 1)
        self.assertFalse(json.loads(output.getvalue())["clean"])
        self.assertNotIn(token, output.getvalue())

    def test_current_repository_staged_scanner_is_clean(self):
        findings = scan_staged_git_secrets(
            Path(__file__).resolve().parents[1]
        )
        self.assertEqual(findings, [])


if __name__ == "__main__":
    unittest.main()
