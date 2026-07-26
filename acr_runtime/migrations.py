from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

EXPECTED_SCHEMA_VERSION = 2


class MigrationRequired(RuntimeError):
    pass


MIGRATION_2_SQL = """
CREATE TABLE IF NOT EXISTS execution_runs (
    run_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    state TEXT NOT NULL,
    event_count INTEGER NOT NULL,
    step_count INTEGER NOT NULL,
    action_count INTEGER NOT NULL,
    duration_ms INTEGER NOT NULL,
    verification_score REAL,
    evaluation_score REAL,
    failure_kind TEXT,
    started_at TEXT NOT NULL,
    completed_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS telemetry_events (
    id TEXT PRIMARY KEY,
    sequence INTEGER,
    category TEXT NOT NULL,
    event_type TEXT NOT NULL,
    task_id TEXT,
    run_id TEXT,
    step_id TEXT,
    provider TEXT,
    model TEXT,
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    cached_tokens INTEGER NOT NULL DEFAULT 0,
    estimated_cost REAL NOT NULL DEFAULT 0,
    latency_ms INTEGER NOT NULL DEFAULT 0,
    status TEXT,
    context_bundle_id TEXT,
    skills_json TEXT NOT NULL DEFAULT '[]',
    memories_json TEXT NOT NULL DEFAULT '[]',
    payload_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS telemetry_events_task
ON telemetry_events(task_id, created_at);
CREATE INDEX IF NOT EXISTS telemetry_events_model
ON telemetry_events(provider, model, created_at);
"""


@dataclass(frozen=True)
class MigrationStatus:
    current_version: int
    expected_version: int
    pending_versions: tuple[int, ...]


class MigrationManager:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def status(self) -> MigrationStatus:
        if not self.path.exists() or self.path.stat().st_size == 0:
            return MigrationStatus(0, EXPECTED_SCHEMA_VERSION, (1, 2))
        connection = sqlite3.connect(self.path)
        try:
            table = connection.execute(
                """
                SELECT COUNT(*) FROM sqlite_master
                WHERE type = 'table' AND name = 'schema_migrations'
                """
            ).fetchone()[0]
            if not table:
                return MigrationStatus(0, EXPECTED_SCHEMA_VERSION, (1, 2))
            current = int(
                connection.execute(
                    "SELECT COALESCE(MAX(version), 0) FROM schema_migrations"
                ).fetchone()[0]
            )
        finally:
            connection.close()
        return MigrationStatus(
            current_version=current,
            expected_version=EXPECTED_SCHEMA_VERSION,
            pending_versions=tuple(
                range(current + 1, EXPECTED_SCHEMA_VERSION + 1)
            ),
        )

    def apply_pending(self) -> MigrationStatus:
        status = self.status()
        if status.current_version == 0:
            raise MigrationRequired(
                "Unversioned databases require an explicit import or a fresh database"
            )
        if status.current_version > EXPECTED_SCHEMA_VERSION:
            raise MigrationRequired(
                f"Database schema {status.current_version} is newer than "
                f"runtime schema {EXPECTED_SCHEMA_VERSION}"
            )
        connection = sqlite3.connect(self.path)
        try:
            connection.execute("PRAGMA foreign_keys = ON")
            if 2 in status.pending_versions:
                connection.executescript(MIGRATION_2_SQL)
                connection.execute(
                    """
                    INSERT INTO schema_migrations(version, applied_at)
                    VALUES (2, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
                    """
                )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
        return self.status()

