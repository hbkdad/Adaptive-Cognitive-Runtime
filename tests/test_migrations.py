from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from acr_runtime.db import RuntimeDB
from acr_runtime.migrations import (
    EXPECTED_SCHEMA_VERSION,
    MigrationManager,
    MigrationRequired,
    SchemaDriftDetected,
    schema_fingerprint,
)


def drop_procedure_detection_schema(
    connection: sqlite3.Connection,
) -> None:
    drop_freshness_schema(connection)
    for trigger in (
        "procedure_detection_runs_no_update",
        "procedure_detection_runs_no_delete",
        "procedure_detection_candidates_no_update",
        "procedure_detection_candidates_no_delete",
    ):
        connection.execute(f"DROP TRIGGER IF EXISTS {trigger}")
    for table in (
        "procedure_detection_candidates",
        "procedure_detection_runs",
    ):
        connection.execute(f"DROP TABLE IF EXISTS {table}")
    connection.execute("DELETE FROM schema_migrations WHERE version >= 65")


def drop_source_class_schema(connection: sqlite3.Connection) -> None:
    drop_active_learning_schema(connection)
    columns = {
        row[1]
        for row in connection.execute("PRAGMA table_info(memories)").fetchall()
    }
    if "source_class" in columns:
        connection.execute("ALTER TABLE memories DROP COLUMN source_class")
    connection.execute("DELETE FROM schema_migrations WHERE version >= 67")


def drop_active_learning_schema(connection: sqlite3.Connection) -> None:
    drop_task_similarity_schema(connection)
    for trigger in (
        "active_learning_runs_no_update",
        "active_learning_runs_no_delete",
    ):
        connection.execute(f"DROP TRIGGER IF EXISTS {trigger}")
    connection.execute("DROP TABLE IF EXISTS active_learning_runs")
    connection.execute("DELETE FROM schema_migrations WHERE version >= 68")


def drop_task_similarity_schema(connection: sqlite3.Connection) -> None:
    for trigger in (
        "task_feature_profiles_no_update",
        "task_feature_profiles_no_delete",
    ):
        connection.execute(f"DROP TRIGGER IF EXISTS {trigger}")
    connection.execute("DROP TABLE IF EXISTS task_feature_profiles")
    connection.execute("DELETE FROM schema_migrations WHERE version >= 69")


def drop_freshness_schema(connection: sqlite3.Connection) -> None:
    drop_source_class_schema(connection)
    columns = {
        row[1]
        for row in connection.execute("PRAGMA table_info(memories)").fetchall()
    }
    for column in (
        "requires_refresh",
        "expected_half_life_days",
        "source_freshness",
        "observed_at",
    ):
        if column in columns:
            connection.execute(f"ALTER TABLE memories DROP COLUMN {column}")
    connection.execute("DELETE FROM schema_migrations WHERE version >= 66")


def drop_project_state_schema(
    connection: sqlite3.Connection,
) -> None:
    drop_procedure_detection_schema(connection)
    for trigger in (
        "project_state_events_no_update",
        "project_state_events_no_delete",
    ):
        connection.execute(f"DROP TRIGGER IF EXISTS {trigger}")
    for table in (
        "project_state_events",
        "project_state_item_dependencies",
        "project_state_items",
        "project_states",
    ):
        connection.execute(f"DROP TABLE IF EXISTS {table}")
    connection.execute("DELETE FROM schema_migrations WHERE version >= 64")


def drop_performance_profiler_schema(
    connection: sqlite3.Connection,
) -> None:
    drop_project_state_schema(connection)
    for trigger in (
        "performance_profile_runs_no_update",
        "performance_profile_runs_no_delete",
        "performance_measurements_no_update",
        "performance_measurements_no_delete",
    ):
        connection.execute(f"DROP TRIGGER IF EXISTS {trigger}")
    for table in (
        "performance_measurements",
        "performance_profile_runs",
    ):
        connection.execute(f"DROP TABLE IF EXISTS {table}")
    connection.execute("DELETE FROM schema_migrations WHERE version >= 63")


def drop_audit_schema(connection: sqlite3.Connection) -> None:
    drop_performance_profiler_schema(connection)
    for trigger in (
        "audit_memory_created",
        "audit_memory_superseded",
        "audit_skill_generated",
        "audit_skill_promoted",
        "audit_skill_retired",
        "audit_routing_changed",
        "audit_agent_created",
        "audit_permission_denied",
        "audit_events_no_update",
        "audit_events_no_delete",
    ):
        connection.execute(f"DROP TRIGGER IF EXISTS {trigger}")
    connection.execute("DROP TABLE IF EXISTS audit_events")
    connection.execute("DELETE FROM schema_migrations WHERE version >= 62")


def drop_failure_recovery_schema(connection: sqlite3.Connection) -> None:
    drop_audit_schema(connection)
    for trigger in (
        "recovery_events_no_update",
        "recovery_events_no_delete",
    ):
        connection.execute(f"DROP TRIGGER IF EXISTS {trigger}")
    for table in (
        "recovery_events",
        "recovery_steps",
        "recovery_runs",
    ):
        connection.execute(f"DROP TABLE IF EXISTS {table}")
    connection.execute("DELETE FROM schema_migrations WHERE version >= 61")


def drop_plugin_schema(connection: sqlite3.Connection) -> None:
    drop_failure_recovery_schema(connection)
    for trigger in (
        "plugin_validation_runs_no_update",
        "plugin_validation_runs_no_delete",
        "plugin_manifests_no_update",
        "plugin_manifests_no_delete",
        "plugin_routes_no_update",
        "plugin_routes_no_delete",
    ):
        connection.execute(f"DROP TRIGGER IF EXISTS {trigger}")
    for table in (
        "plugin_routes",
        "plugin_manifests",
        "plugin_validation_runs",
    ):
        connection.execute(f"DROP TABLE IF EXISTS {table}")
    connection.execute("DELETE FROM schema_migrations WHERE version >= 60")


def drop_migration_integrity_schema(connection: sqlite3.Connection) -> None:
    drop_plugin_schema(connection)
    columns = {
        row[1] for row in connection.execute("PRAGMA table_info(schema_migrations)")
    }
    if "schema_hash" in columns:
        connection.execute(
            "ALTER TABLE schema_migrations DROP COLUMN schema_hash"
        )
    connection.execute("DELETE FROM schema_migrations WHERE version >= 59")


def drop_safe_mode_schema(connection: sqlite3.Connection) -> None:
    drop_migration_integrity_schema(connection)
    for trigger in (
        "safe_mode_state_no_delete",
        "safe_mode_events_no_update",
        "safe_mode_events_no_delete",
    ):
        connection.execute(f"DROP TRIGGER IF EXISTS {trigger}")
    for table in ("safe_mode_events", "safe_mode_state"):
        connection.execute(f"DROP TABLE IF EXISTS {table}")


def drop_human_override_schema(connection: sqlite3.Connection) -> None:
    drop_safe_mode_schema(connection)
    for trigger in (
        "human_overrides_no_update",
        "human_overrides_no_delete",
        "human_override_events_no_update",
        "human_override_events_no_delete",
    ):
        connection.execute(f"DROP TRIGGER IF EXISTS {trigger}")
    for table in ("human_override_events", "human_overrides"):
        connection.execute(f"DROP TABLE IF EXISTS {table}")


def drop_evidence_graph_schema(connection: sqlite3.Connection) -> None:
    drop_human_override_schema(connection)
    for trigger in (
        "evidence_graph_edges_integrity",
        "evidence_graph_nodes_no_update",
        "evidence_graph_nodes_no_delete",
        "evidence_graph_edges_no_update",
        "evidence_graph_edges_no_delete",
        "evidence_graph_bundles_no_update",
        "evidence_graph_bundles_no_delete",
        "evidence_graph_bundle_nodes_no_update",
        "evidence_graph_bundle_nodes_no_delete",
        "evidence_graph_bundle_edges_no_update",
        "evidence_graph_bundle_edges_integrity",
        "evidence_graph_bundle_edges_no_delete",
    ):
        connection.execute(f"DROP TRIGGER IF EXISTS {trigger}")
    for table in (
        "evidence_graph_bundle_edges",
        "evidence_graph_bundle_nodes",
        "evidence_graph_bundles",
        "evidence_graph_edges",
        "evidence_graph_nodes",
    ):
        connection.execute(f"DROP TABLE IF EXISTS {table}")


def drop_parallel_research_schema(connection: sqlite3.Connection) -> None:
    drop_evidence_graph_schema(connection)
    for trigger in (
        "research_references_no_update",
        "research_references_no_delete",
        "research_plans_no_update",
        "research_plans_integrity",
        "research_plans_no_delete",
        "research_runs_no_update",
        "research_runs_no_delete",
        "research_findings_no_update",
        "research_findings_integrity",
        "research_findings_no_delete",
        "research_benchmarks_no_update",
        "research_benchmarks_integrity",
        "research_benchmarks_no_delete",
    ):
        connection.execute(f"DROP TRIGGER IF EXISTS {trigger}")
    for table in (
        "research_parallel_benchmarks",
        "research_findings",
        "research_runs",
        "research_plans",
        "research_references",
    ):
        connection.execute(f"DROP TABLE IF EXISTS {table}")


def drop_reasoning_budget_schema(connection: sqlite3.Connection) -> None:
    drop_parallel_research_schema(connection)
    for trigger in (
        "reasoning_budget_policies_no_update",
        "reasoning_budget_policies_no_delete",
        "reasoning_budget_decisions_no_update",
        "reasoning_budget_decisions_integrity",
        "reasoning_budget_decisions_no_delete",
        "reasoning_budget_outcomes_no_update",
        "reasoning_budget_outcomes_no_delete",
        "reasoning_budget_evaluations_no_update",
        "reasoning_budget_evaluations_no_delete",
    ):
        connection.execute(f"DROP TRIGGER IF EXISTS {trigger}")
    for table in (
        "reasoning_budget_policy_evaluations",
        "reasoning_budget_outcomes",
        "reasoning_budget_decisions",
        "reasoning_budget_policies",
    ):
        connection.execute(f"DROP TABLE IF EXISTS {table}")


def drop_tool_exposure_schema(connection: sqlite3.Connection) -> None:
    drop_reasoning_budget_schema(connection)
    for trigger in (
        "tool_definitions_no_update",
        "tool_definitions_no_delete",
        "tool_routes_no_update",
        "tool_routes_no_delete",
        "tool_route_candidates_no_update",
        "tool_route_candidates_no_delete",
        "tool_exposure_projections_integrity",
        "tool_exposure_projections_no_update",
        "tool_exposure_projections_no_delete",
        "tool_exposure_runs_start_guard",
        "tool_exposure_runs_terminal_guard",
        "tool_exposure_runs_no_delete",
        "tool_exposure_cases_running_insert",
        "tool_exposure_cases_no_update",
        "tool_exposure_cases_no_delete",
        "tool_exposure_trials_running_insert",
        "tool_exposure_trials_no_update",
        "tool_exposure_trials_no_delete",
    ):
        connection.execute(f"DROP TRIGGER IF EXISTS {trigger}")
    for table in (
        "tool_exposure_benchmark_trials",
        "tool_exposure_benchmark_cases",
        "tool_exposure_benchmark_runs",
        "tool_exposure_projections",
    ):
        connection.execute(f"DROP TABLE IF EXISTS {table}")


def drop_token_waste_schema(connection: sqlite3.Connection) -> None:
    drop_tool_exposure_schema(connection)
    for trigger in (
        "token_waste_runs_start_guard",
        "token_waste_runs_terminal_guard",
        "token_waste_runs_no_delete",
        "token_waste_findings_running_insert",
        "token_waste_findings_no_update",
        "token_waste_findings_no_delete",
    ):
        connection.execute(f"DROP TRIGGER IF EXISTS {trigger}")
    for table in (
        "token_waste_findings",
        "token_waste_runs",
    ):
        connection.execute(f"DROP TABLE IF EXISTS {table}")


def drop_cost_accounting_schema(connection: sqlite3.Connection) -> None:
    drop_token_waste_schema(connection)
    for trigger in (
        "price_rates_no_update",
        "price_rates_no_delete",
        "local_cost_profiles_no_update",
        "local_cost_profiles_no_delete",
        "cost_events_no_update",
        "cost_events_no_delete",
        "cost_meter_lines_no_update",
        "cost_meter_lines_guard_insert",
        "cost_meter_lines_validate_rate",
        "cost_meter_lines_validate_amount",
        "cost_meter_lines_no_delete",
        "cost_skill_allocations_no_update",
        "cost_skill_allocations_guard_insert",
        "cost_skill_allocations_no_delete",
        "cost_event_seals_validate",
        "cost_event_seals_no_update",
        "cost_event_seals_no_delete",
        "price_rates_no_overlap",
        "local_cost_profiles_no_overlap",
    ):
        connection.execute(f"DROP TRIGGER IF EXISTS {trigger}")
    for table in (
        "cost_event_seals",
        "cost_skill_allocations",
        "cost_meter_lines",
        "cost_events",
        "local_cost_profiles",
        "price_rates",
    ):
        connection.execute(f"DROP TABLE IF EXISTS {table}")


def drop_utility_governance_schema(connection: sqlite3.Connection) -> None:
    drop_cost_accounting_schema(connection)
    for trigger in (
        "utility_assets_no_update",
        "utility_assets_no_delete",
        "utility_observations_no_update",
        "utility_observations_no_delete",
        "utility_snapshots_no_update",
        "utility_snapshots_no_delete",
        "context_strategy_uses_guard",
        "context_strategy_uses_no_delete",
        "utility_context_selections_no_update",
        "utility_context_selections_no_delete",
    ):
        connection.execute(f"DROP TRIGGER IF EXISTS {trigger}")
    for table in (
        "utility_context_selections",
        "context_strategy_uses",
        "utility_snapshots",
        "utility_observations",
        "utility_assets",
    ):
        connection.execute(f"DROP TABLE IF EXISTS {table}")


def drop_skill_coevolution_schema(connection: sqlite3.Connection) -> None:
    drop_utility_governance_schema(connection)
    for trigger in (
        "skill_support_links_no_update",
        "skill_support_links_no_delete",
        "skill_support_invalidations_no_update",
        "skill_support_invalidations_no_delete",
        "skill_reliability_no_update",
        "skill_reliability_no_delete",
        "skill_coevolution_events_no_update",
        "skill_coevolution_events_no_delete",
    ):
        connection.execute(f"DROP TRIGGER IF EXISTS {trigger}")
    for table in (
        "skill_coevolution_events",
        "skill_reliability_snapshots",
        "skill_support_invalidations",
        "skill_support_links",
    ):
        connection.execute(f"DROP TABLE IF EXISTS {table}")


def drop_meta_context_schema(connection: sqlite3.Connection) -> None:
    drop_skill_coevolution_schema(connection)
    for trigger in (
        "meta_context_strategies_guard",
        "meta_context_strategies_no_delete",
        "meta_context_cases_no_update",
        "meta_context_cases_running_insert",
        "meta_context_cases_no_delete",
        "meta_context_runs_terminal_guard",
        "meta_context_runs_no_delete",
        "meta_context_events_no_update",
        "meta_context_events_no_delete",
    ):
        connection.execute(f"DROP TRIGGER IF EXISTS {trigger}")
    for table in (
        "meta_context_case_results",
        "meta_context_runs",
        "meta_context_events",
        "meta_context_strategies",
    ):
        connection.execute(f"DROP TABLE IF EXISTS {table}")


