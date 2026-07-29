from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime

EVENT_TYPES = frozenset(
    {
        "MEMORY_CREATED",
        "MEMORY_SUPERSEDED",
        "SKILL_GENERATED",
        "SKILL_PROMOTED",
        "SKILL_RETIRED",
        "ROUTING_CHANGED",
        "AGENT_CREATED",
        "PERMISSION_DENIED",
    }
)
ENTITY_TYPES = frozenset(
    {"memory", "skill", "routing", "agent", "permission"}
)


def _timestamp(value: str | None, field: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be an ISO-8601 timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{field} must include a timezone")
    return value


@dataclass(frozen=True)
class AuditQuery:
    event_type: str | None = None
    entity_type: str | None = None
    entity_id: str | None = None
    after: str | None = None
    before: str | None = None
    limit: int = 100

    def __post_init__(self) -> None:
        if self.event_type is not None and self.event_type not in EVENT_TYPES:
            raise ValueError("Unknown audit event type")
        if self.entity_type is not None and self.entity_type not in ENTITY_TYPES:
            raise ValueError("Unknown audit entity type")
        if self.entity_id is not None and (
            not isinstance(self.entity_id, str)
            or not self.entity_id.strip()
            or self.entity_id != self.entity_id.strip()
            or len(self.entity_id) > 512
        ):
            raise ValueError("Audit entity ID is invalid")
        _timestamp(self.after, "after")
        _timestamp(self.before, "before")
        if (
            isinstance(self.limit, bool)
            or not isinstance(self.limit, int)
            or not 1 <= self.limit <= 1_000
        ):
            raise ValueError("Audit limit must be 1..1000")


class AuditViewer:
    """Read-only projection over selected high-value mutation events."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    @staticmethod
    def _event(row: sqlite3.Row) -> dict[str, object]:
        return {
            "sequence": int(row["sequence"]),
            "id": row["id"],
            "event_type": row["event_type"],
            "entity_type": row["entity_type"],
            "entity_id": row["entity_id"],
            "source_table": row["source_table"],
            "source_row_id": row["source_row_id"],
            "details": json.loads(row["details_json"]),
            "created_at": row["created_at"],
        }

    def list(self, query: AuditQuery = AuditQuery()) -> list[dict[str, object]]:
        clauses: list[str] = []
        parameters: list[object] = []
        for column, value in (
            ("event_type", query.event_type),
            ("entity_type", query.entity_type),
            ("entity_id", query.entity_id),
        ):
            if value is not None:
                clauses.append(f"{column}=?")
                parameters.append(value)
        if query.after is not None:
            clauses.append("created_at>=?")
            parameters.append(query.after)
        if query.before is not None:
            clauses.append("created_at<=?")
            parameters.append(query.before)
        where = "" if not clauses else "WHERE " + " AND ".join(clauses)
        rows = self.connection.execute(
            f"""
            SELECT *
            FROM audit_events
            {where}
            ORDER BY sequence DESC
            LIMIT ?
            """,
            (*parameters, query.limit),
        ).fetchall()
        return [self._event(row) for row in rows]

    def get(self, event_id: str) -> dict[str, object]:
        if (
            not isinstance(event_id, str)
            or len(event_id) != 32
            or any(character not in "0123456789abcdef" for character in event_id)
        ):
            raise ValueError("Audit event ID is invalid")
        row = self.connection.execute(
            "SELECT * FROM audit_events WHERE id=?", (event_id,)
        ).fetchone()
        if row is None:
            raise LookupError(f"Unknown audit event: {event_id}")
        return self._event(row)

    def summary(self) -> dict[str, object]:
        rows = self.connection.execute(
            """
            SELECT event_type, COUNT(*) AS count,
                   MIN(sequence) AS first_sequence,
                   MAX(sequence) AS last_sequence
            FROM audit_events
            GROUP BY event_type
            ORDER BY event_type
            """
        ).fetchall()
        latest = self.connection.execute(
            "SELECT COALESCE(MAX(sequence), 0) FROM audit_events"
        ).fetchone()[0]
        return {
            "event_types": {
                row["event_type"]: {
                    "count": int(row["count"]),
                    "first_sequence": int(row["first_sequence"]),
                    "last_sequence": int(row["last_sequence"]),
                }
                for row in rows
            },
            "latest_sequence": int(latest),
            "scope": sorted(EVENT_TYPES),
            "ordinary_state_event_sourced": False,
        }
