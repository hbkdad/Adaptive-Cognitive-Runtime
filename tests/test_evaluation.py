from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from acr_runtime import AdaptiveRuntime
from acr_runtime.cli import main
from acr_runtime.evaluation import (
    CompletenessJudge,
    ConstraintJudge,
    EfficiencyJudge,
    EvaluationCase,
    EvaluationEvidence,
    EvaluationPanel,
    EvidenceQualityJudge,
    ExactMatchJudge,
    JsonSchemaJudge,
    LLMJudge,
    SecurityJudge,
    TokenWasteJudge,
    default_deterministic_judges,
)
from acr_runtime.providers import MockProvider


class EvaluationTests(unittest.TestCase):
    def test_default_panel_deterministically_covers_every_required_dimension(self):
        actual = '{"answer":"Paris","evidence":"atlas"}'
        case = EvaluationCase(
            objective="Return an evidenced JSON answer efficiently",
            actual=actual,
            expected=actual,
            required_elements=("Paris", "atlas"),
            constraints=("contains:Paris", "max_chars:100"),
            evidence=(
                EvaluationEvidence(
                    source="reference-atlas",
                    claim="Paris is the answer",
                    verified=True,
                ),
            ),
            output_schema_json=json.dumps(
                {
                    "type": "object",
                    "properties": {"answer": {"type": "string"}},
                    "required": ["answer"],
                }
            ),
            input_tokens=20,
            output_tokens=10,
            token_budget=50,
            necessary_token_estimate=30,
        )
        result = EvaluationPanel(default_deterministic_judges()).evaluate(case)

        self.assertTrue(result.passed)
        self.assertEqual(
            {item.criterion for item in result.criteria},
            {
                "correctness",
                "completeness",
                "constraint_compliance",
                "schema_compliance",
                "evidence_quality",
                "efficiency",
                "security",
                "unnecessary_token_usage",
            },
        )
        self.assertTrue(all(item.grounded for item in result.criteria))
        self.assertTrue(
            all(item.deterministic_count >= 1 for item in result.criteria)
        )

    def test_deterministic_fail_cannot_be_averaged_away_by_llm(self):
        provider = MockProvider(
            lambda request: json.dumps(
                {
                    "correctness": 1.0,
                    "completeness": 1.0,
                    "evidence_quality": 1.0,
                    "feedback": "Confident",
                }
            )
        )
        result = EvaluationPanel(
            [
                ExactMatchJudge(),
                SecurityJudge(),
                LLMJudge(
                    provider,
                    model="mock-chat",
                    allow_content_transmission=True,
                ),
            ],
            pass_threshold=0.5,
        ).evaluate(EvaluationCase(objective="test", actual="A", expected="B"))

        correctness = next(
            item for item in result.criteria if item.criterion == "correctness"
        )
        self.assertEqual(correctness.score, 0.5)
        self.assertFalse(correctness.passed)
        self.assertEqual(correctness.disagreement, 1.0)
        self.assertFalse(result.passed)

    def test_llm_only_criterion_is_recorded_but_ungrounded(self):
        provider = MockProvider(
            lambda request: json.dumps(
                {
                    "correctness": 1.0,
                    "completeness": 1.0,
                    "evidence_quality": 1.0,
                    "feedback": "Looks complete",
                }
            )
        )
        result = EvaluationPanel(
            [
                SecurityJudge(),
                LLMJudge(
                    provider,
                    model="mock-chat",
                    allow_content_transmission=True,
                ),
            ]
        ).evaluate(EvaluationCase(objective="test", actual="A"))

        completeness = next(
            item for item in result.criteria if item.criterion == "completeness"
        )
        self.assertEqual(completeness.llm_count, 1)
        self.assertEqual(completeness.deterministic_count, 0)
        self.assertFalse(completeness.grounded)
        self.assertFalse(completeness.passed)
        self.assertFalse(result.passed)

    def test_llm_judge_requires_authorization_and_valid_bounded_output(self):
        provider = MockProvider(lambda request: "{}")
        guarded = LLMJudge(provider, model="mock-chat")
        with self.assertRaises(PermissionError):
            guarded.evaluate(EvaluationCase(objective="test", actual="A"))
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

        invalid = MockProvider(
            lambda request: json.dumps(
                {
                    "correctness": 1.2,
                    "completeness": 1.0,
                    "evidence_quality": 1.0,
                    "feedback": "invalid",
                }
            )
        )
        with self.assertRaises(ValueError):
            LLMJudge(
                invalid,
                model="mock-chat",
                allow_content_transmission=True,
            ).evaluate(EvaluationCase(objective="test", actual="A"))

    def test_schema_security_completeness_evidence_and_waste_fail_closed(self):
        panel = EvaluationPanel(
            [
                CompletenessJudge(),
                JsonSchemaJudge(),
                EvidenceQualityJudge(),
                EfficiencyJudge(),
                SecurityJudge(),
                TokenWasteJudge(),
            ]
        )
        result = panel.evaluate(
            EvaluationCase(
                objective="Return structured data",
                actual='{"answer": "api_key=secret-value"}',
                required_elements=("missing",),
                evidence=(
                    EvaluationEvidence(
                        source="claim-file", claim="unsupported", verified=False
                    ),
                ),
                output_schema_json=json.dumps(
                    {
                        "type": "object",
                        "properties": {"count": {"type": "integer"}},
                        "required": ["count"],
                    }
                ),
                input_tokens=70,
                output_tokens=30,
                token_budget=80,
                necessary_token_estimate=20,
            )
        )
        self.assertFalse(result.passed)
        scores = {item.criterion: item.score for item in result.criteria}
        for criterion in (
            "completeness",
            "schema_compliance",
            "evidence_quality",
            "security",
            "unnecessary_token_usage",
        ):
            self.assertEqual(scores[criterion], 0.0)

    def test_case_parser_rejects_unknown_fields_and_bad_evidence(self):
        with self.assertRaises(ValueError):
            EvaluationCase.from_dict(
                {"objective": "x", "actual": "y", "surprise": True}
            )
        with self.assertRaises(ValueError):
            EvaluationCase.from_dict(
                {
                    "objective": "x",
                    "actual": "y",
                    "evidence": [
                        {"source": "s", "claim": "c", "verified": "yes"}
                    ],
                }
            )

    def test_results_and_disagreement_are_persisted_without_case_content(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "acr.db"
            secret_candidate = "private candidate text 74c71b"
            with AdaptiveRuntime(path) as runtime:
                run = runtime.evaluate(
                    EvaluationCase(
                        objective="private objective 74c71b",
                        actual=secret_candidate,
                        expected=secret_candidate,
                    )
                )
                loaded = runtime.evaluation(run.id)
                self.assertEqual(loaded.as_dict(), run.as_dict())
                metadata = runtime.db.connection.execute(
                    "SELECT case_metadata_json FROM evaluation_runs WHERE id = ?",
                    (run.id,),
                ).fetchone()[0]
                self.assertNotIn(secret_candidate, metadata)
                self.assertEqual(
                    runtime.db.connection.execute(
                        """
                        SELECT COUNT(*) FROM evaluation_criterion_results
                        WHERE run_id = ?
                        """,
                        (run.id,),
                    ).fetchone()[0],
                    len(run.result.criteria),
                )

    def test_cli_runs_and_reports_retained_evaluation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / "acr.db"
            case_file = root / "case.json"
            case_file.write_text(
                json.dumps(
                    {
                        "objective": "Answer",
                        "actual": "Paris",
                        "expected": "Paris",
                        "required_elements": ["Paris"],
                    }
                ),
                encoding="utf-8",
            )
            output = io.StringIO()
            with redirect_stdout(output):
                self.assertEqual(
                    main(
                        [
                            "--db",
                            str(database),
                            "evaluate",
                            "run",
                            str(case_file),
                        ]
                    ),
                    0,
                )
            run = json.loads(output.getvalue())
            report = io.StringIO()
            with redirect_stdout(report):
                self.assertEqual(
                    main(
                        [
                            "--db",
                            str(database),
                            "evaluate",
                            "report",
                            run["id"],
                        ]
                    ),
                    0,
                )
            self.assertEqual(json.loads(report.getvalue())["id"], run["id"])

    def test_constraint_bad_numeric_value_fails_instead_of_crashing(self):
        result = EvaluationPanel([ConstraintJudge()]).evaluate(
            EvaluationCase(
                objective="test",
                actual="text",
                constraints=("max_chars:not-a-number",),
            )
        )
        self.assertFalse(result.passed)


if __name__ == "__main__":
    unittest.main()
