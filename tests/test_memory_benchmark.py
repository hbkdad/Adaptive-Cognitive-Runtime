from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from acr_runtime.cli import main
from acr_runtime.memory_benchmark import (
    ARMS,
    MEMORY_CASE_CATEGORIES,
    MemoryBenchmarkDataset,
    MemoryBenchmarkRunner,
)


DATASET = Path(__file__).parents[1] / "benchmarks" / "v1" / "memory.jsonl"


class MemoryBenchmarkTests(unittest.TestCase):
    def test_dataset_has_exact_required_adversarial_coverage(self):
        dataset = MemoryBenchmarkDataset.load(DATASET)
        self.assertEqual(
            {case.category for case in dataset.cases},
            MEMORY_CASE_CATEGORIES,
        )
        self.assertEqual(len(dataset.cases), 8)
        self.assertEqual(
            next(case for case in dataset.cases
                 if case.category == "large_history").noise_count,
            500,
        )

    def test_four_paired_arms_measure_accuracy_and_tokens(self):
        report = MemoryBenchmarkRunner().run(
            MemoryBenchmarkDataset.load(DATASET)
        )
        self.assertEqual(len(report.results), 8 * 4)
        for case_id in {row.case_id for row in report.results}:
            self.assertEqual(
                {row.arm for row in report.results if row.case_id == case_id},
                set(ARMS),
            )
        self.assertEqual(report.summary["no_memory"]["average_accuracy"], 0)
        self.assertEqual(report.summary["acr_memory"]["average_accuracy"], 1)
        self.assertLess(
            report.summary["acr_memory"]["input_tokens"],
            report.summary["raw_conversation"]["input_tokens"],
        )
        self.assertLessEqual(
            report.summary["acr_memory"]["input_tokens"],
            report.summary["simple_rag"]["input_tokens"],
        )

    def test_temporal_scope_poison_and_conflict_safety_are_visible(self):
        report = MemoryBenchmarkRunner().run(
            MemoryBenchmarkDataset.load(DATASET)
        )
        acr = {
            row.category: row for row in report.results
            if row.arm == "acr_memory"
        }
        for category in (
            "temporal_change", "cross_project_isolation", "memory_poisoning"
        ):
            self.assertEqual(acr[category].accuracy, 1)
            self.assertEqual(acr[category].harmful_selected, 0)
        self.assertTrue(acr["contradiction"].conflict_detected)
        self.assertEqual(acr["contradiction"].accuracy, 1)

    def test_invalid_dataset_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "memory.jsonl"
            header = {
                "record_type": "memory_dataset",
                "name": "incomplete",
                "version": 1,
                "description": "missing categories",
            }
            path.write_text(
                json.dumps(header) + "\n"
                + DATASET.read_text(encoding="utf-8").splitlines()[1] + "\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "coverage mismatch"):
                MemoryBenchmarkDataset.load(path)

    def test_cli_validate_and_run(self):
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(main([
                "benchmark", "validate-memory", str(DATASET),
            ]), 0)
        self.assertEqual(json.loads(output.getvalue())["cases"], 8)
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(main([
                "benchmark", "memory", str(DATASET),
            ]), 0)
        payload = json.loads(output.getvalue())
        self.assertEqual(payload["summary"]["acr_memory"]["average_accuracy"], 1)
        self.assertIn("no_model_quality_claim", payload["interpretation"])


if __name__ == "__main__":
    unittest.main()
