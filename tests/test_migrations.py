from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from acr_runtime.db import RuntimeDB
from acr_runtime.migrations import MigrationManager, MigrationRequired


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
            self.assertEqual(status.current_version, 27)
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
            self.assertEqual(second.apply_pending().current_version, 27)
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
                connection.execute("DROP TABLE learning_regressions")
                connection.execute("DROP TABLE learning_routing_improvements")
                connection.execute("DROP TABLE learning_memory_candidates")
                connection.execute("DROP TABLE learning_stage_results")
                connection.execute("DROP TABLE learning_runs")
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
                connection.execute("DROP TABLE model_route_attempts")
                connection.execute("DROP TABLE model_routes")
                connection.execute("DROP TABLE model_outcomes")
                connection.execute("DROP TABLE model_profiles")
                connection.execute(
                    "DELETE FROM schema_migrations WHERE version = 27"
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


if __name__ == "__main__":
    unittest.main()
