from __future__ import annotations

import io
import json
import sqlite3
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from acr_runtime.cli import main
from acr_runtime.db import RuntimeDB
from acr_runtime.experiments import (
    ExperimentController,
    ExperimentCreate,
    ExperimentOutcome,
    ExperimentVariant,
)
from acr_runtime.secret_management import SecretBoundaryError


class ExperimentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "acr.db"
        self.db = RuntimeDB(self.path)
        self.experiments = ExperimentController(self.db.connection)

    def tearDown(self) -> None:
        self.db.close()
        self.temp.cleanup()

    def request(self, **changes) -> ExperimentCreate:
        values = {
            "name": "retrieval-budget-test",
            "domain": "context_budget",
            "hypothesis": "A smaller context budget preserves quality.",
            "randomization_unit": "task_id",
            "seed": 42,
            "variants": (
                ExperimentVariant(
                    "control", 5_000, {"token_budget": 4_000}, True
                ),
                ExperimentVariant(
                    "smaller", 5_000, {"token_budget": 2_000}, False
                ),
            ),
            "primary_metric": "quality",
        }
        values.update(changes)
        return ExperimentCreate(**values)

    def test_assignment_is_stable_hashed_and_opt_in_only(self):
        experiment = self.experiments.create(self.request())
        with self.assertRaisesRegex(ValueError, "running"):
            self.experiments.assign(experiment["id"], "task-private-1")
        self.experiments.start(experiment["id"])
        first = self.experiments.assign(
            experiment["id"], "task-private-1"
        )
        second = self.experiments.assign(
            experiment["id"], "task-private-1"
        )
        self.assertEqual(first, second)
        self.assertEqual(len(first["unit_hash"]), 64)
        self.assertFalse(first["production_default_changed"])
        stored = json.dumps([
            dict(row) for row in self.db.connection.execute(
                "SELECT * FROM experiment_assignments"
            ).fetchall()
        ])
        self.assertNotIn("task-private-1", stored)

    def test_closed_design_rejects_bad_allocation_and_secret_config(self):
        with self.assertRaises(ValueError):
            self.request(variants=(
                ExperimentVariant("control", 4_000, {"value": 1}, True),
                ExperimentVariant("test", 4_000, {"value": 2}, False),
            ))
        token = "sk-" + "A1b2C3d4E5f6G7h8I9j0K1"
        with self.assertRaises(SecretBoundaryError):
            ExperimentVariant(
                "unsafe", 5_000, {"api_key": token}, False
            )
        with self.assertRaises(ValueError):
            self.request(domain="unknown")

    def test_outcomes_are_bound_once_and_report_is_descriptive(self):
        experiment = self.experiments.create(self.request())
        self.experiments.start(experiment["id"])
        assignments = [
            self.experiments.assign(experiment["id"], f"task-{index}")
            for index in range(40)
        ]
        for index, assignment in enumerate(assignments):
            self.experiments.record(
                experiment["id"],
                ExperimentOutcome(
                    assignment_id=assignment["id"],
                    quality=0.9 if assignment["variant_id"] == "smaller" else 0.8,
                    tokens=2_000 if assignment["variant_id"] == "smaller" else 4_000,
                    cost=0.0,
                    latency_ms=20 + index,
                    failed=False,
                    evidence=(f"benchmark:case-{index}",),
                ),
            )
        with self.assertRaises(sqlite3.IntegrityError):
            self.experiments.record(
                experiment["id"],
                ExperimentOutcome(
                    assignment_id=assignments[0]["id"],
                    quality=1.0,
                    tokens=1,
                    cost=0.0,
                    latency_ms=1,
                    failed=False,
                    evidence=("duplicate:test",),
                ),
            )
        report = self.experiments.report(experiment["id"])
        self.assertGreater(
            report["variants"]["smaller"]["delta_vs_baseline"]["quality"],
            0,
        )
        self.assertLess(
            report["variants"]["smaller"]["delta_vs_baseline"]["tokens"],
            0,
        )
        self.assertEqual(
            report["interpretation"],
            "descriptive_only_replicate_before_production_decision",
        )
        self.assertFalse(report["production_default_changed"])
        completed = self.experiments.finish(experiment["id"])
        self.assertEqual(completed["status"], "completed")
        self.assertFalse(completed["production_default_changed"])

    def test_cross_experiment_assignment_is_rejected(self):
        first = self.experiments.create(self.request(name="first-test"))
        second = self.experiments.create(self.request(name="second-test"))
        self.experiments.start(first["id"])
        self.experiments.start(second["id"])
        assignment = self.experiments.assign(first["id"], "task-1")
        with self.assertRaisesRegex(ValueError, "does not belong"):
            self.experiments.record(
                second["id"],
                ExperimentOutcome(
                    assignment_id=assignment["id"],
                    quality=0.5,
                    tokens=10,
                    cost=0.0,
                    latency_ms=5,
                    failed=False,
                    evidence=("test:cross-experiment",),
                ),
            )

    def test_cli_create_start_assign_report_and_finish(self):
        request_file = Path(self.temp.name) / "experiment.json"
        request_file.write_text(json.dumps({
            "name": "cli-experiment",
            "domain": "planner_strategy",
            "hypothesis": "Bounded planning improves quality.",
            "randomization_unit": "task_id",
            "seed": 7,
            "variants": [
                {
                    "id": "control", "allocation": 5000,
                    "config": {"planner": "single-step"}, "baseline": True,
                },
                {
                    "id": "treatment", "allocation": 5000,
                    "config": {"planner": "hierarchical"}, "baseline": False,
                },
            ],
            "primary_metric": "quality",
        }), encoding="utf-8")
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(main([
                "--db", str(self.path), "experiments", "create",
                str(request_file),
            ]), 0)
        experiment_id = json.loads(output.getvalue())["id"]
        with redirect_stdout(io.StringIO()):
            self.assertEqual(main([
                "--db", str(self.path), "experiments", "start",
                experiment_id,
            ]), 0)
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(main([
                "--db", str(self.path), "experiments", "assign",
                experiment_id, "task-cli",
            ]), 0)
        self.assertIn(
            json.loads(output.getvalue())["variant_id"],
            {"control", "treatment"},
        )
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(main([
                "--db", str(self.path), "experiments", "report",
                experiment_id,
            ]), 0)
        self.assertFalse(
            json.loads(output.getvalue())["production_default_changed"]
        )


if __name__ == "__main__":
    unittest.main()
