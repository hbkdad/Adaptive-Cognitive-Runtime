from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

EXPECTED_SCHEMA_VERSION = 8


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

MIGRATION_4_SQL = """
ALTER TABLE memories
ADD COLUMN retention_reason_json TEXT NOT NULL
DEFAULT '["legacy_or_direct_write"]'
CHECK (json_valid(retention_reason_json));

CREATE TABLE memory_write_decisions (
    id TEXT PRIMARY KEY,
    candidate_hash TEXT NOT NULL,
    outcome TEXT NOT NULL CHECK (
        outcome IN (
            'ignore', 'store_temporary', 'store_candidate',
            'store_confirmed', 'update_existing', 'supersede_existing',
            'request_verification', 'quarantine'
        )
    ),
    memory_id TEXT REFERENCES memories(id),
    matched_memory_id TEXT REFERENCES memories(id),
    reasons_json TEXT NOT NULL CHECK (json_valid(reasons_json)),
    risk_flags_json TEXT NOT NULL DEFAULT '[]' CHECK (
        json_valid(risk_flags_json)
    ),
    scope TEXT,
    memory_type TEXT NOT NULL,
    confidence REAL NOT NULL,
    evidence_count INTEGER NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX memory_write_decisions_created
ON memory_write_decisions(created_at);
CREATE INDEX memory_write_decisions_memory
ON memory_write_decisions(memory_id);
"""

MIGRATION_5_SQL = """
CREATE TABLE memory_consolidation_runs (
    id TEXT PRIMARY KEY,
    status TEXT NOT NULL CHECK (
        status IN ('planned', 'applied', 'partially_applied', 'cancelled')
    ),
    scope TEXT,
    config_json TEXT NOT NULL CHECK (json_valid(config_json)),
    summary_json TEXT NOT NULL CHECK (json_valid(summary_json)),
    created_at TEXT NOT NULL,
    applied_at TEXT
);

CREATE TABLE memory_consolidation_actions (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES memory_consolidation_runs(id),
    kind TEXT NOT NULL CHECK (
        kind IN (
            'merge', 'archive', 'supersession',
            'promotion', 'conflict', 'decay'
        )
    ),
    target_ids_json TEXT NOT NULL CHECK (json_valid(target_ids_json)),
    expected_versions_json TEXT NOT NULL CHECK (
        json_valid(expected_versions_json)
    ),
    payload_json TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(payload_json)),
    reason TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'proposed' CHECK (
        status IN (
            'proposed', 'applied', 'skipped',
            'review_required', 'error'
        )
    ),
    error_type TEXT,
    created_at TEXT NOT NULL,
    applied_at TEXT
);

CREATE INDEX memory_consolidation_actions_run
ON memory_consolidation_actions(run_id, kind);
"""

