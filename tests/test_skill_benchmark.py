from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from acr_runtime.cli import main
from acr_runtime.db import RuntimeDB
from acr_runtime.secret_management import SecretBoundaryError
from acr_runtime.skill_benchmark import (
    SkillBenchmarkController,
    SkillBenchmarkRequest,
    SkillTrial,
)


class SkillBenchmarkTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "acr.db"
        self.db = RuntimeDB(self.path)
        self.controller = SkillBenchmarkController(self.db.connection)

    def tearDown(self) -> None:
        self.db.close()
        self.temp.cleanup()

    @staticmethod
    def trial(
        case_id: str,
        arm: str,
        *,
        quality: float,
        tokens: int,
        latency_ms: int,
        cost: float,
        failed: bool = False,
    ) -> SkillTrial:
        return SkillTrial(
            case_id=case_id,
            task_class="database-diagnostics",
            arm=arm,
            quality=quality,
            tokens=tokens,
            latency_ms=latency_ms,
            cost=cost,
            failed=failed,
            evidence=(f"test:{case_id}:{arm}",),
        )

    def request(
        self,
        *,
        existing: tuple[float, int, int, float] = (0.8, 150, 130, 0.015),
        candidate: tuple[float, int, int, float] = (0.9, 90, 90, 0.009),
        cases: int = 5,
    ) -> SkillBenchmarkRequest:
        trials = []
        for index in range(cases):
            case_id = f"case-{index}"
            trials.extend((
                self.trial(
                    case_id, "without_skill", quality=0.8, tokens=100,
                    latency_ms=100, cost=0.01,
                ),
                self.trial(
                    case_id, "existing_skill", quality=existing[0],
                    tokens=existing[1], latency_ms=existing[2],
                    cost=existing[3],
                ),
                self.trial(
                    case_id, "candidate_skill", quality=candidate[0],
                    tokens=candidate[1], latency_ms=candidate[2],
                    cost=candidate[3],
                ),
            ))
        return SkillBenchmarkRequest(
            skill_name="sqlite-diagnostics",
            existing_ref="sqlite-diagnostics@1.0.0",
            candidate_ref="sqlite-diagnostics@1.1.0",
            trials=tuple(trials),
        )

    def test_measures_all_five_metrics_for_three_paired_arms(self):
        report = self.controller.analyze(self.request())
        self.assertEqual(set(report["summary"]), {
            "without_skill", "existing_skill", "candidate_skill"
        })
        candidate = report["summary"]["candidate_skill"]
        self.assertEqual(candidate["quality"], 0.9)
        self.assertEqual(candidate["tokens"], 450)
        self.assertEqual(candidate["latency_ms"], 450)
        self.assertAlmostEqual(candidate["cost"], 0.045)
        self.assertEqual(candidate["failure_rate"], 0)
        self.assertEqual(len(report["trials"]), 15)

    def test_overhead_without_value_recommends_deprecation_only(self):
        report = self.controller.analyze(self.request())
        by_ref = {
            item["target_ref"]: item for item in report["recommendations"]
        }
        self.assertEqual(
            by_ref["sqlite-diagnostics@1.0.0"]["action"], "deprecate"
        )
        self.assertEqual(
            by_ref["sqlite-diagnostics@1.1.0"]["action"],
            "consider_candidate",
        )
        self.assertFalse(report["automatic_lifecycle_change_performed"])
        self.assertEqual(
            self.db.connection.execute(
                "SELECT COUNT(*) FROM skills"
            ).fetchone()[0],
            0,
        )

    def test_value_earning_incumbent_is_kept_and_regressed_candidate_rejected(self):
        report = self.controller.analyze(self.request(
            existing=(0.9, 120, 105, 0.011),
            candidate=(0.85, 110, 100, 0.010),
        ))
        actions = {
            item["target_ref"]: item["action"]
            for item in report["recommendations"]
        }
        self.assertEqual(actions["sqlite-diagnostics@1.0.0"], "keep")
        self.assertEqual(
            actions["sqlite-diagnostics@1.1.0"], "reject_candidate"
        )

    def test_small_sample_is_insufficient_not_deprecation(self):
        report = self.controller.analyze(self.request(cases=2))
        self.assertEqual(
            {item["action"] for item in report["recommendations"]},
            {"insufficient_evidence"},
        )

    def test_closed_pairing_and_secret_evidence_fail_closed(self):
        request = self.request()
        with self.assertRaisesRegex(ValueError, "all three arms"):
            SkillBenchmarkRequest(
                skill_name=request.skill_name,
                existing_ref=request.existing_ref,
                candidate_ref=request.candidate_ref,
                trials=request.trials[:-1],
            )
        token = "sk-" + "A1b2C3d4E5f6G7h8I9j0K1"
        with self.assertRaises(SecretBoundaryError):
            SkillTrial(
                case_id="secret", task_class="test", arm="without_skill",
                quality=1, tokens=1, latency_ms=1, cost=0, failed=False,
                evidence=(token,),
            )

    def test_cli_analyze_and_report(self):
        request = self.request()
        source = Path(self.temp.name) / "skill-benchmark.json"
        source.write_text(json.dumps({
            "skill_name": request.skill_name,
            "existing_ref": request.existing_ref,
            "candidate_ref": request.candidate_ref,
            "trials": [as_trial(item) for item in request.trials],
        }), encoding="utf-8")
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(main([
                "--db", str(self.path), "benchmark", "skill", str(source),
            ]), 0)
        run_id = json.loads(output.getvalue())["run"]["id"]
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(main([
                "--db", str(self.path), "benchmark", "skill-report", run_id,
            ]), 0)
        self.assertEqual(
            json.loads(output.getvalue())["run"]["status"], "completed"
        )


def as_trial(item: SkillTrial) -> dict[str, object]:
    return {
        "case_id": item.case_id,
        "task_class": item.task_class,
        "arm": item.arm,
        "quality": item.quality,
        "tokens": item.tokens,
        "latency_ms": item.latency_ms,
        "cost": item.cost,
        "failed": item.failed,
        "evidence": list(item.evidence),
    }


if __name__ == "__main__":
    unittest.main()
