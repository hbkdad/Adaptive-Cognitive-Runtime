from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal

from .secret_management import assert_secret_free


OverrideAction = Literal[
    "pin_memory",
    "block_memory",
    "force_model",
    "force_skill",
    "disable_skill",
    "limit_agents",
    "disable_learning",
    "freeze_architecture",
    "rollback_version",
]

_ACTIONS = {
    "pin_memory",
    "block_memory",
    "force_model",
    "force_skill",
    "disable_skill",
    "limit_agents",
    "disable_learning",
    "freeze_architecture",
    "rollback_version",
}
_TARGETED = {
    "pin_memory",
    "block_memory",
    "force_model",
    "force_skill",
    "disable_skill",
    "rollback_version",
}
_SINGLETON = {
    "force_model",
    "force_skill",
    "limit_agents",
    "disable_learning",
    "freeze_architecture",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _text(value: object, field: str, maximum: int) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be text")
    text = value.strip()
    if not text or len(text) > maximum:
        raise ValueError(f"{field} must be 1..{maximum} characters")
    assert_secret_free(text, f"human override {field}")
    return text


@dataclass(frozen=True)
class HumanOverrideRequest:
    action: OverrideAction
    scope: str
    target_id: str | None
    value: dict[str, object]
    actor_id: str
    reason: str

    def __post_init__(self) -> None:
        if self.action not in _ACTIONS:
            raise ValueError("unsupported human override action")
        _text(self.scope, "scope", 255)
        _text(self.actor_id, "actor_id", 255)
        _text(self.reason, "reason", 4_000)
        if self.action in _TARGETED:
            if self.target_id is None:
                raise ValueError(f"{self.action} requires target_id")
            _text(self.target_id, "target_id", 512)
        elif self.target_id is not None:
            raise ValueError(f"{self.action} does not accept target_id")
        if not isinstance(self.value, dict):
            raise ValueError("override value must be an object")
        if self.action == "limit_agents":
            if set(self.value) != {"max_agents"}:
                raise ValueError("limit_agents requires only max_agents")
            limit = self.value["max_agents"]
            if type(limit) is not int or not 1 <= limit <= 8:
                raise ValueError("max_agents must be 1..8")
        elif self.action == "rollback_version":
            kind = self.value.get("version_kind")
            allowed = (
                {"version_kind"}
                if kind == "skill_evolution"
                else {"version_kind", "expected_head_id"}
            )
            if kind not in {"skill_evolution", "improvement_policy"}:
                raise ValueError("unsupported rollback version_kind")
            if set(self.value) != allowed:
                raise ValueError("rollback_version has an invalid value shape")
            if kind == "improvement_policy":
                _text(self.value["expected_head_id"], "expected_head_id", 512)
        elif self.value:
            raise ValueError(f"{self.action} does not accept value fields")
        assert_secret_free(
            json.dumps(self.value, sort_keys=True),
            "human override value",
        )

    @classmethod
    def from_dict(cls, payload: object) -> "HumanOverrideRequest":
        if not isinstance(payload, dict) or set(payload) != {
            "action",
            "scope",
            "target_id",
            "value",
            "actor_id",
            "reason",
        }:
            raise ValueError("human override request has an invalid shape")
        return cls(
            action=str(payload["action"]),
            scope=str(payload["scope"]),
            target_id=(
                None
                if payload["target_id"] is None
                else str(payload["target_id"])
            ),
            value=payload["value"],
            actor_id=str(payload["actor_id"]),
            reason=str(payload["reason"]),
        )


@dataclass(frozen=True)
class HumanOverride:
    id: str
    action: str
    scope: str
    target_id: str | None
    value: dict[str, object]
    actor_id: str
    reason: str
    status: str
    events: tuple[dict[str, object], ...]
    created_at: str

    def as_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "action": self.action,
            "scope": self.scope,
            "target_id": self.target_id,
            "value": self.value,
            "actor_id": self.actor_id,
            "reason": self.reason,
            "status": self.status,
            "events": list(self.events),
            "created_at": self.created_at,
        }


