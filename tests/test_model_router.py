import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from acr_runtime.cli import main
from acr_runtime.db import RuntimeDB
from acr_runtime.model_router import (
    ModelOutcome,
    ModelProfile,
    ModelRouter,
    RouteAttempt,
    RouteRequest,
)


class ModelRouterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = RuntimeDB(Path(self.temp.name) / "acr.db")
        self.router = ModelRouter(self.db.connection)
        self.router.register(ModelProfile(
            provider="test", model="cheap", context_capacity=8_000,
            supports_tools=True, input_cost_per_million=0.1,
            output_cost_per_million=0.2,
        ))
        self.router.register(ModelProfile(
            provider="test", model="strong", context_capacity=32_000,
            supports_tools=True, input_cost_per_million=2.0,
            output_cost_per_million=4.0,
        ))

    def tearDown(self):
        self.db.close()
        self.temp.cleanup()

    def outcomes(
        self, model_id, *, quality, successes=4, samples=4,
        tools=True, task_class="coding",
    ):
        for index in range(samples):
            self.router.record_outcome(ModelOutcome(
                model_id=model_id, task_class=task_class,
                success=index < successes, quality=quality,
                latency_ms=100 + index, input_tokens=100, output_tokens=50,
                tool_attempts=1 if tools else 0,
                tool_successes=1 if tools and index < successes else 0,
                evidence=(f"eval:{model_id}:{index}",),
            ))

    @staticmethod
    def request(**overrides):
        values = {
            "task_class": "coding", "quality_threshold": 0.7,
            "minimum_success_rate": 0.7, "estimated_input_tokens": 1_000,
            "estimated_output_tokens": 500, "required_context": 4_000,
            "requires_tools": True, "minimum_tool_reliability": 0.7,
            "minimum_samples": 3, "confidence_z": 0.0,
            "attempt_confidence_threshold": 0.75,
        }
        values.update(overrides)
        return RouteRequest(**values)

    def test_selects_cheapest_model_expected_to_meet_threshold(self):
        self.outcomes("test:cheap", quality=0.8)
        self.outcomes("test:strong", quality=0.95)
        route = self.router.route(self.request())
        self.assertEqual(route.selected_model_id, "test:cheap")
        eligible = [item for item in route.candidates if item["eligible"]]
        self.assertLess(eligible[0]["expected_cost"], eligible[1]["expected_cost"])

    def test_sparse_history_and_capabilities_are_explicit_rejections(self):
        self.outcomes("test:cheap", quality=0.9, samples=2)
        self.outcomes("test:strong", quality=0.95)
        route = self.router.route(self.request(required_context=10_000))
        cheap = next(
            item for item in route.candidates if item["model_id"] == "test:cheap"
        )
        self.assertIn("insufficient_context", cheap["rejection_reasons"])
        self.assertIn("insufficient_verified_samples", cheap["rejection_reasons"])
        self.assertEqual(route.selected_model_id, "test:strong")

    def test_no_qualified_model_is_retained_with_rejection_evidence(self):
        route = self.router.route(self.request())
        self.assertEqual(route.state, "exhausted")
        self.assertIsNone(route.selected_model_id)
        self.assertEqual(len(route.candidates), 2)
        self.assertTrue(all(not item["eligible"] for item in route.candidates))
        with self.assertRaises(ValueError):
            self.router.record_attempt(route.id, RouteAttempt(
                model_id="test:cheap", verification_passed=True,
                confidence=1, quality=1, latency_ms=1, input_tokens=1,
                output_tokens=1, tool_attempts=1, tool_successes=1,
                evidence=("not-selected",),
            ))

    def test_conservative_lower_bound_can_reject_raw_success_rate(self):
        self.outcomes("test:cheap", quality=1.0, samples=3)
        self.outcomes("test:strong", quality=1.0, successes=30, samples=30)
        route = self.router.route(self.request(confidence_z=1.96))
        cheap = next(
            item for item in route.candidates if item["model_id"] == "test:cheap"
        )
        self.assertEqual(cheap["success_rate"], 1.0)
        self.assertIn("success_below_threshold", cheap["rejection_reasons"])
        self.assertEqual(route.selected_model_id, "test:strong")

    def test_failed_verification_escalates_once_and_measures_improvement(self):
        self.outcomes("test:cheap", quality=0.8)
        self.outcomes("test:strong", quality=0.95)
        route = self.router.route(self.request())
        route = self.router.record_attempt(route.id, RouteAttempt(
            model_id="test:cheap", verification_passed=False,
            confidence=0.4, quality=0.5, latency_ms=80,
            input_tokens=900, output_tokens=400, tool_attempts=1,
            tool_successes=0, evidence=("verification:cheap-failed",),
        ))
        self.assertEqual(route.state, "escalation_recommended")
        self.assertEqual(route.escalation_model_id, "test:strong")

        route = self.router.record_attempt(route.id, RouteAttempt(
            model_id="test:strong", verification_passed=True,
            confidence=0.95, quality=0.92, latency_ms=180,
            input_tokens=950, output_tokens=450, tool_attempts=1,
            tool_successes=1, evidence=("verification:strong-passed",),
        ))
        self.assertEqual(route.state, "completed")
        self.assertTrue(route.escalation_improved)
        self.assertEqual(len(route.attempts), 2)
        outcomes = self.db.connection.execute(
            "SELECT COUNT(*) FROM model_outcomes WHERE task_class='coding'"
        ).fetchone()[0]
        self.assertEqual(outcomes, 10)
        with self.assertRaises(ValueError):
            self.router.record_attempt(route.id, RouteAttempt(
                model_id="test:strong", verification_passed=True,
                confidence=1.0, quality=1.0, latency_ms=1,
                input_tokens=1, output_tokens=1, tool_attempts=0,
                tool_successes=0, evidence=("third-attempt",),
            ))

    def test_passing_first_attempt_does_not_escalate(self):
        self.outcomes("test:cheap", quality=0.8)
        self.outcomes("test:strong", quality=0.95)
        route = self.router.route(self.request())
        route = self.router.record_attempt(route.id, RouteAttempt(
            model_id="test:cheap", verification_passed=True,
            confidence=0.9, quality=0.8, latency_ms=80,
            input_tokens=900, output_tokens=400, tool_attempts=1,
            tool_successes=1, evidence=("verification:passed",),
        ))
        self.assertEqual(route.state, "completed")
        self.assertIsNone(route.escalation_model_id)
        self.assertIsNone(route.escalation_improved)

    def test_input_contracts_reject_unverified_or_malformed_records(self):
        with self.assertRaises(ValueError):
            ModelOutcome.from_dict({
                "model_id": "test:cheap", "task_class": "coding",
                "success": True, "quality": 1, "latency_ms": 1,
                "input_tokens": 1, "output_tokens": 1, "tool_attempts": 0,
                "tool_successes": 0, "evidence": [], "extra": True,
            })

    def test_cli_register_route_and_report_are_inspectable(self):
        self.db.close()
        database = Path(self.temp.name) / "cli.db"
        profile_file = Path(self.temp.name) / "profile.json"
        profile_file.write_text(json.dumps({
            "provider": "cli", "model": "model", "context_capacity": 8_000,
            "supports_tools": False, "input_cost_per_million": 0,
            "output_cost_per_million": 0,
        }), encoding="utf-8")
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(main([
                "--db", str(database), "models", "register", str(profile_file)
            ]), 0)
        self.assertEqual(json.loads(output.getvalue())["id"], "cli:model")

        cli_db = RuntimeDB(database)
        runtime = ModelRouter(cli_db.connection)
        for index in range(3):
            runtime.record_outcome(ModelOutcome(
                model_id="cli:model", task_class="general", success=True,
                quality=1, latency_ms=1, input_tokens=1, output_tokens=1,
                tool_attempts=0, tool_successes=0,
                evidence=(f"cli-eval:{index}",),
            ))
        cli_db.close()
        request_file = Path(self.temp.name) / "request.json"
        request_file.write_text(json.dumps({
            "task_class": "general", "quality_threshold": 0.5,
            "minimum_success_rate": 0.5, "estimated_input_tokens": 10,
            "estimated_output_tokens": 10, "required_context": 10,
            "confidence_z": 0,
        }), encoding="utf-8")
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(main([
                "--db", str(database), "models", "route", str(request_file)
            ]), 0)
        route_id = json.loads(output.getvalue())["id"]
        report = io.StringIO()
        with redirect_stdout(report):
            self.assertEqual(main([
                "--db", str(database), "models", "route-report", route_id
            ]), 0)
        self.assertEqual(json.loads(report.getvalue())["selected_model_id"], "cli:model")
        self.db = RuntimeDB(Path(self.temp.name) / "acr.db")
        with self.assertRaises(ValueError):
            ModelProfile.from_dict({
                "provider": "test", "model": "bad", "context_capacity": 1,
                "supports_tools": "yes", "input_cost_per_million": 0,
                "output_cost_per_million": 0,
            })


if __name__ == "__main__":
    unittest.main()
