from __future__ import annotations

import io
import json
import sqlite3
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from acr_runtime import AdaptiveRuntime
from acr_runtime.active_learning import (
    ActiveLearningRequest,
    VerificationActionKind,
)
from acr_runtime.cli import main
from acr_runtime.reflection import (
    MissingInformation,
    ReflectionRequest,
)
from acr_runtime.safe_mode import SafeModeViolation


class ActiveLearningTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.database = Path(self.directory.name) / "acr.db"
        self.runtime = AdaptiveRuntime(self.database)

    def tearDown(self) -> None:
        self.runtime.close()
        self.directory.cleanup()

    @staticmethod
    def request(**changes: object) -> ActiveLearningRequest:
        payload: dict[str, object] = {
            "scope": "project:alpha",
            "task_class": "repository-verification",
            "uncertainty_key": "database-configuration",
            "action_kind": VerificationActionKind.INSPECT_REPOSITORY,
            "target_ref": "project database configuration",
            "expected_future_uses": 4,
            "impact_micros": 800_000,
            "resolution_probability_micros": 900_000,
            "interruption_cost_micros": 200_000,
            "verification_cost_micros": 300_000,
            "evidence": ("policy:prompt118",),
        }
        payload.update(changes)
        return ActiveLearningRequest(**payload)  # type: ignore[arg-type]

    def observe(
        self,
        key: str = "database-configuration",
        *,
        scope: str = "project:alpha",
    ) -> str:
        task_id = self.runtime.db.create_task(
            objective=f"Resolve {key}",
            scope=scope,
            token_budget=100,
        )
        self.runtime.reflect(
            ReflectionRequest(
                task_id=task_id,
                task_success=True,
                missing_information=(
                    MissingInformation(
                        key,
                        (f"task:{task_id}:missing",),
                    ),
                ),
            )
        )
        return task_id

    def test_repeated_high_value_uncertainty_suggests_non_executing_action(self):
        for _ in range(3):
            self.observe()
        memories_before = self.runtime.db.connection.execute(
            "SELECT COUNT(*) FROM memories"
        ).fetchone()[0]

        result = self.runtime.assess_active_learning(self.request())

        self.assertEqual(result.status, "suggested")
        self.assertEqual(result.distinct_task_count, 3)
        self.assertEqual(result.recurrence_micros, 1_000_000)
        self.assertEqual(result.expected_benefit_micros, 2_880_000)
        self.assertEqual(result.total_cost_micros, 500_000)
        self.assertEqual(result.expected_net_value_micros, 2_380_000)
        action = result.as_dict()["proposed_action"]
        self.assertEqual(action["kind"], "inspect_repository")
        self.assertEqual(action["required_capability"], "filesystem.read")
        self.assertFalse(action["execution_authority"])
        self.assertEqual(
            self.runtime.db.connection.execute(
                "SELECT COUNT(*) FROM memories"
            ).fetchone()[0],
            memories_before,
        )

    def test_insufficient_repetition_or_value_defers_without_action(self):
        self.observe()
        insufficient = self.runtime.assess_active_learning(self.request())
        self.assertEqual(insufficient.status, "deferred")
        self.assertIn("insufficient_occurrences", insufficient.reasons)
        self.assertIsNone(insufficient.as_dict()["proposed_action"])

        self.observe()
        self.observe()
        expensive = self.runtime.assess_active_learning(
            self.request(
                interruption_cost_micros=2_000_000,
                verification_cost_micros=2_000_000,
            )
        )
        self.assertEqual(expensive.status, "deferred")
        self.assertIn(
            "interruption_or_cost_not_justified",
            expensive.reasons,
        )
        self.assertIsNone(expensive.as_dict()["proposed_action"])

    def test_scope_is_exact_and_same_task_repetition_is_not_independent(self):
        task_id = self.observe()
        for _ in range(2):
            self.runtime.reflect(
                ReflectionRequest(
                    task_id=task_id,
                    task_success=True,
                    missing_information=(
                        MissingInformation(
                            " Database-Configuration ",
                            ("repeat:same-task",),
                        ),
                    ),
                )
            )
        for _ in range(3):
            self.observe(scope="project:beta")

        result = self.runtime.assess_active_learning(self.request())

        self.assertEqual(result.occurrence_count, 3)
        self.assertEqual(result.distinct_task_count, 1)
        self.assertEqual(result.total_reflected_task_count, 1)
        self.assertEqual(result.status, "deferred")

    def test_assessment_is_idempotent_immutable_and_cli_reportable(self):
        for _ in range(3):
            self.observe()
        request = self.request()
        first = self.runtime.assess_active_learning(request)
        replay = self.runtime.assess_active_learning(request)
        self.assertEqual(replay.id, first.id)
        with self.assertRaises(sqlite3.IntegrityError):
            self.runtime.db.connection.execute(
                "UPDATE active_learning_runs SET status='deferred' WHERE id=?",
                (first.id,),
            )
        self.runtime.db.connection.rollback()

        request_path = Path(self.directory.name) / "active-request.json"
        request_path.write_text(
            json.dumps(request.as_dict()),
            encoding="utf-8",
        )
        self.runtime.close()
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(
                main([
                    "--db",
                    str(self.database),
                    "learn",
                    "active-assess",
                    str(request_path),
                ]),
                0,
            )
        payload = json.loads(output.getvalue())
        self.assertEqual(payload["id"], first.id)
        report = io.StringIO()
        with redirect_stdout(report):
            self.assertEqual(
                main([
                    "--db",
                    str(self.database),
                    "learn",
                    "active-report",
                    first.id,
                ]),
                0,
            )
        self.assertEqual(json.loads(report.getvalue()), payload)
        self.runtime = AdaptiveRuntime(self.database)

    def test_request_schema_and_safe_mode_fail_closed(self):
        payload = self.request().as_dict()
        self.assertEqual(
            ActiveLearningRequest.from_dict(payload),
            self.request(),
        )
        with self.assertRaises(ValueError):
            ActiveLearningRequest.from_dict({**payload, "unknown": True})
        with self.assertRaises(ValueError):
            ActiveLearningRequest.from_dict(
                {**payload, "impact_micros": True}
            )
        with self.assertRaises(ValueError):
            self.request(
                interruption_cost_micros=0,
                verification_cost_micros=0,
            )
        with self.assertRaises(ValueError):
            ActiveLearningRequest(
                **{
                    **self.request().__dict__,
                    "action_kind": "browse_everywhere",
                }
            )

        for _ in range(3):
            self.observe()
        self.runtime.safe_mode.enable(
            actor_id="operator:test",
            reason="Contain active-learning writes.",
        )
        with self.assertRaises(SafeModeViolation):
            self.runtime.assess_active_learning(self.request())
        with self.assertRaises(SafeModeViolation):
            self.runtime.active_learning.assess(self.request())


if __name__ == "__main__":
    unittest.main()