MIGRATION_6_SQL = """
ALTER TABLE memories ADD COLUMN lifecycle_state TEXT NOT NULL DEFAULT 'active'
CHECK (lifecycle_state IN ('active', 'cold', 'archived', 'deleted'));
ALTER TABLE memories ADD COLUMN pinned INTEGER NOT NULL DEFAULT 0
CHECK (pinned IN (0, 1));
ALTER TABLE memories ADD COLUMN pinned_at TEXT;
ALTER TABLE memories ADD COLUMN pin_reason TEXT;
ALTER TABLE memories ADD COLUMN lifecycle_updated_at TEXT;
ALTER TABLE memories ADD COLUMN archived_at TEXT;
ALTER TABLE memories ADD COLUMN deleted_at TEXT;

UPDATE memories
SET lifecycle_state = CASE status
        WHEN 'archived' THEN 'archived'
        WHEN 'deleted' THEN 'deleted'
        ELSE 'active'
    END,
    lifecycle_updated_at = updated_at,
    archived_at = CASE WHEN status = 'archived' THEN updated_at END,
    deleted_at = CASE WHEN status = 'deleted' THEN updated_at END;

CREATE TABLE memory_scope_activity (
    scope TEXT PRIMARY KEY,
    last_active_at TEXT NOT NULL,
    access_count INTEGER NOT NULL DEFAULT 0 CHECK (access_count >= 0),
    updated_at TEXT NOT NULL
);

INSERT INTO memory_scope_activity(scope, last_active_at, access_count, updated_at)
SELECT scope, MAX(COALESCE(last_accessed, created_at)), SUM(access_count),
       MAX(updated_at)
FROM memories
GROUP BY scope;

CREATE TABLE memory_gc_runs (
    id TEXT PRIMARY KEY,
    status TEXT NOT NULL CHECK (
        status IN ('planned', 'applied', 'partially_applied', 'cancelled')
    ),
    scope TEXT,
    config_json TEXT NOT NULL CHECK (json_valid(config_json)),
    summary_json TEXT NOT NULL CHECK (json_valid(summary_json)),
    created_at TEXT NOT NULL,
    applied_at TEXT
);

CREATE TABLE memory_gc_actions (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES memory_gc_runs(id),
    memory_id TEXT NOT NULL REFERENCES memories(id),
    from_state TEXT NOT NULL CHECK (
        from_state IN ('active', 'cold', 'archived', 'deleted')
    ),
    to_state TEXT NOT NULL CHECK (
        to_state IN ('active', 'cold', 'archived', 'deleted')
    ),
    expected_updated_at TEXT NOT NULL,
    score_json TEXT NOT NULL CHECK (json_valid(score_json)),
    reason TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'proposed' CHECK (
        status IN ('proposed', 'applied', 'skipped', 'error')
    ),
    error_type TEXT,
    created_at TEXT NOT NULL,
    applied_at TEXT
);

CREATE INDEX memories_live_lifecycle
ON memories(scope, lifecycle_state, last_accessed)
WHERE lifecycle_state IN ('active', 'cold');
CREATE INDEX memories_pinned
ON memories(scope, pinned)
WHERE pinned = 1;
CREATE INDEX memory_gc_actions_run
ON memory_gc_actions(run_id, to_state);
"""

MIGRATION_7_SQL = """
CREATE TABLE failure_records (
    id TEXT PRIMARY KEY,
    memory_id TEXT NOT NULL UNIQUE REFERENCES memories(id),
    scope TEXT NOT NULL,
    task_class TEXT NOT NULL,
    strategy_attempted TEXT NOT NULL,
    environment_json TEXT NOT NULL DEFAULT '{}' CHECK (
        json_valid(environment_json)
    ),
    symptoms_json TEXT NOT NULL CHECK (json_valid(symptoms_json)),
    root_cause TEXT,
    failed_action TEXT NOT NULL,
    error_type TEXT,
    error_message TEXT,
    resolution TEXT,
    avoidance_rule TEXT,
    deterministic INTEGER NOT NULL DEFAULT 0 CHECK (
        deterministic IN (0, 1)
    ),
    occurrence_count INTEGER NOT NULL DEFAULT 1 CHECK (
        occurrence_count >= 1
    ),
    status TEXT NOT NULL DEFAULT 'unresolved' CHECK (
        status IN ('unresolved', 'resolved')
    ),
    remediation_memory_id TEXT REFERENCES memories(id),
    fingerprint TEXT NOT NULL,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    resolved_at TEXT,
    UNIQUE(scope, fingerprint)
);

CREATE INDEX failure_records_lookup
ON failure_records(scope, task_class, status, last_seen_at);
CREATE INDEX failure_records_memory
ON failure_records(memory_id);
CREATE INDEX failure_records_remediation
ON failure_records(remediation_memory_id)
WHERE remediation_memory_id IS NOT NULL;
"""

