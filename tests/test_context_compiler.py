from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from acr_runtime import AdaptiveRuntime, ContextCandidate, ContextRequest
from acr_runtime.compiler import PIPELINE


class ExpandedContextCompilerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.runtime = AdaptiveRuntime(Path(self.temp_dir.name) / "acr.db")

    def tearDown(self) -> None:
        self.runtime.close()
        self.temp_dir.cleanup()

    @staticmethod
    def candidate(
        source_type,
        source_id,
        content,
        *,
        required=False,
        dependencies=(),
        utility=0.7,
    ):
        return ContextCandidate(
            source_type=source_type,
            source_id=source_id,
            label=source_id,
            content=content,
            confidence=0.9,
            expected_utility=utility,
            required=required,
            dependencies=dependencies,
            reason="test_candidate",
        )

    def test_all_input_classes_are_attributed_with_required_fields(self):
        request = ContextRequest(
            task="diagnose SQLite migration failure using tools and prior state",
            scope="alpha",
            token_budget=300,
            system_rules=(
                self.candidate(
                    "system_rule", "rule-1", "Never delete the SQLite database.",
                    required=True,
                ),
            ),
            relevant_files=(
                self.candidate(
                    "file", "schema.sql", "SQLite migration schema and indexes."
                ),
            ),
            tool_definitions=(
                self.candidate(
                    "tool", "sqlite-check", "Tool checks SQLite integrity."
                ),
            ),
            agent_state=(
                self.candidate(
                    "agent_state", "state-1", "Migration is awaiting verification."
                ),
            ),
            previous_observations=(
                self.candidate(
                    "observation", "obs-1", "Prior SQLite migration was locked."
                ),
            ),
        )

        bundle = self.runtime.compile_context_request(request)

        self.assertEqual(bundle.pipeline, PIPELINE)
        self.assertEqual(
            {block.source_type for block in bundle.blocks},
            {"system_rule", "file", "tool", "agent_state", "observation"},
        )
        self.assertLessEqual(bundle.total_tokens, request.token_budget)
        for block in bundle.blocks:
            self.assertGreater(block.tokens, 0)
            self.assertGreaterEqual(block.relevance_score, 0)
            self.assertGreaterEqual(block.confidence, 0)
            self.assertGreaterEqual(block.expected_utility, 0)
            self.assertTrue(block.reason_selected)
        rows = self.runtime.db.connection.execute(
            "SELECT source_type FROM context_uses WHERE task_id = ?",
            (bundle.task_id,),
        ).fetchall()
        self.assertEqual(
            {row[0] for row in rows},
            {"system_rule", "file", "tool", "agent_state", "observation"},
        )

    def test_dependency_expansion_and_deduplication_are_deterministic(self):
        dependency = self.candidate(
            "file", "schema.sql", "SQLite schema definition.", utility=0.1
        )
        tool = self.candidate(
            "tool",
            "migration-tool",
            "Use SQLite migration tool.",
            dependencies=("schema.sql",),
        )
        duplicate = self.candidate(
            "observation",
            "duplicate",
            "  use sqlite   MIGRATION tool. ",
            utility=0.2,
        )
        bundle = self.runtime.compile_context_request(
            ContextRequest(
                task="use SQLite migration tool",
                token_budget=100,
                relevant_files=(dependency,),
                tool_definitions=(tool,),
                previous_observations=(duplicate,),
            )
        )

        selected = {block.source_id: block for block in bundle.blocks}
        self.assertIn("migration-tool", selected)
        self.assertIn("schema.sql", selected)
        self.assertTrue(selected["schema.sql"].required)
        self.assertEqual(selected["schema.sql"].reason_selected, "dependency_of:migration-tool")
        self.assertIn("duplicate", {item.source_id for item in bundle.rejected})

    def test_required_context_fails_closed_when_budget_is_insufficient(self):
        with self.assertRaisesRegex(ValueError, "Required context"):
            self.runtime.compile_context_request(
                ContextRequest(
                    task="x",
                    token_budget=5,
                    system_rules=(
                        self.candidate(
                            "system_rule",
                            "large-rule",
                            "mandatory " * 30,
                            required=True,
                        ),
                    ),
                )
            )

    def test_irrelevant_context_is_rejected_instead_of_filling_budget(self):
        bundle = self.runtime.compile_context_request(
            ContextRequest(
                task="diagnose SQLite",
                token_budget=500,
                relevant_files=(
                    self.candidate(
                        "file",
                        "unrelated",
                        "Watercolor landscape painting supplies.",
                    ),
                ),
            )
        )

        self.assertEqual(bundle.blocks, [])
        self.assertLess(bundle.total_tokens, bundle.token_budget)
        self.assertEqual(bundle.rejected[0].reason, "low_marginal_value")


if __name__ == "__main__":
    unittest.main()
