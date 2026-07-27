from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from acr_runtime import AdaptiveRuntime, AttributionSignals, ContextRequest


class SkillRouterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.runtime = AdaptiveRuntime(
            Path(self.directory.name) / "acr.db"
        )

    def tearDown(self) -> None:
        self.runtime.close()
        self.directory.cleanup()

    def add_skill(
        self,
        name: str,
        description: str,
        instructions: str,
        *,
        trusted: bool = True,
    ) -> str:
        return self.runtime.register_skill(
            name,
            instructions,
            description=description,
            tags=("database-diagnostics", "sqlite", "fts5"),
            trusted=trusted,
        )

    def test_selects_smallest_useful_set_and_explains_rejections(self):
        primary = self.add_skill(
            "Focused SQLite Diagnostics",
            "Diagnose SQLite FTS5 database integrity with focused queries.",
            "Inspect SQLite schema and FTS5 integrity with focused queries.",
        )
        duplicate = self.add_skill(
            "Alternate SQLite Diagnostics",
            "Diagnose SQLite FTS5 database integrity with focused queries.",
            "Run alternate SQLite FTS5 database integrity checks.",
        )
        quarantined = self.add_skill(
            "Unsafe SQLite Diagnostics",
            "Diagnose SQLite FTS5 database integrity.",
            "Do not load this quarantined skill.",
            trusted=False,
        )

        route = self.runtime.route_skills(
            "Diagnose SQLite FTS5 database integrity with focused queries",
            task_class="database-diagnostics",
            token_budget=200,
        )

        self.assertEqual(len(route.selected), 1)
        self.assertIn(route.selected[0].id, {primary, duplicate})
        rejected = {item.id: item for item in route.rejected}
        other = ({primary, duplicate} - {route.selected[0].id}).pop()
        self.assertEqual(
            rejected[other].rejection_reason,
            "overlap_without_measurable_gain",
        )
        self.assertNotIn(quarantined, {item.id for item in route.candidates})
        self.assertIn("benefit=", route.selected[0].reason)

    def test_route_is_persisted_and_outcome_tracks_whether_it_helped(self):
        skill_id = self.add_skill(
            "SQLite FTS5 Verifier",
            "Verify SQLite FTS5 database schema and focused queries.",
            "Verify SQLite FTS5 schema, integrity, and focused database queries.",
        )
        bundle = self.runtime.compile_context_request(
            ContextRequest(
                task="Verify SQLite FTS5 database schema with focused queries",
                task_class="database-diagnostics",
                token_budget=300,
            )
        )
        selected_skills = [
            block for block in bundle.blocks if block.source_type == "skill"
        ]
        self.assertEqual([block.source_id for block in selected_skills], [skill_id])
        before = self.runtime.db.skill_route(bundle.task_id)
        self.assertIsNotNone(before)
        candidate = next(
            item for item in before["candidates"] if item["skill_id"] == skill_id
        )
        self.assertEqual(candidate["router_selected"], 1)
        self.assertEqual(candidate["compiler_selected"], 1)
        self.assertIsNone(candidate["outcome"])

        self.runtime.complete_task(
            bundle,
            success=True,
            critic_score=0.95,
            duration_ms=12,
            attribution_signals=AttributionSignals(
                execution_sources=(("skill", skill_id),)
            ),
            task_class="database-diagnostics",
            model="local-test",
        )

        after = self.runtime.db.skill_route(bundle.task_id)
        candidate = next(
            item for item in after["candidates"] if item["skill_id"] == skill_id
        )
        self.assertEqual(candidate["outcome"], "contributed")
        routing = self.runtime.telemetry_skill_routing()
        self.assertEqual(routing[0]["contributed"], 1)

    def test_hard_token_budget_rejects_oversized_skill(self):
        skill_id = self.add_skill(
            "Large SQLite Playbook",
            "Diagnose SQLite FTS5 database.",
            "SQLite FTS5 database " * 200,
        )
        route = self.runtime.route_skills(
            "Diagnose SQLite FTS5 database",
            token_budget=5,
        )
        rejected = {item.id: item for item in route.rejected}
        self.assertEqual(rejected[skill_id].rejection_reason, "token_budget")

    def test_task_class_performance_changes_expected_benefit(self):
        poor = self.add_skill(
            "SQLite Generalist",
            "Diagnose SQLite FTS5 database integrity.",
            "Diagnose SQLite FTS5 database integrity.",
        )
        strong = self.add_skill(
            "SQLite Specialist",
            "Diagnose SQLite FTS5 database integrity.",
            "Diagnose SQLite FTS5 database integrity.",
        )
        self.runtime.db.connection.executemany(
            """
            INSERT INTO skill_performance(
                skill_id, task_class, model, uses, successful_uses, failures
            ) VALUES (?, 'database-diagnostics', '', 10, ?, ?)
            """,
            ((poor, 0, 10), (strong, 10, 0)),
        )
        self.runtime.db.connection.commit()

        route = self.runtime.route_skills(
            "Diagnose SQLite FTS5 database integrity",
            task_class="database-diagnostics",
            token_budget=100,
        )
        by_id = {item.id: item for item in route.candidates}
        self.assertGreater(
            by_id[strong].expected_benefit,
            by_id[poor].expected_benefit,
        )
        self.assertEqual(route.selected[0].id, strong)


if __name__ == "__main__":
    unittest.main()
