from __future__ import annotations

import io
import json
import sqlite3
import tempfile
import threading
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from acr_runtime.db import RuntimeDB
from acr_runtime.cli import main
from acr_runtime.execution import (
    ExecutionOutput,
    FunctionExecutor,
    PassEvaluator,
    PassVerifier,
    SingleStepPlanner,
    Step,
    Task,
    TaskRunner,
    TaskState,
)
from acr_runtime.providers import MockProvider, ProviderExecutor
from acr_runtime.providers.base import ModelMetadata
from acr_runtime.resource_governor import (
    BudgetExceeded,
    ResourceBudget,
    ResourceGovernor,
    ResourceVector,
)


def vector(value: int) -> ResourceVector:
    return ResourceVector(
        input_tokens=value,
        output_tokens=value,
        model_calls=value,
        tool_calls=value,
        agents=value,
        cost=value,
        duration=value,
    )


class ResourceGovernorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "acr.db"
        self.db = RuntimeDB(self.path)
        self.governor = ResourceGovernor(self.db.connection)

    def tearDown(self) -> None:
        self.db.close()
        self.temp.cleanup()

    def create(
        self,
        *,
        task_id: str = "task-1",
        soft: int = 5,
        hard: int = 10,
        mode: str = "manual_exact",
    ) -> None:
        self.governor.create_budget(
            ResourceBudget(
                task_id=task_id,
                soft=vector(soft),
                hard=vector(hard),
                escalation_mode=mode,
                evidence=("operator_budget",),
            )
        )

    def test_reserve_commit_and_release_are_conservative_and_idempotent(self):
        self.create()
        quote = vector(4)
        reservation = self.governor.reserve(
            "task-1",
            quote,
            idempotency_key="model-call-1",
            kind="model",
            evidence=("provider_quote",),
        )
        replay = self.governor.reserve(
            "task-1",
            quote,
            idempotency_key="model-call-1",
            kind="model",
            evidence=("provider_quote",),
        )
        self.assertEqual(replay.id, reservation.id)
        with self.assertRaisesRegex(ValueError, "another quote"):
            self.governor.reserve(
                "task-1",
                ResourceVector(model_calls=1),
                idempotency_key="model-call-1",
                kind="model",
                evidence=("changed_quote",),
            )
        committed = self.governor.commit(
            reservation.id, vector(2), evidence=("provider_usage",)
        )
        self.assertEqual(committed.state, "committed")
        status = self.governor.status("task-1")
        self.assertEqual(status["held"], vector(0).as_dict())
        self.assertEqual(status["used"], vector(2).as_dict())

        second = self.governor.reserve(
            "task-1",
            ResourceVector(tool_calls=1),
            idempotency_key="tool-call-never-started",
            kind="tool",
            evidence=("tool_quote",),
        )
        released = self.governor.release(
            second.id, not_started_evidence=("adapter_not_invoked",)
        )
        self.assertEqual(released.state, "released")
        self.assertEqual(
            self.governor.status("task-1")["held"], vector(0).as_dict()
        )

    def test_hard_limit_never_changes_and_failed_actual_stays_held(self):
        self.create(soft=10, hard=10)
        reservation = self.governor.reserve(
            "task-1",
            ResourceVector(model_calls=10),
            idempotency_key="all-calls",
            kind="model",
            evidence=("bounded_batch",),
        )
        before = self.governor.status("task-1")
        with self.assertRaises(BudgetExceeded):
            self.governor.reserve(
                "task-1",
                ResourceVector(model_calls=1),
                idempotency_key="one-too-many",
                kind="model",
                evidence=("retry",),
            )
        self.assertEqual(self.governor.status("task-1"), before)
        with self.assertRaisesRegex(ValueError, "exceeds reservation"):
            self.governor.commit(
                reservation.id,
                ResourceVector(model_calls=11),
                evidence=("invalid_provider_usage",),
            )
        self.assertEqual(
            self.governor.status("task-1")["held"]["model_calls"], 10
        )
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.connection.execute(
                """
                UPDATE task_resource_usage
                SET held_model_calls = 11 WHERE task_id = 'task-1'
                """
            )
        self.db.connection.rollback()

    def test_soft_limit_requires_exact_expiring_one_shot_approval(self):
        self.create(soft=2, hard=5)
        quote = ResourceVector(output_tokens=3)
        with self.assertRaisesRegex(
            BudgetExceeded, "soft_limit_requires_escalation"
        ):
            self.governor.reserve(
                "task-1",
                quote,
                idempotency_key="without-approval",
                kind="model",
                evidence=("provider_quote",),
            )
        escalation_id = self.governor.approve_escalation(
            "task-1",
            quote,
            approval_reference="approval-123",
            reason="verified response needs three tokens",
            evidence=("operator_review",),
        )
        reservation = self.governor.reserve(
            "task-1",
            quote,
            idempotency_key="with-approval",
            kind="model",
            evidence=("provider_quote",),
            escalation_id=escalation_id,
        )
        self.assertEqual(reservation.escalation_id, escalation_id)
        with self.assertRaisesRegex(
            BudgetExceeded, "soft_limit_requires_escalation"
        ):
            self.governor.reserve(
                "task-1",
                ResourceVector(output_tokens=1),
                idempotency_key="approval-replay",
                kind="model",
                evidence=("retry",),
                escalation_id=escalation_id,
            )

    def test_validation_rejects_bool_shape_and_soft_above_hard(self):
        with self.assertRaisesRegex(ValueError, "non-negative integer"):
            ResourceVector(model_calls=True)
        with self.assertRaisesRegex(ValueError, "requires"):
            ResourceVector.from_dict({"model_calls": 1})
        with self.assertRaisesRegex(ValueError, "soft resource"):
            ResourceBudget(
                task_id="bad",
                soft=vector(2),
                hard=vector(1),
                escalation_mode="none",
                evidence=("test",),
            )

    def test_parallel_last_unit_can_be_reserved_only_once(self):
        self.create(task_id="race", soft=1, hard=1, mode="none")
        barrier = threading.Barrier(2)
        outcomes: list[str] = []
        lock = threading.Lock()

        def worker(key: str) -> None:
            database = RuntimeDB(self.path)
            try:
                barrier.wait()
                ResourceGovernor(database.connection).reserve(
                    "race",
                    ResourceVector(model_calls=1),
                    idempotency_key=key,
                    kind="model",
                    evidence=("parallel_test",),
                )
                result = "allowed"
            except BudgetExceeded:
                result = "denied"
            finally:
                database.close()
            with lock:
                outcomes.append(result)

        threads = [
            threading.Thread(target=worker, args=("one",)),
            threading.Thread(target=worker, args=("two",)),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertCountEqual(outcomes, ["allowed", "denied"])
        self.assertEqual(
            self.governor.status("race")["held"]["model_calls"], 1
        )

    def test_provider_reserves_before_dispatch_caps_output_and_commits_actual(self):
        budget = ResourceBudget(
            task_id="provider-task",
            soft=ResourceVector(
                input_tokens=100,
                output_tokens=5,
                model_calls=1,
                duration=1_000,
            ),
            hard=ResourceVector(
                input_tokens=100,
                output_tokens=5,
                model_calls=1,
                duration=1_000,
            ),
            escalation_mode="none",
            evidence=("provider_test",),
        )
        self.governor.create_budget(budget)
        seen_caps: list[int | None] = []
        provider = MockProvider(
            lambda request: seen_caps.append(request.max_output_tokens) or "ok"
        )
        executor = ProviderExecutor(
            provider,
            model="mock-chat",
            governor=self.governor,
            resource_quote=budget.hard,
        )

        executor.execute(
            Task("bounded provider call", id="provider-task"),
            Step(sequence=1, name="call", operation="execute", id="step-1"),
        )

        self.assertEqual(seen_caps, [5])
        status = self.governor.status("provider-task")
        self.assertEqual(status["held"]["model_calls"], 0)
        self.assertEqual(status["used"]["model_calls"], 1)
        self.assertLessEqual(status["used"]["input_tokens"], 100)
        self.assertLessEqual(status["used"]["output_tokens"], 5)

    def test_failed_provider_call_keeps_uncertain_reservation_held(self):
        hard = ResourceVector(
            input_tokens=100,
            output_tokens=5,
            model_calls=1,
            duration=1_000,
        )
        self.governor.create_budget(
            ResourceBudget(
                task_id="failed-provider",
                soft=hard,
                hard=hard,
                escalation_mode="none",
                evidence=("provider_test",),
            )
        )

        def fail(_request):
            raise RuntimeError("provider failed after dispatch")

        executor = ProviderExecutor(
            MockProvider(fail),
            model="mock-chat",
            governor=self.governor,
            resource_quote=hard,
        )
        with self.assertRaisesRegex(RuntimeError, "after dispatch"):
            executor.execute(
                Task("bounded provider call", id="failed-provider"),
                Step(sequence=1, name="call", operation="execute", id="step-2"),
            )
        status = self.governor.status("failed-provider")
        self.assertEqual(status["held"]["model_calls"], 1)
        self.assertEqual(status["used"]["model_calls"], 0)

    def test_governed_provider_requires_known_pricing_and_cost_quote(self):
        class PaidMockProvider(MockProvider):
            def list_models(self):
                model = super().list_models()[0]
                return (
                    ModelMetadata(
                        provider=model.provider,
                        model=model.model,
                        capabilities=model.capabilities,
                        local=False,
                        input_cost_per_million=0.5,
                        output_cost_per_million=1.5,
                    ),
                )

        with self.assertRaisesRegex(ValueError, "pricing upper bound"):
            ProviderExecutor(
                PaidMockProvider(),
                model="mock-chat",
                governor=self.governor,
                resource_quote=ResourceVector(
                    input_tokens=10,
                    output_tokens=10,
                    model_calls=1,
                    cost=19,
                    duration=1_000,
                ),
            )

    def test_cli_creates_reports_and_approves_exact_budget(self):
        self.db.close()
        budget_file = Path(self.temp.name) / "budget.json"
        quote_file = Path(self.temp.name) / "quote.json"
        budget_file.write_text(
            json.dumps(
                {
                    "soft": vector(2).as_dict(),
                    "hard": vector(5).as_dict(),
                    "escalation_mode": "manual_exact",
                    "evidence": ["operator_budget"],
                }
            ),
            encoding="utf-8",
        )
        quote_file.write_text(
            json.dumps(ResourceVector(output_tokens=3).as_dict()),
            encoding="utf-8",
        )
        for arguments in (
            ["resources", "create", "cli-task", str(budget_file)],
            ["resources", "status", "cli-task"],
            [
                "resources",
                "approve",
                "cli-task",
                str(quote_file),
                "--approval-reference",
                "approval-1",
                "--reason",
                "reviewed",
                "--evidence",
                "operator_review",
            ],
        ):
            output = io.StringIO()
            with redirect_stdout(output):
                code = main(["--db", str(self.path), "--json", *arguments])
            self.assertEqual(code, 0)
            self.assertIsInstance(json.loads(output.getvalue()), dict)
        self.db = RuntimeDB(self.path)
        self.governor = ResourceGovernor(self.db.connection)

    def test_task_deadline_allows_exact_boundary_and_fails_after_it(self):
        for elapsed, expected in (
            (1.0, TaskState.COMPLETED),
            (1.001, TaskState.FAILED),
        ):
            clock = [0.0]

            def execute(_task, _step):
                clock[0] = elapsed
                return ExecutionOutput("done")

            runner = TaskRunner(
                planner=SingleStepPlanner(),
                executor=FunctionExecutor({"execute": execute}),
                verifier=PassVerifier(),
                evaluator=PassEvaluator(),
                clock=lambda: clock[0],
            )
            result = runner.run(
                Task("deadline test", time_budget_seconds=1.0)
            )
            self.assertEqual(result.state, expected)
            if expected is TaskState.FAILED:
                self.assertEqual(result.failure.kind, "TimeBudgetExceeded")


if __name__ == "__main__":
    unittest.main()
