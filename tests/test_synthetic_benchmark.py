from __future__ import annotations

import io
import json
import sqlite3
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from acr_runtime.cli import main
from acr_runtime.safe_mode import SafeModeViolation
from acr_runtime.service import AdaptiveRuntime
from acr_runtime.synthetic_benchmark import (
    SyntheticBenchmarkCreate,
    SyntheticBenchmarkError,
    SyntheticBenchmarkReviewCreate,
)


VERSION_HASH = "a" * 64


class SyntheticBenchmarkTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.database = self.root / "acr.db"
        self.runtime = AdaptiveRuntime(self.database)

    def tearDown(self) -> None:
        self.runtime.close()
        self.temporary.cleanup()

    @staticmethod
    def request_payload(**changes) -> dict[str, object]:
        payload: dict[str, object] = {
            "schema_version": 1,
            "name": "Core capability challenge set",
            "generator_ref": "generator:deterministic-template-v1",
            "generator_version_hash": VERSION_HASH,
            "seed": 17,
            "capability_classes": [
                {
                    "id": "context_compilation",
                    "description": "Select focused context under a fixed budget.",
                    "objective_template": (
                        "Compile only the evidence required to diagnose {variant} "
                        "without loading unrelated repository history."
                    ),
                    "variants": [
                        {
                            "id": "exact_error",
                            "value": "an exact dependency error",
                            "difficulty": "basic",
                        },
                        {
                            "id": "conflict",
                            "value": "conflicting scoped memories",
                            "difficulty": "advanced",
                        },
                    ],
                    "evaluation_spec": {
                        "metric": "required_evidence_recall",
                        "minimum_quality_micros": 800000,
                    },
                },
                {
                    "id": "skill_routing",
                    "description": "Choose the smallest useful skill set.",
                    "objective_template": (
                        "Route a bounded skill set for {variant} while rejecting "
                        "untrusted or irrelevant packages."
                    ),
                    "variants": [
                        {
                            "id": "single",
                            "value": "one exact database diagnostic",
                            "difficulty": "intermediate",
                        },
                        {
                            "id": "quarantine",
                            "value": "a task with one quarantined candidate",
                            "difficulty": "advanced",
                        },
                    ],
                    "evaluation_spec": {
                        "metric": "routing_precision",
                        "minimum_quality_micros": 900000,
                    },
                },
            ],
            "evidence": ["design:prompt-121"],
        }
        payload.update(changes)
        return payload

    @staticmethod
    def review_payload(
        suite: dict[str, object], **changes
    ) -> dict[str, object]:
        payload: dict[str, object] = {
            "schema_version": 1,
            "suite_id": suite["id"],
            "suite_hash": suite["suite_hash"],
            "reviewer_ref": "human:operator-miche",
            "assessments": {
                "leakage": {
                    "status": "passed",
                    "rationale": (
                        "Compared case hashes and phrasing against the bounded "
                        "local benchmark inventory; no copied evaluation item found."
                    ),
                    "evidence": ["audit:leakage-review-v1"],
                },
                "triviality": {
                    "status": "passed",
                    "rationale": (
                        "Each class contains distinct constraints and at least "
                        "two difficulty levels with non-identical objectives."
                    ),
                    "evidence": ["audit:triviality-review-v1"],
                },
                "coverage": {
                    "status": "passed",
                    "rationale": (
                        "All declared capability classes and variants are present "
                        "in the immutable suite report."
                    ),
                    "evidence": ["audit:coverage-review-v1"],
                },
            },
            "real_task_evidence": ["task-set:real-evaluation-v1"],
            "evidence": ["review:prompt-121-human-v1"],
        }
        payload.update(changes)
        return payload

    def generate(self) -> dict[str, object]:
        return self.runtime.generate_synthetic_benchmark(
            SyntheticBenchmarkCreate.from_dict(self.request_payload())
        )

    def test_generation_is_deterministic_idempotent_and_synthetic_only(self):
        first = self.generate()
        second = self.generate()

        self.assertEqual(first["id"], second["id"])
        self.assertEqual(first["suite_hash"], second["suite_hash"])
        self.assertEqual(first["case_count"], 4)
        self.assertEqual(first["capability_class_count"], 2)
        self.assertEqual(first["origin"], "synthetic")
        self.assertEqual(first["historical_task_rows_used"], 0)
        self.assertFalse(first["promotion_authority"])
        self.assertTrue(first["review_required"])
        self.assertEqual(
            {case["difficulty"] for case in first["cases"]},
            {"basic", "intermediate", "advanced"},
        )
        self.assertTrue(
            all(case["origin"] == "synthetic" for case in first["cases"])
        )

    def test_review_requires_all_dimensions_and_real_task_gate(self):
        suite = self.generate()
        accepted = self.runtime.review_synthetic_benchmark(
            SyntheticBenchmarkReviewCreate.from_dict(
                self.review_payload(suite)
            )
        )

        self.assertTrue(accepted["accepted_for_synthetic_evaluation"])
        self.assertTrue(accepted["real_task_gate_satisfied"])
        self.assertFalse(accepted["promotion_authority"])
        self.assertFalse(accepted["deployment_authority"])
        self.assertEqual(
            self.runtime.synthetic_benchmarks.report(suite["id"])["review_id"],
            accepted["id"],
        )

    def test_passed_review_without_real_evidence_remains_unaccepted(self):
        suite = self.generate()
        review = self.runtime.review_synthetic_benchmark(
            SyntheticBenchmarkReviewCreate.from_dict(
                self.review_payload(suite, real_task_evidence=[])
            )
        )

        self.assertFalse(review["accepted_for_synthetic_evaluation"])
        self.assertFalse(review["real_task_gate_satisfied"])

    def test_failed_dimension_remains_unaccepted(self):
        suite = self.generate()
        payload = self.review_payload(suite)
        payload["assessments"]["triviality"]["status"] = "failed"
        review = self.runtime.review_synthetic_benchmark(
            SyntheticBenchmarkReviewCreate.from_dict(payload)
        )
        self.assertFalse(review["accepted_for_synthetic_evaluation"])

    def test_review_is_exactly_idempotent_and_cannot_be_replaced(self):
        suite = self.generate()
        request = SyntheticBenchmarkReviewCreate.from_dict(
            self.review_payload(suite)
        )
        first = self.runtime.review_synthetic_benchmark(request)
        second = self.runtime.review_synthetic_benchmark(request)
        self.assertEqual(first["id"], second["id"])

        changed = self.review_payload(suite)
        changed["reviewer_ref"] = "human:different-reviewer"
        with self.assertRaisesRegex(
            SyntheticBenchmarkError, "different review"
        ):
            self.runtime.review_synthetic_benchmark(
                SyntheticBenchmarkReviewCreate.from_dict(changed)
            )

    def test_invalid_generation_inputs_fail_closed(self):
        one_class = self.request_payload(
            capability_classes=self.request_payload()["capability_classes"][:1]
        )
        with self.assertRaisesRegex(
            SyntheticBenchmarkError, "2..16 capability classes"
        ):
            SyntheticBenchmarkCreate.from_dict(one_class)

        bad_template = self.request_payload()
        bad_template["capability_classes"][0]["objective_template"] = (
            "This objective has no deterministic substitution field at all."
        )
        with self.assertRaisesRegex(
            SyntheticBenchmarkError, "exactly one"
        ):
            SyntheticBenchmarkCreate.from_dict(bad_template)

        secret = self.request_payload(name="token ghp_" + "a" * 40)
        with self.assertRaisesRegex(
            SyntheticBenchmarkError, "secret material"
        ):
            SyntheticBenchmarkCreate.from_dict(secret)

    def test_suite_review_hash_mismatch_fails(self):
        suite = self.generate()
        payload = self.review_payload(suite, suite_hash="b" * 64)
        with self.assertRaisesRegex(
            SyntheticBenchmarkError, "does not match"
        ):
            self.runtime.review_synthetic_benchmark(
                SyntheticBenchmarkReviewCreate.from_dict(payload)
            )

    def test_review_cannot_self_approve_as_a_model(self):
        suite = self.generate()
        payload = self.review_payload(
            suite, reviewer_ref="model:automatic-judge"
        )
        with self.assertRaisesRegex(
            SyntheticBenchmarkError, "explicit human reviewer"
        ):
            SyntheticBenchmarkReviewCreate.from_dict(payload)

    def test_tables_are_immutable_and_separate_from_tasks(self):
        suite = self.generate()
        self.assertEqual(
            self.runtime.db.connection.execute(
                "SELECT COUNT(*) FROM tasks"
            ).fetchone()[0],
            0,
        )
        with self.assertRaises(sqlite3.IntegrityError):
            self.runtime.db.connection.execute(
                "UPDATE synthetic_benchmark_suites SET name='changed' WHERE id=?",
                (suite["id"],),
            )
        with self.assertRaises(sqlite3.IntegrityError):
            self.runtime.db.connection.execute(
                "DELETE FROM synthetic_benchmark_cases WHERE suite_id=?",
                (suite["id"],),
            )

    def test_safe_mode_blocks_generation_and_review_but_allows_reports(self):
        suite = self.generate()
        review = SyntheticBenchmarkReviewCreate.from_dict(
            self.review_payload(suite)
        )
        self.runtime.safe_mode.enable(
            actor_id="operator:test",
            reason="Contain synthetic benchmark writes.",
        )

        with self.assertRaises(SafeModeViolation):
            self.generate()
        with self.assertRaises(SafeModeViolation):
            self.runtime.review_synthetic_benchmark(review)
        self.assertEqual(
            self.runtime.synthetic_benchmarks.report(suite["id"])["id"],
            suite["id"],
        )

    def test_cli_generates_reports_and_reviews_without_execution(self):
        request_file = self.root / "synthetic-request.json"
        request_file.write_text(
            json.dumps(self.request_payload()), encoding="utf-8"
        )

        def invoke(*arguments: str) -> dict[str, object]:
            output = io.StringIO()
            with redirect_stdout(output):
                result = main(["--db", str(self.database), *arguments])
            self.assertEqual(result, 0)
            return json.loads(output.getvalue())

        with patch.dict(
            "os.environ",
            {
                "ACR_STATE_DIR": str(self.root / "state"),
                "ACR_SKILLS_DIR": str(self.root / "skills"),
            },
        ):
            generated = invoke(
                "benchmark", "synthetic-generate", str(request_file)
            )
            reported = invoke(
                "benchmark", "synthetic-report", generated["id"]
            )
            review_file = self.root / "synthetic-review.json"
            review_file.write_text(
                json.dumps(self.review_payload(generated)), encoding="utf-8"
            )
            reviewed = invoke(
                "benchmark", "synthetic-review", str(review_file)
            )
            review_report = invoke(
                "benchmark",
                "synthetic-review-report",
                reviewed["id"],
            )

        self.assertEqual(generated["suite_hash"], reported["suite_hash"])
        self.assertTrue(review_report["accepted_for_synthetic_evaluation"])
        self.assertFalse(review_report["promotion_authority"])


if __name__ == "__main__":
    unittest.main()
