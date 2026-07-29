from __future__ import annotations

import io
import json
import sqlite3
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from acr_runtime.cli import main
from acr_runtime.db import RuntimeDB
from acr_runtime.failure_recovery import (
    ActionClass,
    FailureRecovery,
    RecoveryConflict,
    RecoveryOutput,
    RecoveryStep,
)


class ScriptedExecutor:
    def __init__(self, outcomes: dict[int, list[object]]) -> None:
        self.outcomes = outcomes
        self.calls: list[tuple[int, str]] = []

    def execute(self, step: RecoveryStep) -> RecoveryOutput:
        self.calls.append((step.sequence, step.idempotency_key))
        outcome = self.outcomes[step.sequence].pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return RecoveryOutput(
            output_json=json.dumps({"result": str(outcome)}),
            evidence=(f"test:step-{step.sequence}",),
        )


class FailureRecoveryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "acr.db"
        self.db = RuntimeDB(self.path)
        self.recovery = FailureRecovery(self.db.connection)

    def tearDown(self) -> None:
        self.db.close()
        self.temp.cleanup()

    @staticmethod
    def step(
        sequence: int,
        action_class: ActionClass,
        *,
        destructive: bool = False,
        max_attempts: int = 3,
    ) -> RecoveryStep:
        return RecoveryStep(
            sequence=sequence,
            operation=f"test.step-{sequence}",
            input_json=json.dumps({"sequence": sequence}),
            action_class=action_class,
            idempotency_key=f"task-recovery-step-{sequence}",
            destructive=destructive,
            max_attempts=max_attempts,
        )

    def test_interrupted_idempotent_step_resumes_without_repeating_completed(self):
        run = self.recovery.create(
            "task-recovery",
            (
                self.step(1, ActionClass.IDEMPOTENT),
                self.step(2, ActionClass.IDEMPOTENT),
            ),
        )
        executor = ScriptedExecutor(
            {1: ["one"], 2: [KeyboardInterrupt(), "two"]}
        )
        with self.assertRaises(KeyboardInterrupt):
            self.recovery.resume(run["id"], executor)
        checkpoint = self.recovery.get(run["id"])
        self.assertEqual(checkpoint["status"], "running")
        self.assertEqual(checkpoint["steps"][0]["state"], "completed")
        self.assertEqual(checkpoint["steps"][1]["state"], "running")

        interrupted = self.recovery.mark_interrupted(
            run["id"],
            actor="operator-test",
            reason="Worker process terminated",
            evidence=("test:confirmed-worker-exit",),
        )
        self.assertEqual(interrupted["status"], "interrupted")
        completed = self.recovery.resume(run["id"], executor)
        self.assertEqual(completed["status"], "completed")
        self.assertEqual(
            executor.calls,
            [
                (1, "task-recovery-step-1"),
                (2, "task-recovery-step-2"),
                (2, "task-recovery-step-2"),
            ],
        )

    def test_retryable_known_failure_retries_but_ambiguous_one_does_not(self):
        retryable = self.recovery.create(
            "task-retryable",
            (self.step(1, ActionClass.RETRYABLE, max_attempts=2),),
        )
        executor = ScriptedExecutor({1: [RuntimeError("transient"), "ok"]})
        failed = self.recovery.resume(retryable["id"], executor)
        self.assertEqual(failed["steps"][0]["state"], "failed")
        completed = self.recovery.resume(retryable["id"], executor)
        self.assertEqual(completed["status"], "completed")

        ambiguous = self.recovery.create(
            "task-ambiguous",
            (
                RecoveryStep(
                    sequence=1,
                    operation="payment.charge",
                    input_json='{"amount":100}',
                    action_class=ActionClass.NON_RETRYABLE,
                    idempotency_key="task-ambiguous-charge",
                    destructive=True,
                    max_attempts=1,
                ),
            ),
        )
        destructive = ScriptedExecutor({1: [KeyboardInterrupt()]})
        with self.assertRaises(KeyboardInterrupt):
            self.recovery.resume(ambiguous["id"], destructive)
        blocked = self.recovery.mark_interrupted(
            ambiguous["id"],
            actor="operator-test",
            reason="Charge response was lost",
            evidence=("test:ambiguous-provider-state",),
        )
        self.assertEqual(blocked["status"], "blocked")
        self.recovery.resume(ambiguous["id"], destructive)
        self.assertEqual(len(destructive.calls), 1)

    def test_human_review_can_accept_ambiguous_destructive_completion(self):
        run = self.recovery.create(
            "task-human-review",
            (
                RecoveryStep(
                    sequence=1,
                    operation="deploy.production",
                    input_json='{"release":"v1"}',
                    action_class=ActionClass.HUMAN_REVIEW_REQUIRED,
                    idempotency_key="task-human-review-deploy",
                    destructive=True,
                    max_attempts=1,
                ),
            ),
        )
        executor = ScriptedExecutor({1: [KeyboardInterrupt()]})
        blocked = self.recovery.resume(run["id"], executor)
        self.assertEqual(blocked["status"], "blocked")
        self.assertEqual(executor.calls, [])
        self.recovery.resolve_review(
            run["id"],
            1,
            "execute",
            actor="operator-test",
            reason="Approved initial production deployment",
            evidence=("approval:test-1",),
        )
        with self.assertRaises(KeyboardInterrupt):
            self.recovery.resume(run["id"], executor)
        self.recovery.mark_interrupted(
            run["id"],
            actor="operator-test",
            reason="Deployment connection ended before acknowledgement",
            evidence=("test:deployment-status-unknown",),
        )
        self.recovery.resolve_review(
            run["id"],
            1,
            "accept_completed",
            actor="operator-test",
            reason="Independent deployment status confirms release",
            evidence=("test:deployment-v1-live",),
        )
        completed = self.recovery.resume(run["id"], executor)
        self.assertEqual(completed["status"], "completed")
        self.assertEqual(len(executor.calls), 1)

    def test_non_retryable_known_failure_requires_review_before_retry(self):
        run = self.recovery.create(
            "task-non-retryable",
            (
                self.step(
                    1,
                    ActionClass.NON_RETRYABLE,
                    max_attempts=1,
                ),
            ),
        )
        executor = ScriptedExecutor({1: [RuntimeError("failed"), "fixed"]})
        blocked = self.recovery.resume(run["id"], executor)
        self.assertEqual(blocked["status"], "blocked")
        self.recovery.resume(run["id"], executor)
        self.assertEqual(len(executor.calls), 1)
        self.recovery.resolve_review(
            run["id"],
            1,
            "execute",
            actor="operator-test",
            reason="Root cause corrected before explicit retry",
            evidence=("test:fix-confirmed",),
        )
        completed = self.recovery.resume(run["id"], executor)
        self.assertEqual(completed["status"], "completed")
        self.assertEqual(len(executor.calls), 2)

    def test_classification_and_concurrent_resume_guards_fail_closed(self):
        with self.assertRaisesRegex(ValueError, "Destructive"):
            self.step(
                1,
                ActionClass.RETRYABLE,
                destructive=True,
            )
        run = self.recovery.create(
            "task-running",
            (self.step(1, ActionClass.IDEMPOTENT),),
        )
        executor = ScriptedExecutor({1: [KeyboardInterrupt()]})
        with self.assertRaises(KeyboardInterrupt):
            self.recovery.resume(run["id"], executor)
        with self.assertRaisesRegex(RecoveryConflict, "already claimed"):
            self.recovery.resume(run["id"], executor)
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.connection.execute(
                "UPDATE recovery_events SET event='changed'"
            )
        self.db.connection.rollback()

    def test_idempotency_key_cannot_be_reused_for_different_intent(self):
        step = self.step(1, ActionClass.IDEMPOTENT)
        first = self.recovery.create("task-key-original", (step,))
        replay = self.recovery.create("task-key-original", (step,))
        self.assertEqual(first["id"], replay["id"])
        changed = RecoveryStep(
            sequence=1,
            operation=step.operation,
            input_json='{"sequence":"different"}',
            action_class=step.action_class,
            idempotency_key=step.idempotency_key,
            max_attempts=step.max_attempts,
        )
        with self.assertRaises(sqlite3.IntegrityError):
            self.recovery.create("task-key-different", (changed,))
        self.assertEqual(self.recovery.get(first["id"])["status"], "planned")

    def test_cli_creates_and_inspects_recovery_plan(self):
        plan = {
            "task_id": "task-cli-recovery",
            "steps": [
                self.step(1, ActionClass.IDEMPOTENT).as_dict(),
            ],
        }
        plan_path = Path(self.temp.name) / "recovery-plan.json"
        plan_path.write_text(json.dumps(plan), encoding="utf-8")
        output = io.StringIO()
        with redirect_stdout(output):
            code = main(
                [
                    "--db",
                    str(self.path),
                    "recovery",
                    "create",
                    str(plan_path),
                ]
            )
        self.assertEqual(code, 0)
        created = json.loads(output.getvalue())
        output = io.StringIO()
        with redirect_stdout(output):
            code = main(
                [
                    "--db",
                    str(self.path),
                    "recovery",
                    "inspect",
                    created["id"],
                ]
            )
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output.getvalue())["status"], "planned")


if __name__ == "__main__":
    unittest.main()
