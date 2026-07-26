from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from acr_runtime.benchmark import BenchmarkDataset, BenchmarkRunner
from acr_runtime.providers import MockProvider


class BenchmarkTests(unittest.TestCase):
    def _dataset(self, directory: str) -> BenchmarkDataset:
        path = Path(directory) / "cases.jsonl"
        records = [
            {"record_type": "dataset", "name": "unit", "version": 1},
            {
                "record_type": "case",
                "id": "one",
                "category": "fact_retrieval",
                "prompt": "answer one",
                "expected": "one",
            },
            {
                "record_type": "case",
                "id": "two",
                "category": "planning",
                "prompt": "answer two",
                "expected": "two",
            },
        ]
        path.write_text(
            "\n".join(json.dumps(record) for record in records),
            encoding="utf-8",
        )
        return BenchmarkDataset.load(path)

    def test_dataset_validation_and_reproducible_order(self):
        with tempfile.TemporaryDirectory() as directory:
            dataset = self._dataset(directory)
            provider = MockProvider(
                lambda request: request.messages[-1].content.split()[-1]
            )
            runner = BenchmarkRunner(provider, model="mock-chat")

            first = runner.run(dataset, seed=17)
            second = runner.run(dataset, seed=17)

            self.assertEqual(
                [case.case_id for case in first.cases],
                [case.case_id for case in second.cases],
            )
            self.assertEqual(first.summary["average_quality"], 1.0)
            self.assertGreater(first.summary["input_tokens"], 0)
            self.assertEqual(first.summary["failure_rate"], 0.0)

    def test_provider_failure_is_measured(self):
        with tempfile.TemporaryDirectory() as directory:
            dataset = self._dataset(directory)

            def fail(request):
                raise RuntimeError("benchmark provider failed")

            report = BenchmarkRunner(
                MockProvider(fail), model="mock-chat"
            ).run(dataset)

            self.assertEqual(report.summary["failure_rate"], 1.0)
            self.assertEqual(report.summary["average_quality"], 0.0)
            self.assertEqual(
                {case.error_kind for case in report.cases},
                {"RuntimeError"},
            )

    def test_unsupported_dataset_version_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "future.jsonl"
            path.write_text(
                json.dumps(
                    {"record_type": "dataset", "name": "future", "version": 99}
                ),
                encoding="utf-8",
            )
            with self.assertRaises(ValueError):
                BenchmarkDataset.load(path)


if __name__ == "__main__":
    unittest.main()