MIGRATION_8_SQL = """
CREATE TABLE experience_traces (
    id TEXT PRIMARY KEY,
    task_id TEXT,
    scope TEXT NOT NULL,
    task_class TEXT NOT NULL,
    outcome TEXT NOT NULL CHECK (
        outcome IN ('succeeded', 'failed', 'partial', 'cancelled')
    ),
    significance_score REAL NOT NULL CHECK (
        significance_score BETWEEN 0 AND 1
    ),
    raw_trace_json TEXT NOT NULL CHECK (json_valid(raw_trace_json)),
    raw_tokens INTEGER NOT NULL CHECK (raw_tokens >= 0),
    event_count INTEGER NOT NULL CHECK (event_count >= 0),
    created_at TEXT NOT NULL
);

CREATE TABLE experience_distillations (
    id TEXT PRIMARY KEY,
    trace_id TEXT NOT NULL REFERENCES experience_traces(id),
    status TEXT NOT NULL CHECK (
        status IN ('planned', 'applied', 'partially_applied', 'rejected')
    ),
    extractor TEXT NOT NULL,
    raw_tokens INTEGER NOT NULL CHECK (raw_tokens >= 0),
    distilled_tokens INTEGER NOT NULL CHECK (distilled_tokens >= 0),
    compression_ratio REAL NOT NULL CHECK (compression_ratio >= 0),
    summary_json TEXT NOT NULL CHECK (json_valid(summary_json)),
    created_at TEXT NOT NULL,
    applied_at TEXT
);

CREATE TABLE experience_distilled_items (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES experience_distillations(id),
    kind TEXT NOT NULL CHECK (
        kind IN (
            'durable_fact', 'decision', 'successful_procedure',
            'failure_pattern', 'environment_discovery',
            'tool_sequence', 'candidate_skill'
        )
    ),
    content TEXT NOT NULL,
    evidence_json TEXT NOT NULL CHECK (json_valid(evidence_json)),
    confidence REAL NOT NULL CHECK (confidence BETWEEN 0 AND 1),
    importance REAL NOT NULL CHECK (importance BETWEEN 0 AND 1),
    source_event_indexes_json TEXT NOT NULL CHECK (
        json_valid(source_event_indexes_json)
    ),
    status TEXT NOT NULL DEFAULT 'proposed' CHECK (
        status IN ('proposed', 'applied', 'skipped', 'error')
    ),
    memory_id TEXT REFERENCES memories(id),
    skill_id TEXT REFERENCES skills(id),
    error_type TEXT,
    created_at TEXT NOT NULL,
    applied_at TEXT
);

CREATE INDEX experience_traces_scope
ON experience_traces(scope, task_class, created_at);
CREATE INDEX experience_distillations_trace
ON experience_distillations(trace_id, created_at);
CREATE INDEX experience_distilled_items_run
ON experience_distilled_items(run_id, kind);
"""

MEMORY_TABLE_V3_SQL = """
CREATE TABLE {table_name} (
    id TEXT PRIMARY KEY,
    type TEXT NOT NULL CHECK (
        type IN (
            'semantic', 'episodic', 'procedural', 'failure',
            'decision', 'preference', 'environment', 'temporary'
        )
    ),
    scope TEXT NOT NULL DEFAULT 'global',
    subject TEXT,
    content TEXT NOT NULL,
    structured_payload_json TEXT NOT NULL DEFAULT '{}' CHECK (
        json_valid(structured_payload_json)
    ),
    confidence REAL NOT NULL CHECK (confidence BETWEEN 0 AND 1),
    importance REAL NOT NULL CHECK (importance BETWEEN 0 AND 1),
    utility_score REAL NOT NULL DEFAULT 0 CHECK (utility_score BETWEEN 0 AND 1),
    source_type TEXT,
    source_id TEXT,
    evidence_json TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(evidence_json)),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    valid_from TEXT NOT NULL,
    valid_until TEXT,
    last_accessed TEXT,
    access_count INTEGER NOT NULL DEFAULT 0 CHECK (access_count >= 0),
    successful_uses INTEGER NOT NULL DEFAULT 0 CHECK (successful_uses >= 0),
    failed_uses INTEGER NOT NULL DEFAULT 0 CHECK (failed_uses >= 0),
    supersedes TEXT REFERENCES {table_name}(id),
    superseded_by TEXT REFERENCES {table_name}(id),
    status TEXT NOT NULL DEFAULT 'candidate' CHECK (
        status IN (
            'candidate', 'confirmed', 'superseded',
            'archived', 'quarantined', 'deleted'
        )
    ),
    token_cost INTEGER NOT NULL CHECK (token_cost >= 0)
)
"""

