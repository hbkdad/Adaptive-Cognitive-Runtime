from __future__ import annotations

import json
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

from acr_runtime import AdaptiveRuntime
from acr_runtime.cli import main
from acr_runtime.execution import (
    ExecutionOutput,
    FunctionExecutor,
    PassEvaluator,
    PassVerifier,
    SingleStepPlanner,
    Task,
    TaskRunner,
    TaskState,
)
from acr_runtime.failure import (
    FailureCreate,
    FailurePlanningAdvisor,
    FailureQuery,
)
from acr_runtime.memory import MemoryCreate, MemoryStatus, MemoryType


class FailureIntelligenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.path = Path(self.temp_dir.name) / "acr.db"
        self.runtime = AdaptiveRuntime(self.path)

    def tearDown(self) -> None:
        self.runtime.close()
        self.temp_dir.cleanup()

    def candidate(
        self,
        *,
        deterministic: bool = False,
        evidence: tuple[str, ...] = ("run-1",),
        confidence: float = 0.9,
    ) -> FailureCreate:
        return FailureCreate(
            task_class="sqlite migration",
            strategy_attempted="rebuild the FTS index while writers are active",
            environment_json=json.dumps(
                {"database": "sqlite", "platform": "windows"}
            ),
            symptoms=("database locked during migration", "FTS rebuild stopped"),
            root_cause="a writer held the SQLite lock",
            failed_action="rebuild memories_fts",
            error_type="sqlite3.OperationalError",
            error_message="database is locked",
            avoidance_rule="quiesce writers before rebuilding FTS",
            confidence=confidence,
            evidence=evidence,
            scope="alpha",
            deterministic=deterministic,
        )

    def query(self) -> FailureQuery:
        return FailureQuery(
            task="run a sqlite migration after a database locked failure",
            task_class="sqlite migration",
            strategy="rebuild the FTS index while writers are active",
            environment_json=json.dumps(
                {"database": "sqlite", "platform": "windows"}
            ),
            scope="alpha",
        )

    def test_record_round_trip_contains_complete_failure_shape(self):
        record = self.runtime.failures.record(self.candidate())

        self.assertEqual(record.task_class, "sqlite migration")
        self.assertEqual(record.occurrence_count, 1)
        self.assertEqual(record.status, "unresolved")
        self.assertEqual(record.evidence, ("run-1",))
        self.assertEqual(record.error_type, "sqlite3.OperationalError")
        memory = self.runtime.db.memories.get(record.memory_id)
        self.assertEqual(memory.type, MemoryType.FAILURE)
        self.assertEqual(memory.status, MemoryStatus.CONFIRMED)
        self.assertEqual(
            json.loads(memory.structured_payload_json)["failure_record_id"],
            record.id,
        )

    def test_repeated_failure_reinforces_one_record_and_increases_weight(self):
        first = self.runtime.failures.record(self.candidate())
        first_weight = self.runtime.failures.query(self.query())[0].avoidance_weight
        repeated = self.runtime.failures.record(
            self.candidate(evidence=("run-2",), confidence=0.95)
        )
        second_weight = self.runtime.failures.query(self.query())[0].avoidance_weight

        self.assertEqual(repeated.id, first.id)
        self.assertEqual(repeated.occurrence_count, 2)
        self.assertEqual(repeated.evidence, ("run-1", "run-2"))
        self.assertEqual(repeated.confidence, 0.95)
        self.assertGreater(second_weight, first_weight)
        self.assertGreaterEqual(
            self.runtime.db.memories.get(repeated.memory_id).importance,
            0.8,
        )

    def test_query_prefers_analogous_failure_and_explains_cause(self):
        relevant = self.runtime.failures.record(self.candidate())
        self.runtime.failures.record(
            FailureCreate(
                task_class="css design",
                strategy_attempted="change the theme",
                symptoms=("button contrast was low",),
                failed_action="update CSS",
                error_type="VisualRegression",
                evidence=("screenshot-1",),
                scope="alpha",
            )
        )

        matches = self.runtime.failures.query(self.query())

        self.assertEqual(matches[0].failure.id, relevant.id)
        self.assertIn(
            "previously failed because a writer held the SQLite lock",
            matches[0].explanation,
        )
        self.assertFalse(matches[0].absolute_prohibition)

    def test_resolved_failure_links_remediation_and_reduces_avoidance(self):
        failure = self.runtime.failures.record(self.candidate())
        before = self.runtime.failures.query(self.query())[0].avoidance_weight
        remediation = self.runtime.db.memories.create(
            MemoryCreate(
                type=MemoryType.PROCEDURAL,
                content="Stop writers, migrate, verify, and restart writers.",
                scope="alpha",
                confidence=0.98,
                importance=0.9,
                evidence=("successful-run-4",),
                status=MemoryStatus.CONFIRMED,
            )
        )

        resolved = self.runtime.failures.resolve(
            failure.id,
            resolution="Quiescing writers allowed the migration to finish.",
            remediation_memory_id=remediation.id,
        )
        after = self.runtime.failures.query(self.query())[0].avoidance_weight

        self.assertEqual(resolved.status, "resolved")
        self.assertEqual(resolved.remediation_memory_id, remediation.id)
        self.assertIsNotNone(resolved.resolved_at)
        self.assertLess(after, before)

        reopened = self.runtime.failures.record(
            self.candidate(evidence=("regression-run",))
        )
        reopened_weight = self.runtime.failures.query(self.query())[0].avoidance_weight
        self.assertEqual(reopened.status, "unresolved")
        self.assertIsNone(reopened.resolved_at)
        self.assertGreater(reopened_weight, after)

    def test_only_strict_deterministic_evidence_can_block_planning(self):
        first = self.runtime.failures.record(
            self.candidate(
                deterministic=True,
                evidence=("run-1",),
                confidence=0.98,
            )
        )
        self.runtime.failures.record(
            self.candidate(
                deterministic=True,
                evidence=("run-2",),
                confidence=0.98,
            )
        )
        before_threshold = self.runtime.failures.query(self.query())[0]
        self.assertFalse(before_threshold.absolute_prohibition)
        self.runtime.failures.record(
            self.candidate(
                deterministic=True,
                evidence=("run-3",),
                confidence=0.98,
            )
        )
        match = self.runtime.failures.query(self.query())[0]
        self.assertEqual(match.failure.id, first.id)
        self.assertTrue(match.absolute_prohibition)

        planned = False

        class TrackingPlanner:
            def plan(self, task):
                nonlocal planned
                planned = True
                return SingleStepPlanner("work").plan(task)

        runner = TaskRunner(
            planner=TrackingPlanner(),
            executor=FunctionExecutor(
                {"work": lambda task, step: ExecutionOutput(content="unsafe")}
            ),
            verifier=PassVerifier(),
            evaluator=PassEvaluator(),
            planning_advisors=(
                FailurePlanningAdvisor(self.runtime.failures),
            ),
        )
        run = runner.run(
            Task(
                self.query().task,
                scope="alpha",
                task_class="sqlite migration",
                strategy="rebuild the FTS index while writers are active",
                environment_json=self.query().environment_json,
            )
        )

        self.assertFalse(planned)
        self.assertEqual(run.state, TaskState.FAILED)
        self.assertEqual(run.failure.kind, "PlanningBlocked")
        advice_event = next(
            event for event in run.events if event.event_type == "plan.advice"
        )
        payload = json.loads(advice_event.payload_json)
        self.assertTrue(payload["blocked"])
        self.assertNotIn("SQLite lock", advice_event.payload_json)
        self.assertNotIn("quiesce writers", advice_event.payload_json)

    def test_negative_procedure_requires_three_distinct_repeated_evidence_refs(self):
        first = self.runtime.failures.record(
            self.candidate(
                deterministic=True,
                evidence=("run-1",),
                confidence=0.98,
            )
        )
        self.runtime.failures.record(
            self.candidate(
                deterministic=True,
                evidence=("run-2",),
                confidence=0.98,
            )
        )
        premature = self.runtime.failures.assess_negative_procedures(
            scope="alpha",
            task_class="sqlite migration",
        )[0]
        self.assertFalse(premature.eligible)
        self.assertIn("insufficient_occurrences", premature.rejection_reasons)
        self.assertIn("insufficient_distinct_evidence", premature.rejection_reasons)

        self.runtime.failures.record(
            self.candidate(
                deterministic=True,
                evidence=("run-3",),
                confidence=0.98,
            )
        )
        assessment = self.runtime.failures.assess_negative_procedures(
            scope="alpha",
            task_class="SQLITE MIGRATION",
        )[0]

        self.assertTrue(assessment.eligible)
        self.assertEqual(assessment.failure_id, first.id)
        self.assertEqual(assessment.rejection_reasons, ())
        self.assertEqual(assessment.procedure.scope, "alpha")
        self.assertEqual(assessment.procedure.occurrence_count, 3)
        self.assertEqual(assessment.procedure.evidence_count, 3)
        self.assertEqual(
            assessment.procedure.authority,
            "planning_constraint_only",
        )
        self.assertTrue(assessment.procedure.id.startswith("negative-"))

    def test_negative_procedure_rejects_global_and_resolved_failures(self):
        global_candidate = self.candidate(
            deterministic=True,
            evidence=("run-1", "run-2", "run-3"),
            confidence=0.98,
        )
        for index in range(3):
            self.runtime.failures.record(
                FailureCreate(
                    **{
                        **global_candidate.__dict__,
                        "scope": "global",
                        "evidence": (f"global-run-{index}",),
                    }
                )
            )
        global_assessment = self.runtime.failures.assess_negative_procedures(
            scope="global",
            task_class="sqlite migration",
        )[0]
        self.assertFalse(global_assessment.eligible)
        self.assertIn(
            "global_scope_requires_cross_scope_evidence",
            global_assessment.rejection_reasons,
        )
        global_query = FailureQuery(
            task=self.query().task,
            task_class=self.query().task_class,
            strategy=self.query().strategy,
            environment_json=self.query().environment_json,
            scope="global",
        )
        self.assertFalse(
            self.runtime.failures.query(global_query)[0].absolute_prohibition
        )

        failure = self.runtime.failures.record(
            self.candidate(
                deterministic=True,
                evidence=("run-1",),
                confidence=0.98,
            )
        )
        self.runtime.failures.record(
            self.candidate(
                deterministic=True,
                evidence=("run-2",),
                confidence=0.98,
            )
        )
        self.runtime.failures.record(
            self.candidate(
                deterministic=True,
                evidence=("run-3",),
                confidence=0.98,
            )
        )
        remediation = self.runtime.db.memories.create(
            MemoryCreate(
                type=MemoryType.PROCEDURAL,
                content="Use the verified safe migration sequence.",
                scope="alpha",
                confidence=0.98,
                importance=0.9,
                evidence=("successful-run",),
                status=MemoryStatus.CONFIRMED,
            )
        )
        self.runtime.failures.resolve(
            failure.id,
            resolution="The safe sequence succeeded.",
            remediation_memory_id=remediation.id,
        )
        resolved = self.runtime.failures.assess_negative_procedures(
            scope="alpha",
            task_class="sqlite migration",
        )[0]
        self.assertFalse(resolved.eligible)
        self.assertIn("failure_is_resolved", resolved.rejection_reasons)

    def test_non_deterministic_failure_adds_warning_without_blocking(self):
        self.runtime.failures.record(self.candidate())
        runner = TaskRunner(
            planner=SingleStepPlanner("work"),
            executor=FunctionExecutor(
                {
                    "work": lambda task, step: ExecutionOutput(
                        content="\n".join(task.constraints)
                    )
                }
            ),
            verifier=PassVerifier(),
            evaluator=PassEvaluator(),
            planning_advisors=(
                FailurePlanningAdvisor(self.runtime.failures),
            ),
        )

        run = runner.run(
            Task(
                self.query().task,
                scope="alpha",
                task_class="sqlite migration",
                strategy="rebuild the FTS index while writers are active",
                environment_json=self.query().environment_json,
            )
        )

        self.assertEqual(run.state, TaskState.COMPLETED)
        self.assertIn("Weighted warning", run.result.content)
        self.assertIn("quiesce writers", run.result.content)

    def test_validation_rejects_evidence_free_or_oversized_errors(self):
        with self.assertRaises(ValueError):
            self.runtime.failures.record(
                self.candidate(evidence=())
            )
        with self.assertRaises(ValueError):
            FailureCreate(
                task_class="test",
                strategy_attempted="retry",
                symptoms=("failed",),
                failed_action="run",
                error_message="x" * 4_001,
                evidence=("run",),
            )

    def test_cli_records_and_queries_failure_intelligence(self):
        self.runtime.close()
        record_output = StringIO()
        with redirect_stdout(record_output):
            self.assertEqual(
                main(
                    [
                        "--db",
                        str(self.path),
                        "failure",
                        "record",
                        "--task-class",
                        "sqlite migration",
                        "--strategy",
                        "rebuild FTS",
                        "--symptom",
                        "database locked",
                        "--failed-action",
                        "rebuild index",
                        "--error-type",
                        "sqlite3.OperationalError",
                        "--root-cause",
                        "active writer",
                        "--avoidance-rule",
                        "stop writers first",
                        "--evidence",
                        "run-1",
                        "--scope",
                        "alpha",
                    ]
                ),
                0,
            )
        recorded = json.loads(record_output.getvalue())
        query_output = StringIO()
        with redirect_stdout(query_output):
            self.assertEqual(
                main(
                    [
                        "--db",
                        str(self.path),
                        "failure",
                        "query",
                        "sqlite migration database locked",
                        "--task-class",
                        "sqlite migration",
                        "--strategy",
                        "rebuild FTS",
                        "--scope",
                        "alpha",
                    ]
                ),
                0,
            )
        matches = json.loads(query_output.getvalue())
        self.assertEqual(matches[0]["failure_id"], recorded["id"])
        self.runtime = AdaptiveRuntime(self.path)

    def test_cli_lists_only_eligible_negative_procedures_by_default(self):
        for run_id in ("run-1", "run-2", "run-3"):
            self.runtime.failures.record(
                self.candidate(
                    deterministic=True,
                    evidence=(run_id,),
                    confidence=0.98,
                )
            )
        self.runtime.close()
        output = StringIO()
        with redirect_stdout(output):
            self.assertEqual(
                main(
                    [
                        "--db",
                        str(self.path),
                        "failure",
                        "negatives",
                        "--scope",
                        "alpha",
                        "--task-class",
                        "sqlite migration",
                    ]
                ),
                0,
            )
        payload = json.loads(output.getvalue())
        self.assertEqual(len(payload), 1)
        self.assertTrue(payload[0]["eligible"])
        self.assertEqual(
            payload[0]["procedure"]["authority"],
            "planning_constraint_only",
        )
        self.runtime = AdaptiveRuntime(self.path)


if __name__ == "__main__":
    unittest.main()
