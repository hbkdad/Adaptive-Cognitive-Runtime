from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from acr_runtime import AdaptiveRuntime
from acr_runtime.cli import main
from acr_runtime.providers import (
    ChatResponse,
    ModelCapabilities,
    ModelMetadata,
    TokenUsage,
)


class FakeReasoningOllama:
    name = "ollama"

    def __init__(
        self,
        *_args,
        reasoning_modes_by_model=None,
        reasoning_efforts_by_model=None,
        **_kwargs,
    ) -> None:
        self.model = next(iter(reasoning_modes_by_model or {"fake": ()}))
        self._capabilities = ModelCapabilities(
            chat=True,
            token_accounting=True,
            context_window=8192,
            reasoning_modes=tuple(
                (reasoning_modes_by_model or {}).get(self.model, ())
            ),
            reasoning_efforts=tuple(
                (reasoning_efforts_by_model or {}).get(self.model, ())
            ),
        )
        self.last_request = None

    def capabilities(self, model):
        if model != self.model:
            raise LookupError(model)
        return self._capabilities

    def list_models(self):
        return (
            ModelMetadata(
                provider=self.name,
                model=self.model,
                capabilities=self._capabilities,
                local=True,
                input_cost_per_million=0.0,
                output_cost_per_million=0.0,
            ),
        )

    def chat(self, request):
        self.last_request = request
        return ChatResponse(
            provider=self.name,
            model=request.model,
            content="classified",
            usage=TokenUsage(input_tokens=8, output_tokens=2),
            latency_ms=1,
            finish_reason="stop",
        )


class ReasoningCLITests(unittest.TestCase):
    def test_classify_policy_and_refine_commands(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "acr.db"
            request = Path(directory) / "request.json"
            request.write_text(
                json.dumps({"task": "classify this item"}),
                encoding="utf-8",
            )
            output = io.StringIO()
            with redirect_stdout(output):
                code = main([
                    "--db", str(database), "--json",
                    "reasoning", "classify", str(request),
                ])
            payload = json.loads(output.getvalue())
            self.assertEqual(code, 0)
            self.assertEqual(payload["complexity"], "low")
            self.assertFalse(payload["automatic_activation"])

            output = io.StringIO()
            with redirect_stdout(output):
                code = main([
                    "--db", str(database), "--json",
                    "reasoning", "refine", "general",
                ])
            report = json.loads(output.getvalue())
            self.assertEqual(code, 0)
            self.assertEqual(report["status"], "insufficient_evidence")
            self.assertFalse(report["automatic_activation"])

    def test_run_applies_and_attributes_validated_reasoning_policy(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "acr.db"
            output = io.StringIO()
            with patch(
                "acr_runtime.cli.OllamaProvider", FakeReasoningOllama
            ), redirect_stdout(output):
                code = main([
                    "--db", str(database), "--json",
                    "run", "classify this item",
                    "--model", "fake",
                    "--reasoning-mode-supported", "effort",
                    "--reasoning-effort-supported", "low",
                    "--max-input-tokens", "100",
                    "--max-output-tokens", "20",
                    "--max-model-calls", "1",
                    "--max-duration-seconds", "10",
                ])
            payload = json.loads(output.getvalue())
            self.assertEqual(code, 0)
            self.assertEqual(payload["reasoning_complexity"], "low")
            self.assertEqual(payload["provider_reasoning_mode"], "effort")
            self.assertTrue(payload["refinement_eligible"])
            self.assertIsNotNone(payload["reasoning_decision_id"])
            self.assertIsNotNone(payload["reasoning_outcome_id"])
            with AdaptiveRuntime(database) as runtime:
                plan = runtime.learning_plan(payload["task_id"])
            self.assertTrue(plan.structurally_eligible)
            self.assertEqual(plan.execution_run_id, payload["run_id"])


if __name__ == "__main__":
    unittest.main()
