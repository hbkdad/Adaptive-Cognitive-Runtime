from __future__ import annotations

import base64
import json
import re
from datetime import datetime, timezone
from typing import Any, Iterable

from .db import RuntimeDB
from .memory import LifecycleState, MemoryStatus, MemoryType, Sensitivity
from .scoring import fts_query
from .secret_management import redact_secret_text


VISIBLE_SENSITIVITIES = (Sensitivity.PUBLIC, Sensitivity.INTERNAL)
VISIBLE_STATUSES = tuple(
    status for status in MemoryStatus if status is not MemoryStatus.DELETED
)
VISIBLE_LIFECYCLES = tuple(
    state for state in LifecycleState if state is not LifecycleState.DELETED
)
MAX_PAGE_SIZE = 100

_WINDOWS_PATH = re.compile(
    r"(?i)(?:[a-z]:\\|\\\\)[^\r\n]*?(?=(?::\d+(?::\d+)?)?$)"
)
_POSIX_PATH = re.compile(r"/(?:[^/\r\n]+/)+[^/\r\n]+")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _bounded_limit(limit: int) -> int:
    if not 1 <= limit <= MAX_PAGE_SIZE:
        raise ValueError(
            f"memory inspector limit must be between 1 and {MAX_PAGE_SIZE}"
        )
    return limit


def _nonempty(value: str, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} cannot be empty")
    return value


def _redact_reference(value: str | None) -> str | None:
    if value is None:
        return None
    redacted = redact_secret_text(value)
    redacted = _WINDOWS_PATH.sub("[REDACTED_PATH]", redacted)
    redacted = _POSIX_PATH.sub("[REDACTED_PATH]", redacted)
    return redacted


