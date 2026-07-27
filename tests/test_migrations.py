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
            self.assertEqual(status.current_version, 8)
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
            self.assertEqual(second.apply_pending().current_version, 8)
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


if __name__ == "__main__":
    unittest.main()
