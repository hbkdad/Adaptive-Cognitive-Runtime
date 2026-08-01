from __future__ import annotations

import json
import os
import sqlite3
import uuid
from datetime import datetime, timezone

from .secret_management import assert_secret_free


BLOCKED_ACTIONS = (
    "skill_generation",
    "skill_mutation",
    "memory_deletion",
    "agent_generation",
    "shell_write",
    "autonomous_optimization",
    "project_state_write",
)
PERMITTED_ACTIONS = (
    "read_only_retrieval",
    "basic_model_inference",
    "inspection",
    "rollback",
    "safe_mode_audit",
)
_FALSE_ENV_VALUES = {"", "0", "false", "no", "off"}


class SafeModeViolation(PermissionError):
    """Raised when containment mode blocks a state-changing operation."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _text(value: object, field: str, maximum: int) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be text")
    normalized = value.strip()
    if not normalized or len(normalized) > maximum:
        raise ValueError(f"{field} must be 1..{maximum} characters")
    assert_secret_free(normalized, f"safe mode {field}")
    return normalized


class SafeModeController:
    """Persistent containment state with an environment emergency latch."""

    def __init__(
        self,
        connection: sqlite3.Connection,
        *,
        environment: dict[str, str] | None = None,
    ) -> None:
        self.connection = connection
        self.environment = os.environ if environment is None else environment

    def _environment_enabled(self) -> bool:
        value = self.environment.get("ACR_SAFE_MODE")
        return value is not None and value.strip().casefold() not in _FALSE_ENV_VALUES

    def _state(self) -> sqlite3.Row:
        row = self.connection.execute(
            "SELECT * FROM safe_mode_state WHERE id=1"
        ).fetchone()
        if row is None:
            raise RuntimeError("safe mode state is unavailable")
        return row

    def enabled(self) -> bool:
        return self._environment_enabled() or bool(self._state()["enabled"])

    def status(self) -> dict[str, object]:
        state = self._state()
        database_enabled = bool(state["enabled"])
        environment_enabled = self._environment_enabled()
        sources = []
        if database_enabled:
            sources.append("database")
        if environment_enabled:
            sources.append("environment")
        return {
            "enabled": database_enabled or environment_enabled,
            "sources": sources,
            "database_enabled": database_enabled,
            "environment_enabled": environment_enabled,
            "changed_at": state["changed_at"],
            "changed_by": state["changed_by"],
            "reason": state["reason"],
            "blocked_actions": list(BLOCKED_ACTIONS),
            "permitted_actions": list(PERMITTED_ACTIONS),
        }

    def _event(
        self,
        event: str,
        *,
        enabled: bool,
        actor_id: str,
        reason: str,
        details: dict[str, object] | None = None,
    ) -> None:
        payload = details or {}
        assert_secret_free(
            json.dumps(payload, sort_keys=True),
            "safe mode event details",
        )
        sequence = int(
            self.connection.execute(
                "SELECT COALESCE(MAX(sequence), 0) + 1 FROM safe_mode_events"
            ).fetchone()[0]
        )
        self.connection.execute(
            """
            INSERT INTO safe_mode_events(
                id, sequence, event, enabled, actor_id, reason,
                details_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(uuid.uuid4()),
                sequence,
                event,
                int(enabled),
                _text(actor_id, "actor_id", 255),
                _text(reason, "reason", 4_000),
                json.dumps(payload, sort_keys=True),
                _now(),
            ),
        )

    def enable(self, *, actor_id: str, reason: str) -> dict[str, object]:
        actor = _text(actor_id, "actor_id", 255)
        explanation = _text(reason, "reason", 4_000)
        state = self._state()
        if bool(state["enabled"]):
            raise ValueError("safe mode is already enabled in the database")
        now = _now()
        with self.connection:
            self.connection.execute(
                """
                UPDATE safe_mode_state
                SET enabled=1, changed_at=?, changed_by=?, reason=?
                WHERE id=1 AND enabled=0
                """,
                (now, actor, explanation),
            )
            self._event(
                "enabled",
                enabled=True,
                actor_id=actor,
                reason=explanation,
                details={"environment_latch": self._environment_enabled()},
            )
        return self.status()

    def disable(self, *, actor_id: str, reason: str) -> dict[str, object]:
        actor = _text(actor_id, "actor_id", 255)
        explanation = _text(reason, "reason", 4_000)
        if self._environment_enabled():
            raise SafeModeViolation(
                "safe mode cannot be disabled while ACR_SAFE_MODE is active"
            )
        state = self._state()
        if not bool(state["enabled"]):
            raise ValueError("safe mode is not enabled in the database")
        now = _now()
        with self.connection:
            self.connection.execute(
                """
                UPDATE safe_mode_state
                SET enabled=0, changed_at=?, changed_by=?, reason=?
                WHERE id=1 AND enabled=1
                """,
                (now, actor, explanation),
            )
            self._event(
                "disabled",
                enabled=False,
                actor_id=actor,
                reason=explanation,
            )
        return self.status()

    def assert_allowed(self, action: str) -> None:
        if action not in BLOCKED_ACTIONS:
            raise ValueError("unknown safe mode action")
        if not self.enabled():
            return
        with self.connection:
            self._event(
                "blocked",
                enabled=True,
                actor_id="runtime",
                reason="safe mode containment policy",
                details={"action": action},
            )
        raise SafeModeViolation(f"safe mode blocks {action.replace('_', ' ')}")

    def events(self, *, limit: int = 100) -> list[dict[str, object]]:
        if type(limit) is not int or not 1 <= limit <= 1_000:
            raise ValueError("safe mode event limit must be 1..1000")
        rows = self.connection.execute(
            """
            SELECT * FROM safe_mode_events
            ORDER BY sequence DESC LIMIT ?
            """,
            (limit,),
        ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["enabled"] = bool(item["enabled"])
            item["details"] = json.loads(item.pop("details_json"))
            result.append(item)
        return result