class MemoryInspector:
    """Bounded, read-only memory projections for an exact authorized scope."""

    def __init__(self, source: RuntimeDB | object) -> None:
        database = (
            source
            if isinstance(source, RuntimeDB)
            else getattr(source, "db", None)
        )
        if not isinstance(database, RuntimeDB):
            raise TypeError("MemoryInspector requires RuntimeDB or AdaptiveRuntime")
        self.db = database
        self.connection = database.connection

    @staticmethod
    def _cursor(created_at: str, memory_id: str) -> str:
        raw = json.dumps(
            [created_at, memory_id], separators=(",", ":")
        ).encode("utf-8")
        return base64.urlsafe_b64encode(raw).decode("ascii")

    @staticmethod
    def _decode_cursor(cursor: str) -> tuple[str, str]:
        try:
            payload = json.loads(
                base64.urlsafe_b64decode(cursor.encode("ascii")).decode("utf-8")
            )
        except Exception as error:
            raise ValueError("invalid memory inspector cursor") from error
        if (
            not isinstance(payload, list)
            or len(payload) != 2
            or not all(isinstance(item, str) for item in payload)
        ):
            raise ValueError("invalid memory inspector cursor")
        return payload[0], payload[1]

    @staticmethod
    def _enum_values(
        values: Iterable[str | Any],
        enum_type: type[Any],
        allowed: tuple[Any, ...],
        name: str,
    ) -> tuple[str, ...]:
        resolved: list[Any] = []
        try:
            for value in values:
                item = value if isinstance(value, enum_type) else enum_type(value)
                if item not in allowed:
                    raise ValueError
                resolved.append(item)
        except (TypeError, ValueError) as error:
            raise ValueError(f"unsupported memory inspector {name}") from error
        return tuple(item.value for item in resolved)

    @staticmethod
    def _placeholders(values: tuple[str, ...]) -> str:
        return ",".join("?" for _ in values)

    def _visible_link(self, memory_id: str | None, *, scope: str) -> str | None:
        if memory_id is None:
            return None
        row = self.connection.execute(
            """
            SELECT id FROM memories
            WHERE id=? AND scope=?
              AND sensitivity IN ('public','internal')
              AND status != 'deleted' AND lifecycle_state != 'deleted'
            """,
            (memory_id, scope),
        ).fetchone()
        return str(row["id"]) if row is not None else None

    def _project(self, row: Any, *, scope: str) -> dict[str, Any]:
        evidence = json.loads(row["evidence_json"])
        safe_evidence = [
            _redact_reference(str(reference)) for reference in evidence
        ]
        return {
            "id": row["id"],
            "type": row["type"],
            "scope": row["scope"],
            "subject": redact_secret_text(row["subject"])
            if row["subject"] is not None
            else None,
            "content": redact_secret_text(row["content"]),
            "status": row["status"],
            "sensitivity": row["sensitivity"],
            "provenance": {
                "source_type": redact_secret_text(row["source_type"])
                if row["source_type"] is not None
                else None,
                "source_id": _redact_reference(row["source_id"]),
                "evidence": safe_evidence,
            },
            "confidence": float(row["confidence"]),
            "importance": float(row["importance"]),
            "utility": float(row["utility_score"]),
            "usage": {
                "last_accessed": row["last_accessed"],
                "access_count": int(row["access_count"]),
                "successful_uses": int(row["successful_uses"]),
                "failed_uses": int(row["failed_uses"]),
                "history_status": "aggregate_only",
            },
            "validity": {
                "valid_from": row["valid_from"],
                "valid_until": row["valid_until"],
            },
            "freshness": {
                "observed_at": row["observed_at"],
                "source_freshness": row["source_freshness"],
                "expected_half_life_days": row["expected_half_life_days"],
                "requires_refresh": bool(row["requires_refresh"]),
            },
            "lifecycle": {
                "state": row["lifecycle_state"],
                "updated_at": row["lifecycle_updated_at"] or row["updated_at"],
                "archived_at": row["archived_at"],
                "pinned": bool(row["pinned"]),
                "pinned_at": row["pinned_at"],
                "pin_reason": redact_secret_text(row["pin_reason"])
                if row["pin_reason"] is not None
                else None,
            },
            "supersession": {
                "supersedes": self._visible_link(
                    row["supersedes"], scope=scope
                ),
                "superseded_by": self._visible_link(
                    row["superseded_by"], scope=scope
                ),
            },
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def search(
        self,
        *,
        scope: str,
        text: str | None = None,
        types: Iterable[MemoryType | str] = (),
        statuses: Iterable[MemoryStatus | str] = (),
        lifecycle_states: Iterable[LifecycleState | str] = (),
        subject: str | None = None,
        minimum_confidence: float = 0.0,
        minimum_utility: float = 0.0,
        limit: int = 50,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        scope = _nonempty(scope, "scope")
        limit = _bounded_limit(limit)
        if not 0 <= minimum_confidence <= 1:
            raise ValueError("minimum_confidence must be between 0 and 1")
        if not 0 <= minimum_utility <= 1:
            raise ValueError("minimum_utility must be between 0 and 1")

        type_values = self._enum_values(
            types, MemoryType, tuple(MemoryType), "type"
        )
        status_values = self._enum_values(
            statuses, MemoryStatus, VISIBLE_STATUSES, "status"
        ) or tuple(status.value for status in VISIBLE_STATUSES)
        lifecycle_values = self._enum_values(
            lifecycle_states,
            LifecycleState,
            VISIBLE_LIFECYCLES,
            "lifecycle state",
        ) or tuple(state.value for state in VISIBLE_LIFECYCLES)

        clauses = [
            "m.scope = ?",
            "m.sensitivity IN ('public','internal')",
            f"m.status IN ({self._placeholders(status_values)})",
            f"m.lifecycle_state IN ({self._placeholders(lifecycle_values)})",
            "m.confidence >= ?",
            "m.utility_score >= ?",
        ]
        params: list[object] = [
            scope,
            *status_values,
            *lifecycle_values,
            minimum_confidence,
            minimum_utility,
        ]
        join = ""
        expression = fts_query(text or "")
        if expression:
            join = "JOIN memories_fts ON memories_fts.rowid=m.rowid"
            clauses.append("memories_fts MATCH ?")
            params.append(expression)
        if type_values:
            clauses.append(
                f"m.type IN ({self._placeholders(type_values)})"
            )
            params.extend(type_values)
        if subject is not None:
            clauses.append("m.subject = ? COLLATE NOCASE")
            params.append(_nonempty(subject, "subject"))
        if cursor is not None:
            created_at, memory_id = self._decode_cursor(cursor)
            clauses.append(
                "(m.created_at < ? OR (m.created_at = ? AND m.id < ?))"
            )
            params.extend((created_at, created_at, memory_id))
        params.append(limit + 1)
        rows = self.connection.execute(
            f"""
            SELECT m.* FROM memories m
            {join}
            WHERE {' AND '.join(clauses)}
            ORDER BY m.created_at DESC, m.id DESC
            LIMIT ?
            """,
            params,
        ).fetchall()
        has_more = len(rows) > limit
        rows = rows[:limit]
        items = [self._project(row, scope=scope) for row in rows]
        return {
            "status": "available" if items else "empty",
            "items": items,
            "count": len(items),
            "next_cursor": (
                self._cursor(rows[-1]["created_at"], rows[-1]["id"])
                if has_more and rows
                else None
            ),
            "reason": None if items else "no_visible_memories",
            "as_of": _now(),
        }

    def list(
        self,
        *,
        scope: str,
        limit: int = 50,
        cursor: str | None = None,
        **filters: Any,
    ) -> dict[str, Any]:
        return self.search(
            scope=scope, limit=limit, cursor=cursor, **filters
        )

    def inspect(self, memory_id: str, *, scope: str) -> dict[str, Any] | None:
        memory_id = _nonempty(memory_id, "memory_id")
        scope = _nonempty(scope, "scope")
        row = self.connection.execute(
            """
            SELECT * FROM memories
            WHERE id=? AND scope=?
              AND sensitivity IN ('public','internal')
              AND status != 'deleted' AND lifecycle_state != 'deleted'
            """,
            (memory_id, scope),
        ).fetchone()
        return self._project(row, scope=scope) if row is not None else None

    def timeline(
        self, subject: str, *, scope: str, limit: int = 100
    ) -> dict[str, Any]:
        subject = _nonempty(subject, "subject")
        scope = _nonempty(scope, "scope")
        limit = _bounded_limit(limit)
        rows = self.connection.execute(
            """
            SELECT * FROM memories
            WHERE subject=? COLLATE NOCASE AND scope=?
              AND sensitivity IN ('public','internal')
              AND status != 'deleted' AND lifecycle_state != 'deleted'
            ORDER BY valid_from ASC, created_at ASC, id ASC
            LIMIT ?
            """,
            (subject, scope, limit),
        ).fetchall()
        items = [self._project(row, scope=scope) for row in rows]
        return {
            "status": "available" if items else "empty",
            "subject": redact_secret_text(subject),
            "scope": scope,
            "items": items,
            "count": len(items),
            "truncated": len(items) == limit,
            "reason": None if items else "no_visible_subject_history",
            "as_of": _now(),
        }

    def related(
        self,
        subject: str,
        *,
        scope: str,
        exclude_id: str | None = None,
        limit: int = 50,
    ) -> dict[str, Any]:
        subject = _nonempty(subject, "subject")
        scope = _nonempty(scope, "scope")
        limit = _bounded_limit(limit)
        clauses = [
            "subject=? COLLATE NOCASE",
            "scope=?",
            "sensitivity IN ('public','internal')",
            "status != 'deleted'",
            "lifecycle_state != 'deleted'",
        ]
        params: list[object] = [subject, scope]
        if exclude_id is not None:
            clauses.append("id != ?")
            params.append(_nonempty(exclude_id, "exclude_id"))
        params.append(limit)
        rows = self.connection.execute(
            f"""
            SELECT * FROM memories
            WHERE {' AND '.join(clauses)}
            ORDER BY updated_at DESC, id DESC
            LIMIT ?
            """,
            params,
        ).fetchall()
        items = [self._project(row, scope=scope) for row in rows]
        return {
            "status": "available" if items else "empty",
            "subject": redact_secret_text(subject),
            "scope": scope,
            "relation": "exact_subject",
            "items": items,
            "count": len(items),
            "truncated": len(items) == limit,
            "reason": None if items else "no_visible_related_memories",
            "as_of": _now(),
        }
