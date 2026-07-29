from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from acr_runtime.agent_spec import AgentSpec
from acr_runtime.performance_review import (
    CATEGORY_UNITS,
    PERFORMANCE_CATEGORIES,
    PerformanceReviewReport,
    main,
)


ROOT = Path(__file__).parents[1]


def _observation(
    category: str,
    *,
    status: str = "unmeasured",
    baseline: int | None = None,
    candidate: int | None = None,
    samples: int = 0,
) -> dict[str, object]:
    measured_waste = status == "measured_waste"
    measured = status != "unmeasured"
    return {
        "category": category,
        "status": status,
        "unit": CATEGORY_UNITS[category],
        "baseline_value": baseline,
        "candidate_value": candidate,
        "sample_count": samples,
        "measurement_ref": f"profile:{category}" if measured else None,
        "quality_gate_passed": measured_waste,
        "security_gate_passed": measured_waste,
        "evidence": ["Bounded comparison evidence."],
        "recommendation": "Measure or apply the smallest verified change.",
    }


def _report(
    replacements: dict[str, dict[str, object]] | None = None,
) -> dict[str, object]:
    observations = []
    for category in PERFORMANCE_CATEGORIES:
        observations.append(
            replacements.get(category, _observation(category))
            if replacements
            else _observation(category)
        )
    return {
        "schema_version": 1,
        "change_ref": "git:abcdef0123456789",
        "observations": observations,
    }


class PerformanceReviewAgentTests(unittest.TestCase):
    def test_report_requires_all_six_categories_in_order(self) -> None:
        report = PerformanceReviewReport.from_dict(_report())
        self.assertEqual(report.opportunities, ())

        payload = _report()
        payload["observations"] = payload["observations"][:-1]
        with self.assertRaisesRegex(ValueError, "every performance category"):
            PerformanceReviewReport.from_dict(payload)

    def test_measured_waste_is_ranked_by_relative_reduction(self) -> None:
        payload = _report(
            {
                "token_usage": _observation(
                    "token_usage",
                    status="measured_waste",
                    baseline=1_000,
                    candidate=800,
                    samples=5,
                ),
                "latency": _observation(
                    "latency",
                    status="measured_waste",
                    baseline=1_000_000,
                    candidate=600_000,
                    samples=5,
                ),
            }
        )
        report = PerformanceReviewReport.from_dict(payload)
        opportunities = report.opportunities
        self.assertEqual(
            tuple(item["category"] for item in opportunities),
            ("latency", "token_usage"),
        )
        self.assertEqual(opportunities[0]["impact"], "high")
        self.assertEqual(opportunities[1]["impact"], "medium")

    def test_overhead_and_unmeasured_data_never_become_opportunities(self) -> None:
        overhead = _observation(
            "model_calls",
            status="observed_overhead",
            baseline=12,
            samples=8,
        )
        report = PerformanceReviewReport.from_dict(
            _report({"model_calls": overhead})
        )
        self.assertEqual(report.opportunities, ())

    def test_waste_requires_pairing_repetition_and_passing_gates(self) -> None:
        base = _observation(
            "tool_calls",
            status="measured_waste",
            baseline=10,
            candidate=8,
            samples=3,
        )
        mutations = (
            ("candidate_value", None),
            ("sample_count", 2),
            ("quality_gate_passed", False),
            ("security_gate_passed", False),
        )
        for field, value in mutations:
            observation = dict(base)
            observation[field] = value
            with self.subTest(field=field):
                with self.assertRaisesRegex(ValueError, "measured waste"):
                    PerformanceReviewReport.from_dict(
                        _report({"tool_calls": observation})
                    )

    def test_units_and_schema_are_closed(self) -> None:
        observation = _observation("database_queries")
        observation["unit"] = "milliseconds"
        with self.assertRaisesRegex(ValueError, "unit"):
            PerformanceReviewReport.from_dict(
                _report({"database_queries": observation})
            )

        payload = _report()
        payload["opportunities"] = []
        with self.assertRaisesRegex(ValueError, "invalid shape"):
            PerformanceReviewReport.from_dict(payload)

    def test_cli_is_machine_readable_and_rejects_invalid_input(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "review.json"
            path.write_text(json.dumps(_report()), encoding="utf-8")
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                code = main(["validate", str(path)])
            self.assertEqual(code, 0)
            self.assertTrue(json.loads(output.getvalue())["valid"])

            path.write_text("{}", encoding="utf-8")
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                code = main(["validate", str(path)])
            self.assertEqual(code, 2)
            self.assertFalse(json.loads(output.getvalue())["valid"])

    def test_role_and_workflow_are_least_privilege_and_measurement_first(
        self,
    ) -> None:
        payload = json.loads(
            (
                ROOT
                / "examples"
                / "agent-spec"
                / "performance-review-worker.json"
            ).read_text(encoding="utf-8")
        )
        spec = AgentSpec.from_dict(payload)
        self.assertEqual(spec.task_scope, ("performance-review",))
        self.assertEqual(spec.tools, ())
        self.assertEqual(spec.permissions, ())
        self.assertEqual(spec.communication.mode, "none")
        self.assertTrue(spec.model_policy.local_only)
        self.assertFalse(spec.model_policy.allow_fallback)
        self.assertEqual(spec.money_budget, 0)

        source = (
            ROOT / "docs" / "agents" / "performance-review.md"
        ).read_text(encoding="utf-8")
        for category in PERFORMANCE_CATEGORIES:
            self.assertIn(category, source)
        self.assertIn("Only `measured_waste`", source)
        self.assertIn("not an executable worker", source)


if __name__ == "__main__":
    unittest.main()
