from __future__ import annotations

import base64
import json
import sqlite3
from datetime import datetime, timezone
from typing import Any

from .db import RuntimeDB


SERIES = frozenset({
    "tokens_per_day",
    "tokens_per_task",
    "cost_per_task",
    "success_rate",
    "skill_roi",
    "memory_usefulness",
    "context_waste",
    "model_routing",
    "failed_tasks",
    "learning_events",
})


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _limit(value: int) -> int:
    if not 1 <= value <= 100:
        raise ValueError("limit must be between 1 and 100")
    return value


def _collection(items: list[dict[str, Any]], *, reason: str) -> dict[str, Any]:
    return {
        "status": "available" if items else "empty",
        "items": items,
        "count": len(items),
        "reason": None if items else reason,
        "as_of": _now(),
    }


def _metric(
    value: Any,
    *,
    unit: str | None,
    samples: int,
    status: str | None = None,
    coverage: float | None = None,
    reason: str | None = None,
) -> dict[str, Any]:
    resolved = status or ("available" if samples else "empty")
    return {
        "status": resolved,
        "value": value if resolved == "available" else None,
        "unit": unit,
        "sample_count": samples,
        "coverage": coverage,
        "reason": reason if resolved != "available" or reason else None,
        "as_of": _now(),
    }


