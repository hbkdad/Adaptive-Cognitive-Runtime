from __future__ import annotations

import json
import math
import sqlite3
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone

from .memory import (
    LifecycleState,
    MemoryRecord,
    MemoryStatus,
    MemoryStore,
    MemoryType,
    parse_timestamp,
    utc_now,
)


@dataclass(frozen=True)
class LifecycleConfig:
    cold_after_days: int = 90
    archive_after_days: int = 180
    recency_half_life_days: int = 90
    scope_half_life_days: int = 120
    cold_threshold: float = 0.55
    archive_threshold: float = 0.40
    critical_failure_threshold: float = 0.80
    high_value_procedure_threshold: float = 0.80
    recency_weight: float = 0.20
    usage_weight: float = 0.15
    importance_weight: float = 0.20
    confidence_weight: float = 0.15
    utility_weight: float = 0.20
    scope_activity_weight: float = 0.10
    superseded_penalty: float = 0.25
    scan_limit: int = 10_000

    def __post_init__(self) -> None:
        if any(
            value < 1
            for value in (
                self.cold_after_days,
                self.archive_after_days,
                self.recency_half_life_days,
                self.scope_half_life_days,
            )
        ):
            raise ValueError("Lifecycle day thresholds must be positive")
        if self.archive_after_days < self.cold_after_days:
            raise ValueError("archive_after_days cannot be less than cold_after_days")
        for value in (
            self.cold_threshold,
            self.archive_threshold,
            self.critical_failure_threshold,
            self.high_value_procedure_threshold,
            self.recency_weight,
            self.usage_weight,
            self.importance_weight,
            self.confidence_weight,
            self.utility_weight,
            self.scope_activity_weight,
            self.superseded_penalty,
        ):
            if not 0 <= value <= 1:
                raise ValueError("Lifecycle score thresholds must be 0..1")
        if (
            self.recency_weight
            + self.usage_weight
            + self.importance_weight
            + self.confidence_weight
            + self.utility_weight
            + self.scope_activity_weight
            <= 0
        ):
            raise ValueError("At least one lifecycle retention weight is required")
        if not 1 <= self.scan_limit <= 10_000:
            raise ValueError("scan_limit must be between 1 and 10000")


@dataclass(frozen=True)
class LifecycleAction:
    id: str
    memory_id: str
    from_state: LifecycleState
    to_state: LifecycleState
    expected_updated_at: str
    score: dict[str, float]
    reason: str
    status: str = "proposed"
    error_type: str | None = None


@dataclass(frozen=True)
class LifecyclePlan:
    id: str
    scope: str | None
    status: str
    actions: tuple[LifecycleAction, ...]
    created_at: str
    applied_at: str | None = None

    def summary(self) -> dict[str, int]:
        return {
            state.value: sum(
                action.to_state is state for action in self.actions
            )
            for state in (LifecycleState.COLD, LifecycleState.ARCHIVED)
        }


