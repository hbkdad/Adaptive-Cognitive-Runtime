from __future__ import annotations

import json
import unittest

from acr_runtime.evaluation import (
    ConstraintJudge,
    EfficiencyJudge,
    EvaluationCase,
    EvaluationPanel,
    ExactMatchJudge,
    JsonSchemaJudge,
    LLMJudge,
    SecurityJudge,
)
from acr_runtime.providers import MockProvider


class EvaluationTests(unittest.TestCase):
    def test_deterministic_panel_checks_correctness_constraints_and_efficiency(self):
        panel = EvaluationPanel(
            [ExactMatchJudge(), ConstraintJudge(), EfficiencyJudge(), SecurityJudge()]
        )
        result = panel.evaluate(
            EvaluationCase(
                objective="Return the answer",
                actual="Paris",
                expected="Paris",
                constraints=("exact:Paris", "max_chars:10"),
                input_tokens=20,
                output_tokens=2,
                token_budget=50,
            )
        )
        self.assertTrue(result.passed)
        self.assertEqual(result.max_disagreement, 0.0)
        self.assertEqual(
            {criterion.criterion for criterion in result.criteria},
            {"correctness", "constraint_compliance", "efficiency", "security"},
        )

    def test_json_schema_and_security_fail_closed(self):
        panel = EvaluationPanel([JsonSchemaJudge(), SecurityJudge()])
        result = panel.evaluate(
            EvaluationCase(
                objective="Return structured data",
                actual='{"answer": "api_key=secret-value"}',
                output_schema_json=json.dumps(
                    {
                        "type": "object",
                        "properties": {"count": {"type": "integer"}},
                        "required": ["count"],
                    }
                ),
            )
        )
        self.assertFalse(result.passed)
        scores = {item.criterion: item.score for item in result.criteria}
        self.assertEqual(scores["schema_compliance"], 0.0)
        self.assertEqual(scores["security"], 0.0)

    def test_llm_judge_requires_explicit_transmission_and_deterministic_peer(self):
        provider = MockProvider(
            lambda request: json.dumps(
                {
                    "correctness": 0.5,
                    "completeness": 1.0,
                    "evidence_quality": 1.0,
                    "feedback": "Mixed result",
                }
            )
        )
        guarded = LLMJudge(provider, model="mock-chat")
        with self.assertRaises(PermissionError):
            guarded.evaluate(
                EvaluationCase(objective="test", actual="A", expected="B")
            )
        with self.assertRaises(ValueError):
            EvaluationPanel(
                [
                    LLMJudge(
                        provider,
                        model="mock-chat",
                        allow_content_transmission=True,
                    )
                ]
            )

    def test_panel_records_llm_and_deterministic_disagreement(self):
        provider = MockProvider(
            lambda request: json.dumps(
                {
                    "correctness": 0.0,
                    "completeness": 1.0,
                    "evidence_quality": 1.0,
                    "feedback": "Judge disagrees",
                }
            )
        )
        panel = EvaluationPanel(
            [
                ExactMatchJudge(),
                LLMJudge(
                    provider,
                    model="mock-chat",
                    allow_content_transmission=True,
                ),
            ]
        )
        result = panel.evaluate(
            EvaluationCase(objective="test", actual="A", expected="A")
        )
        correctness = next(
            item for item in result.criteria if item.criterion == "correctness"
        )
        self.assertEqual(correctness.disagreement, 1.0)
        self.assertEqual(correctness.judge_count, 2)
        self.assertEqual(result.max_disagreement, 1.0)


if __name__ == "__main__":
    unittest.main()