def create_v2_database(path: Path, *, valid_evidence: bool = True) -> str:
    memory_id = "11111111-1111-4111-8111-111111111111"
    connection = sqlite3.connect(path)
    try:
        connection.executescript(
            """
            CREATE TABLE schema_migrations (
                version INTEGER PRIMARY KEY,
                applied_at TEXT NOT NULL
            );
            INSERT INTO schema_migrations VALUES (1, '2026-01-01T00:00:00Z');
            INSERT INTO schema_migrations VALUES (2, '2026-01-02T00:00:00Z');

            CREATE TABLE memories (
                id TEXT PRIMARY KEY,
                kind TEXT NOT NULL CHECK (
                    kind IN ('semantic', 'episodic', 'procedural', 'failure')
                ),
                content TEXT NOT NULL,
                scope TEXT NOT NULL DEFAULT 'global',
                confidence REAL NOT NULL CHECK (confidence BETWEEN 0 AND 1),
                importance REAL NOT NULL CHECK (importance BETWEEN 0 AND 1),
                evidence_json TEXT NOT NULL DEFAULT '[]',
                source TEXT,
                status TEXT NOT NULL DEFAULT 'active' CHECK (
                    status IN ('candidate', 'active', 'superseded', 'archived')
                ),
                valid_from TEXT NOT NULL,
                valid_to TEXT,
                supersedes TEXT REFERENCES memories(id),
                token_cost INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                last_used_at TEXT NOT NULL,
                use_count INTEGER NOT NULL DEFAULT 0,
                success_count INTEGER NOT NULL DEFAULT 0
            );
            CREATE VIRTUAL TABLE memories_fts USING fts5(
                content, scope, kind, content='memories', content_rowid='rowid'
            );
            CREATE TRIGGER memories_ai AFTER INSERT ON memories BEGIN
                INSERT INTO memories_fts(rowid, content, scope, kind)
                VALUES (new.rowid, new.content, new.scope, new.kind);
            END;
            """
        )
        connection.execute(
            """
            INSERT INTO memories (
                id, kind, content, scope, confidence, importance, evidence_json,
                source, status, valid_from, valid_to, supersedes, token_cost,
                created_at, last_used_at, use_count, success_count
            ) VALUES (?, 'semantic', 'SQLite uses FTS5', 'project', 0.9, 0.8,
                      ?, 'README.md', 'active', '2026-01-01T00:00:00Z',
                      NULL, NULL, 4, '2026-01-01T00:00:00Z',
                      '2026-01-03T00:00:00Z', 3, 2)
            """,
            (memory_id, json.dumps(["README.md"]) if valid_evidence else "broken"),
        )
        connection.commit()
    finally:
        connection.close()
    return memory_id