class SQLiteLifecycleAudit:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def save(self, plan: LifecyclePlan, config: LifecycleConfig) -> None:
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO memory_gc_runs(
                    id, status, scope, config_json, summary_json,
                    created_at, applied_at
                ) VALUES (?, ?, ?, ?, ?, ?, NULL)
                """,
                (
                    plan.id,
                    plan.status,
                    plan.scope,
                    json.dumps(asdict(config)),
                    json.dumps(plan.summary()),
                    plan.created_at,
                ),
            )
            self.connection.executemany(
                """
                INSERT INTO memory_gc_actions(
                    id, run_id, memory_id, from_state, to_state,
                    expected_updated_at, score_json, reason, status,
                    error_type, created_at, applied_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, NULL)
                """,
                (
                    (
                        action.id,
                        plan.id,
                        action.memory_id,
                        action.from_state.value,
                        action.to_state.value,
                        action.expected_updated_at,
                        json.dumps(action.score),
                        action.reason,
                        action.status,
                        plan.created_at,
                    )
                    for action in plan.actions
                ),
            )

    def load(self, run_id: str) -> LifecyclePlan:
        run = self.connection.execute(
            "SELECT * FROM memory_gc_runs WHERE id = ?", (run_id,)
        ).fetchone()
        if run is None:
            raise KeyError(run_id)
        rows = self.connection.execute(
            """
            SELECT * FROM memory_gc_actions
            WHERE run_id = ? ORDER BY created_at, id
            """,
            (run_id,),
        ).fetchall()
        return LifecyclePlan(
            id=run["id"],
            scope=run["scope"],
            status=run["status"],
            actions=tuple(
                LifecycleAction(
                    id=row["id"],
                    memory_id=row["memory_id"],
                    from_state=LifecycleState(row["from_state"]),
                    to_state=LifecycleState(row["to_state"]),
                    expected_updated_at=row["expected_updated_at"],
                    score=dict(json.loads(row["score_json"])),
                    reason=row["reason"],
                    status=row["status"],
                    error_type=row["error_type"],
                )
                for row in rows
            ),
            created_at=run["created_at"],
            applied_at=run["applied_at"],
        )

    def mark_action(
        self, action_id: str, status: str, *, error_type: str | None = None
    ) -> None:
        with self.connection:
            self.connection.execute(
                """
                UPDATE memory_gc_actions
                SET status = ?, error_type = ?, applied_at = ?
                WHERE id = ?
                """,
                (status, error_type, utc_now(), action_id),
            )

    def mark_run(self, run_id: str, status: str) -> None:
        with self.connection:
            self.connection.execute(
                "UPDATE memory_gc_runs SET status = ?, applied_at = ? WHERE id = ?",
                (status, utc_now(), run_id),
            )


class MemoryLifecycleManager:
    """Plans conservative, reversible lifecycle transitions for long-term memory."""

    def __init__(
        self,
        store: MemoryStore,
        audit: SQLiteLifecycleAudit,
        *,
        config: LifecycleConfig | None = None,
    ) -> None:
        self.store = store
        self.audit = audit
        self.config = config or LifecycleConfig()

    @staticmethod
    def _age_days(value: str, now: datetime) -> float:
        return max(0.0, (now - parse_timestamp(value)).total_seconds() / 86_400)

    @staticmethod
    def _payload_flag(record: MemoryRecord, name: str) -> bool:
        payload = json.loads(record.structured_payload_json)
        return isinstance(payload, dict) and payload.get(name) is True

    def protection_reason(self, record: MemoryRecord) -> str | None:
        if record.pinned:
            return "pinned"
        if record.type is MemoryType.DECISION:
            return "architecture_or_operational_decision"
        if (
            record.type is MemoryType.FAILURE
            and (
                record.importance >= self.config.critical_failure_threshold
                or self._payload_flag(record, "critical")
            )
        ):
            return "critical_failure"
        if (
            record.type is MemoryType.PROCEDURAL
            and max(record.importance, record.utility_score)
            >= self.config.high_value_procedure_threshold
        ):
            return "high_value_procedure"
        if self._payload_flag(record, "security_event"):
            return "security_event"
        return None

    def _scope_last_active(self, scope: str) -> str | None:
        row = self.audit.connection.execute(
            "SELECT last_active_at FROM memory_scope_activity WHERE scope = ?",
            (scope,),
        ).fetchone()
        task = self.audit.connection.execute(
            """
            SELECT COALESCE(completed_at, created_at)
            FROM tasks WHERE scope = ?
            ORDER BY COALESCE(completed_at, created_at) DESC LIMIT 1
            """,
            (scope,),
        ).fetchone()
        values = [str(item[0]) for item in (row, task) if item and item[0]]
        return max(values, key=parse_timestamp) if values else None

    def score(self, record: MemoryRecord, *, now: datetime) -> dict[str, float]:
        last_use = record.last_accessed or record.created_at
        age = self._age_days(last_use, now)
        scope_last = self._scope_last_active(record.scope)
        scope_age = self._age_days(scope_last, now) if scope_last else age
        recency = math.pow(0.5, age / self.config.recency_half_life_days)
        scope_activity = math.pow(
            0.5, scope_age / self.config.scope_half_life_days
        )
        usage = min(1.0, math.log1p(record.access_count) / math.log1p(20))
        superseded = float(
            record.status is MemoryStatus.SUPERSEDED
            or record.superseded_by is not None
        )
        weight_total = (
            self.config.recency_weight
            + self.config.usage_weight
            + self.config.importance_weight
            + self.config.confidence_weight
            + self.config.utility_weight
            + self.config.scope_activity_weight
        )
        retention = (
            self.config.recency_weight * recency
            + self.config.usage_weight * usage
            + self.config.importance_weight * record.importance
            + self.config.confidence_weight * record.confidence
            + self.config.utility_weight * record.utility_score
            + self.config.scope_activity_weight * scope_activity
        ) / weight_total - self.config.superseded_penalty * superseded
        return {
            "retention": round(max(0.0, min(1.0, retention)), 6),
            "age_days": round(age, 3),
            "usage": round(usage, 6),
            "importance": record.importance,
            "confidence": record.confidence,
            "utility": record.utility_score,
            "superseded": superseded,
            "scope_activity": round(scope_activity, 6),
        }

    def dry_run(
        self, *, scope: str | None = None, now: datetime | None = None
    ) -> LifecyclePlan:
        evaluated_at = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        actions: list[LifecycleAction] = []
        records = self.store.scan(scope=scope, limit=self.config.scan_limit)
        for record in records:
            if record.lifecycle_state not in (
                LifecycleState.ACTIVE,
                LifecycleState.COLD,
            ):
                continue
            if self.protection_reason(record):
                continue
            score = self.score(record, now=evaluated_at)
            target: LifecycleState | None = None
            reason = ""
            lifecycle_age = self._age_days(
                record.lifecycle_updated_at, evaluated_at
            )
            if (
                record.lifecycle_state is LifecycleState.ACTIVE
                and score["age_days"] >= self.config.cold_after_days
                and score["retention"] < self.config.cold_threshold
            ):
                target = LifecycleState.COLD
                reason = "inactive_low_retention"
            elif (
                record.lifecycle_state is LifecycleState.COLD
                and lifecycle_age >= self.config.archive_after_days
                and score["retention"] < self.config.archive_threshold
            ):
                target = LifecycleState.ARCHIVED
                reason = "cold_low_retention"
            if target:
                actions.append(
                    LifecycleAction(
                        id=str(uuid.uuid4()),
                        memory_id=record.id,
                        from_state=record.lifecycle_state,
                        to_state=target,
                        expected_updated_at=record.updated_at,
                        score=score,
                        reason=reason,
                    )
                )
        plan = LifecyclePlan(
            id=str(uuid.uuid4()),
            scope=scope,
            status="planned",
            actions=tuple(actions),
            created_at=evaluated_at.isoformat(),
        )
        self.audit.save(plan, self.config)
        return plan

    def approve(self, run_id: str) -> LifecyclePlan:
        plan = self.audit.load(run_id)
        if plan.status != "planned":
            raise ValueError("Only a planned garbage-collection run can be approved")
        failures = 0
        for action in plan.actions:
            current = self.store.get(action.memory_id)
            if (
                current is None
                or current.updated_at != action.expected_updated_at
                or current.lifecycle_state is not action.from_state
                or self.protection_reason(current)
            ):
                self.audit.mark_action(action.id, "skipped")
                failures += 1
                continue
            try:
                self.store.set_lifecycle(
                    action.memory_id, action.to_state, reason=action.reason
                )
            except Exception as error:
                self.audit.mark_action(
                    action.id, "error", error_type=type(error).__name__
                )
                failures += 1
            else:
                self.audit.mark_action(action.id, "applied")
        self.audit.mark_run(
            run_id, "partially_applied" if failures else "applied"
        )
        return self.audit.load(run_id)

    def pin(self, memory_id: str, *, reason: str | None = None) -> MemoryRecord:
        return self.store.set_pinned(memory_id, True, reason=reason)

    def unpin(self, memory_id: str) -> MemoryRecord:
        return self.store.set_pinned(memory_id, False)

    def archive(self, memory_id: str, *, force: bool = False) -> MemoryRecord:
        return self.store.set_lifecycle(
            memory_id, LifecycleState.ARCHIVED, force=force, reason="manual_archive"
        )

    def restore(self, memory_id: str) -> MemoryRecord:
        restored = self.store.set_lifecycle(memory_id, LifecycleState.ACTIVE)
        if restored.status is MemoryStatus.ARCHIVED:
            return self.store.set_status(memory_id, MemoryStatus.CANDIDATE)
        return restored
