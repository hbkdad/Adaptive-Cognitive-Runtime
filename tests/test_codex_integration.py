from __future__ import annotations

import tomllib
import unittest
from pathlib import Path

from acr_runtime.mcp_stdio import TOOL_CATALOG


ROOT = Path(__file__).parents[1]


class CodexIntegrationTests(unittest.TestCase):
    def test_project_config_is_bounded_and_matches_provider_catalog(self) -> None:
        config = tomllib.loads(
            (ROOT / ".codex" / "config.toml").read_text(encoding="utf-8")
        )
        server = config["mcp_servers"]["acr"]
        self.assertEqual(server["command"], "python")
        self.assertEqual(server["cwd"], "..")
        self.assertFalse(server["required"])
        self.assertEqual(server["default_tools_approval_mode"], "auto")
        enabled = server["enabled_tools"]
        catalog = {item["name"] for item in TOOL_CATALOG}
        self.assertEqual(set(enabled), catalog - {"execute_skill"})
        self.assertNotIn("execute_skill", enabled)
        self.assertEqual(
            server["tools"]["retrieve_context"]["approval_mode"], "prompt"
        )
        joined = "\n".join(str(item) for item in server["args"])
        self.assertIn("codex-local", joined)
        self.assertNotIn("token", config["mcp_servers"]["acr"])

    def test_agents_contract_is_small_and_covers_both_phases(self) -> None:
        source = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
        self.assertLess(len(source.encode("utf-8")), 4_096)
        normalized = " ".join(source.split())
        for required in (
            "Before non-trivial coding work",
            "search_memory",
            "failure_lookup",
            "find_skill",
            "retrieve_context",
            "After verified work",
            "architecture changes",
            "successful procedures",
            "diagnosed failures",
            "Do not inject the entire project history",
            "Inspect the affected subsystem",
            "search for existing interfaces",
            "read adjacent tests",
            "identify architecture constraints",
            "minimum complete change",
            "avoid unrelated refactors",
            "Run targeted tests first",
            "broader relevant tests",
            "Inspect the diff",
            "update affected documentation",
            "available metrics",
            "Never invent unavailable measurements",
        ):
            self.assertIn(required, normalized)

        ordered = (
            "Inspect the affected subsystem",
            "Implement the minimum complete change",
            "Add or update focused tests",
            "Run targeted tests first",
            "Inspect the diff",
        )
        positions = tuple(normalized.index(item) for item in ordered)
        self.assertEqual(positions, tuple(sorted(positions)))

    def test_guidance_uses_real_cli_and_no_blanket_persistence(self) -> None:
        source = (
            ROOT / "docs" / "integrations" / "codex.md"
        ).read_text(encoding="utf-8")
        self.assertIn("memory consider decision", source)
        self.assertIn("memory consider procedural", source)
        self.assertIn("failure record", source)
        self.assertIn("code slice", source)
        self.assertIn("explicitly authorizes ACR state changes", source)
        self.assertNotIn("save every", source.casefold())

    def test_development_agent_checklist_is_documented_without_parallel_policy(self) -> None:
        source = (
            ROOT / "docs" / "specs" / "development-agent-instructions.md"
        ).read_text(encoding="utf-8")
        for required in (
            "inspect the affected subsystem",
            "search for existing interfaces",
            "read adjacent tests",
            "identify architecture constraints",
            "minimum complete change",
            "targeted tests and then broader relevant tests",
            "inspect the diff and staged secret scan",
            "update affected documentation",
            "without fabricating unavailable metrics",
            "Unrelated refactors are outside the task",
        ):
            self.assertIn(required, source)
        self.assertNotIn("spawn", source.casefold())


if __name__ == "__main__":
    unittest.main()
