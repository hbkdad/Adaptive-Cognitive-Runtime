from __future__ import annotations

import json
import unittest
from pathlib import Path

from acr_runtime.agent_spec import AgentSpec


ROOT = Path(__file__).parents[1]


class BugFixAgentTests(unittest.TestCase):
    def test_workflow_is_evidence_first_ordered_and_non_random(self) -> None:
        source = (ROOT / "docs" / "agents" / "bug-fix.md").read_text(
            encoding="utf-8"
        )
        normalized = " ".join(source.split())
        ordered = (
            "Reproduce first.",
            "Capture the exact error.",
            "Identify the smallest failing boundary.",
            "Inspect recent changes.",
            "Form hypotheses.",
            "Test hypotheses.",
            "Fix the root cause.",
            "Add a regression test.",
            "Verify.",
        )
        positions = tuple(normalized.index(item) for item in ordered)
        self.assertEqual(positions, tuple(sorted(positions)))
        self.assertIn(
            "Do not mask symptoms, swallow exceptions, weaken tests, or make "
            "random edits until the error disappears.",
            normalized,
        )
        self.assertIn("both positive and negative results", normalized)

    def test_runtime_role_template_is_valid_bounded_and_least_privilege(self) -> None:
        payload = json.loads(
            (
                ROOT / "examples" / "agent-spec" / "bug-fix-worker.json"
            ).read_text(encoding="utf-8")
        )
        spec = AgentSpec.from_dict(payload)
        self.assertEqual(spec.id, "bug-fix-worker")
        self.assertEqual(spec.task_scope, ("bug-fix",))
        self.assertEqual(spec.tools, ())
        self.assertEqual(spec.permissions, ())
        self.assertEqual(spec.communication.mode, "none")
        self.assertTrue(spec.model_policy.local_only)
        self.assertFalse(spec.model_policy.allow_fallback)
        self.assertEqual(spec.money_budget, 0)
        self.assertTrue(
            any("regression test" in item for item in spec.verification_requirements)
        )

    def test_failure_memory_requires_authorization_and_verified_cause(self) -> None:
        source = (ROOT / "docs" / "agents" / "bug-fix.md").read_text(
            encoding="utf-8"
        )
        normalized = " ".join(source.split())
        self.assertIn(
            "only when the task explicitly authorizes ACR state changes",
            normalized,
        )
        self.assertIn("not an executable worker", normalized)
        self.assertIn("The template itself cannot change code or write memory", normalized)
        self.assertIn("root cause, resolution, avoidance rule", normalized)
        self.assertIn("Do not store raw logs", normalized)
        self.assertIn("not itself a confirmed failure-memory root cause", normalized)

    def test_root_contract_routes_debugging_without_exceeding_budget(self) -> None:
        source = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
        self.assertLess(len(source.encode("utf-8")), 4_096)
        self.assertIn("docs/agents/bug-fix.md", source)
        self.assertIn("Reproduce and", source)
        self.assertIn("never make random edits", source)


if __name__ == "__main__":
    unittest.main()
