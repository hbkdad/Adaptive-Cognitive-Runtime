from __future__ import annotations

import json
import tempfile
import unittest
import uuid
from pathlib import Path

from acr_runtime.db import RuntimeDB, utc_now
from acr_runtime.learning_dashboard import LearningDashboardReader


SECRET = "NEVER-EXPOSE-LEARNING-DASHBOARD"


class LearningDashboardReaderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = RuntimeDB(Path(self.temp.name) / "learning-dashboard.db")
        self.reader = LearningDashboardReader(self.db)

    def tearDown(self):
        self.db.close()
        self.temp.cleanup()

    def _promotion(self, item_id: str, created_at: str):
        run_id = str(uuid.uuid4())
        self.db.connection.execute(
            """
            INSERT INTO memory_consolidation_runs(
                id, status, scope, config_json, summary_json,
                created_at, applied_at
            ) VALUES (?, 'applied', NULL, '{}', '{}', ?, ?)
            """,
            (run_id, created_at, created_at),
        )
        self.db.connection.execute(
            """
            INSERT INTO memory_consolidation_actions(
                id, run_id, kind, target_ids_json,
                expected_versions_json, payload_json, reason,
                status, created_at, applied_at
            ) VALUES (?, ?, 'promotion', '["private-memory-id"]',
                      '[]', '{}', ?, 'applied', ?, ?)
            """,
            (item_id, run_id, SECRET, created_at, created_at),
        )

    def seed(self):
        now = utc_now()
        memory_id = self.db.add_memory(
            kind="semantic",
            content=SECRET,
            scope="private-scope",
            evidence=(SECRET,),
        )
        first_skill = self.db.add_skill(
            name="diagnostics",
            version="1.0.0",
            description=SECRET,
            instructions=SECRET,
        )
        second_skill = self.db.add_skill(
            name="diagnostics",
            version="2.0.0",
            description=SECRET,
            instructions=SECRET,
        )
        task_id = self.db.create_task(
            objective=SECRET,
            scope="private-scope",
            token_budget=500,
        )
        self._promotion(str(uuid.uuid4()), now)
        generation_run = str(uuid.uuid4())
        with self.db.connection:
            self.db.connection.execute(
                """
                INSERT INTO memory_deletion_requests(
                    id, memory_id, classification, expected_updated_at,
                    deletion_requirement, requested_by, reason, status,
                    verification_json, created_at, completed_at
                ) VALUES (?, ?, 'secret', ?, 'secure', ?, ?, 'completed',
                          ?, ?, ?)
                """,
                (
                    str(uuid.uuid4()), memory_id, now, SECRET, SECRET,
                    json.dumps({
                        "content_fields_erased": True,
                        "fts_residual_rows": 0,
                    }),
                    now, now,
                ),
            )
            self.db.connection.execute(
                """
                INSERT INTO skill_generation_runs(
                    id, status, scope, config_json, candidate_count,
                    created_at, applied_at
                ) VALUES (?, 'applied', ?, '{}', 1, ?, ?)
                """,
                (generation_run, SECRET, now, now),
            )
            self.db.connection.execute(
                """
                INSERT INTO skill_generation_candidates(
                    id, run_id, pattern_hash, trigger_kind, scope,
                    task_class, occurrence_count, average_significance,
                    procedure, applicability_json, inputs_json, outputs_json,
                    verification_json, failure_modes_json, permissions_json,
                    tools_json, evidence_json, trace_ids_json, status,
                    package_path, skill_id, created_at, applied_at
                ) VALUES (?, ?, ?, 'repeated_successful_procedure', ?, ?,
                          4, .9, ?, '[]', '[]', '[]', '[]', '[]', '[]',
                          '[]', ?, '[]', 'generated', ?, ?, ?, ?)
                """,
                (
                    str(uuid.uuid4()), generation_run, "a" * 64,
                    SECRET, SECRET, SECRET, json.dumps([SECRET]),
                    f"C:\\private\\{SECRET}", first_skill, now, now,
                ),
            )
            self.db.connection.execute(
                """
                INSERT INTO skill_evolution_runs(
                    id, source_skill_id, candidate_skill_id, source_version,
                    candidate_version, status, mutation_json, source_hash,
                    candidate_hash, created_at
                ) VALUES (?, ?, ?, '1.0.0', '2.0.0', 'candidate', ?,
                          ?, ?, ?)
                """,
                (
                    str(uuid.uuid4()), first_skill, second_skill,
                    json.dumps({"instructions": SECRET, "workflow": None}),
                    "b" * 64, "c" * 64, now,
                ),
            )
            self.db.connection.execute(
                """
                INSERT INTO agent_topology_recipes(
                    id, task_class, topology, structure_hash, recipe_json,
                    worker_count, models_json, skills_json, parallelism,
                    created_at
                ) VALUES (?, 'database', 'specialist_critic', ?, ?, 2,
                          '[]', '[]', .5, ?)
                """,
                (
                    str(uuid.uuid4()), "d" * 64,
                    json.dumps({"private": SECRET}), now,
                ),
            )
            self.db.connection.execute(
                """
                INSERT INTO token_budget_plans(
                    id, task_id, complexity, task_importance,
                    model_context_window, requested_input_budget,
                    output_headroom, reasoning_headroom,
                    effective_input_budget, context_budget,
                    candidate_count, selected_count, expected_utility,
                    created_at
                ) VALUES (?, ?, 'medium', .8, 4096, 500, 100, 100,
                          300, 200, 3, 1, .9, ?)
                """,
                (str(uuid.uuid4()), task_id, now),
            )
            self.db.connection.execute(
                """
                INSERT INTO context_uses(
                    task_id, source_type, source_id, tokens, utility, roi,
                    useful, compression_strategy, original_tokens,
                    exact_preserved, content_origin
                ) VALUES (?, 'file', ?, 60, .8, .01, NULL, 'extractive',
                          100, 1, 'document')
                """,
                (task_id, f"C:\\private\\{SECRET}"),
            )

    def test_empty_feed_is_explicit(self):
        result = self.reader.events()
        self.assertEqual(result["status"], "empty")
        self.assertEqual(result["reason"], "no_retained_learning_events")

    def test_projection_exposes_categories_without_private_source_content(self):
        self.seed()
        result = self.reader.events()
        rendered = json.dumps(result)
        self.assertNotIn(SECRET, rendered)
        self.assertNotIn("private-memory-id", rendered)
        self.assertNotIn("private-scope", rendered)
        self.assertNotIn("C:\\\\private", rendered)
        categories = {item["category"] for item in result["items"]}
        self.assertEqual(categories, {
            "memory_promotion", "memory_deletion", "new_skill",
            "skill_mutation", "topology_discovery",
            "context_optimization",
        })
        deletion = next(
            item for item in result["items"]
            if item["category"] == "memory_deletion"
        )
        self.assertNotIn("classification", deletion["evidence"])
        topology = next(
            item for item in result["items"]
            if item["category"] == "topology_discovery"
        )
        self.assertEqual(topology["autonomy"], "runtime_derived_advisory")
        self.assertFalse(
            topology["evidence"]["production_topology_changed"]
        )

    def test_keyset_pagination_is_stable_for_equal_timestamps(self):
        now = utc_now()
        with self.db.connection:
            for item_id in ("a", "b", "c"):
                self._promotion(item_id, now)
        first = self.reader.events(limit=2, category="memory_promotion")
        second = self.reader.events(
            limit=2,
            category="memory_promotion",
            cursor=first["next_cursor"],
        )
        ids = [item["id"] for item in first["items"] + second["items"]]
        self.assertEqual(ids, [
            "memory_promotion:c",
            "memory_promotion:b",
            "memory_promotion:a",
        ])
        self.assertEqual(len(ids), len(set(ids)))
        with self.assertRaisesRegex(ValueError, "invalid learning"):
            self.reader.events(
                limit=2,
                category="memory_deletion",
                cursor=first["next_cursor"],
            )

    def test_filters_and_cursor_fail_closed(self):
        with self.assertRaisesRegex(ValueError, "unknown learning"):
            self.reader.events(category="not-real")
        with self.assertRaisesRegex(ValueError, "invalid learning"):
            self.reader.events(cursor="not-a-cursor")
        with self.assertRaisesRegex(ValueError, "between 1 and 100"):
            self.reader.events(limit=101)


if __name__ == "__main__":
    unittest.main()