class HumanOverrideController:
    """Append-only human controls with exact scope and explicit revocation."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def _event(
        self,
        override_id: str,
        event: str,
        *,
        actor_id: str,
        reason: str,
        details: dict[str, object] | None = None,
    ) -> None:
        assert_secret_free(
            json.dumps(details or {}, sort_keys=True),
            "human override event",
        )
        sequence = int(
            self.connection.execute(
                """
                SELECT COALESCE(MAX(sequence), 0) + 1
                FROM human_override_events WHERE override_id=?
                """,
                (override_id,),
            ).fetchone()[0]
        )
        self.connection.execute(
            """
            INSERT INTO human_override_events(
                id, override_id, sequence, event, actor_id, reason,
                details_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(uuid.uuid4()),
                override_id,
                sequence,
                event,
                _text(actor_id, "actor_id", 255),
                _text(reason, "reason", 4_000),
                json.dumps(details or {}, sort_keys=True),
                _now(),
            ),
        )

    def _active_rows(
        self,
        action: str,
        *,
        scope: str | None = None,
        target_id: str | None = None,
    ) -> list[sqlite3.Row]:
        clauses = [
            "o.action=?",
            (
                "latest.event='activated'"
                if action == "rollback_version"
                else "latest.event IN ('activated','applied')"
            ),
        ]
        params: list[object] = [action]
        if scope is not None:
            clauses.append("o.scope IN (?, 'global')")
            params.append(scope)
        if target_id is not None:
            clauses.append("o.target_id=?")
            params.append(target_id)
        return self.connection.execute(
            f"""
            SELECT o.*, latest.event AS current_event
            FROM human_overrides o
            JOIN human_override_events latest
              ON latest.override_id=o.id
             AND latest.sequence=(
                 SELECT MAX(e.sequence) FROM human_override_events e
                 WHERE e.override_id=o.id
             )
            WHERE {' AND '.join(clauses)}
            ORDER BY CASE WHEN o.scope=? THEN 0 ELSE 1 END,
                     o.created_at DESC, o.id DESC
            """,
            (*params, scope or ""),
        ).fetchall()

    def begin(self, request: HumanOverrideRequest) -> HumanOverride:
        conflict_target = request.target_id if request.action not in _SINGLETON else None
        if self._active_rows(
            request.action,
            scope=request.scope,
            target_id=conflict_target,
        ):
            raise ValueError(
                "an active human override already controls this action and scope"
            )
        override_id = str(uuid.uuid4())
        now = _now()
        try:
            self.connection.execute("BEGIN IMMEDIATE")
            self.connection.execute(
                """
                INSERT INTO human_overrides(
                    id, action, scope, target_id, value_json, actor_id,
                    reason, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    override_id,
                    request.action,
                    request.scope,
                    request.target_id,
                    json.dumps(request.value, sort_keys=True),
                    request.actor_id,
                    request.reason,
                    now,
                ),
            )
            self._event(
                override_id,
                "activated",
                actor_id=request.actor_id,
                reason=request.reason,
            )
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise
        return self.get(override_id)

    def mark(
        self,
        override_id: str,
        event: Literal["applied", "failed"],
        *,
        details: dict[str, object],
    ) -> HumanOverride:
        current = self.get(override_id)
        if current.status != "active":
            raise ValueError("human override is not active")
        with self.connection:
            self._event(
                override_id,
                event,
                actor_id=current.actor_id,
                reason=current.reason,
                details=details,
            )
        return self.get(override_id)

    def revoke(
        self, override_id: str, *, actor_id: str, reason: str
    ) -> HumanOverride:
        current = self.get(override_id)
        if current.status != "active":
            raise ValueError("only an active override can be revoked")
        with self.connection:
            self._event(
                override_id,
                "revoked",
                actor_id=actor_id,
                reason=reason,
                details={"domain_state_automatically_reversed": False},
            )
        return self.get(override_id)

    def get(self, override_id: str) -> HumanOverride:
        row = self.connection.execute(
            "SELECT * FROM human_overrides WHERE id=?", (override_id,)
        ).fetchone()
        if row is None:
            raise KeyError(override_id)
        events = tuple(
            {
                **dict(item),
                "details": json.loads(item["details_json"]),
            }
            for item in self.connection.execute(
                """
                SELECT sequence, event, actor_id, reason, details_json, created_at
                FROM human_override_events
                WHERE override_id=? ORDER BY sequence
                """,
                (override_id,),
            ).fetchall()
        )
        for item in events:
            item.pop("details_json")
        latest = str(events[-1]["event"])
        status = (
            "active"
            if latest == "activated"
            or (
                latest == "applied"
                and row["action"] != "rollback_version"
            )
            else latest
        )
        return HumanOverride(
            id=row["id"],
            action=row["action"],
            scope=row["scope"],
            target_id=row["target_id"],
            value=json.loads(row["value_json"]),
            actor_id=row["actor_id"],
            reason=row["reason"],
            status=status,
            events=events,
            created_at=row["created_at"],
        )

    def list(self, *, active_only: bool = False) -> tuple[HumanOverride, ...]:
        ids = [
            str(row["id"])
            for row in self.connection.execute(
                "SELECT id FROM human_overrides ORDER BY created_at DESC, id DESC"
            ).fetchall()
        ]
        records = tuple(self.get(identifier) for identifier in ids)
        return (
            tuple(item for item in records if item.status == "active")
            if active_only
            else records
        )

    def effective(
        self,
        action: str,
        scope: str,
        *,
        target_id: str | None = None,
    ) -> HumanOverride | None:
        rows = self._active_rows(action, scope=scope, target_id=target_id)
        return None if not rows else self.get(str(rows[0]["id"]))

    def targets(self, action: str, scope: str) -> frozenset[str]:
        return frozenset(
            str(row["target_id"])
            for row in self._active_rows(action, scope=scope)
            if row["target_id"] is not None
        )
