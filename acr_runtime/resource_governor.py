from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from typing import Literal

ResourceKind = Literal["context", "model", "tool", "agent", "task", "other"]
EscalationMode = Literal["none", "manual_exact"]

FIELDS = (
    "input_tokens",
    "output_tokens",
    "model_calls",
    "tool_calls",
    "agents",
    "cost",
    "duration",
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _text(value: str, field: str) -> str:
    result = value.strip()
    if not result:
        raise ValueError(f"{field} cannot be empty")
    return result


def _evidence(values: tuple[str, ...], field: str) -> tuple[str, ...]:
    if not values or any(not item.strip() for item in values):
        raise ValueError(f"{field} requires non-empty evidence")
    return tuple(sorted(set(values)))


def _time(value: str, field: str) -> datetime:
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field} must be an ISO-8601 timestamp") from exc
    if result.tzinfo is None:
        raise ValueError(f"{field} must include a timezone")
    return result.astimezone(timezone.utc)


@dataclass(frozen=True)
class ResourceVector:
    input_tokens: int = 0
    output_tokens: int = 0
    model_calls: int = 0
    tool_calls: int = 0
    agents: int = 0
    cost: int = 0
    duration: int = 0

    def __post_init__(self) -> None:
        for field in FIELDS:
            value = getattr(self, field)
            if type(value) is not int or value < 0:
                raise ValueError(
                    f"{field} must be a non-negative integer"
                )

    @classmethod
    def from_dict(cls, payload: object) -> "ResourceVector":
        if not isinstance(payload, dict) or set(payload) != set(FIELDS):
            raise ValueError(f"resource vector requires {sorted(FIELDS)}")
        return cls(**payload)

    @classmethod
    def from_row(cls, row: sqlite3.Row, prefix: str = "") -> "ResourceVector":
        return cls(*(int(row[f"{prefix}{field}"]) for field in FIELDS))

    def as_dict(self) -> dict[str, int]:
        return asdict(self)

    def values(self) -> tuple[int, ...]:
        return tuple(getattr(self, field) for field in FIELDS)

    def plus(self, other: "ResourceVector") -> "ResourceVector":
        return ResourceVector(
            *(left + right for left, right in zip(self.values(), other.values()))
        )

    def minus(self, other: "ResourceVector") -> "ResourceVector":
        values = tuple(
            left - right for left, right in zip(self.values(), other.values())
        )
        if any(value < 0 for value in values):
            raise ValueError("resource subtraction cannot become negative")
        return ResourceVector(*values)

    def exceeds(self, limit: "ResourceVector") -> tuple[str, ...]:
        return tuple(
            field for field in FIELDS
            if getattr(self, field) > getattr(limit, field)
        )

    def bounded_by(self, reserved: "ResourceVector") -> bool:
        return not self.exceeds(reserved)

    @property
    def is_zero(self) -> bool:
        return not any(self.values())


@dataclass(frozen=True)
class ResourceBudget:
    task_id: str
    soft: ResourceVector
    hard: ResourceVector
    escalation_mode: EscalationMode
    evidence: tuple[str, ...]

    def __post_init__(self) -> None:
        _text(self.task_id, "task_id")
        _evidence(self.evidence, "resource budget")
        if self.escalation_mode not in ("none", "manual_exact"):
            raise ValueError(
                "escalation_mode must be none or manual_exact"
            )
        if self.soft.exceeds(self.hard):
            raise ValueError("soft resource limits cannot exceed hard limits")

    @classmethod
    def from_dict(cls, task_id: str, payload: object) -> "ResourceBudget":
        if not isinstance(payload, dict) or set(payload) != {
            "soft", "hard", "escalation_mode", "evidence"
        }:
            raise ValueError(
                "budget requires soft, hard, escalation_mode, and evidence"
            )
        evidence = payload["evidence"]
        if not isinstance(evidence, list):
            raise ValueError("budget evidence must be a list")
        return cls(
            task_id=task_id,
            soft=ResourceVector.from_dict(payload["soft"]),
            hard=ResourceVector.from_dict(payload["hard"]),
            escalation_mode=str(payload["escalation_mode"]),
            evidence=tuple(str(item) for item in evidence),
        )


