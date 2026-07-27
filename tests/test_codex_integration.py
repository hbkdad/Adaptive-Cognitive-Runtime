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
        ):
            self.assertIn(required, source)

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


if __name__ == "__main__":
    unittest.main()
