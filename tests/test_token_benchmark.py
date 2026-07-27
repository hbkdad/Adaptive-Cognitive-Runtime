from __future__ import annotations

import io
import json
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from acr_runtime.cli import main
from acr_runtime.token_benchmark import (
    ARMS,
    REQUIRED_CATEGORIES,
    TokenBenchmarkDataset,
    TokenBenchmarkRunner,
)


DATASET = (
    Path(__file__).parents[1] / "benchmarks" / "v1"
    / "token-optimization.jsonl"
)


class TokenBenchmarkTests(unittest.TestCase):
    def test_dataset_covers_excessive_and_precision_sensitive_context(self):
        dataset = TokenBenchmarkDataset.load(DATASET)
        self.assertEqual(
            {case.category for case in dataset.cases}, REQUIRED_CATEGORIES
        )
        self.assertEqual(len(dataset.cases), 5)
        self.assertGreater(sum(case.noise_count for case in dataset.cases), 250)

    def test_four_arms_measure_quality_tokens_latency_and_cost(self):
        report = TokenBenchmarkRunner().run(
            TokenBenchmarkDataset.load(DATASET)
        )
        self.assertEqual(len(report.results), 5 * 4)
        for arm in ARMS:
            summary = report.summary[arm]
            self.assertIn("average_quality", summary)
            self.assertIn("input_tokens", summary)
            self.assertGreaterEqual(summary["latency_ms"], 0)
            self.assertGreaterEqual(summary["cost"], 0)
        self.assertEqual(
            report.summary["acr_context_compiler"]["average_quality"], 1
        )
        self.assertTrue(
            report.summary["acr_context_compiler"]["quality_non_regression"]
        )
        self.assertTrue(
            report.summary["acr_context_compiler"]["primary_goal_met"]
        )
        self.assertLess(
            report.summary["acr_context_compiler"]["input_tokens"],
            report.summary["full_context"]["input_tokens"],
        )

    def test_exact_values_and_dependencies_survive_compiler(self):
        report = TokenBenchmarkRunner().run(
            TokenBenchmarkDataset.load(DATASET)
        )
        acr = {
            row.category: row for row in report.results
            if row.arm == "acr_context_compiler"
        }
        for category in ("exact_command", "exact_error", "code_expression"):
            self.assertTrue(acr[category].exact_preserved)
            self.assertEqual(acr[category].quality, 1)
        self.assertEqual(
            set(acr["dependency_expansion"].selected_labels),
            {"checksum-workflow", "checksum-algorithm"},
        )

    def test_cli_validate_and_run(self):
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(main([
                "benchmark", "validate-token", str(DATASET),
            ]), 0)
        self.assertEqual(json.loads(output.getvalue())["cases"], 5)
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(main([
                "benchmark", "token", str(DATASET),
            ]), 0)
        payload = json.loads(output.getvalue())
        self.assertTrue(
            payload["summary"]["acr_context_compiler"]["primary_goal_met"]
        )


if __name__ == "__main__":
    unittest.main()
