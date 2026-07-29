from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from acr_runtime.agent_spec import AgentSpec
from acr_runtime.architecture_review import (
    ARCHITECTURE_DIMENSIONS,
    ArchitectureReviewReport,
    main,
)


ROOT = Path(__file__).parents[1]


def _dimension(
    dimension: str,
    *,
    rating: str = "sound",
    severity: str = "none",
    evidence_status: str = "verified",
) -> dict[str, object]:
    concern = rating == "concern"
    return {
        "dimension": dimension,
        "rating": rating,
        "severity": severity,
        "evidence_status": evidence_status,
        "evidence": ["Bounded source and test evidence."],
        "impact_path": (
            ["The boundary leaks a concrete dependency.", "Replacement fails."]
            if concern
            else []
        ),
        "recommendation": "Keep or repair the smallest explicit boundary.",
    }


def _report(
    replacements: dict[str, dict[str, object]] | None = None,
    *,
    abstractions: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "change_ref": "git:abcdef0123456789",
        "dimensions": [
            replacements.get(item, _dimension(item))
            if replacements
            else _dimension(item)
            for item in ARCHITECTURE_DIMENSIONS
        ],
        "abstractions": abstractions or [],
    }


def _abstraction(
    *,
    verdict: str,
    evidence_status: str,
) -> dict[str, object]:
    return {
        "id": "provider.factory.layer",
        "verdict": verdict,
        "evidence_status": evidence_status,
        "purpose": "Select the only available provider implementation.",
        "use_evidence": (
            ["Two governed providers currently use the boundary."]
            if verdict == "justified"
            else []
        ),
        "complexity_cost": ["Adds a wrapper and configuration branch."],
        "simplification_path": (
            ["Call the existing provider port.", "Delete the unused factory."]
            if verdict == "needless"
            else []
        ),
        "recommendation": "Keep evidence-backed seams and remove needless ones.",
    }


class ArchitectureReviewAgentTests(unittest.TestCase):
    def test_report_requires_all_dimensions_in_order(self) -> None:
        report = ArchitectureReviewReport.from_dict(_report())
        self.assertEqual(report.verdict, "pass")
        payload = _report()
        payload["dimensions"] = payload["dimensions"][:-1]
        with self.assertRaisesRegex(ValueError, "every architecture dimension"):
            ArchitectureReviewReport.from_dict(payload)

    def test_only_verified_high_or_critical_concerns_reject(self) -> None:
        verified = ArchitectureReviewReport.from_dict(
            _report(
                {
                    "coupling": _dimension(
                        "coupling", rating="concern", severity="high"
                    )
                }
            )
        )
        self.assertEqual(verified.verdict, "reject")
        self.assertEqual(verified.rejection_reasons, ("dimension:coupling",))

        supported = ArchitectureReviewReport.from_dict(
            _report(
                {
                    "coupling": _dimension(
                        "coupling",
                        rating="concern",
                        severity="critical",
                        evidence_status="supported",
                    )
                }
            )
        )
        self.assertEqual(supported.verdict, "pass")

    def test_verified_needless_abstraction_rejects(self) -> None:
        report = ArchitectureReviewReport.from_dict(
            _report(
                abstractions=[
                    _abstraction(verdict="needless", evidence_status="verified")
                ]
            )
        )
        self.assertEqual(report.verdict, "reject")
        self.assertEqual(
            report.rejection_reasons,
            ("abstraction:provider.factory.layer",),
        )

    def test_needless_abstraction_requires_cost_and_simplification(self) -> None:
        for field, value in (
            ("evidence_status", "supported"),
            ("complexity_cost", []),
            ("simplification_path", ["Delete it."]),
        ):
            abstraction = _abstraction(
                verdict="needless", evidence_status="verified"
            )
            abstraction[field] = value
            with self.subTest(field=field):
                with self.assertRaisesRegex(ValueError, "needless abstraction"):
                    ArchitectureReviewReport.from_dict(
                        _report(abstractions=[abstraction])
                    )

    def test_schema_and_cli_are_closed_and_machine_readable(self) -> None:
        payload = _report()
        payload["verdict"] = "pass"
        with self.assertRaisesRegex(ValueError, "invalid shape"):
            ArchitectureReviewReport.from_dict(payload)

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "review.json"
            path.write_text(json.dumps(_report()), encoding="utf-8")
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                code = main(["validate", str(path)])
            self.assertEqual(code, 0)
            self.assertTrue(json.loads(output.getvalue())["valid"])

    def test_role_and_workflow_are_complete_and_least_privilege(self) -> None:
        payload = json.loads(
            (
                ROOT / "examples" / "agent-spec" / "architecture-review-worker.json"
            ).read_text(encoding="utf-8")
        )
        spec = AgentSpec.from_dict(payload)
        self.assertEqual(spec.task_scope, ("architecture-review",))
        self.assertEqual(spec.tools, ())
        self.assertEqual(spec.permissions, ())
        self.assertEqual(spec.communication.mode, "none")
        self.assertTrue(spec.model_policy.local_only)
        self.assertFalse(spec.model_policy.allow_fallback)
        self.assertEqual(spec.money_budget, 0)

        source = (
            ROOT / "docs" / "agents" / "architecture-review.md"
        ).read_text(encoding="utf-8")
        for dimension in ARCHITECTURE_DIMENSIONS:
            self.assertIn(dimension, source)
        self.assertIn("not an executable worker", source)
        self.assertIn("needless abstraction", source)


if __name__ == "__main__":
    unittest.main()
