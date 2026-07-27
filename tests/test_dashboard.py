from __future__ import annotations

import json
import tempfile
import unittest
import uuid
from pathlib import Path

from acr_runtime.dashboard import DashboardReader, SERIES
from acr_runtime.db import RuntimeDB, utc_now


SECRET = "NEVER-EXPOSE-DASHBOARD-SECRET"


class DashboardReaderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.db = RuntimeDB(Path(self.temp.name) / "dashboard.db")
        self.reader = DashboardReader(self.db)

    def tearDown(self) -> None:
        self.db.close()
        self.temp.cleanup()

    def seed(self) -> tuple[str, str]:
        task_id = self.db.create_task(
            objective=f"private objective {SECRET}",
            scope="alpha",
            token_budget=1_000,
        )
        memory_id = self.db.add_memory(
            kind="semantic",
            content=f"private memory {SECRET}",
            scope="alpha",
            evidence=("test:dashboard",),
        )
        skill_id = self.db.add_skill(
            name="safe-skill",
            version="1.0.0",
            description=f"private description {SECRET}",
            instructions=f"private instructions {SECRET}",
            status="active",
        )
        now = utc_now()
        with self.db.connection:
            self.db.connection.execute(
                """
                UPDATE tasks SET status='succeeded', selected_tokens=40,
                    critic_score=.9, duration_ms=100, completed_at=?
                WHERE id=?
                """,
                (now, task_id),
            )
            self.db.connection.execute(
                """
                UPDATE memories SET access_count=4, successful_uses=3,
                    failed_uses=1 WHERE id=?
                """,
                (memory_id,),
            )
            self.db.connection.execute(
                """
                INSERT INTO context_uses(
                    task_id, source_type, source_id, tokens, utility, roi,
                    useful, compression_strategy, original_tokens,
                    exact_preserved, content_origin
                ) VALUES (?, 'skill', ?, 20, .8, .04, 0, 'none', 20, 1,
                          'skill_instruction')
                """,
                (task_id, skill_id),
            )
            self.db.connection.execute(
                """
                INSERT INTO context_attributions(
                    id, task_id, source_type, source_id, role, outcome,
                    impact_score, confidence, approximate_roi, evidence_json,
                    created_at
                ) VALUES (?, ?, 'skill', ?, 'support', 'contributed',
                          .8, .9, .04, ?, ?)
                """,
                (
                    str(uuid.uuid4()), task_id, skill_id,
                    json.dumps([f"private evidence {SECRET}"]), now,
                ),
            )
            self.db.connection.execute(
                """
                INSERT INTO model_profiles(
                    id, provider, model, context_capacity, supports_tools,
                    input_cost_per_million, output_cost_per_million,
                    active, local, created_at
                ) VALUES ('test:model', 'test', 'model', 8000, 0, 1, 2, 1, 0, ?)
                """,
                (now,),
            )
            self.db.connection.execute(
                """
                INSERT INTO telemetry_events(
                    id, category, event_type, task_id, provider, model,
                    input_tokens, output_tokens, estimated_cost, latency_ms,
                    payload_json, created_at
                ) VALUES (?, 'model', 'model.chat', ?, 'test', 'model',
                          100, 20, .00014, 50, ?, ?)
                """,
                (
                    str(uuid.uuid4()), task_id,
                    json.dumps({"private": SECRET}), now,
                ),
            )
            self.db.connection.execute(
                """
                INSERT INTO agent_specs(
                    id, role, objective, task_scope_json, memory_scope_json,
                    tools_json, skills_json, permissions_json, spec_json,
                    resolved_skills_json, content_hash, status, created_at
                ) VALUES ('agent-one', 'worker', ?, '["general"]', '["alpha"]',
                          '[]', '[]', '[]', ?, '[]', ?, 'defined', ?)
                """,
                (
                    f"private agent objective {SECRET}",
                    json.dumps({"objective": SECRET}), "a" * 64, now,
                ),
            )
            self.db.connection.execute(
                """
                INSERT INTO tool_definitions(
                    name, description, input_schema_json, output_schema_json,
                    permissions_json, cost, latency_estimate_ms, side_effect,
                    network_access, filesystem_access,
                    credential_requirements_json, definition_hash, created_at
                ) VALUES ('safe-tool', ?, ?, '{}', '[]', 0, 5, 'READ_ONLY',
                          0, 'NONE', ?, ?, ?)
                """,
                (
                    f"private tool {SECRET}",
                    json.dumps({"secret": SECRET}),
                    json.dumps([f"secret-ref:{SECRET}"]),
                    "b" * 64, now,
                ),
            )
        return task_id, skill_id

    def test_seeded_metrics_are_accurate_and_explicit(self):
        task_id, _ = self.seed()
        overview = self.reader.overview()
        self.assertEqual(overview["metrics"]["model_tokens"]["value"], 120)
        self.assertEqual(overview["metrics"]["success_rate"]["value"], 1.0)
        self.assertEqual(self.reader.tasks()["items"][0]["id"], task_id)
        self.assertEqual(self.reader.memory()["items"][0]["uses"], 4)
        context = self.reader.context()
        self.assertEqual(context["metrics"]["selected_tokens"]["value"], 20)
        self.assertEqual(context["metrics"]["wasted_tokens"]["value"], 20)
        costs = self.reader.costs()
        self.assertEqual(costs["items"][0]["cost_status"], "available")
        self.assertAlmostEqual(costs["items"][0]["estimated_cost"], 0.00014)
        tokens = self.reader.series("tokens_per_day")
        self.assertEqual(tokens["points"][0]["value"], 120)
        roi = self.reader.series("skill_roi")
        self.assertAlmostEqual(roi["points"][0]["value"], 0.04)
        usefulness = self.reader.series("memory_usefulness")
        self.assertAlmostEqual(usefulness["points"][0]["value"], 0.75)

    def test_every_section_is_bounded_and_never_exposes_content_or_evidence(self):
        self.seed()
        payloads = {
            "overview": self.reader.overview(),
            "tasks": self.reader.tasks(),
            "memory": self.reader.memory(),
            "skills": self.reader.skills(),
            "agents": self.reader.agents(),
            "models": self.reader.models(),
            "tools": self.reader.tools(),
            "context": self.reader.context(),
            "costs": self.reader.costs(),
            "benchmarks": self.reader.benchmarks(),
            "security": self.reader.security(),
            **{f"series:{name}": self.reader.series(name) for name in SERIES},
        }
        encoded = json.dumps(payloads, sort_keys=True)
        self.assertNotIn(SECRET, encoded)
        for forbidden in (
            "objective", "content", "instructions", "payload_json",
            "evidence_json", "package_path", "credential_requirements_json",
            "spec_json", "input_schema_json",
        ):
            self.assertNotIn(f'"{forbidden}"', encoded)
        with self.assertRaises(ValueError):
            self.reader.tasks(limit=101)
        with self.assertRaises(ValueError):
            self.reader.series("invented_metric")

    def test_empty_and_unavailable_states_are_not_zero_fabrications(self):
        self.assertEqual(self.reader.tasks()["status"], "empty")
        self.assertEqual(self.reader.memory()["status"], "empty")
        self.assertEqual(self.reader.series("tokens_per_day")["status"], "empty")
        self.assertEqual(
            self.reader.benchmarks()["memory"]["status"], "unavailable"
        )
        task_id = self.db.create_task(
            objective="unpriced call", scope="alpha", token_budget=10
        )
        with self.db.connection:
            self.db.connection.execute(
                """
                INSERT INTO telemetry_events(
                    id, category, event_type, task_id, provider, model,
                    input_tokens, output_tokens, estimated_cost, latency_ms,
                    payload_json, created_at
                ) VALUES (?, 'model', 'model.chat', ?, 'unknown', 'unknown',
                          5, 1, 0, 1, '{}', ?)
                """,
                (str(uuid.uuid4()), task_id, utc_now()),
            )
        self.assertEqual(self.reader.costs()["items"][0]["cost_status"], "unavailable")
        self.assertIsNone(self.reader.costs()["items"][0]["estimated_cost"])
        self.assertEqual(
            self.reader.series("cost_per_task")["status"], "unavailable"
        )

    def test_accepts_runtime_shape_and_cursor_round_trip(self):
        class RuntimeShape:
            def __init__(self, db):
                self.db = db

        for index in range(3):
            self.db.create_task(
                objective=f"task {index}", scope="alpha", token_budget=10
            )
        reader = DashboardReader(RuntimeShape(self.db))
        first = reader.tasks(limit=2)
        second = reader.tasks(limit=2, cursor=first["next_cursor"])
        self.assertEqual(first["count"], 2)
        self.assertEqual(second["count"], 1)
        self.assertFalse(
            {item["id"] for item in first["items"]}
            & {item["id"] for item in second["items"]}
        )


if __name__ == "__main__":
    unittest.main()