class MigrationTests(unittest.TestCase):
    def test_v2_database_requires_explicit_upgrade_and_preserves_data(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "acr.db"
            memory_id = create_v2_database(path)

            with self.assertRaises(MigrationRequired):
                RuntimeDB(path)

            manager = MigrationManager(path)
            status = manager.apply_pending()
            self.assertEqual(status.current_version, EXPECTED_SCHEMA_VERSION)
            self.assertEqual(status.pending_versions, ())
            self.assertIsNotNone(manager.last_backup_path)
            self.assertTrue(manager.last_backup_path.exists())

            with RuntimeDB(path) as upgraded:
                memory = upgraded.memories.get(memory_id)
                self.assertIsNotNone(memory)
                self.assertEqual(memory.type.value, "semantic")
                self.assertEqual(memory.status.value, "confirmed")
                self.assertEqual(memory.source_type, "legacy")
                self.assertEqual(memory.source_id, "README.md")
                self.assertEqual(memory.access_count, 3)
                self.assertEqual(memory.successful_uses, 2)
                self.assertEqual(memory.failed_uses, 1)
                self.assertAlmostEqual(memory.utility_score, 2 / 3)
                self.assertEqual(
                    memory.retention_reasons, ("legacy_or_direct_write",)
                )
                self.assertTrue(upgraded.health()["schema_current"])

            second = MigrationManager(path)
            self.assertEqual(
                second.apply_pending().current_version,
                EXPECTED_SCHEMA_VERSION,
            )
            self.assertIsNone(second.last_backup_path)

    def test_failed_v3_migration_rolls_back_and_keeps_backup(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "acr.db"
            create_v2_database(path, valid_evidence=False)
            manager = MigrationManager(path)

            with self.assertRaises(sqlite3.IntegrityError):
                manager.apply_pending()

            self.assertEqual(manager.status().current_version, 2)
            self.assertTrue(manager.last_backup_path.exists())
            connection = sqlite3.connect(path)
            try:
                columns = {
                    row[1] for row in connection.execute("PRAGMA table_info(memories)")
                }
                self.assertIn("kind", columns)
                self.assertNotIn("type", columns)
            finally:
                connection.close()

    def test_failed_v4_migration_rolls_back_added_column(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "acr.db"
            create_v2_database(path)
            connection = sqlite3.connect(path)
            try:
                MigrationManager._apply_migration_3(connection)
                connection.execute(
                    "CREATE TABLE memory_write_decisions (placeholder TEXT)"
                )
                connection.commit()
            finally:
                connection.close()
            manager = MigrationManager(path)

            with self.assertRaises(sqlite3.OperationalError):
                manager.apply_pending()

            self.assertEqual(manager.status().current_version, 3)
            connection = sqlite3.connect(path)
            try:
                columns = {
                    row[1] for row in connection.execute("PRAGMA table_info(memories)")
                }
                self.assertNotIn("retention_reason_json", columns)
            finally:
                connection.close()

    def test_failed_v5_migration_rolls_back_created_run_table(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "acr.db"
            create_v2_database(path)
            connection = sqlite3.connect(path)
            try:
                MigrationManager._apply_migration_3(connection)
                MigrationManager._apply_migration_4(connection)
                connection.execute(
                    "CREATE TABLE memory_consolidation_actions (placeholder TEXT)"
                )
                connection.commit()
            finally:
                connection.close()
            manager = MigrationManager(path)

            with self.assertRaises(sqlite3.OperationalError):
                manager.apply_pending()

            self.assertEqual(manager.status().current_version, 4)
            connection = sqlite3.connect(path)
            try:
                run_table = connection.execute(
                    """
                    SELECT COUNT(*) FROM sqlite_master
                    WHERE type = 'table'
                      AND name = 'memory_consolidation_runs'
                    """
                ).fetchone()[0]
                self.assertEqual(run_table, 0)
            finally:
                connection.close()

    def test_failed_v6_migration_rolls_back_lifecycle_columns(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "acr.db"
            create_v2_database(path)
            connection = sqlite3.connect(path)
            try:
                MigrationManager._apply_migration_3(connection)
                MigrationManager._apply_migration_4(connection)
                MigrationManager._apply_migration_5(connection)
                connection.execute("CREATE TABLE memory_gc_actions (placeholder TEXT)")
                connection.commit()
            finally:
                connection.close()
            manager = MigrationManager(path)

            with self.assertRaises(sqlite3.OperationalError):
                manager.apply_pending()

            self.assertEqual(manager.status().current_version, 5)
            connection = sqlite3.connect(path)
            try:
                columns = {
                    row[1] for row in connection.execute("PRAGMA table_info(memories)")
                }
                self.assertNotIn("lifecycle_state", columns)
                run_table = connection.execute(
                    """
                    SELECT COUNT(*) FROM sqlite_master
                    WHERE type = 'table' AND name = 'memory_gc_runs'
                    """
                ).fetchone()[0]
                self.assertEqual(run_table, 0)
            finally:
                connection.close()

    def test_failed_v7_migration_rolls_back_failure_tables(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "acr.db"
            create_v2_database(path)
            connection = sqlite3.connect(path)
            try:
                MigrationManager._apply_migration_3(connection)
                MigrationManager._apply_migration_4(connection)
                MigrationManager._apply_migration_5(connection)
                MigrationManager._apply_migration_6(connection)
                connection.execute("CREATE INDEX failure_records_lookup ON memories(id)")
                connection.commit()
            finally:
                connection.close()
            manager = MigrationManager(path)

            with self.assertRaises(sqlite3.OperationalError):
                manager.apply_pending()

            self.assertEqual(manager.status().current_version, 6)
            connection = sqlite3.connect(path)
            try:
                table_count = connection.execute(
                    """
                    SELECT COUNT(*) FROM sqlite_master
                    WHERE type = 'table' AND name = 'failure_records'
                    """
                ).fetchone()[0]
                self.assertEqual(table_count, 0)
            finally:
                connection.close()

    def test_failed_v8_migration_rolls_back_experience_tables(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "acr.db"
            create_v2_database(path)
            connection = sqlite3.connect(path)
            try:
                MigrationManager._apply_migration_3(connection)
                MigrationManager._apply_migration_4(connection)
                MigrationManager._apply_migration_5(connection)
                MigrationManager._apply_migration_6(connection)
                MigrationManager._apply_migration_7(connection)
                connection.execute(
                    "CREATE INDEX experience_traces_scope ON memories(id)"
                )
                connection.commit()
            finally:
                connection.close()
            manager = MigrationManager(path)

            with self.assertRaises(sqlite3.OperationalError):
                manager.apply_pending()

            self.assertEqual(manager.status().current_version, 7)
            connection = sqlite3.connect(path)
            try:
                table_count = connection.execute(
                    """
                    SELECT COUNT(*) FROM sqlite_master
                    WHERE type = 'table' AND name = 'experience_traces'
                    """
                ).fetchone()[0]
                self.assertEqual(table_count, 0)
            finally:
                connection.close()

    def test_failed_v9_migration_rolls_back_context_rebuild(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "acr.db"
            create_v2_database(path)
            connection = sqlite3.connect(path)
            try:
                MigrationManager._apply_migration_3(connection)
                MigrationManager._apply_migration_4(connection)
                MigrationManager._apply_migration_5(connection)
                MigrationManager._apply_migration_6(connection)
                MigrationManager._apply_migration_7(connection)
                MigrationManager._apply_migration_8(connection)
                connection.executescript(
                    """
                    CREATE TABLE tasks (
                        id TEXT PRIMARY KEY
                    );
                    CREATE TABLE context_uses (
                        task_id TEXT NOT NULL REFERENCES tasks(id),
                        source_type TEXT NOT NULL CHECK (
                            source_type IN ('memory', 'skill')
                        ),
                        source_id TEXT NOT NULL,
                        tokens INTEGER NOT NULL,
                        utility REAL NOT NULL,
                        roi REAL NOT NULL,
                        useful INTEGER,
                        PRIMARY KEY(task_id, source_type, source_id)
                    );
                    """
                )
                connection.execute("CREATE TABLE context_uses_v8 (placeholder TEXT)")
                connection.commit()
            finally:
                connection.close()
            manager = MigrationManager(path)

            with self.assertRaises(sqlite3.OperationalError):
                manager.apply_pending()

            self.assertEqual(manager.status().current_version, 8)
            connection = sqlite3.connect(path)
            try:
                tables = {
                    row[0]
                    for row in connection.execute(
                        """
                        SELECT name FROM sqlite_master
                        WHERE type = 'table' AND name LIKE 'context_uses%'
                        """
                    )
                }
                self.assertEqual(tables, {"context_uses", "context_uses_v8"})
            finally:
                connection.close()

    def test_failed_v10_migration_rolls_back_budget_table(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "acr.db"
            create_v2_database(path)
            connection = sqlite3.connect(path)
            try:
                MigrationManager._apply_migration_3(connection)
                MigrationManager._apply_migration_4(connection)
                MigrationManager._apply_migration_5(connection)
                MigrationManager._apply_migration_6(connection)
                MigrationManager._apply_migration_7(connection)
                MigrationManager._apply_migration_8(connection)
                MigrationManager._apply_migration_9(connection)
                connection.execute(
                    "CREATE INDEX token_budget_plans_complexity ON memories(id)"
                )
                connection.commit()
            finally:
                connection.close()
            manager = MigrationManager(path)

            with self.assertRaises(sqlite3.OperationalError):
                manager.apply_pending()

            self.assertEqual(manager.status().current_version, 9)
            connection = sqlite3.connect(path)
            try:
                table_count = connection.execute(
                    """
                    SELECT COUNT(*) FROM sqlite_master
                    WHERE type = 'table' AND name = 'token_budget_plans'
                    """
                ).fetchone()[0]
                self.assertEqual(table_count, 0)
            finally:
                connection.close()

    def test_failed_v11_migration_rolls_back_attribution_table(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "acr.db"
            create_v2_database(path)
            connection = sqlite3.connect(path)
            try:
                MigrationManager._apply_migration_3(connection)
                MigrationManager._apply_migration_4(connection)
                MigrationManager._apply_migration_5(connection)
                MigrationManager._apply_migration_6(connection)
                MigrationManager._apply_migration_7(connection)
                MigrationManager._apply_migration_8(connection)
                MigrationManager._apply_migration_9(connection)
                MigrationManager._apply_migration_10(connection)
                connection.execute(
                    "CREATE INDEX context_attributions_outcome ON memories(id)"
                )
                connection.commit()
            finally:
                connection.close()
            manager = MigrationManager(path)

            with self.assertRaises(sqlite3.OperationalError):
                manager.apply_pending()

            self.assertEqual(manager.status().current_version, 10)
            connection = sqlite3.connect(path)
            try:
                count = connection.execute(
                    """
                    SELECT COUNT(*) FROM sqlite_master
                    WHERE type = 'table' AND name = 'context_attributions'
                    """
                ).fetchone()[0]
                self.assertEqual(count, 0)
            finally:
                connection.close()

    def test_failed_v12_migration_rolls_back_compression_columns(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "acr.db"
            create_v2_database(path)
            connection = sqlite3.connect(path)
            try:
                for migration in (
                    MigrationManager._apply_migration_3,
                    MigrationManager._apply_migration_4,
                    MigrationManager._apply_migration_5,
                    MigrationManager._apply_migration_6,
                    MigrationManager._apply_migration_7,
                    MigrationManager._apply_migration_8,
                    MigrationManager._apply_migration_9,
                    MigrationManager._apply_migration_10,
                    MigrationManager._apply_migration_11,
                ):
                    migration(connection)
                connection.execute(
                    "ALTER TABLE context_uses ADD COLUMN original_tokens INTEGER"
                )
                connection.commit()
            finally:
                connection.close()
            manager = MigrationManager(path)

            with self.assertRaises(sqlite3.OperationalError):
                manager.apply_pending()

            self.assertEqual(manager.status().current_version, 11)
            connection = sqlite3.connect(path)
            try:
                columns = {
                    row[1]
                    for row in connection.execute(
                        "PRAGMA table_info(context_uses)"
                    )
                }
                self.assertNotIn("compression_strategy", columns)
                self.assertIn("original_tokens", columns)
            finally:
                connection.close()

    def test_failed_v13_migration_rolls_back_registry_columns(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "acr.db"
            create_v2_database(path)
            connection = sqlite3.connect(path)
            try:
                for migration in (
                    MigrationManager._apply_migration_3,
                    MigrationManager._apply_migration_4,
                    MigrationManager._apply_migration_5,
                    MigrationManager._apply_migration_6,
                    MigrationManager._apply_migration_7,
                    MigrationManager._apply_migration_8,
                    MigrationManager._apply_migration_9,
                    MigrationManager._apply_migration_10,
                    MigrationManager._apply_migration_11,
                    MigrationManager._apply_migration_12,
                ):
                    migration(connection)
                connection.execute(
                    "CREATE TABLE skill_registry_history(placeholder TEXT)"
                )
                connection.commit()
            finally:
                connection.close()
            manager = MigrationManager(path)

            with self.assertRaises(sqlite3.OperationalError):
                manager.apply_pending()

            self.assertEqual(manager.status().current_version, 12)
            connection = sqlite3.connect(path)
            try:
                columns = {
                    row[1] for row in connection.execute(
                        "PRAGMA table_info(skills)"
                    )
                }
                self.assertNotIn("manifest_id", columns)
            finally:
                connection.close()

    def test_failed_v14_migration_rolls_back_routing_tables(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "acr.db"
            create_v2_database(path)
            connection = sqlite3.connect(path)
            try:
                for migration in (
                    MigrationManager._apply_migration_3,
                    MigrationManager._apply_migration_4,
                    MigrationManager._apply_migration_5,
                    MigrationManager._apply_migration_6,
                    MigrationManager._apply_migration_7,
                    MigrationManager._apply_migration_8,
                    MigrationManager._apply_migration_9,
                    MigrationManager._apply_migration_10,
                    MigrationManager._apply_migration_11,
                    MigrationManager._apply_migration_12,
                    MigrationManager._apply_migration_13,
                ):
                    migration(connection)
                connection.execute(
                    "CREATE TABLE skill_routing_candidates(placeholder TEXT)"
                )
                connection.commit()
            finally:
                connection.close()
            manager = MigrationManager(path)

            with self.assertRaises(sqlite3.OperationalError):
                manager.apply_pending()

            self.assertEqual(manager.status().current_version, 13)
            connection = sqlite3.connect(path)
            try:
                count = connection.execute(
                    """
                    SELECT COUNT(*) FROM sqlite_master
                    WHERE type = 'table' AND name = 'skill_routing_runs'
                    """
                ).fetchone()[0]
                self.assertEqual(count, 0)
            finally:
                connection.close()

    def test_failed_v15_migration_rolls_back_generation_tables(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "acr.db"
            create_v2_database(path)
            connection = sqlite3.connect(path)
            try:
                for migration in (
                    MigrationManager._apply_migration_3,
                    MigrationManager._apply_migration_4,
                    MigrationManager._apply_migration_5,
                    MigrationManager._apply_migration_6,
                    MigrationManager._apply_migration_7,
                    MigrationManager._apply_migration_8,
                    MigrationManager._apply_migration_9,
                    MigrationManager._apply_migration_10,
                    MigrationManager._apply_migration_11,
                    MigrationManager._apply_migration_12,
                    MigrationManager._apply_migration_13,
                    MigrationManager._apply_migration_14,
                ):
                    migration(connection)
                connection.execute(
                    "CREATE TABLE skill_generation_candidates(placeholder TEXT)"
                )
                connection.commit()
            finally:
                connection.close()
            manager = MigrationManager(path)

            with self.assertRaises(sqlite3.OperationalError):
                manager.apply_pending()

            self.assertEqual(manager.status().current_version, 14)
            connection = sqlite3.connect(path)
            try:
                count = connection.execute(
                    """
                    SELECT COUNT(*) FROM sqlite_master
                    WHERE type = 'table' AND name = 'skill_generation_runs'
                    """
                ).fetchone()[0]
                self.assertEqual(count, 0)
            finally:
                connection.close()

    def test_failed_v16_migration_rolls_back_validation_tables(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "acr.db"
            create_v2_database(path)
            connection = sqlite3.connect(path)
            try:
                for migration in (
                    MigrationManager._apply_migration_3,
                    MigrationManager._apply_migration_4,
                    MigrationManager._apply_migration_5,
                    MigrationManager._apply_migration_6,
                    MigrationManager._apply_migration_7,
                    MigrationManager._apply_migration_8,
                    MigrationManager._apply_migration_9,
                    MigrationManager._apply_migration_10,
                    MigrationManager._apply_migration_11,
                    MigrationManager._apply_migration_12,
                    MigrationManager._apply_migration_13,
                    MigrationManager._apply_migration_14,
                    MigrationManager._apply_migration_15,
                ):
                    migration(connection)
                connection.execute(
                    "CREATE TABLE skill_validation_results(placeholder TEXT)"
                )
                connection.commit()
            finally:
                connection.close()
            manager = MigrationManager(path)

            with self.assertRaises(sqlite3.OperationalError):
                manager.apply_pending()

            self.assertEqual(manager.status().current_version, 15)
            connection = sqlite3.connect(path)
            try:
                count = connection.execute(
                    """
                    SELECT COUNT(*) FROM sqlite_master
                    WHERE type = 'table' AND name = 'skill_validation_runs'
                    """
                ).fetchone()[0]
                self.assertEqual(count, 0)
            finally:
                connection.close()

    def test_v16_quarantines_pre_pipeline_active_skills(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "acr.db"
            create_v2_database(path)
            connection = sqlite3.connect(path)
            try:
                for migration in (
                    MigrationManager._apply_migration_3,
                    MigrationManager._apply_migration_4,
                    MigrationManager._apply_migration_5,
                    MigrationManager._apply_migration_6,
                    MigrationManager._apply_migration_7,
                    MigrationManager._apply_migration_8,
                    MigrationManager._apply_migration_9,
                    MigrationManager._apply_migration_10,
                    MigrationManager._apply_migration_11,
                    MigrationManager._apply_migration_12,
                    MigrationManager._apply_migration_13,
                    MigrationManager._apply_migration_14,
                    MigrationManager._apply_migration_15,
                ):
                    migration(connection)
                connection.execute(
                    """
                    INSERT INTO skills(
                        id, name, version, description, instructions,
                        status, token_cost, created_at, manifest_id,
                        lifecycle_status
                    ) VALUES (
                        'legacy-active', 'Legacy active', '1.0.0', 'legacy',
                        'legacy', 'active', 1, '2026-01-01T00:00:00Z',
                        'legacy-active', 'active'
                    )
                    """
                )
                connection.commit()
                MigrationManager._apply_migration_16(connection)
                row = connection.execute(
                    """
                    SELECT status, lifecycle_status FROM skills
                    WHERE id = 'legacy-active'
                    """
                ).fetchone()
                history = connection.execute(
                    """
                    SELECT event FROM skill_registry_history
                    WHERE skill_id = 'legacy-active'
                    """
                ).fetchone()
                self.assertEqual(row, ("quarantine", "quarantined"))
                self.assertEqual(history[0], "validation_required")
            finally:
                connection.close()

    def test_failed_v17_migration_rolls_back_evolution_tables(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "acr.db"
            create_v2_database(path)
            connection = sqlite3.connect(path)
            try:
                for migration in (
                    MigrationManager._apply_migration_3,
                    MigrationManager._apply_migration_4,
                    MigrationManager._apply_migration_5,
                    MigrationManager._apply_migration_6,
                    MigrationManager._apply_migration_7,
                    MigrationManager._apply_migration_8,
                    MigrationManager._apply_migration_9,
                    MigrationManager._apply_migration_10,
                    MigrationManager._apply_migration_11,
                    MigrationManager._apply_migration_12,
                    MigrationManager._apply_migration_13,
                    MigrationManager._apply_migration_14,
                    MigrationManager._apply_migration_15,
                    MigrationManager._apply_migration_16,
                ):
                    migration(connection)
                connection.execute(
                    "CREATE TABLE skill_evolution_rollbacks(placeholder TEXT)"
                )
                connection.commit()
            finally:
                connection.close()
            manager = MigrationManager(path)

            with self.assertRaises(sqlite3.OperationalError):
                manager.apply_pending()

            self.assertEqual(manager.status().current_version, 16)
            connection = sqlite3.connect(path)
            try:
                count = connection.execute(
                    """
                    SELECT COUNT(*) FROM sqlite_master
                    WHERE type = 'table' AND name = 'skill_evolution_runs'
                    """
                ).fetchone()[0]
                self.assertEqual(count, 0)
            finally:
                connection.close()

    def test_failed_v18_migration_rolls_back_merger_tables(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "acr.db"
            create_v2_database(path)
            connection = sqlite3.connect(path)
            try:
                for migration in (
                    MigrationManager._apply_migration_3,
                    MigrationManager._apply_migration_4,
                    MigrationManager._apply_migration_5,
                    MigrationManager._apply_migration_6,
                    MigrationManager._apply_migration_7,
                    MigrationManager._apply_migration_8,
                    MigrationManager._apply_migration_9,
                    MigrationManager._apply_migration_10,
                    MigrationManager._apply_migration_11,
                    MigrationManager._apply_migration_12,
                    MigrationManager._apply_migration_13,
                    MigrationManager._apply_migration_14,
                    MigrationManager._apply_migration_15,
                    MigrationManager._apply_migration_16,
                    MigrationManager._apply_migration_17,
                ):
                    migration(connection)
                connection.execute(
                    """
                    CREATE TABLE skill_merge_analysis_pairs(
                        placeholder TEXT
                    )
                    """
                )
                connection.commit()
            finally:
                connection.close()
            manager = MigrationManager(path)

            with self.assertRaises(sqlite3.OperationalError):
                manager.apply_pending()

            self.assertEqual(manager.status().current_version, 17)
            connection = sqlite3.connect(path)
            try:
                count = connection.execute(
                    """
                    SELECT COUNT(*) FROM sqlite_master
                    WHERE type = 'table'
                      AND name = 'skill_merge_analysis_runs'
                    """
                ).fetchone()[0]
                self.assertEqual(count, 0)
            finally:
                connection.close()

    def test_failed_v19_migration_rolls_back_genome_tables(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "acr.db"
            create_v2_database(path)
            connection = sqlite3.connect(path)
            try:
                for migration in (
                    MigrationManager._apply_migration_3,
                    MigrationManager._apply_migration_4,
                    MigrationManager._apply_migration_5,
                    MigrationManager._apply_migration_6,
                    MigrationManager._apply_migration_7,
                    MigrationManager._apply_migration_8,
                    MigrationManager._apply_migration_9,
                    MigrationManager._apply_migration_10,
                    MigrationManager._apply_migration_11,
                    MigrationManager._apply_migration_12,
                    MigrationManager._apply_migration_13,
                    MigrationManager._apply_migration_14,
                    MigrationManager._apply_migration_15,
                    MigrationManager._apply_migration_16,
                    MigrationManager._apply_migration_17,
                    MigrationManager._apply_migration_18,
                ):
                    migration(connection)
                connection.execute(
                    """
                    CREATE TABLE skill_genome_tournaments(
                        placeholder TEXT
                    )
                    """
                )
                connection.commit()
            finally:
                connection.close()
            manager = MigrationManager(path)

            with self.assertRaises(sqlite3.OperationalError):
                manager.apply_pending()

            self.assertEqual(manager.status().current_version, 18)
            connection = sqlite3.connect(path)
            try:
                count = connection.execute(
                    """
                    SELECT COUNT(*) FROM sqlite_master
                    WHERE type = 'table' AND name = 'skill_genomes'
                    """
                ).fetchone()[0]
                self.assertEqual(count, 0)
            finally:
                connection.close()

    def test_failed_v20_migration_rolls_back_agent_specs(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "acr.db"
            create_v2_database(path)
            connection = sqlite3.connect(path)
            try:
                for migration in (
                    MigrationManager._apply_migration_3,
                    MigrationManager._apply_migration_4,
                    MigrationManager._apply_migration_5,
                    MigrationManager._apply_migration_6,
                    MigrationManager._apply_migration_7,
                    MigrationManager._apply_migration_8,
                    MigrationManager._apply_migration_9,
                    MigrationManager._apply_migration_10,
                    MigrationManager._apply_migration_11,
                    MigrationManager._apply_migration_12,
                    MigrationManager._apply_migration_13,
                    MigrationManager._apply_migration_14,
                    MigrationManager._apply_migration_15,
                    MigrationManager._apply_migration_16,
                    MigrationManager._apply_migration_17,
                    MigrationManager._apply_migration_18,
                    MigrationManager._apply_migration_19,
                ):
                    migration(connection)
                connection.execute(
                    "CREATE INDEX agent_specs_role ON skills(id)"
                )
                connection.commit()
            finally:
                connection.close()
            manager = MigrationManager(path)

            with self.assertRaises(sqlite3.OperationalError):
                manager.apply_pending()

            self.assertEqual(manager.status().current_version, 19)
            connection = sqlite3.connect(path)
            try:
                count = connection.execute(
                    """
                    SELECT COUNT(*) FROM sqlite_master
                    WHERE type = 'table' AND name = 'agent_specs'
                    """
                ).fetchone()[0]
                self.assertEqual(count, 0)
            finally:
                connection.close()

    def test_failed_v21_migration_rolls_back_agent_factory(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "acr.db"
            create_v2_database(path)
            connection = sqlite3.connect(path)
            try:
                for migration in (
                    MigrationManager._apply_migration_3,
                    MigrationManager._apply_migration_4,
                    MigrationManager._apply_migration_5,
                    MigrationManager._apply_migration_6,
                    MigrationManager._apply_migration_7,
                    MigrationManager._apply_migration_8,
                    MigrationManager._apply_migration_9,
                    MigrationManager._apply_migration_10,
                    MigrationManager._apply_migration_11,
                    MigrationManager._apply_migration_12,
                    MigrationManager._apply_migration_13,
                    MigrationManager._apply_migration_14,
                    MigrationManager._apply_migration_15,
                    MigrationManager._apply_migration_16,
                    MigrationManager._apply_migration_17,
                    MigrationManager._apply_migration_18,
                    MigrationManager._apply_migration_19,
                    MigrationManager._apply_migration_20,
                ):
                    migration(connection)
                connection.execute(
                    "CREATE INDEX agent_factory_plans_topology ON skills(id)"
                )
                connection.commit()
            finally:
                connection.close()
            manager = MigrationManager(path)

            with self.assertRaises(sqlite3.OperationalError):
                manager.apply_pending()

            self.assertEqual(manager.status().current_version, 20)
            connection = sqlite3.connect(path)
            try:
                count = connection.execute(
                    """
                    SELECT COUNT(*) FROM sqlite_master
                    WHERE type = 'table' AND name = 'agent_factory_plans'
                    """
                ).fetchone()[0]
                self.assertEqual(count, 0)
            finally:
                connection.close()

    def test_failed_v22_migration_rolls_back_topology_learning(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "acr.db"
            create_v2_database(path)
            connection = sqlite3.connect(path)
            try:
                for migration in (
                    MigrationManager._apply_migration_3,
                    MigrationManager._apply_migration_4,
                    MigrationManager._apply_migration_5,
                    MigrationManager._apply_migration_6,
                    MigrationManager._apply_migration_7,
                    MigrationManager._apply_migration_8,
                    MigrationManager._apply_migration_9,
                    MigrationManager._apply_migration_10,
                    MigrationManager._apply_migration_11,
                    MigrationManager._apply_migration_12,
                    MigrationManager._apply_migration_13,
                    MigrationManager._apply_migration_14,
                    MigrationManager._apply_migration_15,
                    MigrationManager._apply_migration_16,
                    MigrationManager._apply_migration_17,
                    MigrationManager._apply_migration_18,
                    MigrationManager._apply_migration_19,
                    MigrationManager._apply_migration_20,
                    MigrationManager._apply_migration_21,
                ):
                    migration(connection)
                connection.execute(
                    "CREATE INDEX agent_topology_recipes_task ON skills(id)"
                )
                connection.commit()
            finally:
                connection.close()
            manager = MigrationManager(path)

            with self.assertRaises(sqlite3.OperationalError):
                manager.apply_pending()

            self.assertEqual(manager.status().current_version, 21)
            connection = sqlite3.connect(path)
            try:
                count = connection.execute(
                    """
                    SELECT COUNT(*) FROM sqlite_master
                    WHERE type = 'table' AND name = 'agent_topology_recipes'
                    """
                ).fetchone()[0]
                self.assertEqual(count, 0)
            finally:
                connection.close()

    def test_failed_v23_migration_rolls_back_hierarchical_plans(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "acr.db"
            create_v2_database(path)
            connection = sqlite3.connect(path)
            try:
                for migration in (
                    MigrationManager._apply_migration_3,
                    MigrationManager._apply_migration_4,
                    MigrationManager._apply_migration_5,
                    MigrationManager._apply_migration_6,
                    MigrationManager._apply_migration_7,
                    MigrationManager._apply_migration_8,
                    MigrationManager._apply_migration_9,
                    MigrationManager._apply_migration_10,
                    MigrationManager._apply_migration_11,
                    MigrationManager._apply_migration_12,
                    MigrationManager._apply_migration_13,
                    MigrationManager._apply_migration_14,
                    MigrationManager._apply_migration_15,
                    MigrationManager._apply_migration_16,
                    MigrationManager._apply_migration_17,
                    MigrationManager._apply_migration_18,
                    MigrationManager._apply_migration_19,
                    MigrationManager._apply_migration_20,
                    MigrationManager._apply_migration_21,
                    MigrationManager._apply_migration_22,
                ):
                    migration(connection)
                connection.execute(
                    "CREATE INDEX hierarchical_plans_status ON skills(id)"
                )
                connection.commit()
            finally:
                connection.close()
            manager = MigrationManager(path)

            with self.assertRaises(sqlite3.OperationalError):
                manager.apply_pending()

            self.assertEqual(manager.status().current_version, 22)
            connection = sqlite3.connect(path)
            try:
                count = connection.execute(
                    """
                    SELECT COUNT(*) FROM sqlite_master
                    WHERE type = 'table' AND name = 'hierarchical_plans'
                    """
                ).fetchone()[0]
                self.assertEqual(count, 0)
            finally:
                connection.close()

    def test_failed_v24_migration_rolls_back_evaluation_tables(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "acr.db"
            RuntimeDB(path).close()
            connection = sqlite3.connect(path)
            try:
                connection.execute("PRAGMA foreign_keys = OFF")
                connection.execute("DROP TABLE reflection_findings")
                connection.execute("DROP TABLE reflection_runs")
                connection.execute("DROP TABLE evaluation_criterion_results")
                connection.execute("DROP TABLE evaluation_judge_results")
                connection.execute("DROP TABLE evaluation_runs")
                connection.execute(
                    "DELETE FROM schema_migrations WHERE version >= 24"
                )
                connection.execute(
                    "CREATE INDEX evaluation_runs_created ON tasks(created_at)"
                )
                connection.commit()
            finally:
                connection.close()

            manager = MigrationManager(path)
            with self.assertRaises(sqlite3.OperationalError):
                manager.apply_pending()

            self.assertEqual(manager.status().current_version, 23)
            connection = sqlite3.connect(path)
            try:
                count = connection.execute(
                    """
                    SELECT COUNT(*) FROM sqlite_master
                    WHERE type = 'table' AND name = 'evaluation_runs'
                    """
                ).fetchone()[0]
                self.assertEqual(count, 0)
            finally:
                connection.close()

    def test_failed_v25_migration_rolls_back_reflection_tables(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "acr.db"
            RuntimeDB(path).close()
            connection = sqlite3.connect(path)
            try:
                connection.execute("PRAGMA foreign_keys = OFF")
                connection.execute("DROP TABLE learning_regressions")
                connection.execute("DROP TABLE learning_routing_improvements")
                connection.execute("DROP TABLE learning_memory_candidates")
                connection.execute("DROP TABLE learning_stage_results")
                connection.execute("DROP TABLE learning_runs")
                drop_tool_exposure_schema(connection)
                connection.execute("DROP TABLE tool_definitions")
                connection.execute("DROP TABLE reflection_findings")
                connection.execute("DROP TABLE reflection_runs")
                connection.execute(
                    "DELETE FROM schema_migrations WHERE version >= 25"
                )
                connection.execute(
                    "CREATE INDEX reflection_runs_task ON tasks(created_at)"
                )
                connection.commit()
            finally:
                connection.close()

            manager = MigrationManager(path)
            with self.assertRaises(sqlite3.OperationalError):
                manager.apply_pending()

            self.assertEqual(manager.status().current_version, 24)
            connection = sqlite3.connect(path)
            try:
                count = connection.execute(
                    """
                    SELECT COUNT(*) FROM sqlite_master
                    WHERE type = 'table' AND name = 'reflection_runs'
                    """
                ).fetchone()[0]
                self.assertEqual(count, 0)
            finally:
                connection.close()

    def test_failed_v26_migration_rolls_back_learning_tables(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "acr.db"
            RuntimeDB(path).close()
            connection = sqlite3.connect(path)
            try:
                connection.execute("PRAGMA foreign_keys = OFF")
                drop_tool_exposure_schema(connection)
                connection.execute("DROP TABLE tool_definitions")
                connection.execute("DROP TABLE learning_regressions")
                connection.execute("DROP TABLE learning_routing_improvements")
                connection.execute("DROP TABLE learning_memory_candidates")
                connection.execute("DROP TABLE learning_stage_results")
                connection.execute("DROP TABLE learning_runs")
                connection.execute("DROP TABLE local_route_policies")
                connection.execute("DROP TABLE local_benchmark_runs")
                connection.execute("DROP TABLE local_model_discoveries")
                connection.execute("DROP TABLE model_route_attempts")
                connection.execute("DROP TABLE model_routes")
                connection.execute("DROP TABLE model_outcomes")
                connection.execute("DROP TABLE model_profiles")
                connection.execute(
                    "DELETE FROM schema_migrations WHERE version >= 26"
                )
                connection.execute(
                    "CREATE INDEX learning_runs_task ON tasks(created_at)"
                )
                connection.commit()
            finally:
                connection.close()

            manager = MigrationManager(path)
            with self.assertRaises(sqlite3.OperationalError):
                manager.apply_pending()

            self.assertEqual(manager.status().current_version, 25)
            connection = sqlite3.connect(path)
            try:
                count = connection.execute(
                    """
                    SELECT COUNT(*) FROM sqlite_master
                    WHERE type = 'table' AND name = 'learning_runs'
                    """
                ).fetchone()[0]
                self.assertEqual(count, 0)
            finally:
                connection.close()

    def test_failed_v27_migration_rolls_back_model_router_tables(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "acr.db"
            RuntimeDB(path).close()
            connection = sqlite3.connect(path)
            try:
                connection.execute("PRAGMA foreign_keys = OFF")
                drop_tool_exposure_schema(connection)
                connection.execute("DROP TABLE tool_definitions")
                connection.execute("DROP TABLE local_route_policies")
                connection.execute("DROP TABLE local_benchmark_runs")
                connection.execute("DROP TABLE local_model_discoveries")
                connection.execute("DROP TABLE model_route_attempts")
                connection.execute("DROP TABLE model_routes")
                connection.execute("DROP TABLE model_outcomes")
                connection.execute("DROP TABLE model_profiles")
                connection.execute(
                    "DELETE FROM schema_migrations WHERE version >= 27"
                )
                connection.execute(
                    "CREATE INDEX model_outcomes_lookup ON tasks(created_at)"
                )
                connection.commit()
            finally:
                connection.close()

            manager = MigrationManager(path)
            with self.assertRaises(sqlite3.OperationalError):
                manager.apply_pending()

            self.assertEqual(manager.status().current_version, 26)
            connection = sqlite3.connect(path)
            try:
                count = connection.execute(
                    """
                    SELECT COUNT(*) FROM sqlite_master
                    WHERE type = 'table' AND name = 'model_profiles'
                    """
                ).fetchone()[0]
                self.assertEqual(count, 0)
            finally:
                connection.close()

    def test_failed_v28_migration_rolls_back_local_router_tables(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "acr.db"
            RuntimeDB(path).close()
            connection = sqlite3.connect(path)
            try:
                connection.execute("PRAGMA foreign_keys = OFF")
                drop_tool_exposure_schema(connection)
                connection.execute("DROP TABLE tool_definitions")
                connection.execute("DROP TABLE local_route_policies")
                connection.execute("DROP TABLE local_benchmark_runs")
                connection.execute("DROP TABLE local_model_discoveries")
                connection.execute(
                    "DELETE FROM schema_migrations WHERE version >= 28"
                )
                connection.execute(
                    "ALTER TABLE model_profiles DROP COLUMN local"
                )
                connection.execute(
                    "CREATE INDEX local_benchmark_runs_model ON tasks(created_at)"
                )
                connection.commit()
            finally:
                connection.close()

            manager = MigrationManager(path)
            with self.assertRaises(sqlite3.OperationalError):
                manager.apply_pending()

            self.assertEqual(manager.status().current_version, 27)
            connection = sqlite3.connect(path)
            try:
                count = connection.execute(
                    """
                    SELECT COUNT(*) FROM sqlite_master
                    WHERE type = 'table' AND name = 'local_model_discoveries'
                    """
                ).fetchone()[0]
                self.assertEqual(count, 0)
            finally:
                connection.close()

    def test_failed_v29_migration_rolls_back_tool_registry(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "acr.db"
            RuntimeDB(path).close()
            connection = sqlite3.connect(path)
            try:
                connection.execute("PRAGMA foreign_keys = OFF")
                drop_tool_exposure_schema(connection)
                connection.execute("DROP TABLE tool_outcomes")
                connection.execute("DROP TABLE tool_route_candidates")
                connection.execute("DROP TABLE tool_routes")
                connection.execute("DROP TABLE tool_definitions")
                connection.execute(
                    "DELETE FROM schema_migrations WHERE version >= 29"
                )
                connection.execute(
                    "CREATE INDEX tool_definitions_side_effect ON tasks(created_at)"
                )
                connection.commit()
            finally:
                connection.close()

            manager = MigrationManager(path)
            with self.assertRaises(sqlite3.OperationalError):
                manager.apply_pending()

            self.assertEqual(manager.status().current_version, 28)
            connection = sqlite3.connect(path)
            try:
                count = connection.execute(
                    """
                    SELECT COUNT(*) FROM sqlite_master
                    WHERE type = 'table' AND name = 'tool_definitions'
                    """
                ).fetchone()[0]
                self.assertEqual(count, 0)
            finally:
                connection.close()

    def test_failed_v30_migration_rolls_back_tool_router_tables(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "acr.db"
            RuntimeDB(path).close()
            connection = sqlite3.connect(path)
            try:
                connection.execute("PRAGMA foreign_keys = OFF")
                connection.execute("DROP TABLE capability_decisions")
                connection.execute("DROP TABLE capability_grants")
                connection.execute("DROP TABLE tool_outcomes")
                connection.execute("DROP TABLE tool_route_candidates")
                connection.execute("DROP TABLE tool_routes")
                connection.execute(
                    "DELETE FROM schema_migrations WHERE version >= 30"
                )
                connection.execute(
                    "CREATE INDEX tool_outcomes_history ON tasks(created_at)"
                )
                connection.commit()
            finally:
                connection.close()

            manager = MigrationManager(path)
            with self.assertRaises(sqlite3.OperationalError):
                manager.apply_pending()

            self.assertEqual(manager.status().current_version, 29)
            connection = sqlite3.connect(path)
            try:
                count = connection.execute(
                    """
                    SELECT COUNT(*) FROM sqlite_master
                    WHERE type = 'table' AND name = 'tool_routes'
                    """
                ).fetchone()[0]
                self.assertEqual(count, 0)
            finally:
                connection.close()

    def test_failed_v31_migration_rolls_back_permission_tables(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "acr.db"
            RuntimeDB(path).close()
            connection = sqlite3.connect(path)
            try:
                connection.execute("PRAGMA foreign_keys = OFF")
                connection.execute("DROP TABLE trusted_workflow_approvals")
                connection.execute("DROP TABLE content_security_assessments")
                connection.execute("DROP TABLE capability_decisions")
                connection.execute("DROP TABLE capability_grants")
                connection.execute(
                    "DELETE FROM schema_migrations WHERE version >= 31"
                )
                connection.execute(
                    "CREATE INDEX capability_grants_active ON tasks(created_at)"
                )
                connection.commit()
            finally:
                connection.close()

            manager = MigrationManager(path)
            with self.assertRaises(sqlite3.OperationalError):
                manager.apply_pending()

            self.assertEqual(manager.status().current_version, 30)
            connection = sqlite3.connect(path)
            try:
                count = connection.execute(
                    """
                    SELECT COUNT(*) FROM sqlite_master
                    WHERE type = 'table' AND name = 'capability_grants'
                    """
                ).fetchone()[0]
                self.assertEqual(count, 0)
            finally:
                connection.close()

    def test_failed_v32_migration_rolls_back_content_security_tables(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "acr.db"
            RuntimeDB(path).close()
            connection = sqlite3.connect(path)
            try:
                connection.execute("PRAGMA foreign_keys = OFF")
                connection.execute("DROP TABLE secret_access_events")
                connection.execute("DROP TABLE trusted_workflow_approvals")
                connection.execute("DROP TABLE content_security_assessments")
                connection.execute(
                    "DELETE FROM schema_migrations WHERE version >= 32"
                )
                connection.execute(
                    "CREATE INDEX content_security_source ON tasks(created_at)"
                )
                connection.commit()
            finally:
                connection.close()

            manager = MigrationManager(path)
            with self.assertRaises(sqlite3.OperationalError):
                manager.apply_pending()

            self.assertEqual(manager.status().current_version, 31)
            connection = sqlite3.connect(path)
            try:
                count = connection.execute(
                    """
                    SELECT COUNT(*) FROM sqlite_master
                    WHERE type = 'table'
                      AND name = 'content_security_assessments'
                    """
                ).fetchone()[0]
                self.assertEqual(count, 0)
            finally:
                connection.close()

    def test_failed_v33_migration_rolls_back_secret_audit_table(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "acr.db"
            RuntimeDB(path).close()
            connection = sqlite3.connect(path)
            try:
                connection.execute("PRAGMA foreign_keys = OFF")
                connection.execute("DROP TABLE memory_deletion_requests")
                connection.execute("DROP TABLE privacy_decisions")
                connection.execute("DROP TABLE privacy_policy_events")
                connection.execute("DROP TABLE privacy_policies")
                connection.execute("DROP TABLE secret_access_events")
                connection.execute(
                    "DELETE FROM schema_migrations WHERE version >= 33"
                )
                connection.execute(
                    "CREATE INDEX secret_access_subject ON tasks(created_at)"
                )
                connection.commit()
            finally:
                connection.close()

            manager = MigrationManager(path)
            with self.assertRaises(sqlite3.OperationalError):
                manager.apply_pending()

            self.assertEqual(manager.status().current_version, 32)
            connection = sqlite3.connect(path)
            try:
                count = connection.execute(
                    """
                    SELECT COUNT(*) FROM sqlite_master
                    WHERE type = 'table'
                      AND name = 'secret_access_events'
                    """
                ).fetchone()[0]
                self.assertEqual(count, 0)
            finally:
                connection.close()

    def test_failed_v34_migration_rolls_back_privacy_tables(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "acr.db"
            RuntimeDB(path).close()
            connection = sqlite3.connect(path)
            try:
                connection.execute("PRAGMA foreign_keys = OFF")
                connection.execute("DROP TABLE experiment_outcomes")
                connection.execute("DROP TABLE experiment_assignments")
                connection.execute("DROP TABLE runtime_experiments")
                connection.execute("DROP TABLE memory_deletion_requests")
                connection.execute("DROP TABLE privacy_decisions")
                connection.execute("DROP TABLE privacy_policy_events")
                connection.execute("DROP TABLE privacy_policies")
                connection.execute(
                    "DELETE FROM schema_migrations WHERE version >= 34"
                )
                connection.commit()
            finally:
                connection.close()

            manager = MigrationManager(path)
            with self.assertRaises(sqlite3.OperationalError):
                manager.apply_pending()

            self.assertEqual(manager.status().current_version, 33)
            connection = sqlite3.connect(path)
            try:
                count = connection.execute(
                    """
                    SELECT COUNT(*) FROM sqlite_master
                    WHERE type = 'table' AND name = 'privacy_policies'
                    """
                ).fetchone()[0]
                self.assertEqual(count, 0)
            finally:
                connection.close()

    def test_failed_v35_migration_rolls_back_experiment_tables(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "acr.db"
            RuntimeDB(path).close()
            connection = sqlite3.connect(path)
            try:
                connection.execute("PRAGMA foreign_keys = OFF")
                connection.execute("DROP TABLE experiment_outcomes")
                connection.execute("DROP TABLE experiment_assignments")
                connection.execute("DROP TABLE runtime_experiments")
                connection.execute(
                    "DELETE FROM schema_migrations WHERE version >= 35"
                )
                connection.execute(
                    "CREATE INDEX experiment_assignments_variant ON tasks(created_at)"
                )
                connection.commit()
            finally:
                connection.close()

            manager = MigrationManager(path)
            with self.assertRaises(sqlite3.OperationalError):
                manager.apply_pending()

            self.assertEqual(manager.status().current_version, 34)
            connection = sqlite3.connect(path)
            try:
                count = connection.execute(
                    """
                    SELECT COUNT(*) FROM sqlite_master
                    WHERE type='table' AND name='runtime_experiments'
                    """
                ).fetchone()[0]
                self.assertEqual(count, 0)
            finally:
                connection.close()

    def test_failed_v36_migration_rolls_back_regression_tables(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "acr.db"
            RuntimeDB(path).close()
            connection = sqlite3.connect(path)
            try:
                connection.execute("PRAGMA foreign_keys = OFF")
                connection.execute("DROP TABLE rollback_recommendations")
                connection.execute("DROP TABLE regression_alerts")
                connection.execute("DROP TABLE regression_metrics")
                connection.execute("DROP TABLE regression_changes")
                connection.execute("DROP TABLE regression_runs")
                connection.execute(
                    "DELETE FROM schema_migrations WHERE version >= 36"
                )
                connection.execute(
                    "CREATE INDEX regression_runs_scope ON tasks(created_at)"
                )
                connection.commit()
            finally:
                connection.close()

            manager = MigrationManager(path)
            with self.assertRaises(sqlite3.OperationalError):
                manager.apply_pending()

            self.assertEqual(manager.status().current_version, 35)
            connection = sqlite3.connect(path)
            try:
                count = connection.execute(
                    """
                    SELECT COUNT(*) FROM sqlite_master
                    WHERE type='table' AND name='regression_runs'
                    """
                ).fetchone()[0]
                self.assertEqual(count, 0)
            finally:
                connection.close()

    def test_failed_v37_migration_rolls_back_skill_benchmark_tables(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "acr.db"
            RuntimeDB(path).close()
            connection = sqlite3.connect(path)
            try:
                connection.execute("PRAGMA foreign_keys = OFF")
                connection.execute("DROP TABLE skill_lab_actions")
                connection.execute("DROP TABLE skill_benchmark_recommendations")
                connection.execute("DROP TABLE skill_benchmark_trials")
                connection.execute("DROP TABLE skill_benchmark_runs")
                connection.execute(
                    "DELETE FROM schema_migrations WHERE version >= 37"
                )
                connection.execute(
                    "CREATE INDEX skill_benchmark_runs_skill ON tasks(created_at)"
                )
                connection.commit()
            finally:
                connection.close()

            manager = MigrationManager(path)
            with self.assertRaises(sqlite3.OperationalError):
                manager.apply_pending()

            self.assertEqual(manager.status().current_version, 36)
            connection = sqlite3.connect(path)
            try:
                count = connection.execute(
                    """
                    SELECT COUNT(*) FROM sqlite_master
                    WHERE type='table' AND name='skill_benchmark_runs'
                    """
                ).fetchone()[0]
                self.assertEqual(count, 0)
            finally:
                connection.close()

    def test_failed_v38_migration_rolls_back_skill_lab_actions(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "acr.db"
            RuntimeDB(path).close()
            connection = sqlite3.connect(path)
            try:
                connection.execute("DROP TABLE skill_lab_actions")
                connection.execute(
                    "DELETE FROM schema_migrations WHERE version >= 38"
                )
                connection.execute(
                    "CREATE INDEX skill_lab_actions_target ON tasks(created_at)"
                )
                connection.commit()
            finally:
                connection.close()

            manager = MigrationManager(path)
            with self.assertRaises(sqlite3.OperationalError):
                manager.apply_pending()

            self.assertEqual(manager.status().current_version, 37)
            connection = sqlite3.connect(path)
            try:
                count = connection.execute(
                    """
                    SELECT COUNT(*) FROM sqlite_master
                    WHERE type='table' AND name='skill_lab_actions'
                    """
                ).fetchone()[0]
                self.assertEqual(count, 0)
            finally:
                connection.close()

    def test_failed_v39_migration_rolls_back_code_index_tables(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "acr.db"
            RuntimeDB(path).close()
            connection = sqlite3.connect(path)
            try:
                connection.execute("PRAGMA foreign_keys = OFF")
                for table in (
                    "document_indexes",
                    "document_relationships",
                    "document_chunks",
                    "document_sections",
                    "document_headings",
                    "documents",
                ):
                    connection.execute(f"DROP TABLE {table}")
                for table in (
                    "code_dependencies",
                    "code_references",
                    "code_imports",
                    "code_symbols",
                    "code_files",
                    "code_index_runs",
                    "code_repositories",
                ):
                    connection.execute(f"DROP TABLE {table}")
                connection.execute(
                    "DELETE FROM schema_migrations WHERE version >= 39"
                )
                connection.execute(
                    "CREATE INDEX code_symbols_name ON tasks(created_at)"
                )
                connection.commit()
            finally:
                connection.close()

            manager = MigrationManager(path)
            with self.assertRaises(sqlite3.OperationalError):
                manager.apply_pending()

            self.assertEqual(manager.status().current_version, 38)
            connection = sqlite3.connect(path)
            try:
                names = {
                    row[0]
                    for row in connection.execute(
                        """
                        SELECT name FROM sqlite_master
                        WHERE type = 'table' AND name LIKE 'code_%'
                        """
                    )
                }
                self.assertEqual(names, set())
            finally:
                connection.close()

    def test_failed_v40_migration_rolls_back_document_tables(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "acr.db"
            RuntimeDB(path).close()
            connection = sqlite3.connect(path)
            try:
                connection.execute("PRAGMA foreign_keys = OFF")
                for table in (
                    "document_indexes",
                    "document_relationships",
                    "document_chunks",
                    "document_sections",
                    "document_headings",
                    "documents",
                ):
                    connection.execute(f"DROP TABLE {table}")
                connection.execute(
                    "DELETE FROM schema_migrations WHERE version >= 40"
                )
                connection.execute(
                    "CREATE INDEX documents_title ON tasks(created_at)"
                )
                connection.commit()
            finally:
                connection.close()

            manager = MigrationManager(path)
            with self.assertRaises(sqlite3.OperationalError):
                manager.apply_pending()

            self.assertEqual(manager.status().current_version, 39)
            connection = sqlite3.connect(path)
            try:
                names = {
                    row[0]
                    for row in connection.execute(
                        """
                        SELECT name FROM sqlite_master
                        WHERE type='table' AND name LIKE 'document_%'
                        """
                    )
                }
                self.assertEqual(names, set())
            finally:
                connection.close()

    def test_failed_v41_migration_rolls_back_memory_scope_table(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "acr.db"
            RuntimeDB(path).close()
            connection = sqlite3.connect(path)
            try:
                connection.execute("DROP TABLE memory_scopes")
                connection.execute(
                    "DELETE FROM schema_migrations WHERE version >= 41"
                )
                connection.execute(
                    "CREATE INDEX memory_scopes_parent ON tasks(created_at)"
                )
                connection.commit()
            finally:
                connection.close()

            manager = MigrationManager(path)
            with self.assertRaises(sqlite3.OperationalError):
                manager.apply_pending()

            self.assertEqual(manager.status().current_version, 40)
            connection = sqlite3.connect(path)
            try:
                table_count = connection.execute(
                    """
                    SELECT COUNT(*) FROM sqlite_master
                    WHERE type='table' AND name='memory_scopes'
                    """
                ).fetchone()[0]
                self.assertEqual(table_count, 0)
            finally:
                connection.close()

    def test_failed_v42_migration_rolls_back_multi_model_schema(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "acr.db"
            RuntimeDB(path).close()
            connection = sqlite3.connect(path)
            try:
                for table in (
                    "multi_model_outcomes",
                    "multi_model_stages",
                    "multi_model_workflows",
                ):
                    connection.execute(f"DROP TABLE {table}")
                connection.execute("ALTER TABLE model_profiles DROP COLUMN tier")
                connection.execute(
                    "DELETE FROM schema_migrations WHERE version >= 42"
                )
                connection.execute(
                    "CREATE INDEX multi_model_workflows_class ON tasks(created_at)"
                )
                connection.commit()
            finally:
                connection.close()

            manager = MigrationManager(path)
            with self.assertRaises(sqlite3.OperationalError):
                manager.apply_pending()

            self.assertEqual(manager.status().current_version, 41)
            connection = sqlite3.connect(path)
            try:
                tables = {
                    row[0]
                    for row in connection.execute(
                        """
                        SELECT name FROM sqlite_master
                        WHERE type='table' AND name LIKE 'multi_model_%'
                        """
                    )
                }
                columns = {
                    row[1]
                    for row in connection.execute(
                        "PRAGMA table_info(model_profiles)"
                    )
                }
                self.assertEqual(tables, set())
                self.assertNotIn("tier", columns)
            finally:
                connection.close()

    def test_failed_v43_migration_rolls_back_calibration_schema(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "acr.db"
            RuntimeDB(path).close()
            connection = sqlite3.connect(path)
            try:
                connection.execute("DROP TABLE confidence_predictions")
                connection.execute(
                    "DELETE FROM schema_migrations WHERE version >= 43"
                )
                connection.execute(
                    """
                    CREATE INDEX confidence_predictions_curve
                    ON tasks(created_at)
                    """
                )
                connection.commit()
            finally:
                connection.close()

            manager = MigrationManager(path)
            with self.assertRaises(sqlite3.OperationalError):
                manager.apply_pending()

            self.assertEqual(manager.status().current_version, 42)
            connection = sqlite3.connect(path)
            try:
                table_count = connection.execute(
                    """
                    SELECT COUNT(*) FROM sqlite_master
                    WHERE type='table' AND name='confidence_predictions'
                    """
                ).fetchone()[0]
                self.assertEqual(table_count, 0)
            finally:
                connection.close()

    def test_failed_v44_migration_rolls_back_resource_governor(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "acr.db"
            RuntimeDB(path).close()
            connection = sqlite3.connect(path)
            try:
                drop_meta_context_schema(connection)
                for trigger in (
                    "improvement_policy_versions_no_update",
                    "improvement_policy_versions_no_delete",
                    "improvement_policy_events_no_update",
                    "improvement_policy_events_no_delete",
                ):
                    connection.execute(f"DROP TRIGGER {trigger}")
                for table in (
                    "task_policy_attributions",
                    "improvement_benchmark_results",
                    "improvement_runs",
                    "improvement_authorizations",
                    "improvement_policy_heads",
                    "improvement_policy_events",
                    "improvement_policy_versions",
                ):
                    connection.execute(f"DROP TABLE {table}")
                for trigger in (
                    "cache_invalidate_memories_insert",
                    "cache_invalidate_memories_update",
                    "cache_invalidate_memories_delete",
                    "cache_invalidate_scopes_insert",
                    "cache_invalidate_scopes_update",
                    "cache_invalidate_scopes_delete",
                    "cache_invalidate_privacy_update",
                    "cache_invalidate_privacy_insert",
                    "cache_invalidate_privacy_delete",
                ):
                    connection.execute(f"DROP TRIGGER {trigger}")
                for table in (
                    "cache_events",
                    "cache_entries",
                    "cache_generations",
                ):
                    connection.execute(f"DROP TABLE {table}")
                for table in (
                    "task_resource_reservations",
                    "task_resource_escalations",
                    "task_resource_usage",
                    "task_resource_budgets",
                ):
                    connection.execute(f"DROP TABLE {table}")
                connection.execute(
                    "DELETE FROM schema_migrations WHERE version >= 44"
                )
                connection.execute(
                    """
                    CREATE INDEX task_resource_reservations_task
                    ON tasks(created_at)
                    """
                )
                connection.commit()
            finally:
                connection.close()

            manager = MigrationManager(path)
            with self.assertRaises(sqlite3.OperationalError):
                manager.apply_pending()

            self.assertEqual(manager.status().current_version, 43)
            connection = sqlite3.connect(path)
            try:
                table_count = connection.execute(
                    """
                    SELECT COUNT(*) FROM sqlite_master
                    WHERE type='table' AND name LIKE 'task_resource_%'
                    """
                ).fetchone()[0]
                self.assertEqual(table_count, 0)
            finally:
                connection.close()

    def test_failed_v45_migration_rolls_back_safe_cache(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "acr.db"
            RuntimeDB(path).close()
            connection = sqlite3.connect(path)
            try:
                drop_meta_context_schema(connection)
                for trigger in (
                    "improvement_policy_versions_no_update",
                    "improvement_policy_versions_no_delete",
                    "improvement_policy_events_no_update",
                    "improvement_policy_events_no_delete",
                ):
                    connection.execute(f"DROP TRIGGER {trigger}")
                for table in (
                    "task_policy_attributions",
                    "improvement_benchmark_results",
                    "improvement_runs",
                    "improvement_authorizations",
                    "improvement_policy_heads",
                    "improvement_policy_events",
                    "improvement_policy_versions",
                ):
                    connection.execute(f"DROP TABLE {table}")
                for trigger in (
                    "deduplication_runs_seal_only",
                    "deduplication_runs_no_delete",
                    "deduplication_items_unsealed_insert",
                    "deduplication_items_no_update",
                    "deduplication_items_no_delete",
                    "deduplication_matches_unsealed_insert",
                    "deduplication_matches_no_update",
                    "deduplication_matches_no_delete",
                ):
                    connection.execute(f"DROP TRIGGER {trigger}")
                for table in (
                    "deduplication_matches",
                    "deduplication_items",
                    "deduplication_runs",
                ):
                    connection.execute(f"DROP TABLE {table}")
                for trigger in (
                    "cache_invalidate_memories_insert",
                    "cache_invalidate_memories_update",
                    "cache_invalidate_memories_delete",
                    "cache_invalidate_scopes_insert",
                    "cache_invalidate_scopes_update",
                    "cache_invalidate_scopes_delete",
                    "cache_invalidate_privacy_update",
                    "cache_invalidate_privacy_insert",
                    "cache_invalidate_privacy_delete",
                ):
                    connection.execute(f"DROP TRIGGER {trigger}")
                for table in (
                    "cache_events",
                    "cache_entries",
                    "cache_generations",
                ):
                    connection.execute(f"DROP TABLE {table}")
                connection.execute(
                    "DELETE FROM schema_migrations WHERE version >= 45"
                )
                connection.execute(
                    "CREATE INDEX cache_entries_expiry ON tasks(created_at)"
                )
                connection.commit()
            finally:
                connection.close()

            manager = MigrationManager(path)
            with self.assertRaises(sqlite3.OperationalError):
                manager.apply_pending()

            self.assertEqual(manager.status().current_version, 44)
            connection = sqlite3.connect(path)
            try:
                migration_count = connection.execute(
                    """
                    SELECT COUNT(*) FROM schema_migrations
                    WHERE version = 45
                    """
                ).fetchone()[0]
                self.assertEqual(migration_count, 0)
                table_count = connection.execute(
                    """
                    SELECT COUNT(*) FROM sqlite_master
                    WHERE type='table' AND name LIKE 'cache_%'
                    """
                ).fetchone()[0]
                self.assertEqual(table_count, 0)
                conflict_count = connection.execute(
                    """
                    SELECT COUNT(*) FROM sqlite_master
                    WHERE type='index' AND name='cache_entries_expiry'
                    """
                ).fetchone()[0]
                self.assertEqual(conflict_count, 1)
                connection.execute("DROP INDEX cache_entries_expiry")
                connection.commit()
            finally:
                connection.close()

            self.assertEqual(manager.apply_pending().current_version, EXPECTED_SCHEMA_VERSION)
            with RuntimeDB(path) as upgraded:
                self.assertEqual(upgraded.health()["quick_check"], "ok")

    def test_failed_v47_migration_rolls_back_improvement_schema(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "acr.db"
            RuntimeDB(path).close()
            connection = sqlite3.connect(path)
            try:
                drop_meta_context_schema(connection)
                for trigger in (
                    "improvement_policy_versions_no_update",
                    "improvement_policy_versions_no_delete",
                    "improvement_policy_events_no_update",
                    "improvement_policy_events_no_delete",
                ):
                    connection.execute(f"DROP TRIGGER {trigger}")
                for table in (
                    "task_policy_attributions",
                    "improvement_benchmark_results",
                    "improvement_runs",
                    "improvement_authorizations",
                    "improvement_policy_heads",
                    "improvement_policy_events",
                    "improvement_policy_versions",
                ):
                    connection.execute(f"DROP TABLE {table}")
                connection.execute(
                    "DELETE FROM schema_migrations WHERE version >= 47"
                )
                connection.execute(
                    "CREATE INDEX improvement_versions_target ON tasks(created_at)"
                )
                connection.commit()
            finally:
                connection.close()

            manager = MigrationManager(path)
            with self.assertRaises(sqlite3.OperationalError):
                manager.apply_pending()
            self.assertEqual(manager.status().current_version, 46)

            connection = sqlite3.connect(path)
            try:
                tables = connection.execute(
                    """
                    SELECT COUNT(*) FROM sqlite_master
                    WHERE type = 'table'
                      AND (
                        name LIKE 'improvement_%'
                        OR name = 'task_policy_attributions'
                      )
                    """
                ).fetchone()[0]
                self.assertEqual(tables, 0)
                connection.execute("DROP INDEX improvement_versions_target")
                connection.commit()
            finally:
                connection.close()

            self.assertEqual(manager.apply_pending().current_version, EXPECTED_SCHEMA_VERSION)
            with RuntimeDB(path) as upgraded:
                self.assertEqual(upgraded.health()["quick_check"], "ok")

    def test_v46_run_seals_only_after_counts_and_rejects_late_children(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "acr.db"
            with RuntimeDB(path) as database:
                connection = database.connection
                connection.execute(
                    """
                    INSERT INTO deduplication_runs(
                        id, algorithm_version, scope_hash, kinds_json,
                        policy_json, item_count, match_count, created_at
                    ) VALUES (
                        'run-1', 'dedup-v1', NULL, '["memory"]', '{}',
                        3, 1, '2026-01-01T00:00:00Z'
                    )
                    """
                )
                item_sql = """
                    INSERT INTO deduplication_items(
                        id, run_id, kind, source_id, source_version,
                        content_hash, evidence_json, provenance_json, created_at
                    ) VALUES (
                        ?, 'run-1', 'memory', ?, '', ?, '{}', '[]',
                        '2026-01-01T00:00:00Z'
                    )
                """
                connection.execute(item_sql, ("item-a", "memory-a", "a" * 64))
                connection.execute(item_sql, ("item-b", "memory-b", "b" * 64))

                with self.assertRaises(sqlite3.IntegrityError):
                    connection.execute(
                        "UPDATE deduplication_runs SET sealed=1 WHERE id='run-1'"
                    )

                connection.execute(item_sql, ("item-c", "memory-c", "c" * 64))
                connection.execute(
                    """
                    INSERT INTO deduplication_matches(
                        id, run_id, left_item_id, right_item_id, relation,
                        recommendation, score, method_id, method_version,
                        evidence_json, provenance_json, created_at
                    ) VALUES (
                        'match-1', 'run-1', 'item-a', 'item-b',
                        'exact_duplicate', 'REFERENCE', 1.0,
                        'canonical-hash', '1', '{}', '[]',
                        '2026-01-01T00:00:00Z'
                    )
                    """
                )
                connection.execute(
                    "UPDATE deduplication_runs SET sealed=1 WHERE id='run-1'"
                )
                self.assertEqual(
                    connection.execute(
                        "SELECT sealed FROM deduplication_runs WHERE id='run-1'"
                    ).fetchone()[0],
                    1,
                )

                with self.assertRaises(sqlite3.IntegrityError):
                    connection.execute(
                        item_sql, ("item-d", "memory-d", "d" * 64)
                    )
                with self.assertRaises(sqlite3.IntegrityError):
                    connection.execute(
                        """
                        INSERT INTO deduplication_matches(
                            id, run_id, left_item_id, right_item_id, relation,
                            recommendation, score, method_id, method_version,
                            evidence_json, provenance_json, created_at
                        ) VALUES (
                            'match-2', 'run-1', 'item-b', 'item-c',
                            'near_duplicate', 'KEEP_SEPARATE', 0.5,
                            'lexical', '1', '{}', '[]',
                            '2026-01-01T00:00:00Z'
                        )
                        """
                    )

    def test_failed_v46_migration_rolls_back_deduplication_schema(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "acr.db"
            RuntimeDB(path).close()
            connection = sqlite3.connect(path)
            try:
                drop_meta_context_schema(connection)
                for trigger in (
                    "improvement_policy_versions_no_update",
                    "improvement_policy_versions_no_delete",
                    "improvement_policy_events_no_update",
                    "improvement_policy_events_no_delete",
                ):
                    connection.execute(f"DROP TRIGGER {trigger}")
                for table in (
                    "task_policy_attributions",
                    "improvement_benchmark_results",
                    "improvement_runs",
                    "improvement_authorizations",
                    "improvement_policy_heads",
                    "improvement_policy_events",
                    "improvement_policy_versions",
                ):
                    connection.execute(f"DROP TABLE {table}")
                for trigger in (
                    "deduplication_runs_seal_only",
                    "deduplication_runs_no_delete",
                    "deduplication_items_unsealed_insert",
                    "deduplication_items_no_update",
                    "deduplication_items_no_delete",
                    "deduplication_matches_unsealed_insert",
                    "deduplication_matches_no_update",
                    "deduplication_matches_no_delete",
                ):
                    connection.execute(f"DROP TRIGGER {trigger}")
                for table in (
                    "deduplication_matches",
                    "deduplication_items",
                    "deduplication_runs",
                ):
                    connection.execute(f"DROP TABLE {table}")
                connection.execute(
                    "DELETE FROM schema_migrations WHERE version >= 46"
                )
                connection.execute(
                    """
                    CREATE INDEX deduplication_matches_run
                    ON tasks(created_at)
                    """
                )
                connection.commit()
            finally:
                connection.close()

            manager = MigrationManager(path)
            with self.assertRaises(sqlite3.OperationalError):
                manager.apply_pending()

            self.assertEqual(manager.status().current_version, 45)
            connection = sqlite3.connect(path)
            try:
                migration_count = connection.execute(
                    """
                    SELECT COUNT(*) FROM schema_migrations
                    WHERE version = 46
                    """
                ).fetchone()[0]
                self.assertEqual(migration_count, 0)
                table_count = connection.execute(
                    """
                    SELECT COUNT(*) FROM sqlite_master
                    WHERE type='table' AND name LIKE 'deduplication_%'
                    """
                ).fetchone()[0]
                self.assertEqual(table_count, 0)
                trigger_count = connection.execute(
                    """
                    SELECT COUNT(*) FROM sqlite_master
                    WHERE type='trigger' AND name LIKE 'deduplication_%'
                    """
                ).fetchone()[0]
                self.assertEqual(trigger_count, 0)
                conflict_count = connection.execute(
                    """
                    SELECT COUNT(*) FROM sqlite_master
                    WHERE type='index' AND name='deduplication_matches_run'
                    """
                ).fetchone()[0]
                self.assertEqual(conflict_count, 1)
                connection.execute("DROP INDEX deduplication_matches_run")
                connection.commit()
            finally:
                connection.close()

            self.assertEqual(manager.apply_pending().current_version, EXPECTED_SCHEMA_VERSION)
            with RuntimeDB(path) as upgraded:
                self.assertEqual(upgraded.health()["quick_check"], "ok")

    def test_failed_v48_migration_rolls_back_meta_context_schema(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "acr.db"
            RuntimeDB(path).close()
            connection = sqlite3.connect(path)
            try:
                drop_meta_context_schema(connection)
                connection.execute(
                    "DELETE FROM schema_migrations WHERE version >= 48"
                )
                connection.execute(
                    "CREATE INDEX meta_context_runs_created ON tasks(created_at)"
                )
                connection.commit()
            finally:
                connection.close()

            manager = MigrationManager(path)
            with self.assertRaises(sqlite3.OperationalError):
                manager.apply_pending()
            self.assertEqual(manager.status().current_version, 47)
            connection = sqlite3.connect(path)
            try:
                tables = connection.execute(
                    """
                    SELECT COUNT(*) FROM sqlite_master
                    WHERE type = 'table' AND name LIKE 'meta_context_%'
                    """
                ).fetchone()[0]
                self.assertEqual(tables, 0)
                connection.execute("DROP INDEX meta_context_runs_created")
                connection.commit()
            finally:
                connection.close()

            self.assertEqual(manager.apply_pending().current_version, EXPECTED_SCHEMA_VERSION)
            with RuntimeDB(path) as upgraded:
                self.assertEqual(upgraded.health()["quick_check"], "ok")

    def test_failed_v49_migration_rolls_back_coevolution_schema(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "acr.db"
            RuntimeDB(path).close()
            connection = sqlite3.connect(path)
            try:
                drop_skill_coevolution_schema(connection)
                connection.execute(
                    "DELETE FROM schema_migrations WHERE version >= 49"
                )
                connection.execute(
                    "CREATE INDEX skill_support_links_skill ON tasks(created_at)"
                )
                connection.commit()
            finally:
                connection.close()

            manager = MigrationManager(path)
            with self.assertRaises(sqlite3.OperationalError):
                manager.apply_pending()
            self.assertEqual(manager.status().current_version, 48)
            connection = sqlite3.connect(path)
            try:
                tables = connection.execute(
                    """
                    SELECT COUNT(*) FROM sqlite_master
                    WHERE type = 'table'
                      AND (
                        name LIKE 'skill_support_%'
                        OR name LIKE 'skill_reliability_%'
                        OR name = 'skill_coevolution_events'
                      )
                    """
                ).fetchone()[0]
                self.assertEqual(tables, 0)
                connection.execute("DROP INDEX skill_support_links_skill")
                connection.commit()
            finally:
                connection.close()

            self.assertEqual(manager.apply_pending().current_version, EXPECTED_SCHEMA_VERSION)
            with RuntimeDB(path) as upgraded:
                self.assertEqual(upgraded.health()["quick_check"], "ok")

    def test_failed_v50_migration_rolls_back_utility_schema(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "acr.db"
            RuntimeDB(path).close()
            connection = sqlite3.connect(path)
            try:
                drop_utility_governance_schema(connection)
                connection.execute(
                    "DELETE FROM schema_migrations WHERE version >= 50"
                )
                connection.execute(
                    "CREATE INDEX utility_assets_kind ON tasks(created_at)"
                )
                connection.commit()
            finally:
                connection.close()

            manager = MigrationManager(path)
            with self.assertRaises(sqlite3.OperationalError):
                manager.apply_pending()
            self.assertEqual(manager.status().current_version, 49)
            connection = sqlite3.connect(path)
            try:
                tables = connection.execute(
                    """
                    SELECT COUNT(*) FROM sqlite_master
                    WHERE type = 'table'
                      AND (
                        name LIKE 'utility_%'
                        OR name = 'context_strategy_uses'
                      )
                    """
                ).fetchone()[0]
                self.assertEqual(tables, 0)
                connection.execute("DROP INDEX utility_assets_kind")
                connection.commit()
            finally:
                connection.close()

            self.assertEqual(manager.apply_pending().current_version, EXPECTED_SCHEMA_VERSION)
            with RuntimeDB(path) as upgraded:
                self.assertEqual(upgraded.health()["quick_check"], "ok")

    def test_failed_v51_migration_rolls_back_cost_accounting_schema(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "acr.db"
            RuntimeDB(path).close()
            connection = sqlite3.connect(path)
            try:
                drop_cost_accounting_schema(connection)
                connection.execute(
                    "DELETE FROM schema_migrations WHERE version >= 51"
                )
                connection.execute(
                    "CREATE INDEX price_rates_lookup ON tasks(created_at)"
                )
                connection.commit()
            finally:
                connection.close()

            manager = MigrationManager(path)
            with self.assertRaises(sqlite3.OperationalError):
                manager.apply_pending()
            self.assertEqual(manager.status().current_version, 50)
            connection = sqlite3.connect(path)
            try:
                tables = connection.execute(
                    """
                    SELECT COUNT(*) FROM sqlite_master
                    WHERE type='table'
                      AND name IN (
                        'price_rates', 'local_cost_profiles', 'cost_events',
                        'cost_meter_lines', 'cost_skill_allocations'
                      )
                    """
                ).fetchone()[0]
                self.assertEqual(tables, 0)
                connection.execute("DROP INDEX price_rates_lookup")
                connection.commit()
            finally:
                connection.close()

            self.assertEqual(manager.apply_pending().current_version, EXPECTED_SCHEMA_VERSION)
            with RuntimeDB(path) as upgraded:
                self.assertEqual(upgraded.health()["quick_check"], "ok")

    def test_failed_v52_migration_rolls_back_token_waste_schema(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "acr.db"
            RuntimeDB(path).close()
            connection = sqlite3.connect(path)
            try:
                drop_token_waste_schema(connection)
                connection.execute(
                    "DELETE FROM schema_migrations WHERE version >= 52"
                )
                connection.execute(
                    """
                    CREATE INDEX token_waste_findings_run
                    ON tasks(created_at)
                    """
                )
                connection.commit()
            finally:
                connection.close()

            manager = MigrationManager(path)
            with self.assertRaises(sqlite3.OperationalError):
                manager.apply_pending()
            self.assertEqual(manager.status().current_version, 51)
            connection = sqlite3.connect(path)
            try:
                tables = connection.execute(
                    """
                    SELECT COUNT(*) FROM sqlite_master
                    WHERE type='table' AND name LIKE 'token_waste_%'
                    """
                ).fetchone()[0]
                self.assertEqual(tables, 0)
                connection.execute("DROP INDEX token_waste_findings_run")
                connection.commit()
            finally:
                connection.close()

            self.assertEqual(manager.apply_pending().current_version, EXPECTED_SCHEMA_VERSION)
            with RuntimeDB(path) as upgraded:
                self.assertEqual(upgraded.health()["quick_check"], "ok")

    def test_v51_populated_database_upgrades_to_v52_and_scans(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "acr.db"
            RuntimeDB(path).close()
            connection = sqlite3.connect(path)
            try:
                drop_token_waste_schema(connection)
                connection.execute(
                    "DELETE FROM schema_migrations WHERE version >= 52"
                )
                connection.execute(
                    """
                    INSERT INTO tasks(
                        id, objective, scope, token_budget, selected_tokens,
                        status, created_at, completed_at
                    ) VALUES (
                        'legacy-v51-task', 'legacy objective', 'global',
                        1000, 600, 'succeeded',
                        '2026-07-28T00:00:00Z',
                        '2026-07-28T00:01:00Z'
                    )
                    """
                )
                connection.commit()
            finally:
                connection.close()

            manager = MigrationManager(path)
            self.assertEqual(manager.status().current_version, 51)
            self.assertEqual(manager.apply_pending().current_version, EXPECTED_SCHEMA_VERSION)
            with RuntimeDB(path) as upgraded:
                from acr_runtime.token_waste import TokenWasteAnalyzer

                report = TokenWasteAnalyzer(upgraded.connection).scan()
                self.assertEqual(len(report.findings), 9)
                self.assertEqual(upgraded.health()["quick_check"], "ok")

    def test_v52_populated_database_upgrades_to_v53(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "acr.db"
            RuntimeDB(path).close()
            connection = sqlite3.connect(path)
            try:
                drop_tool_exposure_schema(connection)
                connection.execute(
                    "DELETE FROM schema_migrations WHERE version >= 53"
                )
                connection.execute(
                    """
                    INSERT INTO tool_definitions(
                        name, description, input_schema_json, output_schema_json,
                        permissions_json, cost, latency_estimate_ms, side_effect,
                        network_access, filesystem_access,
                        credential_requirements_json, definition_hash, created_at
                    ) VALUES (
                        'legacy.tool', 'retained canonical definition',
                        ?, ?,
                        '[]', 0, 1, 'READ_ONLY', 0, 'NONE', '[]',
                        ?, '2026-07-28T00:00:00Z'
                    )
                    """,
                    (
                        json.dumps({
                            "type": "object", "properties": {},
                            "required": [], "additionalProperties": False,
                        }),
                        json.dumps({
                            "type": "object", "properties": {},
                            "required": [], "additionalProperties": False,
                        }),
                        "a" * 64,
                    ),
                )
                connection.commit()
            finally:
                connection.close()

            manager = MigrationManager(path)
            self.assertEqual(manager.status().current_version, 52)
            self.assertEqual(manager.apply_pending().current_version, EXPECTED_SCHEMA_VERSION)
            self.assertIsNotNone(manager.last_backup_path)
            with RuntimeDB(path) as upgraded:
                self.assertEqual(
                    upgraded.connection.execute(
                        "SELECT COUNT(*) FROM tool_definitions"
                    ).fetchone()[0],
                    1,
                )
                self.assertEqual(upgraded.health()["quick_check"], "ok")

    def test_failed_v53_migration_rolls_back_tool_exposure_schema(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "acr.db"
            RuntimeDB(path).close()
            connection = sqlite3.connect(path)
            try:
                drop_tool_exposure_schema(connection)
                connection.execute(
                    "DELETE FROM schema_migrations WHERE version >= 53"
                )
                connection.execute(
                    "CREATE INDEX tool_exposure_projections_task "
                    "ON tasks(created_at)"
                )
                connection.commit()
            finally:
                connection.close()

            manager = MigrationManager(path)
            with self.assertRaises(sqlite3.OperationalError):
                manager.apply_pending()
            self.assertEqual(manager.status().current_version, 52)
            connection = sqlite3.connect(path)
            try:
                self.assertEqual(
                    connection.execute(
                        """
                        SELECT COUNT(*) FROM sqlite_master
                        WHERE type='table' AND name LIKE 'tool_exposure_%'
                        """
                    ).fetchone()[0],
                    0,
                )
                connection.execute("DROP INDEX tool_exposure_projections_task")
                connection.commit()
            finally:
                connection.close()
            self.assertEqual(
                manager.apply_pending().current_version,
                EXPECTED_SCHEMA_VERSION,
            )

    def test_v53_database_upgrades_to_v54_reasoning_budget_schema(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "acr.db"
            RuntimeDB(path).close()
            connection = sqlite3.connect(path)
            try:
                drop_reasoning_budget_schema(connection)
                connection.execute(
                    "DELETE FROM schema_migrations WHERE version >= 54"
                )
                connection.commit()
            finally:
                connection.close()

            manager = MigrationManager(path)
            self.assertEqual(manager.status().current_version, 53)
            self.assertEqual(
                manager.apply_pending().current_version,
                EXPECTED_SCHEMA_VERSION,
            )
            self.assertIsNotNone(manager.last_backup_path)
            with RuntimeDB(path) as upgraded:
                from acr_runtime.reasoning_depth import ReasoningDepthEngine

                policy = ReasoningDepthEngine(upgraded.connection).policy()
                self.assertEqual(policy["status"], "active")
                self.assertEqual(upgraded.health()["quick_check"], "ok")

    def test_failed_v54_migration_rolls_back_reasoning_budget_schema(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "acr.db"
            RuntimeDB(path).close()
            connection = sqlite3.connect(path)
            try:
                drop_reasoning_budget_schema(connection)
                connection.execute(
                    "DELETE FROM schema_migrations WHERE version >= 54"
                )
                connection.execute(
                    "CREATE INDEX reasoning_budget_one_active "
                    "ON tasks(created_at)"
                )
                connection.commit()
            finally:
                connection.close()

            manager = MigrationManager(path)
            with self.assertRaises(sqlite3.OperationalError):
                manager.apply_pending()
            self.assertEqual(manager.status().current_version, 53)
            connection = sqlite3.connect(path)
            try:
                tables = connection.execute(
                    """
                    SELECT COUNT(*) FROM sqlite_master
                    WHERE type='table' AND name LIKE 'reasoning_budget_%'
                    """
                ).fetchone()[0]
                self.assertEqual(tables, 0)
                connection.execute("DROP INDEX reasoning_budget_one_active")
                connection.commit()
            finally:
                connection.close()
            self.assertEqual(
                manager.apply_pending().current_version,
                EXPECTED_SCHEMA_VERSION,
            )

    def test_v54_database_upgrades_to_v55_parallel_research_schema(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "acr.db"
            RuntimeDB(path).close()
            connection = sqlite3.connect(path)
            try:
                drop_parallel_research_schema(connection)
                connection.execute(
                    "DELETE FROM schema_migrations WHERE version >= 55"
                )
                connection.commit()
            finally:
                connection.close()

            manager = MigrationManager(path)
            self.assertEqual(manager.status().current_version, 54)
            self.assertEqual(
                manager.apply_pending().current_version,
                EXPECTED_SCHEMA_VERSION,
            )
            self.assertIsNotNone(manager.last_backup_path)
            with RuntimeDB(path) as upgraded:
                tables = upgraded.connection.execute(
                    """
                    SELECT COUNT(*) FROM sqlite_master
                    WHERE type='table' AND name LIKE 'research_%'
                    """
                ).fetchone()[0]
                self.assertEqual(tables, 5)
                self.assertEqual(upgraded.health()["quick_check"], "ok")

    def test_failed_v55_migration_rolls_back_parallel_research_schema(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "acr.db"
            RuntimeDB(path).close()
            connection = sqlite3.connect(path)
            try:
                drop_parallel_research_schema(connection)
                connection.execute(
                    "DELETE FROM schema_migrations WHERE version >= 55"
                )
                connection.execute(
                    "CREATE INDEX research_runs_plan ON tasks(created_at)"
                )
                connection.commit()
            finally:
                connection.close()

            manager = MigrationManager(path)
            with self.assertRaises(sqlite3.OperationalError):
                manager.apply_pending()
            self.assertEqual(manager.status().current_version, 54)
            connection = sqlite3.connect(path)
            try:
                tables = connection.execute(
                    """
                    SELECT COUNT(*) FROM sqlite_master
                    WHERE type='table' AND name LIKE 'research_%'
                    """
                ).fetchone()[0]
                self.assertEqual(tables, 0)
                connection.execute("DROP INDEX research_runs_plan")
                connection.commit()
            finally:
                connection.close()
            self.assertEqual(
                manager.apply_pending().current_version,
                EXPECTED_SCHEMA_VERSION,
            )

    def test_v55_database_upgrades_to_v56_evidence_graph_schema(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "acr.db"
            RuntimeDB(path).close()
            connection = sqlite3.connect(path)
            try:
                drop_evidence_graph_schema(connection)
                connection.execute(
                    "DELETE FROM schema_migrations WHERE version >= 56"
                )
                connection.commit()
            finally:
                connection.close()
            manager = MigrationManager(path)
            self.assertEqual(manager.status().current_version, 55)
            self.assertEqual(
                manager.apply_pending().current_version,
                EXPECTED_SCHEMA_VERSION,
            )
            self.assertIsNotNone(manager.last_backup_path)
            with RuntimeDB(path) as upgraded:
                tables = upgraded.connection.execute(
                    """
                    SELECT COUNT(*) FROM sqlite_master
                    WHERE type='table' AND name LIKE 'evidence_graph_%'
                    """
                ).fetchone()[0]
                self.assertEqual(tables, 5)
                self.assertEqual(upgraded.health()["quick_check"], "ok")

    def test_failed_v56_migration_rolls_back_evidence_graph_schema(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "acr.db"
            RuntimeDB(path).close()
            connection = sqlite3.connect(path)
            try:
                drop_evidence_graph_schema(connection)
                connection.execute(
                    "DELETE FROM schema_migrations WHERE version >= 56"
                )
                connection.execute(
                    "CREATE INDEX evidence_graph_edges_from ON tasks(created_at)"
                )
                connection.commit()
            finally:
                connection.close()
            manager = MigrationManager(path)
            with self.assertRaises(sqlite3.OperationalError):
                manager.apply_pending()
            self.assertEqual(manager.status().current_version, 55)
            connection = sqlite3.connect(path)
            try:
                count = connection.execute(
                    """
                    SELECT COUNT(*) FROM sqlite_master
                    WHERE type='table' AND name LIKE 'evidence_graph_%'
                    """
                ).fetchone()[0]
                self.assertEqual(count, 0)
                connection.execute("DROP INDEX evidence_graph_edges_from")
                connection.commit()
            finally:
                connection.close()
            self.assertEqual(
                manager.apply_pending().current_version,
                EXPECTED_SCHEMA_VERSION,
            )

    def test_v56_database_upgrades_to_v57_human_override_schema(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "acr.db"
            RuntimeDB(path).close()
            connection = sqlite3.connect(path)
            try:
                drop_human_override_schema(connection)
                connection.execute(
                    "DELETE FROM schema_migrations WHERE version >= 57"
                )
                connection.commit()
            finally:
                connection.close()
            manager = MigrationManager(path)
            self.assertEqual(manager.status().current_version, 56)
            self.assertEqual(
                manager.apply_pending().current_version,
                EXPECTED_SCHEMA_VERSION,
            )
            self.assertIsNotNone(manager.last_backup_path)
            with RuntimeDB(path) as upgraded:
                tables = upgraded.connection.execute(
                    """
                    SELECT COUNT(*) FROM sqlite_master
                    WHERE type='table' AND name LIKE 'human_override%'
                    """
                ).fetchone()[0]
                self.assertEqual(tables, 2)
                self.assertEqual(upgraded.health()["quick_check"], "ok")

    def test_failed_v57_migration_rolls_back_human_override_schema(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "acr.db"
            RuntimeDB(path).close()
            connection = sqlite3.connect(path)
            try:
                drop_human_override_schema(connection)
                connection.execute(
                    "DELETE FROM schema_migrations WHERE version >= 57"
                )
                connection.execute(
                    "CREATE INDEX human_overrides_action_scope "
                    "ON tasks(created_at)"
                )
                connection.commit()
            finally:
                connection.close()
            manager = MigrationManager(path)
            with self.assertRaises(sqlite3.OperationalError):
                manager.apply_pending()
            self.assertEqual(manager.status().current_version, 56)
            connection = sqlite3.connect(path)
            try:
                count = connection.execute(
                    """
                    SELECT COUNT(*) FROM sqlite_master
                    WHERE type='table' AND name LIKE 'human_override%'
                    """
                ).fetchone()[0]
                self.assertEqual(count, 0)
                connection.execute("DROP INDEX human_overrides_action_scope")
                connection.commit()
            finally:
                connection.close()
            self.assertEqual(
                manager.apply_pending().current_version,
                EXPECTED_SCHEMA_VERSION,
            )

    def test_v57_database_upgrades_to_v58_safe_mode_schema(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "acr.db"
            RuntimeDB(path).close()
            connection = sqlite3.connect(path)
            try:
                drop_safe_mode_schema(connection)
                connection.execute(
                    "DELETE FROM schema_migrations WHERE version = 58"
                )
                connection.commit()
            finally:
                connection.close()
            manager = MigrationManager(path)
            self.assertEqual(manager.status().current_version, 57)
            self.assertEqual(
                manager.apply_pending().current_version,
                EXPECTED_SCHEMA_VERSION,
            )
            self.assertIsNotNone(manager.last_backup_path)
            with RuntimeDB(path) as upgraded:
                tables = upgraded.connection.execute(
                    """
                    SELECT COUNT(*) FROM sqlite_master
                    WHERE type='table' AND name LIKE 'safe_mode_%'
                    """
                ).fetchone()[0]
                self.assertEqual(tables, 2)
                state = upgraded.connection.execute(
                    "SELECT enabled FROM safe_mode_state WHERE id=1"
                ).fetchone()
                self.assertEqual(state[0], 0)
                self.assertEqual(upgraded.health()["quick_check"], "ok")

    def test_failed_v58_migration_rolls_back_safe_mode_schema(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "acr.db"
            RuntimeDB(path).close()
            connection = sqlite3.connect(path)
            try:
                drop_safe_mode_schema(connection)
                connection.execute(
                    "DELETE FROM schema_migrations WHERE version = 58"
                )
                connection.execute(
                    "CREATE TABLE safe_mode_events (placeholder TEXT)"
                )
                connection.commit()
            finally:
                connection.close()
            manager = MigrationManager(path)
            with self.assertRaises(sqlite3.OperationalError):
                manager.apply_pending()
            self.assertEqual(manager.status().current_version, 57)
            connection = sqlite3.connect(path)
            try:
                state_tables = connection.execute(
                    """
                    SELECT COUNT(*) FROM sqlite_master
                    WHERE type='table' AND name='safe_mode_state'
                    """
                ).fetchone()[0]
                self.assertEqual(state_tables, 0)
                connection.execute("DROP TABLE safe_mode_events")
                connection.commit()
            finally:
                connection.close()
            self.assertEqual(
                manager.apply_pending().current_version,
                EXPECTED_SCHEMA_VERSION,
            )

    def test_fresh_database_records_and_validates_schema_fingerprint(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "acr.db"
            with RuntimeDB(path) as runtime:
                row = runtime.connection.execute(
                    """
                    SELECT schema_hash FROM schema_migrations
                    WHERE version=?
                    """,
                    (EXPECTED_SCHEMA_VERSION,),
                ).fetchone()
                self.assertIsNotNone(row)
                self.assertEqual(len(row[0]), 64)
                self.assertEqual(row[0], schema_fingerprint(runtime.connection))
                self.assertTrue(runtime.health()["schema_fingerprint_valid"])
            self.assertEqual(MigrationManager(path).validate_schema(), row[0])

    def test_v58_database_upgrades_to_v59_schema_fingerprint(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "acr.db"
            RuntimeDB(path).close()
            connection = sqlite3.connect(path)
            try:
                drop_migration_integrity_schema(connection)
                connection.commit()
            finally:
                connection.close()
            manager = MigrationManager(path)
            self.assertEqual(manager.status().current_version, 58)
            status = manager.apply_pending()
            self.assertEqual(status.current_version, EXPECTED_SCHEMA_VERSION)
            self.assertIsNotNone(manager.last_backup_path)
            self.assertTrue(manager.last_backup_path.exists())
            with RuntimeDB(path) as upgraded:
                self.assertEqual(
                    manager.validate_schema(),
                    schema_fingerprint(upgraded.connection),
                )
                self.assertEqual(upgraded.health()["quick_check"], "ok")

    def test_same_version_schema_drift_fails_closed_without_repair(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "acr.db"
            RuntimeDB(path).close()
            connection = sqlite3.connect(path)
            try:
                connection.execute(
                    "CREATE TABLE unauthorized_schema_change (id INTEGER)"
                )
                connection.commit()
            finally:
                connection.close()
            with self.assertRaisesRegex(
                SchemaDriftDetected, "No automatic repair was attempted"
            ):
                RuntimeDB(path)
            with self.assertRaises(SchemaDriftDetected):
                MigrationManager(path).apply_pending()
            connection = sqlite3.connect(path)
            try:
                exists = connection.execute(
                    """
                    SELECT COUNT(*) FROM sqlite_schema
                    WHERE type='table' AND name='unauthorized_schema_change'
                    """
                ).fetchone()[0]
                self.assertEqual(exists, 1)
            finally:
                connection.close()

    def test_failed_v59_migration_rolls_back_version_record(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "acr.db"
            RuntimeDB(path).close()
            connection = sqlite3.connect(path)
            try:
                drop_migration_integrity_schema(connection)
                connection.execute(
                    "ALTER TABLE schema_migrations ADD COLUMN schema_hash TEXT"
                )
                connection.commit()
            finally:
                connection.close()
            manager = MigrationManager(path)
            with self.assertRaises(sqlite3.OperationalError):
                manager.apply_pending()
            self.assertEqual(manager.status().current_version, 58)
            connection = sqlite3.connect(path)
            try:
                row = connection.execute(
                    "SELECT COUNT(*) FROM schema_migrations WHERE version=59"
                ).fetchone()
                self.assertEqual(row[0], 0)
            finally:
                connection.close()

    def test_newer_schema_refuses_implicit_downgrade_without_writes(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "acr.db"
            RuntimeDB(path).close()
            connection = sqlite3.connect(path)
            try:
                before = schema_fingerprint(connection)
                connection.execute(
                    """
                    INSERT INTO schema_migrations(version, applied_at)
                    VALUES (?, '2026-07-29T00:00:00Z')
                    """,
                    (EXPECTED_SCHEMA_VERSION + 1,),
                )
                connection.commit()
            finally:
                connection.close()
            with self.assertRaisesRegex(MigrationRequired, "newer than"):
                RuntimeDB(path)
            with self.assertRaisesRegex(MigrationRequired, "newer than"):
                MigrationManager(path).apply_pending()
            connection = sqlite3.connect(path)
            try:
                self.assertEqual(schema_fingerprint(connection), before)
                self.assertEqual(
                    connection.execute(
                        "SELECT MAX(version) FROM schema_migrations"
                    ).fetchone()[0],
                    EXPECTED_SCHEMA_VERSION + 1,
                )
            finally:
                connection.close()

    def test_v59_database_upgrades_to_v60_plugin_schema(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "acr.db"
            RuntimeDB(path).close()
            connection = sqlite3.connect(path)
            try:
                drop_plugin_schema(connection)
                connection.commit()
            finally:
                connection.close()
            manager = MigrationManager(path)
            self.assertEqual(manager.status().current_version, 59)
            self.assertEqual(
                manager.apply_pending().current_version,
                EXPECTED_SCHEMA_VERSION,
            )
            self.assertIsNotNone(manager.last_backup_path)
            with RuntimeDB(path) as upgraded:
                tables = upgraded.connection.execute(
                    """
                    SELECT COUNT(*) FROM sqlite_schema
                    WHERE type='table' AND name LIKE 'plugin_%'
                    """
                ).fetchone()[0]
                self.assertEqual(tables, 3)
                self.assertTrue(
                    upgraded.health()["schema_fingerprint_valid"]
                )

    def test_failed_v60_migration_rolls_back_partial_plugin_schema(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "acr.db"
            RuntimeDB(path).close()
            connection = sqlite3.connect(path)
            try:
                drop_plugin_schema(connection)
                connection.execute(
                    "CREATE TABLE plugin_manifests (placeholder TEXT)"
                )
                connection.commit()
            finally:
                connection.close()
            manager = MigrationManager(path)
            with self.assertRaises(sqlite3.OperationalError):
                manager.apply_pending()
            self.assertEqual(manager.status().current_version, 59)
            connection = sqlite3.connect(path)
            try:
                created = connection.execute(
                    """
                    SELECT COUNT(*) FROM sqlite_schema
                    WHERE type='table' AND name='plugin_validation_runs'
                    """
                ).fetchone()[0]
                self.assertEqual(created, 0)
                connection.execute("DROP TABLE plugin_manifests")
                connection.commit()
            finally:
                connection.close()
            self.assertEqual(
                manager.apply_pending().current_version,
                EXPECTED_SCHEMA_VERSION,
            )

    def test_v60_database_upgrades_to_v61_failure_recovery_schema(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "acr.db"
            RuntimeDB(path).close()
            connection = sqlite3.connect(path)
            try:
                drop_failure_recovery_schema(connection)
                connection.commit()
            finally:
                connection.close()
            manager = MigrationManager(path)
            self.assertEqual(manager.status().current_version, 60)
            self.assertEqual(
                manager.apply_pending().current_version,
                EXPECTED_SCHEMA_VERSION,
            )
            self.assertIsNotNone(manager.last_backup_path)
            with RuntimeDB(path) as upgraded:
                tables = upgraded.connection.execute(
                    """
                    SELECT COUNT(*) FROM sqlite_schema
                    WHERE type='table' AND name LIKE 'recovery_%'
                    """
                ).fetchone()[0]
                self.assertEqual(tables, 3)
                self.assertTrue(
                    upgraded.health()["schema_fingerprint_valid"]
                )

    def test_failed_v61_migration_rolls_back_partial_recovery_schema(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "acr.db"
            RuntimeDB(path).close()
            connection = sqlite3.connect(path)
            try:
                drop_failure_recovery_schema(connection)
                connection.execute(
                    "CREATE TABLE recovery_steps (placeholder TEXT)"
                )
                connection.commit()
            finally:
                connection.close()
            manager = MigrationManager(path)
            with self.assertRaises(sqlite3.OperationalError):
                manager.apply_pending()
            self.assertEqual(manager.status().current_version, 60)
            connection = sqlite3.connect(path)
            try:
                created = connection.execute(
                    """
                    SELECT COUNT(*) FROM sqlite_schema
                    WHERE type='table' AND name='recovery_runs'
                    """
                ).fetchone()[0]
                self.assertEqual(created, 0)
                connection.execute("DROP TABLE recovery_steps")
                connection.commit()
            finally:
                connection.close()
            self.assertEqual(
                manager.apply_pending().current_version,
                EXPECTED_SCHEMA_VERSION,
            )

    def test_v61_database_upgrades_to_v62_audit_schema(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "acr.db"
            RuntimeDB(path).close()
            connection = sqlite3.connect(path)
            try:
                drop_audit_schema(connection)
                connection.commit()
            finally:
                connection.close()
            manager = MigrationManager(path)
            self.assertEqual(manager.status().current_version, 61)
            self.assertEqual(
                manager.apply_pending().current_version,
                EXPECTED_SCHEMA_VERSION,
            )
            self.assertIsNotNone(manager.last_backup_path)
            with RuntimeDB(path) as upgraded:
                table = upgraded.connection.execute(
                    """
                    SELECT COUNT(*) FROM sqlite_schema
                    WHERE type='table' AND name='audit_events'
                    """
                ).fetchone()[0]
                triggers = upgraded.connection.execute(
                    """
                    SELECT COUNT(*) FROM sqlite_schema
                    WHERE type='trigger' AND name LIKE 'audit_%'
                    """
                ).fetchone()[0]
                self.assertEqual(table, 1)
                self.assertEqual(triggers, 10)
                self.assertTrue(
                    upgraded.health()["schema_fingerprint_valid"]
                )

    def test_failed_v62_migration_rolls_back_partial_audit_schema(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "acr.db"
            RuntimeDB(path).close()
            connection = sqlite3.connect(path)
            try:
                drop_audit_schema(connection)
                connection.execute(
                    "CREATE TABLE audit_events (placeholder TEXT)"
                )
                connection.commit()
            finally:
                connection.close()
            manager = MigrationManager(path)
            with self.assertRaises(sqlite3.OperationalError):
                manager.apply_pending()
            self.assertEqual(manager.status().current_version, 61)
            connection = sqlite3.connect(path)
            try:
                created = connection.execute(
                    """
                    SELECT COUNT(*) FROM sqlite_schema
                    WHERE type='trigger' AND name LIKE 'audit_%'
                    """
                ).fetchone()[0]
                self.assertEqual(created, 0)
                connection.execute("DROP TABLE audit_events")
                connection.commit()
            finally:
                connection.close()
            self.assertEqual(
                manager.apply_pending().current_version,
                EXPECTED_SCHEMA_VERSION,
            )

    def test_v62_database_upgrades_to_v63_performance_profiler_schema(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "acr.db"
            RuntimeDB(path).close()
            connection = sqlite3.connect(path)
            try:
                drop_performance_profiler_schema(connection)
                connection.commit()
            finally:
                connection.close()
            manager = MigrationManager(path)
            self.assertEqual(manager.status().current_version, 62)
            self.assertEqual(
                manager.apply_pending().current_version,
                EXPECTED_SCHEMA_VERSION,
            )
            self.assertIsNotNone(manager.last_backup_path)
            with RuntimeDB(path) as upgraded:
                tables = upgraded.connection.execute(
                    """
                    SELECT COUNT(*) FROM sqlite_schema
                    WHERE type='table' AND name LIKE 'performance_%'
                    """
                ).fetchone()[0]
                triggers = upgraded.connection.execute(
                    """
                    SELECT COUNT(*) FROM sqlite_schema
                    WHERE type='trigger' AND name LIKE 'performance_%'
                    """
                ).fetchone()[0]
                self.assertEqual(tables, 2)
                self.assertEqual(triggers, 4)
                self.assertTrue(
                    upgraded.health()["schema_fingerprint_valid"]
                )

    def test_failed_v63_migration_rolls_back_partial_profiler_schema(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "acr.db"
            RuntimeDB(path).close()
            connection = sqlite3.connect(path)
            try:
                drop_performance_profiler_schema(connection)
                connection.execute(
                    "CREATE TABLE performance_measurements (placeholder TEXT)"
                )
                connection.commit()
            finally:
                connection.close()
            manager = MigrationManager(path)
            with self.assertRaises(sqlite3.OperationalError):
                manager.apply_pending()
            self.assertEqual(manager.status().current_version, 62)
            connection = sqlite3.connect(path)
            try:
                created = connection.execute(
                    """
                    SELECT COUNT(*) FROM sqlite_schema
                    WHERE type='table'
                      AND name='performance_profile_runs'
                    """
                ).fetchone()[0]
                self.assertEqual(created, 0)
                connection.execute("DROP TABLE performance_measurements")
                connection.commit()
            finally:
                connection.close()
            self.assertEqual(
                manager.apply_pending().current_version,
                EXPECTED_SCHEMA_VERSION,
            )

    def test_v63_database_upgrades_to_v64_project_state_schema(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "acr.db"
            RuntimeDB(path).close()
            connection = sqlite3.connect(path)
            try:
                drop_project_state_schema(connection)
                connection.commit()
            finally:
                connection.close()
            manager = MigrationManager(path)
            self.assertEqual(manager.status().current_version, 63)
            self.assertEqual(
                manager.apply_pending().current_version,
                EXPECTED_SCHEMA_VERSION,
            )
            self.assertIsNotNone(manager.last_backup_path)
            with RuntimeDB(path) as upgraded:
                tables = upgraded.connection.execute(
                    """
                    SELECT COUNT(*) FROM sqlite_schema
                    WHERE type='table' AND name LIKE 'project_state%'
                    """
                ).fetchone()[0]
                triggers = upgraded.connection.execute(
                    """
                    SELECT COUNT(*) FROM sqlite_schema
                    WHERE type='trigger' AND name LIKE 'project_state%'
                    """
                ).fetchone()[0]
                self.assertEqual(tables, 4)
                self.assertEqual(triggers, 2)
                self.assertTrue(
                    upgraded.health()["schema_fingerprint_valid"]
                )

    def test_failed_v64_migration_rolls_back_partial_project_state_schema(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "acr.db"
            RuntimeDB(path).close()
            connection = sqlite3.connect(path)
            try:
                drop_project_state_schema(connection)
                connection.execute(
                    "CREATE TABLE project_state_items (placeholder TEXT)"
                )
                connection.commit()
            finally:
                connection.close()
            manager = MigrationManager(path)
            with self.assertRaises(sqlite3.OperationalError):
                manager.apply_pending()
            self.assertEqual(manager.status().current_version, 63)
            connection = sqlite3.connect(path)
            try:
                created = connection.execute(
                    """
                    SELECT COUNT(*) FROM sqlite_schema
                    WHERE type='table' AND name='project_states'
                    """
                ).fetchone()[0]
                self.assertEqual(created, 0)
                connection.execute("DROP TABLE project_state_items")
                connection.commit()
            finally:
                connection.close()
            self.assertEqual(
                manager.apply_pending().current_version,
                EXPECTED_SCHEMA_VERSION,
            )

    def test_v64_database_upgrades_to_v65_procedure_detection_schema(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "acr.db"
            RuntimeDB(path).close()
            connection = sqlite3.connect(path)
            try:
                drop_procedure_detection_schema(connection)
                connection.commit()
            finally:
                connection.close()
            manager = MigrationManager(path)
            self.assertEqual(manager.status().current_version, 64)
            self.assertEqual(
                manager.apply_pending().current_version,
                EXPECTED_SCHEMA_VERSION,
            )
            self.assertIsNotNone(manager.last_backup_path)
            with RuntimeDB(path) as upgraded:
                tables = upgraded.connection.execute(
                    """
                    SELECT COUNT(*) FROM sqlite_schema
                    WHERE type='table' AND name LIKE 'procedure_detection%'
                    """
                ).fetchone()[0]
                triggers = upgraded.connection.execute(
                    """
                    SELECT COUNT(*) FROM sqlite_schema
                    WHERE type='trigger' AND name LIKE 'procedure_detection%'
                    """
                ).fetchone()[0]
                self.assertEqual(tables, 2)
                self.assertEqual(triggers, 4)
                self.assertTrue(
                    upgraded.health()["schema_fingerprint_valid"]
                )

    def test_failed_v65_migration_rolls_back_partial_procedure_schema(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "acr.db"
            RuntimeDB(path).close()
            connection = sqlite3.connect(path)
            try:
                drop_procedure_detection_schema(connection)
                connection.execute(
                    "CREATE TABLE procedure_detection_candidates "
                    "(placeholder TEXT)"
                )
                connection.commit()
            finally:
                connection.close()
            manager = MigrationManager(path)
            with self.assertRaises(sqlite3.OperationalError):
                manager.apply_pending()
            self.assertEqual(manager.status().current_version, 64)
            connection = sqlite3.connect(path)
            try:
                created = connection.execute(
                    """
                    SELECT COUNT(*) FROM sqlite_schema
                    WHERE type='table' AND name='procedure_detection_runs'
                    """
                ).fetchone()[0]
                self.assertEqual(created, 0)
                connection.execute(
                    "DROP TABLE procedure_detection_candidates"
                )
                connection.commit()
            finally:
                connection.close()
            self.assertEqual(
                manager.apply_pending().current_version,
                EXPECTED_SCHEMA_VERSION,
            )

    def test_v65_database_upgrades_to_v66_freshness_schema(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "acr.db"
            with RuntimeDB(path) as runtime:
                memory_id = runtime.add_memory(
                    kind="semantic",
                    content="Legacy fact",
                    evidence=("legacy-run",),
                )
            connection = sqlite3.connect(path)
            try:
                drop_freshness_schema(connection)
                connection.commit()
            finally:
                connection.close()
            manager = MigrationManager(path)
            self.assertEqual(manager.status().current_version, 65)
            self.assertEqual(
                manager.apply_pending().current_version,
                EXPECTED_SCHEMA_VERSION,
            )
            self.assertIsNotNone(manager.last_backup_path)
            with RuntimeDB(path) as upgraded:
                record = upgraded.memories.get(memory_id)
                self.assertIsNone(record.observed_at)
                self.assertEqual(record.source_freshness.value, "unknown")
                self.assertIsNone(record.expected_half_life_days)
                self.assertFalse(record.requires_refresh)

    def test_failed_v66_migration_rolls_back_partial_freshness_columns(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "acr.db"
            RuntimeDB(path).close()
            connection = sqlite3.connect(path)
            try:
                drop_freshness_schema(connection)
                connection.execute("ALTER TABLE memories ADD COLUMN observed_at TEXT")
                connection.commit()
            finally:
                connection.close()
            manager = MigrationManager(path)
            with self.assertRaises(sqlite3.OperationalError):
                manager.apply_pending()
            self.assertEqual(manager.status().current_version, 65)
            connection = sqlite3.connect(path)
            try:
                columns = {
                    row[1]
                    for row in connection.execute(
                        "PRAGMA table_info(memories)"
                    ).fetchall()
                }
                self.assertNotIn("source_freshness", columns)
                connection.execute("ALTER TABLE memories DROP COLUMN observed_at")
                connection.commit()
            finally:
                connection.close()
            self.assertEqual(
                manager.apply_pending().current_version,
                EXPECTED_SCHEMA_VERSION,
            )

    def test_v66_database_upgrades_to_v67_source_class_schema(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "acr.db"
            with RuntimeDB(path) as runtime:
                memory_id = runtime.add_memory(
                    kind="semantic",
                    content="Legacy unclassified fact",
                    evidence=("legacy-run",),
                )
            connection = sqlite3.connect(path)
            try:
                drop_source_class_schema(connection)
                connection.commit()
            finally:
                connection.close()
            manager = MigrationManager(path)
            self.assertEqual(manager.status().current_version, 66)
            self.assertEqual(
                manager.apply_pending().current_version,
                EXPECTED_SCHEMA_VERSION,
            )
            self.assertIsNotNone(manager.last_backup_path)
            with RuntimeDB(path) as upgraded:
                self.assertIsNone(upgraded.memories.get(memory_id).source_class)
                with self.assertRaises(sqlite3.IntegrityError):
                    upgraded.connection.execute(
                        "UPDATE memories SET source_class='social_media'"
                    )

    def test_failed_v67_migration_rolls_back_source_class_column(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "acr.db"
            RuntimeDB(path).close()
            connection = sqlite3.connect(path)
            try:
                drop_source_class_schema(connection)
                connection.execute(
                    "ALTER TABLE memories ADD COLUMN source_class TEXT"
                )
                connection.commit()
            finally:
                connection.close()
            manager = MigrationManager(path)
            with self.assertRaises(sqlite3.OperationalError):
                manager.apply_pending()
            self.assertEqual(manager.status().current_version, 66)
            connection = sqlite3.connect(path)
            try:
                columns = {
                    row[1]
                    for row in connection.execute(
                        "PRAGMA table_info(memories)"
                    ).fetchall()
                }
                self.assertIn("source_class", columns)
                connection.execute(
                    "ALTER TABLE memories DROP COLUMN source_class"
                )
                connection.commit()
            finally:
                connection.close()
            self.assertEqual(
                manager.apply_pending().current_version,
                EXPECTED_SCHEMA_VERSION,
            )

    def test_v67_database_upgrades_to_v68_active_learning_schema(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "acr.db"
            RuntimeDB(path).close()
            connection = sqlite3.connect(path)
            try:
                drop_active_learning_schema(connection)
                connection.commit()
            finally:
                connection.close()
            manager = MigrationManager(path)
            self.assertEqual(manager.status().current_version, 67)
            self.assertEqual(
                manager.apply_pending().current_version,
                EXPECTED_SCHEMA_VERSION,
            )
            self.assertIsNotNone(manager.last_backup_path)
            with RuntimeDB(path) as upgraded:
                table = upgraded.connection.execute(
                    """
                    SELECT COUNT(*) FROM sqlite_schema
                    WHERE type='table' AND name='active_learning_runs'
                    """
                ).fetchone()[0]
                self.assertEqual(table, 1)

    def test_failed_v68_migration_rolls_back_active_learning_schema(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "acr.db"
            RuntimeDB(path).close()
            connection = sqlite3.connect(path)
            try:
                drop_active_learning_schema(connection)
                connection.execute(
                    "CREATE TABLE active_learning_runs (placeholder TEXT)"
                )
                connection.commit()
            finally:
                connection.close()
            manager = MigrationManager(path)
            with self.assertRaises(sqlite3.OperationalError):
                manager.apply_pending()
            self.assertEqual(manager.status().current_version, 67)
            connection = sqlite3.connect(path)
            try:
                columns = [
                    row[1]
                    for row in connection.execute(
                        "PRAGMA table_info(active_learning_runs)"
                    ).fetchall()
                ]
                self.assertEqual(columns, ["placeholder"])
                connection.execute("DROP TABLE active_learning_runs")
                connection.commit()
            finally:
                connection.close()
            self.assertEqual(
                manager.apply_pending().current_version,
                EXPECTED_SCHEMA_VERSION,
            )

    def test_v68_database_upgrades_to_v69_task_similarity_schema(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "acr.db"
            with RuntimeDB(path) as runtime:
                task_id = runtime.create_task(
                    objective="Preserve this task",
                    scope="project:runtime",
                    token_budget=100,
                )
            connection = sqlite3.connect(path)
            try:
                drop_task_similarity_schema(connection)
                connection.commit()
            finally:
                connection.close()
            manager = MigrationManager(path)
            self.assertEqual(manager.status().current_version, 68)
            self.assertEqual(
                manager.apply_pending().current_version,
                EXPECTED_SCHEMA_VERSION,
            )
            self.assertIsNotNone(manager.last_backup_path)
            with RuntimeDB(path) as upgraded:
                self.assertIsNotNone(
                    upgraded.connection.execute(
                        "SELECT id FROM tasks WHERE id=?", (task_id,)
                    ).fetchone()
                )
                table = upgraded.connection.execute(
                    """
                    SELECT COUNT(*) FROM sqlite_schema
                    WHERE type='table' AND name='task_feature_profiles'
                    """
                ).fetchone()[0]
                self.assertEqual(table, 1)

    def test_failed_v69_migration_rolls_back_task_similarity_schema(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "acr.db"
            RuntimeDB(path).close()
            connection = sqlite3.connect(path)
            try:
                drop_task_similarity_schema(connection)
                connection.execute(
                    "CREATE TABLE task_feature_profiles (placeholder TEXT)"
                )
                connection.commit()
            finally:
                connection.close()
            manager = MigrationManager(path)
            with self.assertRaises(sqlite3.OperationalError):
                manager.apply_pending()
            self.assertEqual(manager.status().current_version, 68)
            connection = sqlite3.connect(path)
            try:
                columns = [
                    row[1]
                    for row in connection.execute(
                        "PRAGMA table_info(task_feature_profiles)"
                    ).fetchall()
                ]
                self.assertEqual(columns, ["placeholder"])
                connection.execute("DROP TABLE task_feature_profiles")
                connection.commit()
            finally:
                connection.close()
            self.assertEqual(
                manager.apply_pending().current_version,
                EXPECTED_SCHEMA_VERSION,
            )


if __name__ == "__main__":
    unittest.main()
