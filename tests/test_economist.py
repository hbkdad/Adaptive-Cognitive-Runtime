from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from acr_runtime import (
    AdaptiveRuntime,
    ContextCandidate,
    ContextRequest,
    TokenEconomist,
)
from acr_runtime.models import ContextBlock


def block(source_id: str, tokens: int, utility: float) -> ContextBlock:
    return ContextBlock(
        source_type="file",
        source_id=source_id,
        label=source_id,
        content=source_id,
        tokens=tokens,
        relevance_score=1,
        confidence=1,
        expected_utility=utility,
        required=False,
        reason_selected="test",
        roi=utility / tokens,
    )


class TokenEconomistTests(unittest.TestCase):
    def test_initial_heuristic_is_exact_and_explainable(self):
        utility, roi = TokenEconomist.expected_value(
            relevance=0.8,
            confidence=0.9,
            historical_utility=0.5,
            task_importance=0.75,
            token_cost=10,
        )
        self.assertAlmostEqual(utility, 0.27)
        self.assertAlmostEqual(roi, 0.027)

    def test_adaptive_budget_reserves_output_and_reasoning_headroom(self):
        economist = TokenEconomist()
        low = economist.budget(
            "format title",
            requested_input_budget=100,
            task_importance=0.5,
            model_context_window=200,
        )
        medium = economist.budget(
            "diagnose SQLite",
            requested_input_budget=100,
            task_importance=0.5,
            model_context_window=200,
        )
        high = economist.budget(
            "diagnose and architect a multi-step security migration with comparison",
            requested_input_budget=100,
            task_importance=0.9,
            model_context_window=200,
        )
        self.assertGreater(low.output_headroom, 0)
        self.assertGreater(low.reasoning_headroom, 0)
        self.assertLess(low.effective_input_budget, medium.effective_input_budget)
        self.assertLessEqual(medium.effective_input_budget, high.effective_input_budget)
        self.assertLess(
            high.effective_input_budget + high.output_headroom + high.reasoning_headroom,
            high.model_context_window + 1,
        )

    def test_exact_knapsack_beats_greedy_roi_choice(self):
        selected = TokenEconomist.optimize(
            (
                block("a", 6, 0.9),
                block("b", 5, 0.7),
                block("c", 5, 0.7),
            ),
            10,
        )
        self.assertEqual({item.source_id for item in selected}, {"b", "c"})
        self.assertEqual(sum(item.tokens for item in selected), 10)

    def test_outcome_and_budget_plan_are_persisted_for_learning(self):
        with tempfile.TemporaryDirectory() as directory:
            with AdaptiveRuntime(Path(directory) / "acr.db") as runtime:
                bundle = runtime.compile_context_request(
                    ContextRequest(
                        task="diagnose SQLite",
                        scope="alpha",
                        token_budget=100,
                        task_importance=0.9,
                        relevant_files=(
                            ContextCandidate(
                                source_type="file",
                                source_id="schema.sql",
                                label="schema",
                                content="SQLite schema diagnostics",
                                confidence=0.9,
                                expected_utility=0.8,
                            ),
                        ),
                    )
                )
                runtime.complete_task(
                    bundle,
                    success=True,
                    critic_score=0.95,
                    duration_ms=20,
                    useful_source_ids=("schema.sql",),
                )
                plan = runtime.db.connection.execute(
                    "SELECT * FROM token_budget_plans WHERE task_id = ?",
                    (bundle.task_id,),
                ).fetchone()
                outcome = runtime.db.connection.execute(
                    """
                    SELECT useful FROM context_uses
                    WHERE task_id = ? AND source_id = 'schema.sql'
                    """,
                    (bundle.task_id,),
                ).fetchone()
                self.assertEqual(plan["task_importance"], 0.9)
                self.assertGreater(plan["output_headroom"], 0)
                self.assertEqual(plan["selected_count"], 1)
                self.assertEqual(outcome["useful"], 1)
                telemetry = runtime.telemetry_token_economy()
                self.assertEqual(len(telemetry), 1)
                self.assertEqual(telemetry[0]["plans"], 1)
                self.assertGreater(telemetry[0]["output_headroom"], 0)


if __name__ == "__main__":
    unittest.main()
