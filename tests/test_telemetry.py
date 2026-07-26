from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from acr_runtime.db import RuntimeDB
from acr_runtime.execution import (
    ExecutionOutput,
    FunctionExecutor,
    PassEvaluator,
    PassVerifier,
    SingleStepPlanner,
    Task,
    TaskEventBus,
    TaskRunner,
    TaskState,
)
from acr_runtime.telemetry import TelemetryRecorder, sanitize_payload_json


class TelemetryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database = RuntimeDB(Path(self.temp_dir.name) / "acr.db")

    def tearDown(self) -> None:
        self.database.close()
        self.temp_dir.cleanup()

    def test_execution_events_and_run_are_persisted(self):
        event_bus = TaskEventBus()
        recorder = TelemetryRecorder(self.database)
        event_bus.subscribe(recorder)
        runner = TaskRunner(
            planner=SingleStepPlanner("safe-operation"),
            executor=FunctionExecutor(
                {
                    "safe-operation": lambda task, step: ExecutionOutput(
                        content="completed",
                        metadata_json=json.dumps({"items": 1}),
                    )
                }
            ),
            verifier=PassVerifier(),
            evaluator=PassEvaluator(),
            event_bus=event_bus,
        )
        task = Task("perform deterministic operation")

        run = runner.run(task)
        recorder.record_run(run)

        self.assertEqual(run.state, TaskState.COMPLETED)
        task_telemetry = self.database.telemetry_task(task.id)
        self.assertEqual(len(task_telemetry["runs"]), 1)
        self.assertEqual(
            len(task_telemetry["events"]),
            len(run.events),
        )
        summary = self.database.telemetry_summary()
        self.assertEqual(summary["execution"]["runs"], 1)
        self.assertEqual(summary["execution"]["completed"], 1)

    def test_payload_redaction_removes_common_secret_shapes(self):
        payload = json.dumps(
            {
                "api_key": "secret-value",
                "message": "Authorization: Bearer abcdefghijklmnop",
                "nested": {"password": "dont-log-me"},
                "provider_response": "key=another-secret",
            }
        )

        sanitized = sanitize_payload_json(payload)

        self.assertNotIn("secret-value", sanitized)
        self.assertNotIn("abcdefghijklmnop", sanitized)
        self.assertNotIn("dont-log-me", sanitized)
        self.assertNotIn("another-secret", sanitized)
        self.assertIn("[REDACTED]", sanitized)

    def test_task_objective_is_hashed_in_telemetry(self):
        objective = "private objective text"
        event_bus = TaskEventBus()
        recorder = TelemetryRecorder(self.database)
        event_bus.subscribe(recorder)
        runner = TaskRunner(
            planner=SingleStepPlanner("work"),
            executor=FunctionExecutor(
                {"work": lambda task, step: ExecutionOutput(content="done")}
            ),
            verifier=PassVerifier(),
            evaluator=PassEvaluator(),
            event_bus=event_bus,
        )
        task = Task(objective)

        run = runner.run(task)
        recorder.record_run(run)
        stored = json.dumps(self.database.telemetry_task(task.id))

        self.assertNotIn(objective, stored)
        self.assertIn("objective_sha256", stored)


if __name__ == "__main__":
    unittest.main()

