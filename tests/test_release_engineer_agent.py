from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from acr_runtime.agent_spec import AgentSpec
from acr_runtime.release_engineering import (
    RELEASE_GATES,
    ReleaseEvidenceManifest,
    main,
)


ROOT = Path(__file__).parents[1]
CREATED = datetime.now(timezone.utc).replace(microsecond=0)


def _gate(gate: str, *, status: str = "passed") -> dict[str, object]:
    completed = CREATED - timedelta(minutes=10)
    available = status != "unavailable"
    return {
        "gate": gate,
        "status": status,
        "command": f"python -m release_gate {gate}",
        "exit_code": (0 if status == "passed" else 1) if available else None,
        "run_ref": f"ci:{gate}:123" if available else None,
        "artifact_sha256": ("a" * 64) if available else None,
        "completed_at": completed.isoformat(),
        "evidence": ["Bounded exact-command result."],
    }


def _manifest(
    *,
    statuses: dict[str, str] | None = None,
    immutable: bool = True,
    tag_absent: bool = True,
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "version": "0.2.0",
        "tag": "v0.2.0",
        "commit_sha": "b" * 40,
        "created_at": CREATED.isoformat(),
        "gates": [
            _gate(gate, status=(statuses or {}).get(gate, "passed"))
            for gate in RELEASE_GATES
        ],
        "tag_absent": tag_absent,
        "tag_check_ref": "git:tag-list:v0.2.0",
        "immutable_release_enabled": immutable,
        "immutability_check_ref": "github:settings:immutable-releases",
    }


class ReleaseEngineerAgentTests(unittest.TestCase):
    def test_ready_manifest_requires_all_nine_ordered_passes(self) -> None:
        manifest = ReleaseEvidenceManifest.from_dict(_manifest())
        self.assertTrue(manifest.ready_to_tag)
        self.assertEqual(manifest.blockers, ())
        self.assertEqual(len(manifest.review_hash), 64)

        payload = _manifest()
        payload["gates"] = payload["gates"][:-1]
        with self.assertRaisesRegex(ValueError, "every release gate"):
            ReleaseEvidenceManifest.from_dict(payload)

    def test_failed_unavailable_or_missing_immutability_blocks(self) -> None:
        failed = ReleaseEvidenceManifest.from_dict(
            _manifest(statuses={"api": "failed"})
        )
        self.assertFalse(failed.ready_to_tag)
        self.assertIn("gate:api:failed", failed.blockers)

        unavailable = ReleaseEvidenceManifest.from_dict(
            _manifest(statuses={"upgrade": "unavailable"})
        )
        self.assertIn("gate:upgrade:unavailable", unavailable.blockers)

        protection = ReleaseEvidenceManifest.from_dict(
            _manifest(immutable=False, tag_absent=False)
        )
        self.assertIn("tag:already_exists", protection.blockers)
        self.assertIn("github:immutable_release_disabled", protection.blockers)

    def test_passed_gate_requires_exit_hash_and_reference(self) -> None:
        for field, value in (
            ("exit_code", 1),
            ("run_ref", None),
            ("artifact_sha256", None),
        ):
            payload = _manifest()
            payload["gates"][0][field] = value
            with self.subTest(field=field):
                with self.assertRaises(ValueError):
                    ReleaseEvidenceManifest.from_dict(payload)

    def test_stale_or_future_evidence_is_rejected(self) -> None:
        for delta in (timedelta(hours=25), timedelta(minutes=-1)):
            payload = _manifest()
            payload["gates"][0]["completed_at"] = (CREATED - delta).isoformat()
            with self.subTest(delta=delta):
                with self.assertRaisesRegex(ValueError, "stale or from the future"):
                    ReleaseEvidenceManifest.from_dict(payload)

        for delta in (timedelta(hours=25), timedelta(minutes=-10)):
            payload = _manifest()
            payload["created_at"] = (CREATED - delta).isoformat()
            payload["gates"] = [
                _gate(gate) for gate in RELEASE_GATES
            ]
            if delta == timedelta(hours=25):
                for gate in payload["gates"]:
                    gate["completed_at"] = (
                        CREATED - delta - timedelta(minutes=1)
                    ).isoformat()
            with self.subTest(manifest_delta=delta):
                with self.assertRaisesRegex(ValueError, "manifest is stale"):
                    ReleaseEvidenceManifest.from_dict(payload)

    def test_cli_returns_ready_blocked_and_invalid_codes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "release.json"
            for payload, expected in (
                (_manifest(), 0),
                (_manifest(statuses={"tests": "failed"}), 1),
            ):
                path.write_text(json.dumps(payload), encoding="utf-8")
                output = io.StringIO()
                with contextlib.redirect_stdout(output):
                    code = main(["validate", str(path)])
                self.assertEqual(code, expected)
                self.assertTrue(json.loads(output.getvalue())["valid"])

            path.write_text("{}", encoding="utf-8")
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main(["validate", str(path)]), 2)

    def test_role_workflow_and_changelog_are_safe_and_complete(self) -> None:
        payload = json.loads(
            (
                ROOT / "examples" / "agent-spec" / "release-engineer-worker.json"
            ).read_text(encoding="utf-8")
        )
        spec = AgentSpec.from_dict(payload)
        self.assertEqual(spec.task_scope, ("release",))
        self.assertEqual(spec.tools, ())
        self.assertEqual(spec.permissions, ())
        self.assertEqual(spec.communication.mode, "none")
        self.assertTrue(spec.model_policy.local_only)
        self.assertFalse(spec.model_policy.allow_fallback)
        self.assertEqual(spec.money_budget, 0)

        source = (
            ROOT / "docs" / "agents" / "release-engineer.md"
        ).read_text(encoding="utf-8")
        for gate in RELEASE_GATES:
            self.assertIn(gate, source)
        self.assertIn("not an executable worker", source)
        self.assertIn("without `--force`", source)
        self.assertIn("separate externally visible actions", source)
        self.assertIn("## Unreleased", (ROOT / "CHANGELOG.md").read_text())


if __name__ == "__main__":
    unittest.main()
