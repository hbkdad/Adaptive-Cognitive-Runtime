from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .migrations import EXPECTED_SCHEMA_VERSION, MigrationManager, MigrationRequired
from .scoring import estimate_tokens, fts_query

SCHEMA_VERSION = EXPECTED_SCHEMA_VERSION


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class RuntimeDB:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        existing_database = self.path.exists() and self.path.stat().st_size > 0
        self.connection = sqlite3.connect(self.path)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")
        self.connection.execute("PRAGMA journal_mode = WAL")
        if existing_database:
            self._validate_schema()
        else:
            self._migrate()

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> "RuntimeDB":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _migrate(self) -> None:
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version INTEGER PRIMARY KEY,
                applied_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS memories (
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

            CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
                content,
                scope,
                kind,
                content='memories',
                content_rowid='rowid',
                tokenize='porter unicode61'
            );

            CREATE TRIGGER IF NOT EXISTS memories_ai AFTER INSERT ON memories BEGIN
                INSERT INTO memories_fts(rowid, content, scope, kind)
                VALUES (new.rowid, new.content, new.scope, new.kind);
            END;
            CREATE TRIGGER IF NOT EXISTS memories_ad AFTER DELETE ON memories BEGIN
                INSERT INTO memories_fts(memories_fts, rowid, content, scope, kind)
                VALUES ('delete', old.rowid, old.content, old.scope, old.kind);
            END;
            CREATE TRIGGER IF NOT EXISTS memories_au AFTER UPDATE ON memories BEGIN
                INSERT INTO memories_fts(memories_fts, rowid, content, scope, kind)
                VALUES ('delete', old.rowid, old.content, old.scope, old.kind);
                INSERT INTO memories_fts(rowid, content, scope, kind)
                VALUES (new.rowid, new.content, new.scope, new.kind);
            END;

            CREATE TABLE IF NOT EXISTS skills (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                version TEXT NOT NULL,
                description TEXT NOT NULL,
                instructions TEXT NOT NULL,
                tags_json TEXT NOT NULL DEFAULT '[]',
                status TEXT NOT NULL DEFAULT 'quarantine' CHECK (
                    status IN ('quarantine', 'active', 'deprecated')
                ),
                token_cost INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                use_count INTEGER NOT NULL DEFAULT 0,
                success_count INTEGER NOT NULL DEFAULT 0,
                UNIQUE(name, version)
            );

            CREATE TABLE IF NOT EXISTS tasks (
                id TEXT PRIMARY KEY,
                objective TEXT NOT NULL,
                scope TEXT NOT NULL,
                token_budget INTEGER NOT NULL,
                selected_tokens INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'planned' CHECK (
                    status IN ('planned', 'succeeded', 'failed')
                ),
                critic_score REAL,
                duration_ms INTEGER,
                created_at TEXT NOT NULL,
                completed_at TEXT
            );

            CREATE TABLE IF NOT EXISTS context_uses (
                task_id TEXT NOT NULL REFERENCES tasks(id),
                source_type TEXT NOT NULL CHECK (source_type IN ('memory', 'skill')),
                source_id TEXT NOT NULL,
                tokens INTEGER NOT NULL,
                utility REAL NOT NULL,
                roi REAL NOT NULL,
                useful INTEGER,
                PRIMARY KEY(task_id, source_type, source_id)
            );

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
        )
        applied_at = utc_now()
        self.connection.executemany(
            """
            INSERT OR IGNORE INTO schema_migrations(version, applied_at)
            VALUES (?, ?)
            """,
            ((version, applied_at) for version in range(1, SCHEMA_VERSION + 1)),
        )
        self.connection.commit()

    def _validate_schema(self) -> None:
        status = MigrationManager(self.path).status()
        if status.current_version != SCHEMA_VERSION:
            self.connection.close()
            if status.current_version < SCHEMA_VERSION:
                raise MigrationRequired(
                    f"Database schema {status.current_version} requires explicit "
                    f"migration to {SCHEMA_VERSION}; run `acr --db {self.path} migrate`"
                )
            raise MigrationRequired(
                f"Database schema {status.current_version} is newer than this "
                f"runtime ({SCHEMA_VERSION})"
            )

    def health(self) -> dict[str, Any]:
        quick_check = self.connection.execute("PRAGMA quick_check").fetchone()[0]
        version_row = self.connection.execute(
            "SELECT COALESCE(MAX(version), 0) FROM schema_migrations"
        ).fetchone()
        schema_version = int(version_row[0])
        fts5_available = (
            self.connection.execute(
                """
                SELECT COUNT(*) FROM sqlite_master
                WHERE type = 'table' AND name = 'memories_fts'
                """
            ).fetchone()[0]
            == 1
        )
        return {
            "quick_check": quick_check,
            "schema_version": schema_version,
            "expected_schema_version": SCHEMA_VERSION,
            "schema_current": schema_version == SCHEMA_VERSION,
            "fts5_available": fts5_available,
        }

    def status_snapshot(self) -> dict[str, Any]:
        memory_rows = self.connection.execute(
            "SELECT kind, status, COUNT(*) AS count FROM memories GROUP BY kind, status"
        ).fetchall()
        skill_rows = self.connection.execute(
            "SELECT status, COUNT(*) AS count FROM skills GROUP BY status"
        ).fetchall()
        task_rows = self.connection.execute(
            "SELECT status, COUNT(*) AS count FROM tasks GROUP BY status"
        ).fetchall()
        return {
            "database": str(self.path),
            "schema": self.health(),
            "memories": [dict(row) for row in memory_rows],
            "skills": [dict(row) for row in skill_rows],
            "tasks": [dict(row) for row in task_rows],
        }

    def list_skills(self) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            """
            SELECT id, name, version, description, status, token_cost,
                   use_count, success_count
            FROM skills
            ORDER BY name, version
            """
        ).fetchall()
        return [dict(row) for row in rows]

    def record_telemetry_event(
        self,
        *,
        event_id: str,
        sequence: int | None,
        category: str,
        event_type: str,
        task_id: str | None,
        run_id: str | None,
        step_id: str | None,
        status: str | None,
        payload_json: str,
        created_at: str,
        provider: str | None = None,
        model: str | None = None,
        input_tokens: int = 0,
        output_tokens: int = 0,
        cached_tokens: int = 0,
        estimated_cost: float = 0,
        latency_ms: int = 0,
        context_bundle_id: str | None = None,
        skills_json: str = "[]",
        memories_json: str = "[]",
    ) -> None:
        self.connection.execute(
            """
            INSERT INTO telemetry_events (
                id, sequence, category, event_type, task_id, run_id, step_id,
                provider, model, input_tokens, output_tokens, cached_tokens,
                estimated_cost, latency_ms, status, context_bundle_id,
                skills_json, memories_json, payload_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event_id,
                sequence,
                category,
                event_type,
                task_id,
                run_id,
                step_id,
                provider,
                model,
                input_tokens,
                output_tokens,
                cached_tokens,
                estimated_cost,
                latency_ms,
                status,
                context_bundle_id,
                skills_json,
                memories_json,
                payload_json,
                created_at,
            ),
        )
        self.connection.commit()

    def record_execution_run(
        self,
        *,
        run_id: str,
        task_id: str,
        state: str,
        event_count: int,
        step_count: int,
        action_count: int,
        duration_ms: int,
        verification_score: float | None,
        evaluation_score: float | None,
        failure_kind: str | None,
        started_at: str,
        completed_at: str,
    ) -> None:
        self.connection.execute(
            """
            INSERT INTO execution_runs (
                run_id, task_id, state, event_count, step_count, action_count,
                duration_ms, verification_score, evaluation_score, failure_kind,
                started_at, completed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(run_id) DO UPDATE SET
                state = excluded.state,
                event_count = excluded.event_count,
                step_count = excluded.step_count,
                action_count = excluded.action_count,
                duration_ms = excluded.duration_ms,
                verification_score = excluded.verification_score,
                evaluation_score = excluded.evaluation_score,
                failure_kind = excluded.failure_kind,
                completed_at = excluded.completed_at
            """,
            (
                run_id,
                task_id,
                state,
                event_count,
                step_count,
                action_count,
                duration_ms,
                verification_score,
                evaluation_score,
                failure_kind,
                started_at,
                completed_at,
            ),
        )
        self.connection.commit()

    def telemetry_task(self, task_id: str) -> dict[str, Any]:
        runs = self.connection.execute(
            "SELECT * FROM execution_runs WHERE task_id = ? ORDER BY started_at",
            (task_id,),
        ).fetchall()
        events = self.connection.execute(
            """
            SELECT sequence, category, event_type, run_id, step_id, status,
                   provider, model, input_tokens, output_tokens, cached_tokens,
                   estimated_cost, latency_ms, payload_json, created_at
            FROM telemetry_events
            WHERE task_id = ?
            ORDER BY created_at, sequence
            """,
            (task_id,),
        ).fetchall()
        return {
            "task_id": task_id,
            "runs": [dict(row) for row in runs],
            "events": [dict(row) for row in events],
        }

    def telemetry_models(self) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            """
            SELECT provider, model, COUNT(*) AS calls,
                   SUM(input_tokens) AS input_tokens,
                   SUM(output_tokens) AS output_tokens,
                   SUM(cached_tokens) AS cached_tokens,
                   SUM(estimated_cost) AS estimated_cost,
                   AVG(latency_ms) AS average_latency_ms,
                   SUM(status = 'succeeded') AS successes
            FROM telemetry_events
            WHERE category = 'model'
            GROUP BY provider, model
            ORDER BY calls DESC
            """
        ).fetchall()
        return [dict(row) for row in rows]

    def telemetry_skills(self) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            """
            SELECT id, name, version, status, use_count, success_count,
                   CASE WHEN use_count = 0 THEN 0.0
                        ELSE CAST(success_count AS REAL) / use_count END AS success_rate,
                   token_cost
            FROM skills
            ORDER BY use_count DESC, name
            """
        ).fetchall()
        return [dict(row) for row in rows]

    def telemetry_memory(self) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            """
            SELECT kind, status, COUNT(*) AS memories,
                   SUM(use_count) AS uses,
                   SUM(success_count) AS successful_uses,
                   SUM(token_cost) AS stored_tokens
            FROM memories
            GROUP BY kind, status
            ORDER BY kind, status
            """
        ).fetchall()
        return [dict(row) for row in rows]

    def telemetry_waste(self) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            """
            SELECT source_type, source_id, COUNT(*) AS selections,
                   SUM(tokens) AS wasted_tokens,
                   AVG(utility) AS average_estimated_utility
            FROM context_uses
            WHERE useful = 0
            GROUP BY source_type, source_id
            ORDER BY wasted_tokens DESC
            """
        ).fetchall()
        return [dict(row) for row in rows]

    def add_memory(
        self,
        *,
        kind: str,
        content: str,
        scope: str = "global",
        confidence: float = 0.8,
        importance: float = 0.5,
        evidence: Iterable[str] = (),
        source: str | None = None,
        status: str = "active",
        valid_from: str | None = None,
        supersedes: str | None = None,
    ) -> str:
        memory_id = str(uuid.uuid4())
        now = utc_now()
        if supersedes:
            self.connection.execute(
                "UPDATE memories SET status = 'superseded', valid_to = ? WHERE id = ?",
                (now, supersedes),
            )
        self.connection.execute(
            """
            INSERT INTO memories (
                id, kind, content, scope, confidence, importance, evidence_json,
                source, status, valid_from, supersedes, token_cost, created_at,
                last_used_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                memory_id,
                kind,
                content,
                scope,
                confidence,
                importance,
                json.dumps(list(evidence)),
                source,
                status,
                valid_from or now,
                supersedes,
                estimate_tokens(content),
                now,
                now,
            ),
        )
        self.connection.commit()
        return memory_id

    def search_memories(
        self, query: str, *, scope: str, limit: int = 50
    ) -> list[dict[str, Any]]:
        expression = fts_query(query)
        if expression:
            rows = self.connection.execute(
                """
                SELECT m.*, bm25(memories_fts) AS fts_rank
                FROM memories_fts
                JOIN memories m ON m.rowid = memories_fts.rowid
                WHERE memories_fts MATCH ?
                  AND m.status = 'active'
                  AND m.valid_to IS NULL
                  AND (m.scope = ? OR m.scope = 'global')
                ORDER BY bm25(memories_fts)
                LIMIT ?
                """,
                (expression, scope, limit),
            ).fetchall()
        else:
            rows = self.connection.execute(
                """
                SELECT m.*, 0.0 AS fts_rank
                FROM memories m
                WHERE m.status = 'active'
                  AND m.valid_to IS NULL
                  AND (m.scope = ? OR m.scope = 'global')
                ORDER BY m.importance DESC, m.created_at DESC
                LIMIT ?
                """,
                (scope, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def add_skill(
        self,
        *,
        name: str,
        version: str,
        description: str,
        instructions: str,
        tags: Iterable[str] = (),
        status: str = "quarantine",
    ) -> str:
        skill_id = str(uuid.uuid4())
        self.connection.execute(
            """
            INSERT INTO skills (
                id, name, version, description, instructions, tags_json, status,
                token_cost, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                skill_id,
                name,
                version,
                description,
                instructions,
                json.dumps(list(tags)),
                status,
                estimate_tokens(instructions),
                utc_now(),
            ),
        )
        self.connection.commit()
        return skill_id

    def active_skills(self) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            "SELECT * FROM skills WHERE status = 'active' ORDER BY name"
        ).fetchall()
        return [dict(row) for row in rows]

    def create_task(
        self, *, objective: str, scope: str, token_budget: int
    ) -> str:
        task_id = str(uuid.uuid4())
        self.connection.execute(
            """
            INSERT INTO tasks (id, objective, scope, token_budget, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (task_id, objective, scope, token_budget, utc_now()),
        )
        self.connection.commit()
        return task_id

    def record_context(
        self, task_id: str, blocks: Iterable[dict[str, Any]], selected_tokens: int
    ) -> None:
        self.connection.executemany(
            """
            INSERT INTO context_uses (
                task_id, source_type, source_id, tokens, utility, roi
            ) VALUES (
                :task_id, :source_type, :source_id, :tokens, :utility, :roi
            )
            """,
            ({"task_id": task_id, **block} for block in blocks),
        )
        self.connection.execute(
            "UPDATE tasks SET selected_tokens = ? WHERE id = ?",
            (selected_tokens, task_id),
        )
        self.connection.commit()

    def complete_task(
        self,
        task_id: str,
        *,
        success: bool,
        critic_score: float,
        duration_ms: int,
        useful_sources: set[tuple[str, str]],
    ) -> None:
        status = "succeeded" if success else "failed"
        now = utc_now()
        rows = self.connection.execute(
            "SELECT source_type, source_id FROM context_uses WHERE task_id = ?",
            (task_id,),
        ).fetchall()
        for row in rows:
            source_type, source_id = row["source_type"], row["source_id"]
            useful = (source_type, source_id) in useful_sources
            self.connection.execute(
                """
                UPDATE context_uses SET useful = ?
                WHERE task_id = ? AND source_type = ? AND source_id = ?
                """,
                (int(useful), task_id, source_type, source_id),
            )
            table = "memories" if source_type == "memory" else "skills"
            extra = ", last_used_at = ?" if table == "memories" else ""
            params: tuple[Any, ...]
            if table == "memories":
                params = (int(success and useful), now, source_id)
            else:
                params = (int(success and useful), source_id)
            self.connection.execute(
                f"""
                UPDATE {table}
                SET use_count = use_count + 1,
                    success_count = success_count + ? {extra}
                WHERE id = ?
                """,
                params,
            )
        self.connection.execute(
            """
            UPDATE tasks
            SET status = ?, critic_score = ?, duration_ms = ?, completed_at = ?
            WHERE id = ?
            """,
            (status, critic_score, duration_ms, now, task_id),
        )
        self.connection.commit()

    def telemetry_summary(self) -> dict[str, Any]:
        task = self.connection.execute(
            """
            SELECT
                COUNT(*) AS tasks,
                COALESCE(SUM(status = 'succeeded'), 0) AS successes,
                COALESCE(SUM(selected_tokens), 0) AS selected_tokens,
                COALESCE(AVG(critic_score), 0) AS avg_critic_score
            FROM tasks
            WHERE status != 'planned'
            """
        ).fetchone()
        attribution = self.connection.execute(
            """
            SELECT
                COUNT(*) AS context_blocks,
                COALESCE(SUM(useful), 0) AS useful_blocks,
                COALESCE(SUM(CASE WHEN useful = 0 THEN tokens ELSE 0 END), 0)
                    AS wasted_tokens
            FROM context_uses
            WHERE useful IS NOT NULL
            """
        ).fetchone()
        execution = self.connection.execute(
            """
            SELECT COUNT(*) AS runs,
                   COALESCE(SUM(state = 'completed'), 0) AS completed,
                   COALESCE(SUM(state = 'failed'), 0) AS failed,
                   COALESCE(SUM(state = 'cancelled'), 0) AS cancelled,
                   COALESCE(AVG(duration_ms), 0) AS average_duration_ms
            FROM execution_runs
            """
        ).fetchone()
        event_count = self.connection.execute(
            "SELECT COUNT(*) FROM telemetry_events"
        ).fetchone()[0]
        return {
            **dict(task),
            **dict(attribution),
            "execution": dict(execution),
            "telemetry_events": event_count,
        }
