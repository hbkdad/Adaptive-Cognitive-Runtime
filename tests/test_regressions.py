from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from acr_runtime.cli import main
from acr_runtime.db import RuntimeDB
from acr_runtime.regressions import (
    ChangeCandidate,
    MetricSummary,
    RegressionDetector,
    RegressionRequest,
)
from acr_runtime.secret_management import SecretBoundaryError


class RegressionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "acr.db"
        self.db = RuntimeDB(self.path)
        self.detector = RegressionDetector(self.db.connection)

    def tearDown(self) -> None:
        self.db.close()
        self.temp.cleanup()

    @staticmethod
    def metrics(**candidate_values: float) -> tuple[MetricSummary, ...]:
        baseline = {
            "token_consumption": 1000.0,
            "quality": 0.90,
            "latency": 200.0,
            "model_escalation": 0.10,
            "memory_retrieval": 0.85,
            "skill_failure": 0.05,
        }
        return tuple(
            MetricSummary(
                name=name,
                baseline_value=value,
                baseline_samples=100,
                candidate_value=candidate_values.get(name, value),
                candidate_samples=100,
                baseline_stddev=0.01 if value <= 1 else 10.0,
            )
            for name, value in baseline.items()
        )

    def request(self, **changes) -> RegressionRequest:
        values = {
            "scope": "project-a",
            "task_class": "sqlite-diagnostics",
            "baseline_start": "2026-07-01T00:00:00Z",
            "baseline_end": "2026-07-08T00:00:00Z",
            "candidate_start": "2026-07-09T00:00:00Z",
            "candidate_end": "2026-07-16T00:00:00Z",
            "metrics": self.metrics(),
            "changes": (),
        }
        values.update(changes)
        return RegressionRequest(**values)

    @staticmethod
    def change(**changes) -> ChangeCandidate:
        values = {
            "id": "router-v2",
            "domain": "model_router",
            "changed_at": "2026-07-08T12:00:00Z",
            "before_ref": "git:router-v1",
            "after_ref": "git:router-v2",
            "rollback_ref": "git:router-v1",
            "affected_metrics": (
                "token_consumption", "quality", "latency", "model_escalation"
            ),
            "evidence": ("deployment:router-v2",),
        }
        values.update(changes)
        return ChangeCandidate(**values)

    def test_all_six_required_regressions_alert_in_correct_direction(self):
        report = self.detector.analyze(self.request(metrics=self.metrics(
            token_consumption=1300,
            quality=0.80,
            latency=300,
            model_escalation=0.20,
            memory_retrieval=0.70,
            skill_failure=0.15,
        )))
        self.assertEqual(report["alert_count"], 6)
        self.assertEqual(
            {row["metric"] for row in report["alerts"]},
            {
                "token_consumption", "quality", "latency",
                "model_escalation", "memory_retrieval", "skill_failure",
            },
        )
        self.assertFalse(report["automatic_rollback_performed"])

    def test_matching_change_gets_hypothesis_and_nonexecuting_recommendation(self):
        report = self.detector.analyze(self.request(
            metrics=self.metrics(latency=300, model_escalation=0.20),
            changes=(self.change(),),
        ))
        self.assertTrue(all(
            alert["likely_change_id"] == "router-v2"
            for alert in report["alerts"]
        ))
        self.assertEqual(len(report["rollback_recommendations"]), 1)
        recommendation = report["rollback_recommendations"][0]
        self.assertEqual(recommendation["rollback_ref"], "git:router-v1")
        self.assertFalse(recommendation["automatic_action_performed"])
        self.assertEqual(
            self.db.connection.execute(
                "SELECT COUNT(*) FROM model_profiles"
            ).fetchone()[0],
            0,
        )

    def test_unmatched_or_ambiguous_change_is_not_claimed(self):
        unrelated = self.change(
            id="skill-v2",
            domain="skill_version",
            affected_metrics=("skill_failure",),
        )
        same_time = self.change(id="router-v3", after_ref="git:router-v3")
        report = self.detector.analyze(self.request(
            metrics=self.metrics(latency=300),
            changes=(self.change(), same_time, unrelated),
        ))
        self.assertIsNone(report["alerts"][0]["likely_change_id"])
        self.assertIn("ambiguous", report["alerts"][0]["attribution"])
        self.assertEqual(report["rollback_recommendations"], [])

    def test_insufficient_samples_and_small_shifts_do_not_alert(self):
        metrics = list(self.metrics(latency=245))
        latency = next(x for x in metrics if x.name == "latency")
        metrics[metrics.index(latency)] = MetricSummary(
            name="latency", baseline_value=200, baseline_samples=29,
            candidate_value=400, candidate_samples=29, baseline_stddev=10,
        )
        report = self.detector.analyze(self.request(metrics=tuple(metrics)))
        self.assertEqual(report["alert_count"], 0)
        self.assertEqual(
            next(x for x in report["metrics"] if x["metric"] == "latency")[
                "status"
            ],
            "insufficient_data",
        )

    def test_closed_request_rejects_missing_metric_bad_windows_and_secret(self):
        with self.assertRaisesRegex(ValueError, "Exactly one"):
            self.request(metrics=self.metrics()[:-1])
        with self.assertRaisesRegex(ValueError, "ordered"):
            self.request(candidate_start="2026-07-07T00:00:00Z")
        token = "sk-" + "A1b2C3d4E5f6G7h8I9j0K1"
        with self.assertRaises(SecretBoundaryError):
            self.change(evidence=(token,))

    def test_cli_analyze_and_report(self):
        request_file = Path(self.temp.name) / "regression.json"
        request = self.request(metrics=self.metrics(quality=0.80))
        request_file.write_text(json.dumps({
            "scope": request.scope,
            "task_class": request.task_class,
            "baseline_start": request.baseline_start,
            "baseline_end": request.baseline_end,
            "candidate_start": request.candidate_start,
            "candidate_end": request.candidate_end,
            "metrics": [
                {
                    "name": item.name,
                    "baseline_value": item.baseline_value,
                    "baseline_samples": item.baseline_samples,
                    "candidate_value": item.candidate_value,
                    "candidate_samples": item.candidate_samples,
                    "baseline_stddev": item.baseline_stddev,
                }
                for item in request.metrics
            ],
            "changes": [],
        }), encoding="utf-8")
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(main([
                "--db", str(self.path), "regressions", "analyze",
                str(request_file),
            ]), 0)
        run_id = json.loads(output.getvalue())["run"]["id"]
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(main([
                "--db", str(self.path), "regressions", "report", run_id,
            ]), 0)
        self.assertEqual(json.loads(output.getvalue())["alert_count"], 1)


if __name__ == "__main__":
    unittest.main()
