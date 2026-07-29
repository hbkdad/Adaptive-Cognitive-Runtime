from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from acr_runtime import AdaptiveRuntime
from acr_runtime.autonomous_improvement import digest
from acr_runtime.cost_accounting import PriceRate
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
from acr_runtime.memory import MemoryCreate, MemoryStatus, MemoryType
from acr_runtime.parallel_research import (
    ParallelResearchRequest,
    ResearchExecutionError,
    ResearchFinding,
    ResearchQuestion,
    ResearchReferenceCreate,
)
from acr_runtime.providers import (
    ModelCapabilities,
    ProviderExecutor,
)
from acr_runtime.providers.base import ModelCallRecord
from acr_runtime.retrieval import RetrievalRequest
from acr_runtime.telemetry import TelemetryRecorder


class FaultingProvider:
    name = "chaos-provider"

    def __init__(self, error: Exception) -> None:
        self.error = error

    def capabilities(self, model: str) -> ModelCapabilities:
        return ModelCapabilities(chat=True)

    def chat(self, request):
        raise self.error


class FaultingAgentAdapter:
    adapter_id = "chaos.agent-v1"

    def research(self, assignment, resolve_reference):
        reference = resolve_reference(assignment.reference_ids[0])
        if assignment.question_id == "agent-fails":
            raise RuntimeError("injected agent failure")
        return (
            ResearchFinding(
                claim="A partial finding that must not be committed",
                evidence_reference_ids=(reference.id,),
                confidence=0.8,
            ),
        )

    def synthesize(self, objective, findings):
        return "must not run after agent failure"


class ChaosTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary.name) / "acr.db"
        self.runtime = AdaptiveRuntime(self.path)

    def tearDown(self) -> None:
        self.runtime.close()
        self.temporary.cleanup()

    @staticmethod
    def runner(operation) -> TaskRunner:
        return TaskRunner(
            planner=SingleStepPlanner("chaos-operation"),
            executor=FunctionExecutor({"chaos-operation": operation}),
            verifier=PassVerifier(),
            evaluator=PassEvaluator(),
        )

    def provider_run(self, error: Exception):
        provider = FaultingProvider(error)
        runner = TaskRunner(
            planner=SingleStepPlanner(),
            executor=ProviderExecutor(provider, model="chaos-model"),
            verifier=PassVerifier(),
            evaluator=PassEvaluator(),
        )
        return runner.run(Task("Exercise a faulting provider"))

    def test_provider_unavailable_is_retained_as_retryable_failure(self):
        run = self.provider_run(ConnectionError("provider unavailable"))

        self.assertEqual(run.state, TaskState.FAILED)
        self.assertEqual(run.failure.kind, "ConnectionError")
        self.assertTrue(run.failure.retryable)
        self.assertEqual(run.events[-1].event_type, "task.error")

    def test_model_timeout_is_retained_as_retryable_failure(self):
        run = self.provider_run(TimeoutError("model timeout"))

        self.assertEqual(run.state, TaskState.FAILED)
        self.assertEqual(run.failure.kind, "TimeoutError")
        self.assertTrue(run.failure.retryable)
        self.assertEqual(run.actions, ())

    def test_database_lock_has_no_partial_write_and_recovers(self):
        self.runtime.db.connection.execute("PRAGMA busy_timeout = 100")
        locker = sqlite3.connect(self.path, isolation_level=None)
        locker.execute("PRAGMA busy_timeout = 100")
        locker.execute("BEGIN EXCLUSIVE")

        def create_memory(task, step):
            memory = self.runtime.db.memories.create(
                MemoryCreate(
                    type=MemoryType.SEMANTIC,
                    content="Write attempted while the database is locked.",
                    scope="global",
                    status=MemoryStatus.CONFIRMED,
                )
            )
            return ExecutionOutput(content=memory.id)

        try:
            blocked = self.runner(create_memory).run(Task("Locked write"))
        finally:
            locker.rollback()
            locker.close()

        self.assertEqual(blocked.state, TaskState.FAILED)
        self.assertEqual(blocked.failure.kind, "OperationalError")
        self.assertTrue(blocked.failure.retryable)
        self.assertEqual(
            self.runtime.db.connection.execute(
                "SELECT COUNT(*) FROM memories"
            ).fetchone()[0],
            0,
        )
        recovered = self.runner(create_memory).run(Task("Recovered write"))
        self.assertEqual(recovered.state, TaskState.COMPLETED)
        self.assertEqual(
            self.runtime.db.connection.execute(
                "SELECT COUNT(*) FROM memories"
            ).fetchone()[0],
            1,
        )

    def test_tool_crash_is_captured_without_false_result(self):
        def crash(task, step):
            raise RuntimeError("injected tool crash")

        run = self.runner(crash).run(Task("Crash one tool"))

        self.assertEqual(run.state, TaskState.FAILED)
        self.assertEqual(run.failure.kind, "RuntimeError")
        self.assertFalse(run.failure.retryable)
        self.assertIsNone(run.result)
        self.assertEqual(run.actions, ())

    def test_corrupt_memory_fails_closed_and_retrieval_recovers(self):
        healthy = self.runtime.db.memories.create(
            MemoryCreate(
                type=MemoryType.SEMANTIC,
                content="Healthy SQLite memory.",
                scope="global",
                status=MemoryStatus.CONFIRMED,
            )
        )
        corrupt = self.runtime.db.memories.create(
            MemoryCreate(
                type=MemoryType.SEMANTIC,
                content="Memory row selected for corruption.",
                scope="global",
                status=MemoryStatus.CONFIRMED,
            )
        )
        self.runtime.db.connection.execute(
            "UPDATE memories SET evidence_json='42' WHERE id=?",
            (corrupt.id,),
        )
        self.runtime.db.connection.commit()

        def retrieve(task, step):
            result = self.runtime.retrieve_memory(
                RetrievalRequest(
                    task="Retrieve SQLite memory",
                    query="memory",
                    scope="global",
                    token_budget=200,
                )
            )
            return ExecutionOutput(
                content=json.dumps(
                    [item.memory.id for item in result.selected]
                )
            )

        failed = self.runner(retrieve).run(Task("Read corrupt memory"))
        self.assertEqual(failed.state, TaskState.FAILED)
        self.assertEqual(failed.failure.kind, "TypeError")
        self.assertFalse(failed.failure.retryable)
        self.assertEqual(
            self.runtime.db.memories.get(healthy.id).content,
            "Healthy SQLite memory.",
        )

        self.runtime.db.connection.execute(
            "UPDATE memories SET evidence_json='[]' WHERE id=?",
            (corrupt.id,),
        )
        self.runtime.db.connection.commit()
        recovered = self.runner(retrieve).run(Task("Read repaired memory"))
        self.assertEqual(recovered.state, TaskState.COMPLETED)

    def test_invalid_skill_is_rejected_without_registry_mutation(self):
        invalid = Path(self.temporary.name) / "invalid-skill"
        invalid.mkdir()

        def validate(task, step):
            package = self.runtime.validate_skill_package(invalid)
            return ExecutionOutput(content=package.content_hash)

        failed = self.runner(validate).run(Task("Validate invalid skill"))
        self.assertEqual(failed.state, TaskState.FAILED)
        self.assertEqual(failed.failure.kind, "SkillFormatError")
        self.assertEqual(
            self.runtime.db.connection.execute(
                "SELECT COUNT(*) FROM skills"
            ).fetchone()[0],
            0,
        )
        valid = (
            Path(__file__).parents[1]
            / "examples"
            / "skill-v1"
            / "sqlite-diagnostics"
        )
        self.assertTrue(
            self.runtime.validate_skill_package(valid).content_hash
        )

    def test_partial_model_accounting_rolls_back_telemetry_and_cost(self):
        source_hash = digest({"chaos": "mixed-currency"})
        for meter, currency in (
            ("uncached_input_token", "USD"),
            ("output_token", "CAD"),
        ):
            self.runtime.costs.add_rate(PriceRate(
                service_kind="model",
                provider="chaos-provider",
                sku="chaos-model",
                operation="chat",
                meter_kind=meter,
                currency_code=currency,
                price_micros=1_000_000,
                unit_size=1_000_000,
                effective_from="2026-01-01T00:00:00+00:00",
                source_url="https://example.test/chaos-pricing",
                source_hash=source_hash,
            ))
        recorder = TelemetryRecorder(self.runtime.db)

        with self.assertRaisesRegex(ValueError, "mix currencies"):
            recorder.record_model_call(ModelCallRecord(
                provider="chaos-provider",
                model="chaos-model",
                operation="chat",
                status="succeeded",
                task_id=None,
                step_id=None,
                context_bundle_id=None,
                input_tokens=10,
                output_tokens=10,
                cached_tokens=0,
                latency_ms=1,
                estimated_cost=0,
                attempt_id="chaos-partial-write",
            ))

        self.assertEqual(
            self.runtime.db.connection.execute(
                """
                SELECT COUNT(*) FROM telemetry_events
                WHERE provider='chaos-provider'
                """
            ).fetchone()[0],
            0,
        )
        self.assertEqual(
            self.runtime.db.connection.execute(
                """
                SELECT COUNT(*) FROM cost_events
                WHERE attempt_id='chaos-partial-write'
                """
            ).fetchone()[0],
            0,
        )

    def test_agent_failure_discards_partial_findings_and_runtime_recovers(self):
        engine = self.runtime.parallel_research
        reference = engine.add_reference(ResearchReferenceCreate(
            locator="local:chaos-source",
            title="Chaos source",
            source_kind="local",
            authority=0.8,
            content="Bounded evidence for the chaos experiment.",
        ))
        request = ParallelResearchRequest(
            objective="Exercise one failing research agent",
            questions=(
                ResearchQuestion(
                    "agent-succeeds", "First question", True, (reference.id,)
                ),
                ResearchQuestion(
                    "agent-fails", "Second question", True, (reference.id,)
                ),
            ),
            max_workers=2,
            max_seconds=5,
        )
        plan = engine.plan(request)

        with self.assertRaises(ResearchExecutionError) as caught:
            engine.execute(plan["id"], FaultingAgentAdapter())

        retained = engine.get_run(caught.exception.run_id)
        self.assertEqual(retained["status"], "failed")
        self.assertEqual(retained["raw_finding_count"], 0)
        self.assertEqual(retained["findings"], [])
        self.assertEqual(
            self.runtime.db.connection.execute(
                "SELECT quick_check FROM pragma_quick_check"
            ).fetchone()[0],
            "ok",
        )


if __name__ == "__main__":
    unittest.main()
