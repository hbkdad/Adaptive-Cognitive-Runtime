from __future__ import annotations

import io
import json
import sqlite3
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from acr_runtime.cli import main
from acr_runtime.development_prioritizer import (
    DevelopmentPriorityError,
    DevelopmentPriorityRequest,
    WORK_KINDS,
)
from acr_runtime.safe_mode import SafeModeViolation
from acr_runtime.service import AdaptiveRuntime


class DevelopmentPrioritizerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.database = self.root / "acr.db"
        self.runtime = AdaptiveRuntime(self.database)

    def tearDown(self) -> None:
        self.runtime.close()
        self.temporary.cleanup()

    @staticmethod
    def candidate(
        identifier: str,
        kind: str,
        *,
        value: int = 80,
        confidence: int = 9000,
        frequency: int = 4,
        effort: int = 4,
        risk: int = 2,
    ) -> dict[str, object]:
        return {
            "id": identifier,
            "kind": kind,
            "title": f"Resolve {identifier}",
            "source_refs": [f"work:{identifier}"],
            "expected_value_points": value,
            "confidence_bps": confidence,
            "frequency_count": frequency,
            "effort_points": effort,
            "delivery_risk_points": risk,
            "estimate_evidence": [f"estimate:{identifier}"],
        }

    @classmethod
    def payload(cls, **changes) -> dict[str, object]:
        payload = {
            "schema_version": 1,
            "scope": "project:runtime",
            "inventory_ref": "inventory:prompt123-fixture-v1",
            "inventory_claim": "complete",
            "candidates": [
                cls.candidate("bug-login", "bug", value=90),
                cls.candidate(
                    "feature-export",
                    "feature_request",
                    value=70,
                    effort=8,
                ),
            ],
        }
        payload.update(changes)
        return payload

    def test_formula_ranks_value_confidence_frequency_over_effort_and_risk(self):
        report = self.runtime.prioritize_development(
            DevelopmentPriorityRequest.from_dict(self.payload())
        )
        self.assertEqual(
            [item["id"] for item in report["ranked_work"]],
            ["bug-login", "feature-export"],
        )
        self.assertEqual(
            report["ranked_work"][0]["priority_micros"],
            40_500_000,
        )
        self.assertTrue(report["advisory_only"])
        self.assertFalse(report["implementation_authority"])

    def test_all_six_work_kinds_are_supported_with_visible_reasoning(self):
        candidates = [
            self.candidate(f"work-{index}", kind)
            for index, kind in enumerate(WORK_KINDS, start=1)
        ]
        report = self.runtime.prioritize_development(
            DevelopmentPriorityRequest.from_dict(
                self.payload(candidates=candidates)
            )
        )
        self.assertEqual(
            {item["kind"] for item in report["ranked_work"]},
            set(WORK_KINDS),
        )
        self.assertTrue(
            all("floor(" in item["reasoning"] for item in report["ranked_work"])
        )

    def test_fixed_point_ties_are_stable_and_exact_requests_are_idempotent(self):
        candidates = [
            self.candidate("work-z", "bug"),
            self.candidate("work-a", "technical_debt"),
        ]
        request = DevelopmentPriorityRequest.from_dict(
            self.payload(candidates=candidates)
        )
        first = self.runtime.prioritize_development(request)
        second = self.runtime.prioritize_development(request)
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(
            [item["id"] for item in first["ranked_work"]],
            ["work-a", "work-z"],
        )

    def test_partial_inventory_is_explicit_and_never_claimed_complete(self):
        report = self.runtime.prioritize_development(
            DevelopmentPriorityRequest.from_dict(
                self.payload(inventory_claim="partial")
            )
        )
        self.assertEqual(report["completeness"], "partial_inventory")
        self.assertFalse(report["automatic_action_performed"])

    def test_closed_schema_bounds_and_secret_scanning_fail_closed(self):
        unknown = self.payload()
        unknown["unexpected"] = True
        with self.assertRaisesRegex(DevelopmentPriorityError, "exactly"):
            DevelopmentPriorityRequest.from_dict(unknown)
        empty = self.payload(candidates=[])
        with self.assertRaisesRegex(DevelopmentPriorityError, "1..256"):
            DevelopmentPriorityRequest.from_dict(empty)
        secret = self.payload()
        secret["candidates"][0]["title"] = "Bearer " + "a" * 40
        with self.assertRaisesRegex(
            DevelopmentPriorityError, "secret material"
        ):
            DevelopmentPriorityRequest.from_dict(secret)

    def test_zero_effort_or_risk_and_duplicate_ids_are_rejected(self):
        for field in ("effort_points", "delivery_risk_points"):
            candidate = self.candidate("invalid-work", "bug")
            candidate[field] = 0
            with self.subTest(field=field), self.assertRaisesRegex(
                DevelopmentPriorityError, field
            ):
                DevelopmentPriorityRequest.from_dict(
                    self.payload(candidates=[candidate])
                )
        duplicate = self.candidate("same-work", "bug")
        with self.assertRaisesRegex(DevelopmentPriorityError, "unique"):
            DevelopmentPriorityRequest.from_dict(
                self.payload(candidates=[duplicate, duplicate])
            )

    def test_rows_are_immutable_and_safe_mode_blocks_new_rankings(self):
        report = self.runtime.prioritize_development(
            DevelopmentPriorityRequest.from_dict(self.payload())
        )
        with self.assertRaises(sqlite3.IntegrityError):
            self.runtime.db.connection.execute(
                "UPDATE development_priority_runs SET scope='changed' WHERE id=?",
                (report["id"],),
            )
        self.runtime.safe_mode.enable(
            actor_id="operator:test",
            reason="Contain development prioritization writes.",
        )
        with self.assertRaises(SafeModeViolation):
            self.runtime.prioritize_development(
                DevelopmentPriorityRequest.from_dict(
                    self.payload(inventory_ref="inventory:new-fixture")
                )
            )
        self.assertEqual(
            self.runtime.development_prioritizer.report(report["id"])["id"],
            report["id"],
        )

    def test_cli_creates_and_reports_the_same_advisory_ranking(self):
        request_file = self.root / "priority.json"
        request_file.write_text(json.dumps(self.payload()), encoding="utf-8")
        self.runtime.close()

        def invoke(*arguments: str) -> dict[str, object]:
            output = io.StringIO()
            with redirect_stdout(output):
                result = main(["--db", str(self.database), *arguments])
            self.assertEqual(result, 0)
            return json.loads(output.getvalue())

        created = invoke("prioritize", "create", str(request_file))
        reported = invoke("prioritize", "report", created["id"])
        self.assertEqual(created["request_hash"], reported["request_hash"])
        self.assertFalse(reported["implementation_authority"])
        self.runtime = AdaptiveRuntime(self.database)


if __name__ == "__main__":
    unittest.main()
