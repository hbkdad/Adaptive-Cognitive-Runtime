from __future__ import annotations

import unittest

from acr_runtime.execution import (
    CancellationToken,
    ExecutionOutput,
    FunctionExecutor,
    InvalidTransition,
    Lifecycle,
    PassEvaluator,
    PassVerifier,
    SingleStepPlanner,
    Task,
    TaskEvent,
    TaskEventBus,
    TaskRunner,
    TaskState,
)


class ExecutionTests(unittest.TestCase):
    def _runner(self, operation):
        return TaskRunner(
            planner=SingleStepPlanner("work"),
            executor=FunctionExecutor({"work": operation}),
            verifier=PassVerifier(),
            evaluator=PassEvaluator(),
        )

    def test_lifecycle_rejects_invalid_transition(self):
        lifecycle = Lifecycle()
        with self.assertRaises(InvalidTransition):
            lifecycle.transition(TaskState.COMPLETED)

    def test_deterministic_task_completes_with_immutable_run_record(self):
        captured: list[TaskEvent] = []
        event_bus = TaskEventBus()
        event_bus.subscribe(captured.append)
        runner = TaskRunner(
            planner=SingleStepPlanner("uppercase"),
            executor=FunctionExecutor(
                {
                    "uppercase": lambda task, step: ExecutionOutput(
                        content=task.objective.upper(),
                        observation="Converted objective to uppercase",
                    )
                }
            ),
            verifier=PassVerifier(),
            evaluator=PassEvaluator(),
            event_bus=event_bus,
        )

        run = runner.run(Task("make this uppercase"))

        self.assertEqual(run.state, TaskState.COMPLETED)
        self.assertEqual(run.result.content, "MAKE THIS UPPERCASE")
        self.assertEqual(len(run.actions), 1)
        self.assertEqual(len(run.observations), 1)
        self.assertEqual(tuple(captured), run.events)
        self.assertEqual(
            [event.sequence for event in run.events],
            list(range(1, len(run.events) + 1)),
        )
        with self.assertRaises(AttributeError):
            run.state = TaskState.FAILED

    def test_execution_failure_is_captured_instead_of_swallowed(self):
        def fail(task, step):
            raise RuntimeError("deterministic operation failed")

        run = self._runner(fail).run(Task("run failing operation"))

        self.assertEqual(run.state, TaskState.FAILED)
        self.assertEqual(run.failure.kind, "RuntimeError")
        self.assertIn("deterministic operation failed", run.failure.message)
        self.assertEqual(run.events[-1].event_type, "task.error")

    def test_pre_cancelled_task_does_not_plan_or_execute(self):
        executed = False

        def operation(task, step):
            nonlocal executed
            executed = True
            return ExecutionOutput(content="should not run")

        token = CancellationToken()
        token.cancel()
        run = self._runner(operation).run(Task("cancel this"), cancellation=token)

        self.assertEqual(run.state, TaskState.CANCELLED)
        self.assertFalse(executed)
        self.assertEqual(run.steps, ())
        self.assertEqual(run.actions, ())

    def test_task_budget_validation(self):
        with self.assertRaises(ValueError):
            Task("invalid budget", token_budget=0)
        with self.assertRaises(ValueError):
            Task("invalid budget", money_budget=-1)
        with self.assertRaises(ValueError):
            Task("invalid budget", time_budget_seconds=0)


if __name__ == "__main__":
    unittest.main()