@dataclass(frozen=True)
class ResourceReservation:
    id: str
    task_id: str
    idempotency_key: str
    kind: ResourceKind
    state: Literal["reserved", "committed", "released"]
    reserved: ResourceVector
    actual: ResourceVector | None
    escalation_id: str | None
    created_at: str
    updated_at: str

    def as_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "task_id": self.task_id,
            "idempotency_key": self.idempotency_key,
            "kind": self.kind,
            "state": self.state,
            "reserved": self.reserved.as_dict(),
            "actual": None if self.actual is None else self.actual.as_dict(),
            "escalation_id": self.escalation_id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


class BudgetExceeded(RuntimeError):
    def __init__(self, reasons: tuple[str, ...]) -> None:
        self.reasons = reasons
        super().__init__("; ".join(reasons))


class ResourceGovernor:
    """Atomic upper-bound reservations for task-scoped hard quotas."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def create_budget(self, budget: ResourceBudget) -> ResourceBudget:
        now = _utc_now()
        try:
            self.connection.execute("BEGIN IMMEDIATE")
            self.connection.execute(
                """
                INSERT INTO task_resource_budgets (
                    task_id,
                    soft_input_tokens, soft_output_tokens, soft_model_calls,
                    soft_tool_calls, soft_agents, soft_cost, soft_duration,
                    max_input_tokens, max_output_tokens, max_model_calls,
                    max_tool_calls, max_agents, max_cost, max_duration,
                    escalation_mode, evidence_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    budget.task_id,
                    *budget.soft.values(),
                    *budget.hard.values(),
                    budget.escalation_mode,
                    json.dumps(_evidence(budget.evidence, "resource budget")),
                    now,
                ),
            )
            self.connection.execute(
                """
                INSERT INTO task_resource_usage(task_id, updated_at)
                VALUES (?, ?)
                """,
                (budget.task_id, now),
            )
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise
        return self.budget(budget.task_id)

    def budget(self, task_id: str) -> ResourceBudget:
        row = self.connection.execute(
            "SELECT * FROM task_resource_budgets WHERE task_id = ?",
            (_text(task_id, "task_id"),),
        ).fetchone()
        if row is None:
            raise LookupError(f"Unknown task resource budget: {task_id}")
        return ResourceBudget(
            task_id=row["task_id"],
            soft=ResourceVector(
                *(int(row[f"soft_{field}"]) for field in FIELDS)
            ),
            hard=ResourceVector(
                *(int(row[f"max_{field}"]) for field in FIELDS)
            ),
            escalation_mode=row["escalation_mode"],
            evidence=tuple(json.loads(row["evidence_json"])),
        )

    def _usage(self, task_id: str) -> tuple[ResourceVector, ResourceVector]:
        row = self.connection.execute(
            "SELECT * FROM task_resource_usage WHERE task_id = ?",
            (task_id,),
        ).fetchone()
        if row is None:
            raise LookupError(f"Unknown task resource budget: {task_id}")
        return (
            ResourceVector(
                *(int(row[f"held_{field}"]) for field in FIELDS)
            ),
            ResourceVector(
                *(int(row[f"used_{field}"]) for field in FIELDS)
            ),
        )

    def approve_escalation(
        self,
        task_id: str,
        quote: ResourceVector,
        *,
        approval_reference: str,
        reason: str,
        evidence: tuple[str, ...],
        expires_at: str | None = None,
    ) -> str:
        budget = self.budget(task_id)
        if budget.escalation_mode != "manual_exact":
            raise ValueError("budget does not permit escalation")
        held, used = self._usage(task_id)
        proposed = held.plus(used).plus(quote)
        if proposed.exceeds(budget.hard):
            raise BudgetExceeded(tuple(
                f"hard_limit:{field}"
                for field in proposed.exceeds(budget.hard)
            ))
        if not proposed.exceeds(budget.soft):
            raise ValueError("escalation is unnecessary below soft limits")
        now = datetime.now(timezone.utc)
        expiry = (
            now + timedelta(hours=1)
            if expires_at is None else _time(expires_at, "expires_at")
        )
        if expiry <= now:
            raise ValueError("escalation expiry must be in the future")
        escalation_id = str(uuid.uuid4())
        self.connection.execute(
            """
            INSERT INTO task_resource_escalations (
                id, task_id, input_tokens, output_tokens, model_calls,
                tool_calls, agents, cost, duration, approval_reference,
                reason, evidence_json, expires_at, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                escalation_id,
                task_id,
                *quote.values(),
                _text(approval_reference, "approval_reference"),
                _text(reason, "reason"),
                json.dumps(_evidence(evidence, "resource escalation")),
                expiry.isoformat(),
                now.isoformat(),
            ),
        )
        self.connection.commit()
        return escalation_id

    def _escalation_valid(
        self,
        task_id: str,
        quote: ResourceVector,
        escalation_id: str | None,
    ) -> bool:
        if escalation_id is None:
            return False
        row = self.connection.execute(
            """
            SELECT * FROM task_resource_escalations
            WHERE id = ? AND task_id = ?
            """,
            (escalation_id, task_id),
        ).fetchone()
        if row is None or _time(row["expires_at"], "expires_at") <= datetime.now(
            timezone.utc
        ):
            return False
        if self.connection.execute(
            """
            SELECT 1 FROM task_resource_reservations
            WHERE escalation_id = ?
            """,
            (escalation_id,),
        ).fetchone():
            return False
        return ResourceVector.from_row(row) == quote

    def _reservation(self, row: sqlite3.Row) -> ResourceReservation:
        actual = (
            ResourceVector(
                *(int(row[f"actual_{field}"]) for field in FIELDS)
            )
            if row["actual_input_tokens"] is not None
            else None
        )
        return ResourceReservation(
            id=row["id"],
            task_id=row["task_id"],
            idempotency_key=row["idempotency_key"],
            kind=row["kind"],
            state=row["state"],
            reserved=ResourceVector(
                *(int(row[f"reserved_{field}"]) for field in FIELDS)
            ),
            actual=actual,
            escalation_id=row["escalation_id"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def get(self, reservation_id: str) -> ResourceReservation:
        row = self.connection.execute(
            "SELECT * FROM task_resource_reservations WHERE id = ?",
            (_text(reservation_id, "reservation_id"),),
        ).fetchone()
        if row is None:
            raise LookupError(f"Unknown resource reservation: {reservation_id}")
        return self._reservation(row)

    def reserve(
        self,
        task_id: str,
        quote: ResourceVector,
        *,
        idempotency_key: str,
        kind: ResourceKind,
        evidence: tuple[str, ...],
        escalation_id: str | None = None,
    ) -> ResourceReservation:
        if quote.is_zero:
            raise ValueError("resource quote cannot be all zero")
        if kind not in ("context", "model", "tool", "agent", "task", "other"):
            raise ValueError("unsupported resource reservation kind")
        key = _text(idempotency_key, "idempotency_key")
        retained_evidence = _evidence(evidence, "resource reservation")
        try:
            self.connection.execute("BEGIN IMMEDIATE")
            existing = self.connection.execute(
                """
                SELECT * FROM task_resource_reservations
                WHERE task_id = ? AND idempotency_key = ?
                """,
                (task_id, key),
            ).fetchone()
            if existing is not None:
                reservation = self._reservation(existing)
                if reservation.kind != kind or reservation.reserved != quote:
                    raise ValueError(
                        "idempotency key was already used for another quote"
                    )
                self.connection.commit()
                return reservation
            budget = self.budget(task_id)
            held, used = self._usage(task_id)
            proposed = held.plus(used).plus(quote)
            hard = proposed.exceeds(budget.hard)
            if hard:
                raise BudgetExceeded(tuple(
                    f"hard_limit:{field}" for field in hard
                ))
            soft = proposed.exceeds(budget.soft)
            retained_escalation: str | None = None
            if soft:
                if not self._escalation_valid(
                    task_id, quote, escalation_id
                ):
                    raise BudgetExceeded(tuple(
                        f"soft_limit_requires_escalation:{field}"
                        for field in soft
                    ))
                retained_escalation = escalation_id
            now = _utc_now()
            reservation_id = str(uuid.uuid4())
            self.connection.execute(
                """
                INSERT INTO task_resource_reservations (
                    id, task_id, idempotency_key, kind, state,
                    reserved_input_tokens, reserved_output_tokens,
                    reserved_model_calls, reserved_tool_calls,
                    reserved_agents, reserved_cost, reserved_duration,
                    escalation_id, evidence_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, 'reserved', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    reservation_id,
                    task_id,
                    key,
                    kind,
                    *quote.values(),
                    retained_escalation,
                    json.dumps(retained_evidence),
                    now,
                    now,
                ),
            )
            assignments = ", ".join(
                f"held_{field} = held_{field} + ?" for field in FIELDS
            )
            self.connection.execute(
                f"""
                UPDATE task_resource_usage SET {assignments}, updated_at = ?
                WHERE task_id = ?
                """,
                (*quote.values(), now, task_id),
            )
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise
        return self.get(reservation_id)

    def commit(
        self,
        reservation_id: str,
        actual: ResourceVector,
        *,
        evidence: tuple[str, ...],
    ) -> ResourceReservation:
        completion_evidence = _evidence(evidence, "reservation completion")
        try:
            self.connection.execute("BEGIN IMMEDIATE")
            reservation = self.get(reservation_id)
            if reservation.state == "committed":
                if reservation.actual != actual:
                    raise ValueError("reservation already committed differently")
                self.connection.commit()
                return reservation
            if reservation.state != "reserved":
                raise ValueError("only a reserved resource quote can commit")
            if not actual.bounded_by(reservation.reserved):
                raise ValueError("actual resource use exceeds reservation")
            now = _utc_now()
            held_assignments = ", ".join(
                f"held_{field} = held_{field} - ?" for field in FIELDS
            )
            used_assignments = ", ".join(
                f"used_{field} = used_{field} + ?" for field in FIELDS
            )
            self.connection.execute(
                f"""
                UPDATE task_resource_usage
                SET {held_assignments}, {used_assignments}, updated_at = ?
                WHERE task_id = ?
                """,
                (
                    *reservation.reserved.values(),
                    *actual.values(),
                    now,
                    reservation.task_id,
                ),
            )
            actual_assignments = ", ".join(
                f"actual_{field} = ?" for field in FIELDS
            )
            self.connection.execute(
                f"""
                UPDATE task_resource_reservations
                SET state = 'committed', {actual_assignments},
                    completion_evidence_json = ?, updated_at = ?
                WHERE id = ? AND state = 'reserved'
                """,
                (
                    *actual.values(),
                    json.dumps(completion_evidence),
                    now,
                    reservation_id,
                ),
            )
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise
        return self.get(reservation_id)

    def release(
        self,
        reservation_id: str,
        *,
        not_started_evidence: tuple[str, ...],
    ) -> ResourceReservation:
        evidence = _evidence(
            not_started_evidence, "reservation release"
        )
        try:
            self.connection.execute("BEGIN IMMEDIATE")
            reservation = self.get(reservation_id)
            if reservation.state == "released":
                self.connection.commit()
                return reservation
            if reservation.state != "reserved":
                raise ValueError("only a reserved resource quote can release")
            now = _utc_now()
            assignments = ", ".join(
                f"held_{field} = held_{field} - ?" for field in FIELDS
            )
            self.connection.execute(
                f"""
                UPDATE task_resource_usage
                SET {assignments}, updated_at = ?
                WHERE task_id = ?
                """,
                (
                    *reservation.reserved.values(),
                    now,
                    reservation.task_id,
                ),
            )
            self.connection.execute(
                """
                UPDATE task_resource_reservations
                SET state = 'released', completion_evidence_json = ?,
                    updated_at = ?
                WHERE id = ? AND state = 'reserved'
                """,
                (json.dumps(evidence), now, reservation_id),
            )
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise
        return self.get(reservation_id)

    def status(self, task_id: str) -> dict[str, object]:
        budget = self.budget(task_id)
        held, used = self._usage(task_id)
        allocated = held.plus(used)
        return {
            "task_id": task_id,
            "soft": budget.soft.as_dict(),
            "hard": budget.hard.as_dict(),
            "held": held.as_dict(),
            "used": used.as_dict(),
            "allocated": allocated.as_dict(),
            "remaining_hard": budget.hard.minus(allocated).as_dict(),
            "soft_exceeded": list(allocated.exceeds(budget.soft)),
            "hard_exceeded": list(allocated.exceeds(budget.hard)),
            "escalation_mode": budget.escalation_mode,
            "cost_unit": "microunits",
            "duration_unit": "milliseconds",
            "hard_limits_enforced_by_database": True,
        }
