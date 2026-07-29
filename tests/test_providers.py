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
    ModelCapabilities,
    ModelMetadata,
    ProviderExecutor,
    ReasoningControl,
    TokenUsage,
)
from acr_runtime.telemetry import TelemetryRecorder


class ProviderTests(unittest.TestCase):
    def test_reasoning_control_and_usage_are_strict(self):
        with self.assertRaises(ValueError):
            ReasoningControl(mode="effort")
        with self.assertRaises(ValueError):
            ReasoningControl(mode="unknown")  # type: ignore[arg-type]
        with self.assertRaises(ValueError):
            ReasoningControl(
                mode="effort", effort="turbo"  # type: ignore[arg-type]
            )
        with self.assertRaises(ValueError):
            ReasoningControl(mode="fixed_budget", budget_tokens=0)
        with self.assertRaises(ValueError):
            TokenUsage(1, 2, reasoning_tokens=3)
        usage = TokenUsage(1, 2, reasoning_tokens=1)
        self.assertEqual(usage.total_tokens, 3)

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

    def test_provider_executor_attributes_effective_reasoning_control(self):
        seen = []
        provider = MockProvider(
            lambda request: seen.append(request) or "provider result"
        )
        provider._models = (
            ModelMetadata(
                provider="mock",
                model="mock-chat",
                capabilities=ModelCapabilities(
                    chat=True,
                    reasoning_modes=("effort",),
                    reasoning_efforts=("high",),
                ),
                local=True,
                input_cost_per_million=0.0,
                output_cost_per_million=0.0,
            ),
        )
        runner = TaskRunner(
            planner=SingleStepPlanner(),
            executor=ProviderExecutor(
                provider,
                model="mock-chat",
                reasoning=ReasoningControl(mode="effort", effort="high"),
                reasoning_decision_id="reasoning-decision-1",
            ),
            verifier=PassVerifier(),
            evaluator=PassEvaluator(),
        )
        run = runner.run(Task("complete this bounded task"))
        metadata = __import__("json").loads(run.actions[0].output_json)
        self.assertEqual(seen[0].reasoning.effort, "high")
        self.assertEqual(
            metadata["reasoning_decision_id"], "reasoning-decision-1"
        )
        self.assertEqual(metadata["provider_reasoning_mode"], "effort")

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
