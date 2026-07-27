from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from acr_runtime import AdaptiveRuntime, EvaluationCase
from acr_runtime.cli import main
from acr_runtime.reflection import (
    REQUIRED_CATEGORIES,
    CheaperModelEvidence,
    MissingInformation,
    ReflectedContext,
    ReflectedToolCall,
    ReflectionBudget,
    ReflectionRequest,
    ReusableExperience,
)


class ReflectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.database = Path(self.directory.name) / "acr.db"
        self.runtime = AdaptiveRuntime(self.database)

    def tearDown(self) -> None:
        self.runtime.close()
        self.directory.cleanup()

    def request(self, **changes: object) -> ReflectionRequest:
        evaluation = self.runtime.evaluate(
            EvaluationCase(objective="Answer", actual="Paris", expected="Paris"),
            task_id="task-1",
        )
        payload: dict[str, object] = {
            "task_id": "task-1",
            "task_success": True,
            "evaluation_run_id": evaluation.id,
            "context": (
                ReflectedContext(
                    "memory",
                    "memory-1",
                    40,
                    "contributed",
                    ("attribution:memory-1",),
                ),
                ReflectedContext(
                    "skill",
                    "skill-1",
                    30,
                    "ignored",
                    ("attribution:skill-1",),
                ),
                ReflectedContext(
                    "file", "file-1", 10, "uncertain", ()
                ),
            ),
            "tool_calls": (
                ReflectedToolCall(
                    "call-1",
                    "broad-search",
                    "unnecessary",
                    True,
                    120,
                    ("trace:call-1",),
                ),
            ),
            "model_candidates": (
                CheaperModelEvidence(
                    "small-model",
                    1.0,
                    0.25,
                    True,
                    True,
                    ("benchmark:model-small-v1",),
                ),
            ),
            "missing_information": (
                MissingInformation(
                    "deployment-region", ("evaluation:missing-region",)
                ),
            ),
            "reusable_experience": (
                ReusableExperience(
                    "experience-1",
                    "procedure",
                    0.9,
                    0.8,
                    ("trace:successful-procedure",),
                ),
            ),
        }
        payload.update(changes)
        return ReflectionRequest(**payload)

    def test_one_pass_answers_all_nine_questions_with_structured_findings(self):
        run = self.runtime.reflect(self.request())

        self.assertEqual(
            tuple(item.category for item in run.findings),
            REQUIRED_CATEGORIES,
        )
        findings = {item.category: item for item in run.findings}
        self.assertEqual(
            findings["what_worked"].verdict, "task_and_evaluation_passed"
        )
        self.assertEqual(
            findings["unnecessary_context"].subject_ids, ("skill-1",)
        )
        self.assertEqual(findings["memory_impact"].verdict, "helped")
        self.assertEqual(findings["skill_impact"].verdict, "did_not_help")
        self.assertEqual(
            findings["model_economy"].verdict, "verified_cheaper_model"
        )
        self.assertEqual(
            findings["tool_economy"].verdict,
            "unnecessary_tool_calls_observed",
        )
        self.assertEqual(
            findings["reusable_experience"].verdict,
            "reusable_candidate_observed",
        )
        self.assertEqual(run.reflection_depth, 1)
        self.assertLessEqual(
            run.estimated_output_tokens, run.budget.max_output_tokens
        )
        for finding in run.as_dict()["findings"]:
            self.assertEqual(
                set(finding),
                {"category", "verdict", "subject_ids", "evidence", "metrics"},
            )

    def test_reflection_is_retained_and_does_not_mutate_learning_state(self):
        memory_before = self.runtime.db.connection.execute(
            "SELECT COUNT(*) FROM memories"
        ).fetchone()[0]
        experience_before = self.runtime.db.connection.execute(
            "SELECT COUNT(*) FROM experience_traces"
        ).fetchone()[0]

        run = self.runtime.reflect(self.request())
        loaded = self.runtime.reflection(run.id)

        self.assertEqual(loaded.as_dict(), run.as_dict())
        self.assertEqual(
            self.runtime.db.connection.execute(
                "SELECT COUNT(*) FROM memories"
            ).fetchone()[0],
            memory_before,
        )
        self.assertEqual(
            self.runtime.db.connection.execute(
                "SELECT COUNT(*) FROM experience_traces"
            ).fetchone()[0],
            experience_before,
        )
        self.assertEqual(
            self.runtime.db.connection.execute(
                "SELECT finding_count FROM reflection_runs WHERE id = ?",
                (run.id,),
            ).fetchone()[0],
            9,
        )

    def test_reflection_cannot_recurse_or_expand_pass_budget(self):
        with self.assertRaises(ValueError):
            self.request(reflection_depth=1)
        with self.assertRaises(ValueError):
            ReflectionBudget(max_passes=2)
        with self.assertRaises(ValueError):
            ReflectionBudget(max_findings=8)

    def test_strict_output_and_input_budgets_fail_closed(self):
        with self.assertRaises(ValueError):
            self.runtime.reflect(
                self.request(budget=ReflectionBudget(max_output_tokens=256))
            )
        with self.assertRaises(ValueError):
            ReflectionRequest(
                task_id="task",
                task_success=True,
                context=tuple(
                    ReflectedContext(
                        "other", f"source-{index}", 1, "uncertain", ()
                    )
                    for index in range(129)
                ),
            )

    def test_asserted_impact_and_necessity_require_evidence(self):
        with self.assertRaises(ValueError):
            ReflectedContext(
                "memory", "memory-1", 10, "contributed", ()
            )
        with self.assertRaises(ValueError):
            ReflectedToolCall(
                "call-1", "search", "unnecessary", True, 10, ()
            )
        with self.assertRaises(ValueError):
            CheaperModelEvidence(
                "small", 1.0, 0.5, True, True, ()
            )

    def test_unverified_model_is_not_declared_cheaper_capable(self):
        run = self.runtime.reflect(
            self.request(
                model_candidates=(
                    CheaperModelEvidence(
                        "small-model",
                        1.0,
                        0.1,
                        True,
                        False,
                        ("capability-check:small",),
                    ),
                )
            )
        )
        finding = next(
            item for item in run.findings if item.category == "model_economy"
        )
        self.assertEqual(finding.verdict, "no_verified_cheaper_model")
        self.assertEqual(finding.subject_ids, ())

    def test_task_success_without_evaluation_is_not_mislabeled(self):
        run = self.runtime.reflect(
            ReflectionRequest(task_id="task-no-eval", task_success=True)
        )
        finding = next(
            item for item in run.findings if item.category == "what_worked"
        )
        self.assertEqual(finding.verdict, "task_passed")

    def test_evaluation_must_belong_to_same_task(self):
        other = self.runtime.evaluate(
            EvaluationCase(objective="Other", actual="yes", expected="yes"),
            task_id="other-task",
        )
        with self.assertRaises(ValueError):
            self.runtime.reflect(self.request(evaluation_run_id=other.id))

    def test_parser_rejects_unknown_fields_and_duplicate_trace_ids(self):
        with self.assertRaises(ValueError):
            ReflectionRequest.from_dict(
                {"task_id": "task", "task_success": True, "essay": "no"}
            )
        tool = {
            "call_id": "same",
            "tool": "search",
            "necessity": "uncertain",
            "success": True,
            "latency_ms": 1,
            "evidence": [],
        }
        with self.assertRaises(ValueError):
            ReflectionRequest.from_dict(
                {
                    "task_id": "task",
                    "task_success": True,
                    "tool_calls": [tool, tool],
                }
            )

    def test_cli_runs_and_reports_reflection(self):
        self.runtime.close()
        request_file = Path(self.directory.name) / "reflection.json"
        request_file.write_text(
            json.dumps(
                {
                    "task_id": "task-cli",
                    "task_success": True,
                    "context": [
                        {
                            "source_type": "memory",
                            "source_id": "memory-cli",
                            "tokens": 8,
                            "outcome": "contributed",
                            "evidence": ["attribution:cli"],
                        }
                    ],
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
                        str(self.database),
                        "reflect",
                        "run",
                        str(request_file),
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
                        str(self.database),
                        "reflect",
                        "report",
                        run["id"],
                    ]
                ),
                0,
            )
        self.assertEqual(json.loads(report.getvalue())["id"], run["id"])
        self.runtime = AdaptiveRuntime(self.database)


if __name__ == "__main__":
    unittest.main()
