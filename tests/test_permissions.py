import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path

from acr_runtime.db import RuntimeDB
from acr_runtime.permissions import (
    CapabilityCheck,
    CapabilityGrantRequest,
    PermissionController,
)
from acr_runtime.cli import main


class PermissionControllerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "acr.db"
        self.db = RuntimeDB(self.path)
        self.permissions = PermissionController(self.db.connection)

    def tearDown(self):
        self.db.close()
        self.temp.cleanup()

    @staticmethod
    def future(hours=2):
        return (datetime.now(timezone.utc) + timedelta(hours=hours)).isoformat()

    def request(self, **changes):
        values = {
            "subject_type": "task", "subject_id": "task-36",
            "capability": "database.read", "resource_scope": "database:demo",
            "expires_at": self.future(), "delegable": False,
            "grantor_type": "trusted_workflow", "grantor_id": "operator-36",
            "reason": "Exact task requirement", "evidence": ("test:requirement",),
        }
        values.update(changes)
        return CapabilityGrantRequest(**values)

    def test_default_deny_and_exact_scoped_grant(self):
        denied = self.permissions.check(CapabilityCheck(
            "task", "task-36", "database.read", "database:demo"
        ))
        self.assertFalse(denied["allowed"])
        grant = self.permissions.grant(self.request())
        allowed = self.permissions.check(CapabilityCheck(
            "task", "task-36", "database.read", "database:demo"
        ))
        self.assertTrue(allowed["allowed"])
        self.assertEqual(allowed["grant_id"], grant["id"])
        self.assertFalse(self.permissions.check(CapabilityCheck(
            "task", "task-36", "database.write", "database:demo"
        ))["allowed"])
        self.assertFalse(self.permissions.check(CapabilityCheck(
            "task", "task-36", "database.read", "database:other"
        ))["allowed"])

    def test_delegation_cannot_expand_scope_capability_or_expiry(self):
        parent = self.permissions.grant(self.request(
            subject_type="agent", subject_id="agent-36", delegable=True,
            expires_at=self.future(2),
        ))
        child = self.permissions.grant(self.request(
            subject_id="task-child", grantor_type="agent",
            grantor_id="agent-36", expires_at=self.future(1),
        ))
        self.assertEqual(child["parent_grant_id"], parent["id"])
        with self.assertRaises(PermissionError):
            self.permissions.grant(self.request(
                subject_id="task-other", grantor_type="agent",
                grantor_id="agent-36", resource_scope="database:other",
                expires_at=self.future(1),
            ))
        with self.assertRaises(PermissionError):
            self.permissions.grant(self.request(
                subject_id="task-other", grantor_type="agent",
                grantor_id="agent-36", expires_at=self.future(3),
            ))

    def test_skills_cannot_grant_and_parent_revocation_cascades(self):
        parent = self.permissions.grant(self.request(
            subject_type="agent", subject_id="agent-36", delegable=True,
        ))
        child = self.permissions.grant(self.request(
            subject_id="task-child", grantor_type="agent",
            grantor_id="agent-36", expires_at=self.future(1),
        ))
        with self.assertRaises(PermissionError):
            self.permissions.grant(self.request(
                subject_id="task-other", grantor_type="skill",
                grantor_id="skill-36",
            ))
        revoked = self.permissions.revoke(
            parent["id"], reason="Parent authority withdrawn"
        )
        self.assertEqual(revoked["cascade_revoked"], 2)
        self.assertIsNotNone(self.permissions.get(child["id"])["revoked_at"])
        self.assertFalse(self.permissions.check(CapabilityCheck(
            "task", "task-child", "database.read", "database:demo"
        ))["allowed"])

    def test_expired_and_nondelegable_authority_fail_closed(self):
        grant = self.permissions.grant(self.request())
        self.db.connection.execute(
            "UPDATE capability_grants SET expires_at=? WHERE id=?",
            (
                (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(),
                grant["id"],
            ),
        )
        self.db.connection.commit()
        self.assertFalse(self.permissions.check(CapabilityCheck(
            "task", "task-36", "database.read", "database:demo"
        ))["allowed"])
        parent = self.permissions.grant(self.request(
            subject_type="agent", subject_id="agent-locked",
            delegable=False,
        ))
        self.assertTrue(parent["id"])
        with self.assertRaises(PermissionError):
            self.permissions.grant(self.request(
                subject_id="task-other", grantor_type="agent",
                grantor_id="agent-locked", expires_at=self.future(1),
            ))

    def test_closed_vocabulary_and_bounded_scope_fail_closed(self):
        with self.assertRaises(ValueError):
            self.request(capability="database.admin")
        with self.assertRaises(ValueError):
            self.request(resource_scope="*")
        with self.assertRaises(ValueError):
            CapabilityCheck("task", "task-36", "database.read", "global")

    def test_cli_grant_check_list_and_revoke_are_inspectable(self):
        grant_file = Path(self.temp.name) / "grant.json"
        grant_file.write_text(json.dumps({
            "subject_type": "task", "subject_id": "task-cli",
            "capability": "memory.read", "resource_scope": "memory:project",
            "expires_at": self.future(), "delegable": False,
            "grantor_type": "trusted_workflow", "grantor_id": "operator-cli",
            "reason": "CLI integration test", "evidence": ["test:cli"],
        }), encoding="utf-8")
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(main([
                "--db", str(self.path), "capabilities", "grant",
                str(grant_file),
            ]), 0)
        grant = json.loads(output.getvalue())

        check_file = Path(self.temp.name) / "check.json"
        check_file.write_text(json.dumps({
            "subject_type": "task", "subject_id": "task-cli",
            "capability": "memory.read", "resource_scope": "memory:project",
        }), encoding="utf-8")
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(main([
                "--db", str(self.path), "capabilities", "check",
                str(check_file),
            ]), 0)
        self.assertTrue(json.loads(output.getvalue())["allowed"])

        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(main([
                "--db", str(self.path), "capabilities", "list",
                "task", "task-cli",
            ]), 0)
        self.assertEqual(len(json.loads(output.getvalue())), 1)

        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(main([
                "--db", str(self.path), "capabilities", "revoke",
                grant["id"], "--reason", "CLI test complete",
            ]), 0)
        self.assertEqual(json.loads(output.getvalue())["cascade_revoked"], 1)


if __name__ == "__main__":
    unittest.main()
