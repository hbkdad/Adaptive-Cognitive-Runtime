from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from acr_runtime import AdaptiveRuntime
from acr_runtime.cli import main
from acr_runtime.confidence_calibration import ConfidenceCalibration
from acr_runtime.db import RuntimeDB
from acr_runtime.evaluation import EvaluationCase
from acr_runtime.model_router import (
    ModelOutcome,
    ModelProfile,
    RouteAttempt,
    RouteRequest,
)


class ConfidenceCalibrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "acr.db"

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_curve_ece_brier_intervals_and_interpretation_are_deterministic(self):
        db = RuntimeDB(self.path)
        calibration = ConfidenceCalibration(db.connection)
        for index, (predicted, outcome) in enumerate(
            ((0.1, False), (0.2, False), (0.8, True), (0.9, False))
        ):
            calibration.observe(
                "evaluation", f"resolved-{index}", predicted, outcome
            )
        calibration.record_prediction(
            "evaluation", "pending", 0.95, evidence=("awaiting_result",)
        )

        report = calibration.report("evaluation", bins=2)

        self.assertEqual(report.sample_count, 4)
        self.assertEqual(report.unresolved_count, 1)
        self.assertAlmostEqual(report.expected_calibration_error, 0.25)
        self.assertAlmostEqual(report.maximum_calibration_error, 0.35)
        self.assertAlmostEqual(report.brier_score, 0.225)
        self.assertEqual([item.sample_count for item in report.bins], [2, 2])
        self.assertLess(
            report.bins[1].actual_rate_lower_95,
            report.bins[1].actual_success_rate,
        )
        self.assertGreater(
            report.bins[1].actual_rate_upper_95,
            report.bins[1].actual_success_rate,
        )

        adjusted = calibration.interpret(
            "evaluation", 0.9, bins=2, minimum_samples=2
        )
        self.assertEqual(adjusted.status, "empirically_adjusted")
        self.assertEqual(adjusted.interpreted_confidence, 0.5)
        self.assertAlmostEqual(adjusted.adjustment_delta, -0.4)
        sparse = calibration.interpret(
            "evaluation", 0.9, bins=2, minimum_samples=3
        )
        self.assertEqual(sparse.status, "insufficient_evidence")
        self.assertIsNone(sparse.interpreted_confidence)
        db.close()

    def test_contracts_reject_boolean_confidence_and_outcome_replay(self):
        db = RuntimeDB(self.path)
        calibration = ConfidenceCalibration(db.connection)
        with self.assertRaisesRegex(ValueError, "numeric"):
            calibration.record_prediction("memory", "m1", True)
        calibration.record_prediction("memory", "m1", 0.8)
        calibration.resolve(
            "memory", "m1", True, evidence=("verified_task_outcome",)
        )
        with self.assertRaisesRegex(LookupError, "already resolved"):
            calibration.resolve(
                "memory", "m1", True, evidence=("replayed_outcome",)
            )
        with self.assertRaises(ValueError):
            calibration.report("memory", bins=1)
        db.close()

    def test_memory_snapshots_confidence_at_selection_and_resolves_later(self):
        with AdaptiveRuntime(self.path) as runtime:
            memory_id = runtime.remember(
                "semantic",
                "Calibration snapshots confidence before task completion.",
                scope="project",
                confidence=0.9,
                importance=1.0,
            )
            bundle = runtime.compile_context(
                "When is calibration confidence snapshotted?",
                scope="project",
                token_budget=160,
            )
            self.assertIn(memory_id, {item.source_id for item in bundle.blocks})
            runtime.db.connection.execute(
                "UPDATE memories SET confidence = 0.1 WHERE id = ?",
                (memory_id,),
            )
            runtime.db.connection.commit()
            runtime.complete_task(
                bundle,
                success=True,
                critic_score=1.0,
                duration_ms=1,
                useful_source_ids={item.source_id for item in bundle.blocks},
            )

            report = runtime.calibration_report(
                "memory", group_key="semantic"
            )
            self.assertEqual(report.sample_count, 1)
            self.assertAlmostEqual(report.mean_predicted_confidence, 0.9)
            self.assertEqual(report.actual_success_rate, 1.0)

    def test_routing_targets_verification_not_policy_derived_success(self):
        with AdaptiveRuntime(self.path) as runtime:
            profile = ModelProfile(
                provider="test",
                model="only",
                context_capacity=8_000,
                supports_tools=False,
                input_cost_per_million=0.1,
                output_cost_per_million=0.1,
            )
            runtime.register_model(profile)
            runtime.record_model_outcome(
                ModelOutcome(
                    model_id=profile.id,
                    task_class="coding",
                    success=True,
                    quality=1.0,
                    latency_ms=1,
                    input_tokens=1,
                    output_tokens=1,
                    tool_attempts=0,
                    tool_successes=0,
                    evidence=("verified-baseline",),
                )
            )
            route = runtime.route_model(
                RouteRequest(
                    task_class="coding",
                    quality_threshold=0.5,
                    minimum_success_rate=0.5,
                    estimated_input_tokens=1,
                    estimated_output_tokens=1,
                    required_context=1,
                    minimum_samples=1,
                    confidence_z=0.0,
                )
            )
            runtime.record_model_attempt(
                route.id,
                RouteAttempt(
                    model_id=profile.id,
                    verification_passed=False,
                    confidence=0.99,
                    quality=1.0,
                    latency_ms=1,
                    input_tokens=1,
                    output_tokens=1,
                    tool_attempts=0,
                    tool_successes=0,
                    evidence=("verification-failed",),
                ),
            )
            report = runtime.calibration_report(
                "routing", group_key="coding"
            )
            self.assertEqual(report.sample_count, 1)
            self.assertEqual(report.actual_success_rate, 0.0)
            self.assertAlmostEqual(report.mean_predicted_confidence, 0.99)

    def test_evaluation_requires_explicit_forecast_and_cli_reports_it(self):
        with AdaptiveRuntime(self.path) as runtime:
            runtime.evaluate(
                EvaluationCase(
                    objective="Match the expected answer",
                    actual="ok",
                    expected="ok",
                )
            )
            self.assertEqual(
                runtime.calibration_report("evaluation").sample_count, 0
            )
            runtime.evaluate(
                EvaluationCase(
                    objective="Match the expected answer",
                    actual="ok",
                    expected="ok",
                ),
                predicted_confidence=0.75,
            )
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            code = main(
                [
                    "--db",
                    str(self.path),
                    "--json",
                    "calibration",
                    "report",
                    "evaluation",
                ]
            )
        self.assertEqual(code, 0)
        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["sample_count"], 1)
        self.assertEqual(payload["actual_success_rate"], 1.0)
        self.assertFalse(payload["policy"]["automatic_rewrite"])


if __name__ == "__main__":
    unittest.main()
