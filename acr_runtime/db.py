from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .memory import (
    MemoryCreate,
    MemoryQuery,
    MemoryStatus,
    MemoryType,
    SQLiteMemoryStore,
)
from .economist import TokenBudgetPlan
from .attribution import ContextAttribution, AttributionOutcome
from .migrations import EXPECTED_SCHEMA_VERSION, MigrationManager, MigrationRequired
from .migrations import (
    MEMORY_FTS_V3_SQL,
    MEMORY_TABLE_V3_SQL,
    MIGRATION_4_SQL,
    MIGRATION_5_SQL,
    MIGRATION_6_SQL,
    MIGRATION_7_SQL,
    MIGRATION_8_SQL,
    MIGRATION_9_SQL,
    MIGRATION_10_SQL,
    MIGRATION_11_SQL,
    MIGRATION_12_SQL,
    MIGRATION_13_SQL,
    MIGRATION_14_SQL,
    MIGRATION_15_SQL,
    MIGRATION_16_SQL,
    MIGRATION_17_SQL,
    MIGRATION_18_SQL,
    MIGRATION_19_SQL,
    MIGRATION_20_SQL,
    MIGRATION_21_SQL,
    MIGRATION_22_SQL,
    MIGRATION_23_SQL,
)
from .scoring import estimate_tokens
from .skill_router import SkillRoute

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
        self.memories = SQLiteMemoryStore(self.connection)

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> "RuntimeDB":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _migrate(self) -> None:
        memory_schema = (
            MEMORY_TABLE_V3_SQL.replace("{table_name}", "memories")
            + ";\n"
            + MEMORY_FTS_V3_SQL
            + "\n"
            + MIGRATION_4_SQL
            + "\n"
            + MIGRATION_5_SQL
            + "\n"
            + MIGRATION_6_SQL
            + "\n"
            + MIGRATION_7_SQL
            + "\n"
            + MIGRATION_8_SQL
        )
        schema = """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version INTEGER PRIMARY KEY,
                applied_at TEXT NOT NULL
            );

            __MEMORY_SCHEMA__

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
            __SKILL_REGISTRY_SCHEMA__

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
                source_type TEXT NOT NULL CHECK (
                    source_type IN (
                        'system_rule', 'memory', 'skill', 'file', 'tool',
                        'agent_state', 'observation'
                    )
                ),
                source_id TEXT NOT NULL,
                tokens INTEGER NOT NULL,
                utility REAL NOT NULL,
                roi REAL NOT NULL,
                useful INTEGER,
                PRIMARY KEY(task_id, source_type, source_id)
            );

            __TOKEN_ECONOMY_SCHEMA__
            __ATTRIBUTION_SCHEMA__
            __COMPRESSION_SCHEMA__
            __SKILL_ROUTING_SCHEMA__
            __SKILL_GENERATION_SCHEMA__
            __SKILL_VALIDATION_SCHEMA__
            __SKILL_EVOLUTION_SCHEMA__
            __SKILL_MERGER_SCHEMA__
            __SKILL_GENOME_SCHEMA__
            __AGENT_SPEC_SCHEMA__
            __AGENT_FACTORY_SCHEMA__
            __AGENT_TOPOLOGY_SCHEMA__
            __HIERARCHICAL_PLANNER_SCHEMA__

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
        self.connection.executescript(
            schema.replace("__MEMORY_SCHEMA__", memory_schema).replace(
                "__TOKEN_ECONOMY_SCHEMA__", MIGRATION_10_SQL
            ).replace("__ATTRIBUTION_SCHEMA__", MIGRATION_11_SQL).replace(
                "__COMPRESSION_SCHEMA__", MIGRATION_12_SQL
            ).replace(
                "__SKILL_REGISTRY_SCHEMA__", MIGRATION_13_SQL
            ).replace(
                "__SKILL_ROUTING_SCHEMA__", MIGRATION_14_SQL
            ).replace(
                "__SKILL_GENERATION_SCHEMA__", MIGRATION_15_SQL
            ).replace(
                "__SKILL_VALIDATION_SCHEMA__", MIGRATION_16_SQL
            ).replace(
                "__SKILL_EVOLUTION_SCHEMA__", MIGRATION_17_SQL
            ).replace(
                "__SKILL_MERGER_SCHEMA__", MIGRATION_18_SQL
            ).replace(
                "__SKILL_GENOME_SCHEMA__", MIGRATION_19_SQL
            ).replace(
                "__AGENT_SPEC_SCHEMA__", MIGRATION_20_SQL
            ).replace(
                "__AGENT_FACTORY_SCHEMA__", MIGRATION_21_SQL
            ).replace(
                "__AGENT_TOPOLOGY_SCHEMA__", MIGRATION_22_SQL
            ).replace(
                "__HIERARCHICAL_PLANNER_SCHEMA__", MIGRATION_23_SQL
            )
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
            """
            SELECT type, status, lifecycle_state, pinned, COUNT(*) AS count
            FROM memories
            GROUP BY type, status, lifecycle_state, pinned
            """
        ).fetchall()
        skill_rows = self.connection.execute(
            "SELECT status, COUNT(*) AS count FROM skills GROUP BY status"
        ).fetchall()
        task_rows = self.connection.execute(
            "SELECT status, COUNT(*) AS count FROM tasks GROUP BY status"
        ).fetchall()
        failure_rows = self.connection.execute(
            """
            SELECT status, deterministic, COUNT(*) AS records,
                   SUM(occurrence_count) AS occurrences
            FROM failure_records
            GROUP BY status, deterministic
            """
        ).fetchall()
        experience_rows = self.connection.execute(
            """
            SELECT status, COUNT(*) AS count
            FROM experience_distillations GROUP BY status
            """
        ).fetchall()
        economy_rows = self.connection.execute(
            """
            SELECT complexity, COUNT(*) AS plans,
                   AVG(effective_input_budget) AS average_input_budget,
                   AVG(selected_count) AS average_selected
            FROM token_budget_plans GROUP BY complexity
            """
        ).fetchall()
        attribution_rows = self.connection.execute(
            """
            SELECT outcome, COUNT(*) AS records,
                   AVG(approximate_roi) AS average_roi
            FROM context_attributions GROUP BY outcome
            """
        ).fetchall()
        return {
            "database": str(self.path),
            "schema": self.health(),
            "memories": [dict(row) for row in memory_rows],
            "skills": [dict(row) for row in skill_rows],
            "tasks": [dict(row) for row in task_rows],
            "failures": [dict(row) for row in failure_rows],
            "distillations": [dict(row) for row in experience_rows],
            "token_economy": [dict(row) for row in economy_rows],
            "attributions": [dict(row) for row in attribution_rows],
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
        routing = self.skill_route(task_id)
        return {
            "task_id": task_id,
            "runs": [dict(row) for row in runs],
            "events": [dict(row) for row in events],
            "skill_routing": routing,
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
            SELECT id, manifest_id, name, version, status, lifecycle_status,
                   use_count, success_count, failure_count,
                   CASE WHEN use_count = 0 THEN 0.0
                        ELSE CAST(success_count AS REAL) / use_count END AS success_rate,
                   CASE WHEN use_count = 0 THEN 0.0
                        ELSE CAST(total_tokens AS REAL) / use_count END AS average_tokens,
                   CASE WHEN use_count = 0 THEN 0.0
                        ELSE total_cost / use_count END AS average_cost,
                   CASE WHEN use_count = 0 THEN 0.0
                        ELSE CAST(total_latency_ms AS REAL) / use_count
                        END AS average_latency_ms,
                   token_cost, last_used
            FROM skills
            ORDER BY use_count DESC, name
            """
        ).fetchall()
        return [dict(row) for row in rows]

    def telemetry_skill_routing(self) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            """
            SELECT task_class, COUNT(DISTINCT run_id) AS runs,
                   SUM(router_selected) AS router_selections,
                   SUM(compiler_selected) AS compiler_selections,
                   COALESCE(SUM(outcome = 'contributed'), 0) AS contributed,
                   COALESCE(SUM(outcome = 'ignored'), 0) AS ignored,
                   COALESCE(SUM(outcome = 'misled'), 0) AS misled,
                   COALESCE(SUM(outcome = 'uncertain'), 0) AS uncertain,
                   AVG(CASE WHEN compiler_selected = 1
                       THEN expected_benefit END) AS selected_expected_benefit
            FROM skill_routing_candidates
            JOIN skill_routing_runs ON id = run_id
            GROUP BY task_class
            ORDER BY runs DESC, task_class
            """
        ).fetchall()
        return [dict(row) for row in rows]

    def telemetry_memory(self) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            """
            SELECT type, status, COUNT(*) AS memories,
                   SUM(access_count) AS uses,
                   SUM(successful_uses) AS successful_uses,
                   SUM(failed_uses) AS failed_uses,
                   SUM(token_cost) AS stored_tokens
            FROM memories
            GROUP BY type, status
            ORDER BY type, status
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

    def telemetry_token_economy(self) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            """
            SELECT complexity, COUNT(*) AS plans,
                   AVG(requested_input_budget) AS requested_input_budget,
                   AVG(effective_input_budget) AS effective_input_budget,
                   AVG(output_headroom) AS output_headroom,
                   AVG(reasoning_headroom) AS reasoning_headroom,
                   AVG(candidate_count) AS candidates,
                   AVG(selected_count) AS selected,
                   AVG(expected_utility) AS expected_utility
            FROM token_budget_plans
            GROUP BY complexity ORDER BY complexity
            """
        ).fetchall()
        return [dict(row) for row in rows]

    def telemetry_compression(self) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            """
            SELECT compression_strategy AS strategy, COUNT(*) AS blocks,
                   SUM(original_tokens) AS original_tokens,
                   SUM(tokens) AS selected_tokens,
                   SUM(original_tokens - tokens) AS tokens_saved,
                   MIN(exact_preserved) AS exact_preserved
            FROM context_uses
            WHERE original_tokens IS NOT NULL
            GROUP BY compression_strategy
            ORDER BY tokens_saved DESC, strategy
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
        status: str = "confirmed",
        valid_from: str | None = None,
        supersedes: str | None = None,
    ) -> str:
        normalized_status = "confirmed" if status == "active" else status
        record = self.memories.create(
            MemoryCreate(
                type=MemoryType(kind),
                content=content,
                scope=scope,
                confidence=confidence,
                importance=importance,
                evidence=tuple(evidence),
                source_type="legacy" if source else None,
                source_id=source,
                status=MemoryStatus(normalized_status),
                valid_from=valid_from,
                supersedes=supersedes,
            )
        )
        return record.id

    def search_memories(
        self, query: str, *, scope: str, limit: int = 50
    ) -> list[dict[str, Any]]:
        page = self.memories.search(
            MemoryQuery(scope=scope, text=query, limit=limit)
        )
        return [
            {
                **record.__dict__,
                "type": record.type.value,
                "status": record.status.value,
                "evidence": list(record.evidence),
            }
            for record in page.records
        ]

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
        manifest_id = "-".join(name.casefold().split())
        lifecycle_status = "active" if status == "active" else "quarantined"
        now = utc_now()
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO skills (
                    id, name, version, description, instructions, tags_json,
                    status, token_cost, created_at, manifest_id,
                    lifecycle_status, task_classes_json, applicability_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    skill_id, name, version, description, instructions,
                    json.dumps(list(tags)), status, estimate_tokens(instructions),
                    now, manifest_id, lifecycle_status, json.dumps(list(tags)),
                    json.dumps(list(tags)),
                ),
            )
            self.connection.execute(
                """
                INSERT INTO skills_fts(
                    skill_id, name, description, task_classes, applicability
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    skill_id, name, description, json.dumps(list(tags)),
                    json.dumps(list(tags)),
                ),
            )
            self.connection.execute(
                """
                INSERT INTO skill_registry_history(
                    id, skill_id, event, from_status, to_status,
                    details_json, created_at
                ) VALUES (?, ?, 'legacy_registered', NULL, ?, '{}', ?)
                """,
                (str(uuid.uuid4()), skill_id, lifecycle_status, now),
            )
        return skill_id

    def active_skills(self) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            """
            SELECT * FROM skills
            WHERE status = 'active' AND lifecycle_status = 'active'
            ORDER BY name
            """
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
                task_id, source_type, source_id, tokens, utility, roi, useful,
                compression_strategy, original_tokens, exact_preserved
            ) VALUES (
                :task_id, :source_type, :source_id, :tokens, :utility, :roi, NULL,
                :compression_strategy, :original_tokens, :exact_preserved
            )
            """,
            ({"task_id": task_id, **block} for block in blocks),
        )
        self.connection.execute(
            "UPDATE tasks SET selected_tokens = ? WHERE id = ?",
            (selected_tokens, task_id),
        )
        self.connection.commit()

    def record_token_budget_plan(
        self,
        *,
        task_id: str,
        plan: TokenBudgetPlan,
        candidate_count: int,
        selected_count: int,
        expected_utility: float,
    ) -> None:
        self.connection.execute(
            """
            INSERT INTO token_budget_plans(
                id, task_id, complexity, task_importance,
                model_context_window, requested_input_budget,
                output_headroom, reasoning_headroom,
                effective_input_budget, context_budget,
                candidate_count, selected_count, expected_utility, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(uuid.uuid4()),
                task_id,
                plan.complexity.value,
                plan.task_importance,
                plan.model_context_window,
                plan.requested_input_budget,
                plan.output_headroom,
                plan.reasoning_headroom,
                plan.effective_input_budget,
                plan.context_budget,
                candidate_count,
                selected_count,
                expected_utility,
                utc_now(),
            ),
        )
        self.connection.commit()

    def record_skill_route(
        self,
        task_id: str,
        route: SkillRoute,
        compiler_selected_ids: set[str],
    ) -> str:
        run_id = str(uuid.uuid4())
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO skill_routing_runs(
                    id, task_id, task_class, token_budget,
                    semantic_available, candidate_count, selected_count,
                    created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id, task_id, route.task_class, route.token_budget,
                    int(route.semantic_available), len(route.candidates),
                    len(route.selected), utc_now(),
                ),
            )
            self.connection.executemany(
                """
                INSERT INTO skill_routing_candidates(
                    run_id, skill_id, router_selected, compiler_selected,
                    applicability, expected_benefit, token_overhead,
                    historical_success, reliability, overlap_penalty,
                    final_score, reason, rejection_reason, outcome
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)
                """,
                (
                    (
                        run_id, item.id, int(item.selected),
                        int(item.id in compiler_selected_ids),
                        item.applicability, item.expected_benefit,
                        item.token_overhead, item.historical_success,
                        item.reliability, item.overlap_penalty,
                        item.final_score, item.reason, item.rejection_reason,
                    )
                    for item in route.candidates
                ),
            )
        return run_id

    def skill_route(self, task_id: str) -> dict[str, Any] | None:
        run = self.connection.execute(
            "SELECT * FROM skill_routing_runs WHERE task_id = ?",
            (task_id,),
        ).fetchone()
        if run is None:
            return None
        candidates = self.connection.execute(
            """
            SELECT skill_id, router_selected, compiler_selected,
                   applicability, expected_benefit, token_overhead,
                   historical_success, reliability, overlap_penalty,
                   final_score, reason, rejection_reason, outcome
            FROM skill_routing_candidates
            WHERE run_id = ?
            ORDER BY router_selected DESC, final_score DESC, skill_id
            """,
            (run["id"],),
        ).fetchall()
        return {**dict(run), "candidates": [dict(row) for row in candidates]}

    def complete_task(
        self,
        task_id: str,
        *,
        success: bool,
        critic_score: float,
        duration_ms: int,
        attributions: tuple[ContextAttribution, ...],
        task_class: str = "general",
        model: str | None = None,
        estimated_cost: float = 0,
    ) -> None:
        status = "succeeded" if success else "failed"
        now = utc_now()
        context_rows = self.connection.execute(
            """
            SELECT source_type, source_id, tokens
            FROM context_uses WHERE task_id = ?
            """,
            (task_id,),
        ).fetchall()
        expected = {
            (row["source_type"], row["source_id"])
            for row in context_rows
        }
        tokens_by_source = {
            (row["source_type"], row["source_id"]): row["tokens"]
            for row in context_rows
        }
        selected_skill_tokens = sum(
            row["tokens"] for row in context_rows
            if row["source_type"] == "skill"
        )
        actual = {
            (item.source_type, item.source_id) for item in attributions
        }
        if expected != actual:
            raise ValueError("Attribution records must cover the selected context")
        for item in attributions:
            useful = {
                AttributionOutcome.CONTRIBUTED: 1,
                AttributionOutcome.IGNORED: 0,
                AttributionOutcome.MISLED: 0,
                AttributionOutcome.UNCERTAIN: None,
            }[item.outcome]
            self.connection.execute(
                """
                INSERT INTO context_attributions(
                    id, task_id, source_type, source_id, role, outcome,
                    impact_score, confidence, approximate_roi, model_score,
                    execution_score, dependency_score, evaluator_score,
                    evidence_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item.id, item.task_id, item.source_type, item.source_id,
                    item.role, item.outcome.value, item.impact_score,
                    item.confidence, item.approximate_roi, item.model_score,
                    item.execution_score, item.dependency_score,
                    item.evaluator_score, item.evidence_json, now,
                ),
            )
            self.connection.execute(
                """
                UPDATE context_uses SET useful = ?
                WHERE task_id = ? AND source_type = ? AND source_id = ?
                """,
                (useful, task_id, item.source_type, item.source_id),
            )
            if item.source_type == "skill":
                self.connection.execute(
                    """
                    UPDATE skill_routing_candidates SET outcome = ?
                    WHERE skill_id = ? AND run_id = (
                        SELECT id FROM skill_routing_runs WHERE task_id = ?
                    )
                    """,
                    (item.outcome.value, item.source_id, task_id),
                )
            conclusive = item.outcome is not AttributionOutcome.UNCERTAIN
            positive = item.outcome is AttributionOutcome.CONTRIBUTED and success
            if item.source_type == "memory" and conclusive:
                self.memories.record_usage(
                    item.source_id, successful=positive
                )
            elif item.source_type == "skill" and conclusive:
                skill_tokens = tokens_by_source[
                    (item.source_type, item.source_id)
                ]
                allocated_cost = (
                    estimated_cost * skill_tokens / selected_skill_tokens
                    if selected_skill_tokens
                    else 0
                )
                self.connection.execute(
                    """
                    UPDATE skills
                    SET use_count = use_count + 1,
                        success_count = success_count + ?,
                        failure_count = failure_count + ?,
                        total_tokens = total_tokens + ?,
                        total_cost = total_cost + ?,
                        total_latency_ms = total_latency_ms + ?,
                        last_used = ?
                    WHERE id = ?
                    """,
                    (
                        int(positive), int(not positive), skill_tokens,
                        allocated_cost, duration_ms, now, item.source_id,
                    ),
                )
                self.connection.execute(
                    """
                    INSERT INTO skill_performance(
                        skill_id, task_class, model, uses, successful_uses,
                        failures, total_tokens, total_cost, total_latency_ms,
                        last_used
                    ) VALUES (?, ?, ?, 1, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(skill_id, task_class, model) DO UPDATE SET
                        uses = uses + 1,
                        successful_uses = successful_uses + excluded.successful_uses,
                        failures = failures + excluded.failures,
                        total_tokens = total_tokens + excluded.total_tokens,
                        total_cost = total_cost + excluded.total_cost,
                        total_latency_ms =
                            total_latency_ms + excluded.total_latency_ms,
                        last_used = excluded.last_used
                    """,
                    (
                        item.source_id, task_class, model or "",
                        int(positive), int(not positive), skill_tokens,
                        allocated_cost, duration_ms, now,
                    ),
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

    def context_attributions(
        self, task_id: str
    ) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            """
            SELECT source_type, source_id, role, outcome, impact_score,
                   confidence, approximate_roi, model_score, execution_score,
                   dependency_score, evaluator_score, evidence_json, created_at
            FROM context_attributions
            WHERE task_id = ?
            ORDER BY source_type, source_id
            """,
            (task_id,),
        ).fetchall()
        return [dict(row) for row in rows]

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
