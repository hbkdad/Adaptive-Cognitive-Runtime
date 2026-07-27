from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from acr_runtime.memory import (
    MemoryCreate,
    MemoryStatus,
    MemoryType,
    Sensitivity,
)
from acr_runtime.permissions import (
    CapabilityGrantRequest,
    PermissionController,
)
from acr_runtime.service import AdaptiveRuntime


ROOT = Path(__file__).parents[1]
HOOK = ROOT / "scripts" / "claude_acr_hook.py"


class ClaudeCodeIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.database = Path(self.temporary.name) / "acr.db"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    @staticmethod
    def future() -> str:
        return (
            datetime.now(timezone.utc) + timedelta(hours=1)
        ).isoformat()

    def _grant(
        self,
        permissions: PermissionController,
        capability: str,
        resource_scope: str,
    ) -> None:
        permissions.grant(
            CapabilityGrantRequest(
                subject_type="agent",
                subject_id="claude-code-local",
                capability=capability,
                resource_scope=resource_scope,
                expires_at=self.future(),
                delegable=False,
                grantor_type="trusted_workflow",
                grantor_id="claude-hook-test",
                reason="Prompt 58 hook test",
                evidence=("test:claude-hook",),
            )
        )

    def _hook(
        self, mode: str, event: dict[str, object]
    ) -> subprocess.CompletedProcess[bytes]:
        command = [sys.executable, str(HOOK), mode]
        if mode == "preflight":
            command.extend(
                [
                    "--database",
                    str(self.database),
                    "--scope",
                    "acr",
                    "--subject-id",
                    "claude-code-local",
                ]
            )
        return subprocess.run(
            command,
            input=json.dumps(event).encode(),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=ROOT,
            timeout=15,
            check=False,
        )

    def test_checked_in_contract_is_small_external_and_default_deny(self) -> None:
        claude = (ROOT / "CLAUDE.md").read_text(encoding="utf-8")
        self.assertLess(len(claude.encode()), 2_000)
        self.assertTrue(claude.startswith("@AGENTS.md"))
        self.assertIn("external persistent intelligence", claude)

        mcp = json.loads((ROOT / ".mcp.json").read_text(encoding="utf-8"))
        server = mcp["mcpServers"]["acr"]
        self.assertEqual(server["type"], "stdio")
        self.assertIn("claude-code-local", server["args"])
        self.assertEqual(server["env"], {})
        self.assertNotIn("alwaysLoad", server)

        settings = json.loads(
            (ROOT / ".claude" / "settings.json").read_text(encoding="utf-8")
        )
        self.assertFalse(settings["autoMemoryEnabled"])
        self.assertEqual(
            set(settings["hooks"]), {"UserPromptSubmit", "Stop"}
        )
        serialized = json.dumps(settings)
        self.assertNotIn("transcript_path", serialized)
        self.assertNotIn("dangerously", serialized)

    def test_preflight_returns_bounded_authority_free_evidence(self) -> None:
        unique_prompt = "Implement SQLite code feature marker-claude-58"
        with AdaptiveRuntime(self.database) as runtime:
            runtime.db.memories.create(
                MemoryCreate(
                    type=MemoryType.SEMANTIC,
                    content="The SQLite code architecture stays local-first.",
                    scope="acr",
                    status=MemoryStatus.CONFIRMED,
                    sensitivity=Sensitivity.INTERNAL,
                )
            )
            permissions = PermissionController(
                runtime.db.connection, runtime.content_security
            )
            self._grant(permissions, "memory.read", "memory:acr")
            self._grant(permissions, "database.read", "skills:registry")

        result = self._hook(
            "preflight",
            {
                "hook_event_name": "UserPromptSubmit",
                "session_id": "session-58",
                "prompt": unique_prompt,
            },
        )
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        payload = json.loads(result.stdout)
        context = payload["hookSpecificOutput"]["additionalContext"]
        self.assertLessEqual(len(context), 9_000)
        self.assertIn('"authority":"none"', context)
        self.assertIn("SQLite code architecture", context)
        self.assertIn("<untrusted_data", context)

        with AdaptiveRuntime(self.database) as runtime:
            persisted = "\n".join(
                str(value)
                for table in ("tasks", "content_security_assessments")
                for row in runtime.db.connection.execute(f"SELECT * FROM {table}")
                for value in row
            )
        self.assertNotIn(unique_prompt, persisted)

    def test_preflight_skips_non_code_and_reports_missing_grants(self) -> None:
        greeting = self._hook(
            "preflight",
            {
                "hook_event_name": "UserPromptSubmit",
                "prompt": "Hello, how are you?",
            },
        )
        self.assertEqual(greeting.stdout, b"")

        denied = self._hook(
            "preflight",
            {
                "hook_event_name": "UserPromptSubmit",
                "prompt": "Implement a repository feature",
            },
        )
        self.assertEqual(denied.returncode, 0)
        context = json.loads(denied.stdout)[
            "hookSpecificOutput"
        ]["additionalContext"]
        self.assertIn("permission_denied", context)

    def test_postflight_requests_one_non_persisting_learning_review(self) -> None:
        event = {
            "hook_event_name": "Stop",
            "stop_hook_active": False,
            "last_assistant_message": (
                "Implemented the fix. Tests passed and files changed."
            ),
        }
        result = self._hook("postflight", event)
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        context = json.loads(result.stdout)[
            "hookSpecificOutput"
        ]["additionalContext"]
        self.assertIn("ACR learning candidates", context)
        self.assertIn("do not write memory", context)

        event["stop_hook_active"] = True
        active = self._hook("postflight", event)
        self.assertEqual(active.stdout, b"")


if __name__ == "__main__":
    unittest.main()