MEMORY_FTS_V3_SQL = """
CREATE VIRTUAL TABLE memories_fts USING fts5(
    subject,
    content,
    scope,
    type,
    content='memories',
    content_rowid='rowid',
    tokenize='porter unicode61'
);

CREATE TRIGGER memories_ai AFTER INSERT ON memories BEGIN
    INSERT INTO memories_fts(rowid, subject, content, scope, type)
    VALUES (new.rowid, new.subject, new.content, new.scope, new.type);
END;
CREATE TRIGGER memories_ad AFTER DELETE ON memories BEGIN
    INSERT INTO memories_fts(
        memories_fts, rowid, subject, content, scope, type
    ) VALUES (
        'delete', old.rowid, old.subject, old.content, old.scope, old.type
    );
END;
CREATE TRIGGER memories_au AFTER UPDATE ON memories BEGIN
    INSERT INTO memories_fts(
        memories_fts, rowid, subject, content, scope, type
    ) VALUES (
        'delete', old.rowid, old.subject, old.content, old.scope, old.type
    );
    INSERT INTO memories_fts(rowid, subject, content, scope, type)
    VALUES (new.rowid, new.subject, new.content, new.scope, new.type);
END;
"""

MEMORY_FTS_V3_STATEMENTS = (
    """
    CREATE VIRTUAL TABLE memories_fts USING fts5(
        subject, content, scope, type, content='memories',
        content_rowid='rowid', tokenize='porter unicode61'
    )
    """,
    """
    CREATE TRIGGER memories_ai AFTER INSERT ON memories BEGIN
        INSERT INTO memories_fts(rowid, subject, content, scope, type)
        VALUES (new.rowid, new.subject, new.content, new.scope, new.type);
    END
    """,
    """
    CREATE TRIGGER memories_ad AFTER DELETE ON memories BEGIN
        INSERT INTO memories_fts(
            memories_fts, rowid, subject, content, scope, type
        ) VALUES (
            'delete', old.rowid, old.subject, old.content, old.scope, old.type
        );
    END
    """,
    """
    CREATE TRIGGER memories_au AFTER UPDATE ON memories BEGIN
        INSERT INTO memories_fts(
            memories_fts, rowid, subject, content, scope, type
        ) VALUES (
            'delete', old.rowid, old.subject, old.content, old.scope, old.type
        );
        INSERT INTO memories_fts(rowid, subject, content, scope, type)
        VALUES (new.rowid, new.subject, new.content, new.scope, new.type);
    END
    """,
)


@dataclass(frozen=True)
class MigrationStatus:
    current_version: int
    expected_version: int
    pending_versions: tuple[int, ...]


