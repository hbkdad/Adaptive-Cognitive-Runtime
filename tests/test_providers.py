from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from acr_runtime.db import RuntimeDB
from acr_runtime.execution import (
    PassEvaluator,
    PassVerifier,
    SingleStepPlanner,
    Task,
    TaskRunner,
    TaskState,
)
from acr_runtime.providers import (
    ChatMessage,
    ChatRequest,
    MockProvider,
    ProviderExecutor,
)
from acr_runtime.telemetry import TelemetryRecorder


class ProviderTests(unittest.TestCase):
    def test_mock_provider_exposes_capabilities_and_token_accounting(self):
        provider = MockProvider(lambda request: "deterministic response")
        metadata = provider.list_models()[0]
        response = provider.chat(
            ChatRequest(
                model=metadata.model,
                messages=(ChatMessage(role="user", content="hello"),),
            )
        )

        self.assertEqual(metadata.provider, "mock")
        self.assertTrue(metadata.capabilities.chat)
        self.assertTrue(metadata.capabilities.streaming)
        self.assertTrue(metadata.capabilities.token_accounting)
        self.assertEqual(response.content, "deterministic response")
        self.assertTrue(response.usage.estimated)
        self.assertGreater(response.usage.total_tokens, 0)

    def test_provider_executor_runs_through_provider_independent_engine(self):
        provider = MockProvider(lambda request: "provider result")
        runner = TaskRunner(
            planner=SingleStepPlanner(),
            executor=ProviderExecutor(provider, model="mock-chat"),
            verifier=PassVerifier(),
            evaluator=PassEvaluator(),
        )

        run = runner.run(Task("complete this bounded task"))

        self.assertEqual(run.state, TaskState.COMPLETED)
        self.assertEqual(run.result.content, "provider result")
        self.assertIn("mock/mock-chat", run.observations[0].content)

    def test_model_calls_emit_content_free_telemetry(self):
        with tempfile.TemporaryDirectory() as directory:
            database = RuntimeDB(Path(directory) / "acr.db")
            try:
                recorder = TelemetryRecorder(database)
                provider = MockProvider(
                    lambda request: "private response text",
                    sink=recorder.record_model_call,
                )
                task_id = "task-provider-test"
                provider.chat(
                    ChatRequest(
                        model="mock-chat",
                        messages=(
                            ChatMessage(role="user", content="private prompt text"),
                        ),
                        task_id=task_id,
                    )
                )

                telemetry = database.telemetry_task(task_id)
                serialized = repr(telemetry)
                self.assertEqual(len(telemetry["events"]), 1)
                self.assertNotIn("private prompt text", serialized)
                self.assertNotIn("private response text", serialized)
                self.assertEqual(telemetry["events"][0]["provider"], "mock")
                self.assertGreater(telemetry["events"][0]["input_tokens"], 0)
            finally:
                database.close()


if __name__ == "__main__":
    unittest.main()

