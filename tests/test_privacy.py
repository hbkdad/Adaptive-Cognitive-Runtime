from __future__ import annotations

import io
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from contextlib import redirect_stdout

from acr_runtime.cli import main
from acr_runtime.db import RuntimeDB
from acr_runtime.memory import (
    LifecycleState,
    MemoryCreate,
    MemoryPatch,
    MemoryStatus,
    MemoryType,
    Sensitivity,
)
from acr_runtime.privacy import DELETED_CONTENT, PrivacyEngine
from acr_runtime.secret_management import SecretBoundaryError


class PrivacyEngineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "acr.db"
        self.db = RuntimeDB(self.path)
        self.privacy = PrivacyEngine(self.db.connection)

    def tearDown(self) -> None:
        self.db.close()
        self.temp.cleanup()

    def create(
        self,
        content: str = "Private northern project schedule",
        *,
        sensitivity: Sensitivity = Sensitivity.INTERNAL,
    ):
        return self.db.memories.create(MemoryCreate(
            type=MemoryType.SEMANTIC,
            content=content,
            scope="privacy-test",
            status=MemoryStatus.CONFIRMED,
            sensitivity=sensitivity,
        ))

    def test_every_memory_is_tagged_and_retention_comes_from_policy(self):
        internal = self.create()
        self.assertEqual(internal.sensitivity, Sensitivity.INTERNAL)
        self.assertIsNone(internal.retention_until)
        personal = self.create(
            "Personal preference for local processing",
            sensitivity=Sensitivity.PERSONAL,
        )
        self.assertEqual(personal.sensitivity, Sensitivity.PERSONAL)
        self.assertEqual(personal.privacy_policy_version, 1)
        duration = (
            datetime.fromisoformat(personal.retention_until)
            - datetime.fromisoformat(personal.created_at)
        )
        self.assertGreater(duration, timedelta(days=364))
        self.assertLess(duration, timedelta(days=366))

    def test_credentials_never_become_secret_class_memory(self):
        token = "sk-" + "A1b2C3d4E5f6G7h8I9j0K1"
        with self.assertRaises(SecretBoundaryError):
            self.create(
                f"api_key={token}",
                sensitivity=Sensitivity.SECRET,
            )

    def test_provider_policy_is_exact_default_deny_and_versioned(self):
        record = self.create(
            sensitivity=Sensitivity.PERSONAL
        )
        local = self.privacy.authorize_provider(
            (record.id,), provider="ollama", local=True
        )
        self.assertTrue(local["allowed"])
        remote = self.privacy.authorize_provider(
            (record.id,), provider="openai", local=False
        )
        self.assertFalse(remote["allowed"])
        policy = self.privacy.update_policy(
            Sensitivity.PERSONAL,
            allowed_providers=("local", "openai"),
            retention_days=120,
            exportable=True,
            deletion_requirement="secure",
            actor="privacy-admin",
            reason="Approved exact processor contract",
        )
        self.assertEqual(policy.version, 2)
        self.assertTrue(self.privacy.authorize_provider(
            (record.id,), provider="openai", local=False
        )["allowed"])
        with self.assertRaises(ValueError):
            self.privacy.update_policy(
                Sensitivity.PERSONAL,
                allowed_providers=("cloud",),
                retention_days=120,
                exportable=True,
                deletion_requirement="secure",
                actor="privacy-admin",
                reason="Wildcard is forbidden",
            )

    def test_reclassification_blocks_implicit_downgrade(self):
        record = self.create(sensitivity=Sensitivity.CONFIDENTIAL)
        with self.assertRaises(PermissionError):
            self.privacy.classify(
                record.id,
                Sensitivity.PUBLIC,
                actor="privacy-admin",
                reason="Review",
            )
        updated = self.privacy.classify(
            record.id,
            Sensitivity.PUBLIC,
            actor="privacy-admin",
            reason="Explicit declassification",
            allow_downgrade=True,
        )
        self.assertEqual(updated.sensitivity, Sensitivity.PUBLIC)

    def test_export_is_all_or_nothing_and_audited(self):
        personal = self.create(
            "Personal exportable preference",
            sensitivity=Sensitivity.PERSONAL,
        )
        confidential = self.create(
            "Confidential nonexportable plan",
            sensitivity=Sensitivity.CONFIDENTIAL,
        )
        exported = self.privacy.export((personal.id,))
        self.assertEqual(
            exported["memories"][0]["content"], personal.content
        )
        with self.assertRaisesRegex(PermissionError, "decision="):
            self.privacy.export((personal.id, confidential.id))
        decisions = self.db.connection.execute(
            """
            SELECT allowed FROM privacy_decisions
            WHERE action='export' ORDER BY created_at
            """
        ).fetchall()
        self.assertEqual([row["allowed"] for row in decisions], [1, 0])

    def test_retention_due_is_content_free(self):
        record = self.create(sensitivity=Sensitivity.PERSONAL)
        past = (
            datetime.now(timezone.utc) - timedelta(days=1)
        ).isoformat()
        self.db.connection.execute(
            "UPDATE memories SET retention_until=? WHERE id=?",
            (past, record.id),
        )
        self.db.connection.commit()
        due = self.privacy.retention_due()
        self.assertEqual(due[0]["id"], record.id)
        self.assertNotIn(record.content, json.dumps(due))

    def test_planned_secure_deletion_erases_fields_fts_and_preserves_audit_id(self):
        content = "Unique personal zephyr schedule"
        record = self.create(
            content, sensitivity=Sensitivity.PERSONAL
        )
        request = self.privacy.plan_deletion(
            record.id,
            requested_by="privacy-admin",
            reason="User erasure request",
        )
        self.assertEqual(request["status"], "planned")
        self.assertNotIn("User erasure request", json.dumps(request))
        completed = self.privacy.approve_deletion(request["id"])
        self.assertEqual(completed["status"], "completed")
        self.assertTrue(
            completed["verification"]["content_fields_erased"]
        )
        self.assertTrue(
            completed["verification"]["backup_cleanup_required"]
        )
        erased = self.db.memories.get(record.id)
        self.assertEqual(erased.content, DELETED_CONTENT)
        self.assertIsNone(erased.subject)
        self.assertEqual(erased.structured_payload_json, "{}")
        self.assertEqual(erased.evidence, ())
        self.assertEqual(erased.status, MemoryStatus.DELETED)
        self.assertEqual(erased.lifecycle_state, LifecycleState.DELETED)
        self.assertEqual(
            self.db.connection.execute(
                """
                SELECT COUNT(*) FROM memories_fts
                WHERE memories_fts MATCH 'zephyr'
                """
            ).fetchone()[0],
            0,
        )
        database_bytes = self.path.read_bytes()
        self.assertNotIn(content.encode("utf-8"), database_bytes)

    def test_changed_memory_invalidates_deletion_plan(self):
        record = self.create()
        request = self.privacy.plan_deletion(
            record.id,
            requested_by="privacy-admin",
            reason="User erasure request",
        )
        self.db.memories.update(
            record.id,
            MemoryPatch(content="Changed content"),
        )
        with self.assertRaisesRegex(RuntimeError, "changed"):
            self.privacy.approve_deletion(request["id"])

    def test_legacy_deleted_state_cannot_bypass_verified_erasure(self):
        record = self.create()
        with self.assertRaisesRegex(ValueError, "PrivacyEngine"):
            self.db.memories.set_status(record.id, MemoryStatus.DELETED)
        with self.assertRaisesRegex(ValueError, "PrivacyEngine"):
            self.db.memories.set_lifecycle(
                record.id, LifecycleState.DELETED
            )

    def test_cli_classification_provider_retention_and_deletion_are_inspectable(self):
        record = self.create(sensitivity=Sensitivity.INTERNAL)
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(main([
                "--db", str(self.path), "privacy", "classify", record.id,
                "personal", "--actor", "privacy-admin",
                "--reason", "Contains a user preference",
            ]), 0)
        self.assertEqual(
            json.loads(output.getvalue())["sensitivity"], "personal"
        )
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(main([
                "--db", str(self.path), "privacy", "provider-check",
                "openai", record.id,
            ]), 0)
        self.assertFalse(json.loads(output.getvalue())["allowed"])
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(main([
                "--db", str(self.path), "privacy", "delete-plan", record.id,
                "--actor", "privacy-admin",
                "--reason", "User requested deletion",
            ]), 0)
        request_id = json.loads(output.getvalue())["id"]
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(main([
                "--db", str(self.path), "privacy", "delete-approve",
                request_id,
            ]), 0)
        self.assertEqual(json.loads(output.getvalue())["status"], "completed")


if __name__ == "__main__":
    unittest.main()
