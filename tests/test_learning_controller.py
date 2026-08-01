from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from acr_runtime import (
    AdaptiveRuntime,
    AttributionSignals,
    EvaluationCase,
    ExperienceEvent,
    ExperienceEventKind,
    ExperienceTraceCreate,
    LearningRequest,
    RegressionBaseline,
)
from acr_runtime.cli import main


class LearningControllerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.database = Path(self.directory.name) / "acr.db"
        self.runtime = AdaptiveRuntime(self.database)
        self.memory_id = self.runtime.remember(
            "semantic",
            "SQLite is local.",
            scope="learning-test",
            confidence=0.95,
            evidence=("test:memory",),
        )
        self.skill_id = self.runtime.register_skill(
            "learning-test-skill",
            "Inspect SQLite evidence.",
            tags=("learning",),
            trusted=True,
        )
        self.task_id = self.runtime.db.create_task(
            objective="Answer from retained evidence",
            scope="learning-test",
            token_budget=100,
        )
        self.runtime.db.record_context(
            self.task_id,
            (
                {
                    "source_type": "memory",
                    "source_id": self.memory_id,
                    "tokens": 20,
                    "utility": 0.8,
                    "roi": 0.04,
                    "compression_strategy": "none",
                    "original_tokens": 20,
                    "exact_preserved": 1,
                },
                {
                    "source_type": "skill",
                    "source_id": self.skill_id,
                    "tokens": 15,
                    "utility": 0.6,
                    "roi": 0.04,
                    "compression_strategy": "none",
                    "original_tokens": 15,
                    "exact_preserved": 1,
                },
            ),
            selected_tokens=35,
        )
        self.execution_run_id = "execution-learning-test"
        self.runtime.db.record_execution_run(
            run_id=self.execution_run_id,
            task_id=self.task_id,
            state="completed",
            event_count=4,
            step_count=1,
            action_count=1,
            duration_ms=250,
            verification_score=1.0,
            evaluation_score=1.0,
            failure_kind=None,
            started_at="2026-01-01T00:00:00+00:00",
            completed_at="2026-01-01T00:00:01+00:00",
        )
        trace = self.runtime.capture_experience(
            ExperienceTraceCreate(
                task_id=self.task_id,
                scope="learning-test",
                task_class="database",
                outcome="succeeded",
                significance_score=0.9,
                events=(
                    ExperienceEvent(
                        kind=ExperienceEventKind.FACT,
                        content="SQLite local mode was confirmed.",
                        evidence=("trace:evidence",),
                        confidence=0.9,
                        importance=0.8,
                    ),
                    ExperienceEvent(
                        kind=ExperienceEventKind.PROCEDURE,
                        content="Inspect only the focused SQLite evidence.",
                        evidence=("trace:procedure",),
                        confidence=0.9,
                        importance=0.8,
                    ),
                ),
            )
        )
        self.trace_id = trace.id

    def tearDown(self) -> None:
        self.runtime.close()
        self.directory.cleanup()

    def request(self, **changes: object) -> LearningRequest:
        payload: dict[str, object] = {
            "execution_run_id": self.execution_run_id,
            "evaluation_case": EvaluationCase(
                objective="Return the expected answer",
                actual="Paris",
                expected="Paris",
                input_tokens=40,
                output_tokens=5,
                token_budget=60,
            ),
            "attribution_signals": AttributionSignals(
                execution_sources=(("memory", self.memory_id),),
                ignored_sources=(("skill", self.skill_id),),
            ),
            "experience_trace_id": self.trace_id,
            "skill_scope": "learning-test",
            "task_class": "database",
            "model": "local-test",
            "baseline": RegressionBaseline(
                quality_floor=0.9,
                max_total_tokens=30,
                max_duration_ms=200,
                max_estimated_cost=0,
            ),
        }
        payload.update(changes)
        return LearningRequest(**payload)

    def counts(self) -> dict[str, int]:
        tables = (
            "learning_runs",
            "evaluation_runs",
            "context_attributions",
            "experience_distillations",
            "skill_generation_runs",
            "learning_memory_candidates",
            "learning_routing_improvements",
            "learning_regressions",
        )
        return {
            table: self.runtime.db.connection.execute(
                f"SELECT COUNT(*) FROM {table}"
            ).fetchone()[0]
            for table in tables
        }

    def test_full_pipeline_commits_all_ten_stages_atomically(self):
        task_before = dict(
            self.runtime.db.connection.execute(
                "SELECT * FROM tasks WHERE id = ?", (self.task_id,)
            ).fetchone()
        )
        execution_before = dict(
            self.runtime.db.connection.execute(
                "SELECT * FROM execution_runs WHERE run_id = ?",
                (self.execution_run_id,),
            ).fetchone()
        )

        run = self.runtime.learn(self.request())

        self.assertEqual(len(run.stages), 10)
        self.assertEqual(
            tuple(item.stage for item in run.stages),
            (
                "evaluate",
                "attribute_context",
                "calculate_resource_efficiency",
                "distill_experience",
                "generate_memory_candidates",
                "update_memory_utility",
                "update_skill_utility",
                "identify_skill_candidate",
                "identify_routing_improvements",
                "detect_regression",
            ),
        )
        self.assertGreaterEqual(run.memory_candidate_count, 1)
        self.assertEqual(run.routing_improvement_count, 1)
        self.assertEqual(run.regression_count, 2)
        self.assertEqual(self.runtime.learning_run(run.id).as_dict(), run.as_dict())
        self.assertEqual(
            dict(
                self.runtime.db.connection.execute(
                    "SELECT * FROM tasks WHERE id = ?", (self.task_id,)
                ).fetchone()
            ),
            task_before,
        )
        self.assertEqual(
            dict(
                self.runtime.db.connection.execute(
                    "SELECT * FROM execution_runs WHERE run_id = ?",
                    (self.execution_run_id,),
                ).fetchone()
            ),
            execution_before,
        )

    def test_injected_failure_rolls_back_every_learning_side_effect(self):
        counts_before = self.counts()
        memory_before = dict(
            self.runtime.db.connection.execute(
                """
                SELECT access_count, successful_uses, failed_uses, utility_score
                FROM memories WHERE id = ?
                """,
                (self.memory_id,),
            ).fetchone()
        )
        skill_before = dict(
            self.runtime.db.connection.execute(
                """
                SELECT use_count, success_count, failure_count
                FROM skills WHERE id = ?
                """,
                (self.skill_id,),
            ).fetchone()
        )
        execution_before = dict(
            self.runtime.db.connection.execute(
                "SELECT * FROM execution_runs WHERE run_id = ?",
                (self.execution_run_id,),
            ).fetchone()
        )

        with self.assertRaises(RuntimeError):
            self.runtime.learning.learn(
                self.request(), _fail_after_stage="update_skill_utility"
            )

        self.assertEqual(self.counts(), counts_before)
        self.assertEqual(
            dict(
                self.runtime.db.connection.execute(
                    """
                    SELECT access_count, successful_uses, failed_uses,
                           utility_score
                    FROM memories WHERE id = ?
                    """,
                    (self.memory_id,),
                ).fetchone()
            ),
            memory_before,
        )
        self.assertEqual(
            dict(
                self.runtime.db.connection.execute(
                    """
                    SELECT use_count, success_count, failure_count
                    FROM skills WHERE id = ?
                    """,
                    (self.skill_id,),
                ).fetchone()
            ),
            skill_before,
        )
        self.assertEqual(
            dict(
                self.runtime.db.connection.execute(
                    "SELECT * FROM execution_runs WHERE run_id = ?",
                    (self.execution_run_id,),
                ).fetchone()
            ),
            execution_before,
        )

    def test_learning_is_idempotence_guarded(self):
        self.runtime.learn(self.request())
        with self.assertRaises(ValueError):
            self.runtime.learn(self.request())

    def test_legacy_attribution_is_rejected_to_prevent_double_updates(self):
        self.runtime.db.connection.execute(
            """
            INSERT INTO context_attributions (
                id, task_id, source_type, source_id, role, outcome,
                impact_score, confidence, approximate_roi, evidence_json,
                created_at
            ) VALUES ('legacy-attribution', ?, 'memory', ?, 'legacy',
                      'contributed', 1, 1, 0.1, '[]', '2026-01-01')
            """,
            (self.task_id, self.memory_id),
        )
        self.runtime.db.connection.commit()
        with self.assertRaises(ValueError):
            self.runtime.learn(self.request())
        self.assertEqual(self.counts()["learning_runs"], 0)

    def test_nonterminal_execution_and_cross_task_trace_fail_closed(self):
        self.runtime.db.connection.execute(
            "UPDATE execution_runs SET state = 'running' WHERE run_id = ?",
            (self.execution_run_id,),
        )
        self.runtime.db.connection.commit()
        with self.assertRaises(ValueError):
            self.runtime.learn(self.request())

    def test_parser_is_strict_and_requires_reference_evaluation(self):
        with self.assertRaises(ValueError):
            LearningRequest.from_dict(
                {
                    "execution_run_id": "run",
                    "evaluation_case": {
                        "objective": "test",
                        "actual": "answer",
                    },
                    "surprise": True,
                }
            )
        with self.assertRaises(ValueError):
            RegressionBaseline(quality_floor=1.1)

    def test_learning_plan_is_read_only_and_content_minimized(self):
        counts_before = self.counts()
        plan = self.runtime.learning_plan(self.task_id)

        self.assertTrue(plan.structurally_eligible)
        self.assertEqual(plan.status, "ready_for_operator_inputs")
        self.assertEqual(plan.execution_run_id, self.execution_run_id)
        self.assertNotIn("evaluation_case", plan.request_draft)
        self.assertEqual(
            {item["source_id"] for item in plan.context_sources},
            {self.memory_id, self.skill_id},
        )
        self.assertEqual(plan.experience_traces[0]["id"], self.trace_id)
        self.assertTrue(
            plan.experience_traces[0]["distillation_eligible"]
        )
        rendered = json.dumps(plan.as_dict())
        self.assertNotIn("Answer from retained evidence", rendered)
        self.assertNotIn("SQLite local mode was confirmed", rendered)
        self.assertEqual(self.counts(), counts_before)

    def test_learning_plan_requires_exact_run_when_ambiguous(self):
        self.runtime.db.record_execution_run(
            run_id="execution-learning-second",
            task_id=self.task_id,
            state="failed",
            event_count=1,
            step_count=1,
            action_count=0,
            duration_ms=10,
            verification_score=0,
            evaluation_score=0,
            failure_kind="test",
            started_at="2026-01-02T00:00:00+00:00",
            completed_at="2026-01-02T00:00:01+00:00",
        )

        ambiguous = self.runtime.learning_plan(self.task_id)
        selected = self.runtime.learning_plan(
            self.task_id, execution_run_id=self.execution_run_id
        )

        self.assertFalse(ambiguous.structurally_eligible)
        self.assertIsNone(ambiguous.execution_run_id)
        self.assertEqual(len(ambiguous.terminal_execution_runs), 2)
        self.assertTrue(selected.structurally_eligible)

    def test_learning_plan_reports_prior_learning_as_ineligible(self):
        self.runtime.learn(self.request())

        plan = self.runtime.learning_plan(self.task_id)

        self.assertFalse(plan.structurally_eligible)
        self.assertEqual(plan.status, "ineligible")
        self.assertIsNone(plan.request_draft)
        learned_check = next(
            item for item in plan.checks
            if item["name"] == "not_previously_learned"
        )
        self.assertFalse(learned_check["passed"])

    def test_cli_plans_learning_without_writes(self):
        self.runtime.close()
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(
                main(
                    [
                        "--db",
                        str(self.database),
                        "learn",
                        "plan",
                        self.task_id,
                    ]
                ),
                0,
            )
        plan = json.loads(output.getvalue())
        self.assertTrue(plan["structurally_eligible"])
        self.assertFalse(plan["mutates_state"])
        self.assertEqual(plan["execution_run_id"], self.execution_run_id)
        self.runtime = AdaptiveRuntime(self.database)

    def test_cli_runs_and_reports_learning(self):
        request_file = Path(self.directory.name) / "learning.json"
        request_file.write_text(
            json.dumps(
                {
                    "execution_run_id": self.execution_run_id,
                    "evaluation_case": {
                        "objective": "Return the expected answer",
                        "actual": "Paris",
                        "expected": "Paris",
                        "input_tokens": 40,
                        "output_tokens": 5,
                        "token_budget": 60,
                    },
                    "attribution_signals": {
                        "execution_sources": [
                            {
                                "source_type": "memory",
                                "source_id": self.memory_id,
                            }
                        ],
                        "ignored_sources": [
                            {
                                "source_type": "skill",
                                "source_id": self.skill_id,
                            }
                        ],
                    },
                    "experience_trace_id": self.trace_id,
                    "skill_scope": "learning-test",
                    "task_class": "database",
                    "model": "local-test",
                    "baseline": {
                        "quality_floor": 0.9,
                        "max_total_tokens": 30,
                        "max_duration_ms": 200,
                        "max_estimated_cost": 0,
                    },
                }
            ),
            encoding="utf-8",
        )
        self.runtime.close()
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(
                main(
                    [
                        "--db",
                        str(self.database),
                        "learn",
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
                        "learn",
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
