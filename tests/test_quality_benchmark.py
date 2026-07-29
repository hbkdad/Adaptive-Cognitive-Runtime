from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from acr_runtime.quality_benchmark import (
    KeywordQualityEvaluator,
    MockQualityProvider,
    QualityBenchmarkRunner,
    QualityCase,
    QualityDataset,
)


class InvalidEvaluator:
    name = "invalid"

    def score(self, output, *, criteria):
        return 2.0


class QualityBenchmarkTests(unittest.TestCase):
    def setUp(self) -> None:
        self.dataset_path = (
            Path(__file__).parents[1]
            / "quality_benchmarks"
            / "v1"
            / "smoke.jsonl"
        )

    def test_mock_repeated_run_is_separate_descriptive_quality_evidence(self) -> None:
        dataset = QualityDataset.load(self.dataset_path)
        runner = QualityBenchmarkRunner(
            MockQualityProvider(
                (
                    "The runtime is local-first and uses SQLite. default deny",
                    "The runtime uses SQLite.",
                    "local-first; default deny",
                )
            ),
            KeywordQualityEvaluator(),
        )
        report = runner.run(dataset, repetitions=6, seed=7)
        self.assertEqual(report["sample_count"], 12)
        self.assertTrue(report["probabilistic_quality_benchmark"])
        self.assertFalse(report["deterministic_assertion"])
        self.assertEqual(report["provider"], "mock-quality")
        self.assertGreater(report["mean_score"], 0)
        self.assertTrue(
            all(case["samples"] == 6 for case in report["cases"])
        )
        self.assertTrue(
            any(
                case["standard_deviation"] > 0
                for case in report["cases"]
            )
        )

    def test_dataset_and_runner_contracts_fail_closed(self) -> None:
        with self.assertRaises(ValueError):
            QualityCase("x", "", ("criterion",), 0.5)
        with self.assertRaises(ValueError):
            MockQualityProvider(())
        dataset = QualityDataset.load(self.dataset_path)
        runner = QualityBenchmarkRunner(
            MockQualityProvider(("safe",)), InvalidEvaluator()
        )
        with self.assertRaisesRegex(ValueError, "invalid score"):
            runner.run(dataset, repetitions=3)
        with self.assertRaises(ValueError):
            runner.run(dataset, repetitions=2)

    def test_dataset_rejects_unknown_fields_and_duplicate_ids(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            unknown = Path(directory) / "unknown.jsonl"
            unknown.write_text(
                '{"schema_version":1,"name":"bad"}\n'
                '{"id":"x","prompt":"p","criteria":["c"],'
                '"minimum_score":0.5,"extra":true}\n',
                encoding="utf-8",
            )
            with self.assertRaises(ValueError):
                QualityDataset.load(unknown)

            duplicate = Path(directory) / "duplicate.jsonl"
            duplicate.write_text(
                '{"schema_version":1,"name":"bad"}\n'
                '{"id":"x","prompt":"p","criteria":["c"],'
                '"minimum_score":0.5}\n'
                '{"id":"x","prompt":"q","criteria":["d"],'
                '"minimum_score":0.5}\n',
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "unique"):
                QualityDataset.load(duplicate)


if __name__ == "__main__":
    unittest.main()
