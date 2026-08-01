from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from acr_runtime.production_readiness import (
    EVIDENCE_LEVELS,
    READINESS_DIMENSIONS,
    ProductionReadinessReport,
    ReadinessDimension,
    main,
    validate_report,
)


ROOT = Path(__file__).parents[1]


def evidence(level: str, status: str) -> dict[str, object]:
    return {
        "level": level,
        "status": status,
        "reference": f"test:{level}" if status != "unavailable" else None,
        "detail": f"{level} evidence is {status}.",
    }


def dimension(name: str, score: int = 2) -> dict[str, object]:
    return {
        "dimension": name,
        "evidence": [
            evidence(level, "passed" if index < score else "unavailable")
            for index, level in enumerate(EVIDENCE_LEVELS)
        ],
        "deficiencies": (
            [] if score == len(EVIDENCE_LEVELS)
            else ["Higher-level evidence is unavailable."]
        ),
        "recommendation": "Gather the next evidence level.",
    }


def report(*, score: int = 2) -> dict[str, object]:
    return {
        "schema_version": 1,
        "target_profile": "networked_production",
        "commit_sha": "a" * 40,
        "assessed_at": "2026-08-01T21:15:00Z",
        "dimensions": [
            dimension(name, score=score)
            for name in READINESS_DIMENSIONS
        ],
    }


class ProductionReadinessTests(unittest.TestCase):
    def test_scores_are_derived_from_contiguous_evidence(self) -> None:
        parsed = ProductionReadinessReport.from_dict(report(score=2))
        self.assertEqual(parsed.total_score, len(READINESS_DIMENSIONS) * 2)
        self.assertFalse(parsed.production_ready)
        self.assertIn("correctness:score_2", parsed.blockers)

    def test_evidence_cannot_skip_levels_or_inflate_score(self) -> None:
        payload = dimension("correctness", score=1)
        payload["evidence"][2] = evidence("rehearsed", "passed")
        with self.assertRaisesRegex(ValueError, "cannot skip"):
            ReadinessDimension.from_dict(payload)

        payload = dimension("correctness", score=2)
        payload["deficiencies"] = []
        with self.assertRaisesRegex(ValueError, "requires deficiencies"):
            ReadinessDimension.from_dict(payload)

    def test_every_dimension_is_required_in_exact_order(self) -> None:
        payload = report()
        payload["dimensions"] = payload["dimensions"][:-1]
        with self.assertRaisesRegex(ValueError, "every readiness area"):
            ProductionReadinessReport.from_dict(payload)

        payload = report()
        payload["schema_version"] = True
        with self.assertRaisesRegex(ValueError, "schema_version"):
            ProductionReadinessReport.from_dict(payload)

    def test_assessment_timestamp_is_strict_and_timezone_aware(self) -> None:
        for value in ("not-a-time", "2026-08-01T21:15:00"):
            payload = report()
            payload["assessed_at"] = value
            with self.subTest(value=value), self.assertRaises(ValueError):
                ProductionReadinessReport.from_dict(payload)

        payload = report()
        payload["dimensions"][0], payload["dimensions"][1] = (
            payload["dimensions"][1],
            payload["dimensions"][0],
        )
        with self.assertRaisesRegex(ValueError, "every readiness area"):
            ProductionReadinessReport.from_dict(payload)

    def test_only_four_complete_levels_are_production_ready(self) -> None:
        complete = ProductionReadinessReport.from_dict(report(score=4))
        self.assertTrue(complete.production_ready)
        self.assertEqual(complete.blockers, ())
        self.assertEqual(complete.score_percent, 100.0)

        incomplete = report(score=4)
        incomplete["dimensions"][10] = dimension("rate_limiting", score=3)
        parsed = ProductionReadinessReport.from_dict(incomplete)
        self.assertFalse(parsed.production_ready)
        self.assertIn("rate_limiting:score_3", parsed.blockers)

    def test_cli_distinguishes_ready_not_ready_and_invalid(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "readiness.json"
            for payload, expected in ((report(score=4), 0), (report(), 1)):
                path.write_text(json.dumps(payload), encoding="utf-8")
                output = io.StringIO()
                with contextlib.redirect_stdout(output):
                    self.assertEqual(main([str(path)]), expected)
                self.assertTrue(json.loads(output.getvalue())["valid"])

            path.write_text("{}", encoding="utf-8")
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main([str(path)]), 2)

    def test_prompt106_assessment_is_complete_and_not_ready(self) -> None:
        path = ROOT / "docs" / "audits" / "prompt-106-readiness.json"
        current = validate_report(path)
        self.assertFalse(current.production_ready)
        self.assertEqual(
            tuple(item.dimension for item in current.dimensions),
            READINESS_DIMENSIONS,
        )
        scores = {
            item.dimension: item.score for item in current.dimensions
        }
        self.assertEqual(scores["rate_limiting"], 1)
        self.assertEqual(scores["backup"], 3)
        self.assertIn("rate_limiting:score_1", current.blockers)


if __name__ == "__main__":
    unittest.main()