class MigrationManager:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.last_backup_path: Path | None = None

    def status(self) -> MigrationStatus:
        if not self.path.exists() or self.path.stat().st_size == 0:
            return MigrationStatus(
                0, EXPECTED_SCHEMA_VERSION, tuple(range(1, EXPECTED_SCHEMA_VERSION + 1))
            )
        connection = sqlite3.connect(self.path)
        try:
            table = connection.execute(
                """
                SELECT COUNT(*) FROM sqlite_master
                WHERE type = 'table' AND name = 'schema_migrations'
                """
            ).fetchone()[0]
            if not table:
                return MigrationStatus(
                    0,
                    EXPECTED_SCHEMA_VERSION,
                    tuple(range(1, EXPECTED_SCHEMA_VERSION + 1)),
                )
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
            pending_versions=tuple(range(current + 1, EXPECTED_SCHEMA_VERSION + 1)),
        )

    def _backup(self, from_version: int) -> Path:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        backup = self.path.with_name(
            f"{self.path.stem}.bak-v{from_version}-{timestamp}{self.path.suffix}"
        )
        source = sqlite3.connect(self.path)
        destination = sqlite3.connect(backup)
        try:
            source.backup(destination)
        finally:
            destination.close()
            source.close()
        self.last_backup_path = backup
        return backup

    @staticmethod
    def _apply_migration_3(connection: sqlite3.Connection) -> None:
        connection.execute("PRAGMA foreign_keys = OFF")
        try:
            connection.execute("BEGIN IMMEDIATE")
            old_ids = {
                row[0] for row in connection.execute("SELECT id FROM memories")
            }
            for trigger in ("memories_ai", "memories_ad", "memories_au"):
                connection.execute(f"DROP TRIGGER IF EXISTS {trigger}")
            connection.execute("DROP TABLE IF EXISTS memories_fts")
            connection.execute("ALTER TABLE memories RENAME TO memories_v2")
            connection.execute(
                MEMORY_TABLE_V3_SQL.replace("{table_name}", "memories")
            )
            connection.execute(
                """
                INSERT INTO memories (
                    id, type, scope, subject, content, structured_payload_json,
                    confidence, importance, utility_score, source_type, source_id,
                    evidence_json, created_at, updated_at, valid_from, valid_until,
                    last_accessed, access_count, successful_uses, failed_uses,
                    supersedes, superseded_by, status, token_cost
                )
                SELECT
                    id, kind, scope, NULL, content, '{}',
                    confidence, importance,
                    CASE WHEN use_count = 0 THEN 0.0
                         ELSE CAST(success_count AS REAL) / use_count END,
                    CASE WHEN source IS NULL THEN NULL ELSE 'legacy' END,
                    source, evidence_json, created_at,
                    COALESCE(last_used_at, created_at), valid_from, valid_to,
                    last_used_at, use_count, success_count,
                    MAX(use_count - success_count, 0),
                    supersedes, NULL,
                    CASE status
                        WHEN 'active' THEN 'confirmed'
                        WHEN 'candidate' THEN 'candidate'
                        WHEN 'superseded' THEN 'superseded'
                        WHEN 'archived' THEN 'archived'
                    END,
                    token_cost
                FROM memories_v2
                """
            )
            connection.execute(
                """
                UPDATE memories
                SET superseded_by = (
                    SELECT child.id FROM memories AS child
                    WHERE child.supersedes = memories.id
                    ORDER BY child.created_at DESC, child.id DESC
                    LIMIT 1
                )
                WHERE EXISTS (
                    SELECT 1 FROM memories AS child
                    WHERE child.supersedes = memories.id
                )
                """
            )
            connection.execute("DROP TABLE memories_v2")
            for statement in MEMORY_FTS_V3_STATEMENTS:
                connection.execute(statement)
            connection.execute(
                "INSERT INTO memories_fts(memories_fts) VALUES ('rebuild')"
            )
            new_ids = {
                row[0] for row in connection.execute("SELECT id FROM memories")
            }
            if old_ids != new_ids:
                raise RuntimeError("Memory migration did not preserve all IDs")
            fts_count = connection.execute(
                "SELECT COUNT(*) FROM memories_fts"
            ).fetchone()[0]
            if fts_count != len(new_ids):
                raise RuntimeError("Memory search index verification failed")
            connection.execute(
                """
                INSERT INTO schema_migrations(version, applied_at)
                VALUES (3, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
                """
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.execute("PRAGMA foreign_keys = ON")

    @staticmethod
    def _apply_migration_4(connection: sqlite3.Connection) -> None:
        try:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                ALTER TABLE memories
                ADD COLUMN retention_reason_json TEXT NOT NULL
                DEFAULT '["legacy_or_direct_write"]'
                CHECK (json_valid(retention_reason_json))
                """
            )
            connection.execute(
                """
                CREATE TABLE memory_write_decisions (
                    id TEXT PRIMARY KEY,
                    candidate_hash TEXT NOT NULL,
                    outcome TEXT NOT NULL CHECK (
                        outcome IN (
                            'ignore', 'store_temporary', 'store_candidate',
                            'store_confirmed', 'update_existing',
                            'supersede_existing', 'request_verification',
                            'quarantine'
                        )
                    ),
                    memory_id TEXT REFERENCES memories(id),
                    matched_memory_id TEXT REFERENCES memories(id),
                    reasons_json TEXT NOT NULL CHECK (json_valid(reasons_json)),
                    risk_flags_json TEXT NOT NULL DEFAULT '[]' CHECK (
                        json_valid(risk_flags_json)
                    ),
                    scope TEXT,
                    memory_type TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    evidence_count INTEGER NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX memory_write_decisions_created
                ON memory_write_decisions(created_at)
                """
            )
            connection.execute(
                """
                CREATE INDEX memory_write_decisions_memory
                ON memory_write_decisions(memory_id)
                """
            )
            connection.execute(
                """
                INSERT INTO schema_migrations(version, applied_at)
                VALUES (4, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
                """
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise

    @staticmethod
    def _apply_migration_5(connection: sqlite3.Connection) -> None:
        try:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                CREATE TABLE memory_consolidation_runs (
                    id TEXT PRIMARY KEY,
                    status TEXT NOT NULL CHECK (
                        status IN (
                            'planned', 'applied',
                            'partially_applied', 'cancelled'
                        )
                    ),
                    scope TEXT,
                    config_json TEXT NOT NULL CHECK (json_valid(config_json)),
                    summary_json TEXT NOT NULL CHECK (json_valid(summary_json)),
                    created_at TEXT NOT NULL,
                    applied_at TEXT
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE memory_consolidation_actions (
                    id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL
                        REFERENCES memory_consolidation_runs(id),
                    kind TEXT NOT NULL CHECK (
                        kind IN (
                            'merge', 'archive', 'supersession',
                            'promotion', 'conflict', 'decay'
                        )
                    ),
                    target_ids_json TEXT NOT NULL CHECK (
                        json_valid(target_ids_json)
                    ),
                    expected_versions_json TEXT NOT NULL CHECK (
                        json_valid(expected_versions_json)
                    ),
                    payload_json TEXT NOT NULL DEFAULT '{}' CHECK (
                        json_valid(payload_json)
                    ),
                    reason TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'proposed' CHECK (
                        status IN (
                            'proposed', 'applied', 'skipped',
                            'review_required', 'error'
                        )
                    ),
                    error_type TEXT,
                    created_at TEXT NOT NULL,
                    applied_at TEXT
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX memory_consolidation_actions_run
                ON memory_consolidation_actions(run_id, kind)
                """
            )
            connection.execute(
                """
                INSERT INTO schema_migrations(version, applied_at)
                VALUES (5, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
                """
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise

    @staticmethod
    def _apply_migration_6(connection: sqlite3.Connection) -> None:
        try:
            connection.execute("BEGIN IMMEDIATE")
            for statement in MIGRATION_6_SQL.split(";"):
                if statement.strip():
                    connection.execute(statement)
            connection.execute(
                """
                INSERT INTO schema_migrations(version, applied_at)
                VALUES (6, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
                """
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise

    @staticmethod
    def _apply_migration_7(connection: sqlite3.Connection) -> None:
        try:
            connection.execute("BEGIN IMMEDIATE")
            for statement in MIGRATION_7_SQL.split(";"):
                if statement.strip():
                    connection.execute(statement)
            connection.execute(
                """
                INSERT INTO schema_migrations(version, applied_at)
                VALUES (7, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
                """
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise

    @staticmethod
    def _apply_migration_8(connection: sqlite3.Connection) -> None:
        try:
            connection.execute("BEGIN IMMEDIATE")
            for statement in MIGRATION_8_SQL.split(";"):
                if statement.strip():
                    connection.execute(statement)
            connection.execute(
                """
                INSERT INTO schema_migrations(version, applied_at)
                VALUES (8, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
                """
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise

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
        if not status.pending_versions:
            return status
        self._backup(status.current_version)
        connection = sqlite3.connect(self.path)
        try:
            if 2 in status.pending_versions:
                connection.executescript(MIGRATION_2_SQL)
                connection.execute(
                    """
                    INSERT INTO schema_migrations(version, applied_at)
                    VALUES (2, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
                    """
                )
                connection.commit()
            if 3 in status.pending_versions:
                self._apply_migration_3(connection)
            if 4 in status.pending_versions:
                self._apply_migration_4(connection)
            if 5 in status.pending_versions:
                self._apply_migration_5(connection)
            if 6 in status.pending_versions:
                self._apply_migration_6(connection)
            if 7 in status.pending_versions:
                self._apply_migration_7(connection)
            if 8 in status.pending_versions:
                self._apply_migration_8(connection)
        finally:
            connection.close()
        return self.status()