class DashboardReader:
    """Bounded, content-free dashboard projections over retained runtime facts."""

    def __init__(self, source: RuntimeDB | object) -> None:
        database = source if isinstance(source, RuntimeDB) else getattr(source, "db", None)
        if not isinstance(database, RuntimeDB):
            raise TypeError("DashboardReader requires RuntimeDB or AdaptiveRuntime")
        self.db = database
        self.connection = database.connection

    @staticmethod
    def _cursor(created_at: str, item_id: str) -> str:
        raw = json.dumps([created_at, item_id], separators=(",", ":")).encode()
        return base64.urlsafe_b64encode(raw).decode("ascii")

    @staticmethod
    def _decode_cursor(cursor: str) -> tuple[str, str]:
        try:
            created_at, item_id = json.loads(
                base64.urlsafe_b64decode(cursor.encode("ascii")).decode()
            )
        except Exception as error:
            raise ValueError("invalid dashboard cursor") from error
        if not isinstance(created_at, str) or not isinstance(item_id, str):
            raise ValueError("invalid dashboard cursor")
        return created_at, item_id

    def overview(self) -> dict[str, Any]:
        tasks = self.connection.execute(
            """
            SELECT COUNT(*) AS total,
                   SUM(status='planned') AS planned,
                   SUM(status='succeeded') AS succeeded,
                   SUM(status='failed') AS failed
            FROM tasks
            """
        ).fetchone()
        calls = self.connection.execute(
            """
            SELECT COUNT(*) AS calls,
                   COALESCE(SUM(input_tokens + output_tokens), 0) AS tokens
            FROM telemetry_events WHERE category='model'
            """
        ).fetchone()
        alerts = self.connection.execute(
            "SELECT COUNT(*) FROM regression_alerts"
        ).fetchone()[0]
        completed = int(tasks["succeeded"] or 0) + int(tasks["failed"] or 0)
        success = (
            float(tasks["succeeded"] or 0) / completed if completed else None
        )
        return {
            "status": "available",
            "as_of": _now(),
            "metrics": {
                "tasks": _metric(int(tasks["total"]), unit="tasks", samples=int(tasks["total"])),
                "model_tokens": _metric(
                    int(calls["tokens"]), unit="tokens", samples=int(calls["calls"])
                ),
                "success_rate": _metric(
                    success, unit="ratio", samples=completed,
                    reason="no_completed_tasks" if not completed else None,
                ),
                "regression_alerts": _metric(
                    int(alerts), unit="alerts", samples=int(alerts),
                    status="available", reason=None,
                ),
            },
            "task_states": {
                "planned": int(tasks["planned"] or 0),
                "succeeded": int(tasks["succeeded"] or 0),
                "failed": int(tasks["failed"] or 0),
            },
        }

    def tasks(
        self, *, limit: int = 50, cursor: str | None = None
    ) -> dict[str, Any]:
        limit = _limit(limit)
        params: list[Any] = []
        where = ""
        if cursor:
            created_at, item_id = self._decode_cursor(cursor)
            where = "WHERE created_at < ? OR (created_at = ? AND id < ?)"
            params.extend((created_at, created_at, item_id))
        params.append(limit + 1)
        rows = self.connection.execute(
            f"""
            SELECT id, token_budget, selected_tokens, status, critic_score,
                   duration_ms, created_at, completed_at
            FROM tasks {where}
            ORDER BY created_at DESC, id DESC LIMIT ?
            """,
            params,
        ).fetchall()
        has_more = len(rows) > limit
        rows = rows[:limit]
        items = [dict(row) for row in rows]
        result = _collection(items, reason="no_tasks")
        result["next_cursor"] = (
            self._cursor(rows[-1]["created_at"], rows[-1]["id"])
            if has_more and rows else None
        )
        return result

    def memory(self) -> dict[str, Any]:
        rows = self.connection.execute(
            """
            SELECT type, status, lifecycle_state, sensitivity,
                   COUNT(*) AS memories, SUM(access_count) AS uses,
                   SUM(successful_uses) AS successful_uses,
                   SUM(failed_uses) AS failed_uses,
                   SUM(token_cost) AS stored_tokens
            FROM memories
            GROUP BY type, status, lifecycle_state, sensitivity
            ORDER BY type, status, lifecycle_state, sensitivity
            """
        ).fetchall()
        return _collection([dict(row) for row in rows], reason="no_memories")

    def skills(self, *, limit: int = 50) -> dict[str, Any]:
        rows = self.connection.execute(
            """
            SELECT id, manifest_id, name, version, lifecycle_status,
                   verification_status, reliability, use_count, success_count,
                   failure_count, token_cost, total_tokens, total_cost,
                   total_latency_ms, last_used, created_at
            FROM skills ORDER BY use_count DESC, name, version LIMIT ?
            """,
            (_limit(limit),),
        ).fetchall()
        return _collection([dict(row) for row in rows], reason="no_skills")

    def agents(self, *, limit: int = 50) -> dict[str, Any]:
        rows = self.connection.execute(
            """
            SELECT id, role, status, created_at,
                   json_array_length(task_scope_json) AS task_scope_count,
                   json_array_length(memory_scope_json) AS memory_scope_count,
                   json_array_length(tools_json) AS tool_count,
                   json_array_length(skills_json) AS skill_count
            FROM agent_specs ORDER BY created_at DESC, id DESC LIMIT ?
            """,
            (_limit(limit),),
        ).fetchall()
        return _collection([dict(row) for row in rows], reason="no_agents")

    def models(self, *, limit: int = 50) -> dict[str, Any]:
        rows = self.connection.execute(
            """
            SELECT p.id, p.provider, p.model, p.context_capacity,
                   p.supports_tools, p.active, p.local, p.created_at,
                   COUNT(e.id) AS calls,
                   COALESCE(SUM(e.input_tokens + e.output_tokens), 0) AS tokens,
                   AVG(e.latency_ms) AS average_latency_ms
            FROM model_profiles p
            LEFT JOIN telemetry_events e
              ON e.category='model' AND e.provider=p.provider AND e.model=p.model
            GROUP BY p.id ORDER BY calls DESC, p.provider, p.model LIMIT ?
            """,
            (_limit(limit),),
        ).fetchall()
        return _collection([dict(row) for row in rows], reason="no_models")

    def tools(self, *, limit: int = 50) -> dict[str, Any]:
        rows = self.connection.execute(
            """
            SELECT d.name, d.side_effect, d.network_access,
                   d.filesystem_access, d.cost, d.latency_estimate_ms,
                   d.created_at, COUNT(o.id) AS uses,
                   COALESCE(SUM(o.success), 0) AS successes,
                   AVG(o.latency_ms) AS average_latency_ms,
                   COALESCE(SUM(o.cost), 0) AS observed_cost
            FROM tool_definitions d
            LEFT JOIN tool_outcomes o ON o.tool_name=d.name
            GROUP BY d.name ORDER BY uses DESC, d.name LIMIT ?
            """,
            (_limit(limit),),
        ).fetchall()
        return _collection([dict(row) for row in rows], reason="no_tools")

    def context(self) -> dict[str, Any]:
        selected = self.connection.execute(
            """
            SELECT COUNT(*) AS blocks, COALESCE(SUM(tokens), 0) AS tokens,
                   COALESCE(SUM(CASE WHEN useful=0 THEN tokens ELSE 0 END), 0)
                       AS wasted_tokens,
                   SUM(useful IS NOT NULL) AS attributed
            FROM context_uses
            """
        ).fetchone()
        compression = [dict(row) for row in self.connection.execute(
            """
            SELECT compression_strategy AS strategy, COUNT(*) AS blocks,
                   COALESCE(SUM(original_tokens), 0) AS original_tokens,
                   COALESCE(SUM(tokens), 0) AS selected_tokens,
                   COALESCE(SUM(original_tokens-tokens), 0) AS tokens_saved
            FROM context_uses WHERE original_tokens IS NOT NULL
            GROUP BY compression_strategy ORDER BY tokens_saved DESC, strategy
            """
        ).fetchall()]
        blocks = int(selected["blocks"])
        return {
            "status": "available" if blocks else "empty",
            "as_of": _now(),
            "reason": None if blocks else "no_context_uses",
            "metrics": {
                "selected_tokens": _metric(
                    int(selected["tokens"]), unit="tokens", samples=blocks
                ),
                "wasted_tokens": _metric(
                    int(selected["wasted_tokens"]), unit="tokens",
                    samples=int(selected["attributed"] or 0),
                ),
            },
            "compression": compression,
        }

    def costs(self, *, limit: int = 50) -> dict[str, Any]:
        rows = self.connection.execute(
            """
            SELECT e.task_id, COUNT(*) AS calls,
                   SUM(e.estimated_cost) AS estimated_cost,
                   SUM(CASE WHEN p.id IS NOT NULL THEN 1 ELSE 0 END) AS priced_calls
            FROM telemetry_events e
            LEFT JOIN model_profiles p
              ON p.provider=e.provider AND p.model=e.model
            WHERE e.category='model'
            GROUP BY e.task_id ORDER BY estimated_cost DESC, e.task_id LIMIT ?
            """,
            (_limit(limit),),
        ).fetchall()
        items = []
        for row in rows:
            item = dict(row)
            calls = int(item["calls"])
            priced = int(item["priced_calls"])
            item["pricing_coverage"] = priced / calls if calls else 0.0
            item["cost_status"] = "available" if priced else "unavailable"
            if not priced:
                item["estimated_cost"] = None
            items.append(item)
        result = _collection(items, reason="no_model_calls")
        result["reason"] = (
            "pricing_unavailable" if items and not any(
                item["cost_status"] == "available" for item in items
            ) else result["reason"]
        )
        return result

    def benchmarks(self, *, limit: int = 50) -> dict[str, Any]:
        limit = _limit(limit)
        local = [dict(row) for row in self.connection.execute(
            """
            SELECT id, model_id, dataset_version, seed, case_count, created_at
            FROM local_benchmark_runs ORDER BY created_at DESC LIMIT ?
            """,
            (limit,),
        ).fetchall()]
        skill = [dict(row) for row in self.connection.execute(
            """
            SELECT r.id, r.skill_name, r.status, r.created_at,
                   rec.action AS recommendation, rec.status AS recommendation_status
            FROM skill_benchmark_runs r
            LEFT JOIN skill_benchmark_recommendations rec ON rec.run_id=r.id
            ORDER BY r.created_at DESC LIMIT ?
            """,
            (limit,),
        ).fetchall()]
        return {
            "status": "available" if local or skill else "empty",
            "as_of": _now(),
            "local_model": _collection(local, reason="no_local_benchmarks"),
            "skill": _collection(skill, reason="no_skill_benchmarks"),
            "memory": _metric(
                None, unit=None, samples=0, status="unavailable",
                reason="memory_benchmark_reports_are_not_persisted",
            ),
            "token": _metric(
                None, unit=None, samples=0, status="unavailable",
                reason="token_benchmark_reports_are_not_persisted",
            ),
        }

    def security(self) -> dict[str, Any]:
        assessments = [dict(row) for row in self.connection.execute(
            """
            SELECT origin, disposition, COUNT(*) AS count
            FROM content_security_assessments
            GROUP BY origin, disposition ORDER BY origin, disposition
            """
        ).fetchall()]
        capabilities = [dict(row) for row in self.connection.execute(
            """
            SELECT capability, allowed, COUNT(*) AS count
            FROM capability_decisions
            GROUP BY capability, allowed ORDER BY capability, allowed
            """
        ).fetchall()]
        privacy = [dict(row) for row in self.connection.execute(
            """
            SELECT action, allowed, COUNT(*) AS count
            FROM privacy_decisions
            GROUP BY action, allowed ORDER BY action, allowed
            """
        ).fetchall()]
        alerts = [dict(row) for row in self.connection.execute(
            """
            SELECT metric, severity, COUNT(*) AS count
            FROM regression_alerts GROUP BY metric, severity
            ORDER BY severity, metric
            """
        ).fetchall()]
        total = len(assessments) + len(capabilities) + len(privacy) + len(alerts)
        return {
            "status": "available" if total else "empty",
            "as_of": _now(),
            "reason": None if total else "no_security_events",
            "assessments": assessments,
            "capability_decisions": capabilities,
            "privacy_decisions": privacy,
            "regression_alerts": alerts,
        }

    def series(self, metric: str, *, limit: int = 100) -> dict[str, Any]:
        if metric not in SERIES:
            raise ValueError(f"unsupported dashboard series: {metric}")
        limit = _limit(limit)
        queries = {
            "tokens_per_day": (
                """
                SELECT substr(created_at,1,10) AS key,
                       SUM(input_tokens+output_tokens) AS value,
                       COUNT(*) AS sample_count
                FROM telemetry_events WHERE category='model'
                GROUP BY key ORDER BY key DESC LIMIT ?
                """, "tokens"
            ),
            "tokens_per_task": (
                """
                SELECT task_id AS key, SUM(input_tokens+output_tokens) AS value,
                       COUNT(*) AS sample_count
                FROM telemetry_events
                WHERE category='model' AND task_id IS NOT NULL
                GROUP BY task_id ORDER BY value DESC, key LIMIT ?
                """, "tokens"
            ),
            "cost_per_task": (
                """
                SELECT e.task_id AS key, SUM(e.estimated_cost) AS value,
                       COUNT(*) AS sample_count,
                       SUM(p.id IS NOT NULL)*1.0/COUNT(*) AS coverage
                FROM telemetry_events e LEFT JOIN model_profiles p
                  ON p.provider=e.provider AND p.model=e.model
                WHERE e.category='model' AND e.task_id IS NOT NULL
                GROUP BY e.task_id ORDER BY value DESC, key LIMIT ?
                """, "currency"
            ),
            "success_rate": (
                """
                SELECT substr(completed_at,1,10) AS key,
                       AVG(status='succeeded') AS value,
                       COUNT(*) AS sample_count
                FROM tasks WHERE status IN ('succeeded','failed')
                GROUP BY key ORDER BY key DESC LIMIT ?
                """, "ratio"
            ),
            "skill_roi": (
                """
                SELECT COALESCE(s.name||'@'||s.version, a.source_id) AS key,
                       AVG(a.approximate_roi) AS value,
                       COUNT(*) AS sample_count
                FROM context_attributions a
                LEFT JOIN skills s ON s.id=a.source_id
                WHERE a.source_type='skill'
                GROUP BY a.source_id ORDER BY value DESC, key LIMIT ?
                """, "approximate_roi"
            ),
            "memory_usefulness": (
                """
                SELECT type AS key,
                       CASE WHEN SUM(access_count)=0 THEN NULL
                            ELSE SUM(successful_uses)*1.0/SUM(access_count) END AS value,
                       SUM(access_count) AS sample_count
                FROM memories GROUP BY type ORDER BY key LIMIT ?
                """, "ratio"
            ),
            "context_waste": (
                """
                SELECT substr(t.completed_at,1,10) AS key,
                       SUM(c.tokens) AS value, COUNT(*) AS sample_count
                FROM context_uses c JOIN tasks t ON t.id=c.task_id
                WHERE c.useful=0 AND t.completed_at IS NOT NULL
                GROUP BY key ORDER BY key DESC LIMIT ?
                """, "tokens"
            ),
            "model_routing": (
                """
                SELECT substr(created_at,1,10)||':'||state AS key,
                       COUNT(*) AS value, COUNT(*) AS sample_count
                FROM model_routes GROUP BY substr(created_at,1,10), state
                ORDER BY substr(created_at,1,10) DESC, state LIMIT ?
                """, "routes"
            ),
            "failed_tasks": (
                """
                SELECT substr(created_at,1,10) AS key,
                       COUNT(*) AS value, COUNT(*) AS sample_count
                FROM tasks WHERE status='failed'
                GROUP BY key ORDER BY key DESC LIMIT ?
                """, "tasks"
            ),
            "learning_events": (
                """
                SELECT substr(created_at,1,10) AS key,
                       COUNT(*) AS value, COUNT(*) AS sample_count
                FROM learning_stage_results
                GROUP BY key ORDER BY key DESC LIMIT ?
                """, "events"
            ),
        }
        sql, unit = queries[metric]
        try:
            points = [dict(row) for row in self.connection.execute(
                sql, (limit,)
            ).fetchall()]
        except sqlite3.OperationalError:
            return {
                "metric": metric, "status": "unavailable", "unit": unit,
                "points": [], "count": 0, "reason": "metric_storage_unavailable",
                "as_of": _now(),
            }
        if metric == "cost_per_task" and points and not any(
            float(point.get("coverage") or 0) > 0 for point in points
        ):
            return {
                "metric": metric, "status": "unavailable", "unit": unit,
                "points": [], "count": 0, "reason": "pricing_unavailable",
                "as_of": _now(),
            }
        return {
            "metric": metric,
            "status": "available" if points else "empty",
            "unit": unit,
            "points": points,
            "count": len(points),
            "reason": None if points else "no_observations",
            "as_of": _now(),
        }
