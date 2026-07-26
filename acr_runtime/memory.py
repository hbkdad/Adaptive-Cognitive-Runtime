from __future__ import annotations

import base64
import json
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Protocol, Sequence

from .scoring import estimate_tokens, fts_query


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_timestamp(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError(f"Invalid ISO timestamp: {value}") from error
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def normalize_timestamp(value: str) -> str:
    return parse_timestamp(value).isoformat()


class MemoryType(str, Enum):
    SEMANTIC = "semantic"
    EPISODIC = "episodic"
    PROCEDURAL = "procedural"
    FAILURE = "failure"
    DECISION = "decision"
    PREFERENCE = "preference"
    ENVIRONMENT = "environment"
    TEMPORARY = "temporary"


class MemoryStatus(str, Enum):
    CANDIDATE = "candidate"
    CONFIRMED = "confirmed"
    SUPERSEDED = "superseded"
    ARCHIVED = "archived"
    QUARANTINED = "quarantined"
    DELETED = "deleted"


ALLOWED_STATUS_TRANSITIONS = {
    MemoryStatus.CANDIDATE: {
        MemoryStatus.CONFIRMED,
        MemoryStatus.QUARANTINED,
        MemoryStatus.ARCHIVED,
        MemoryStatus.DELETED,
    },
    MemoryStatus.CONFIRMED: {
        MemoryStatus.SUPERSEDED,
        MemoryStatus.ARCHIVED,
        MemoryStatus.QUARANTINED,
        MemoryStatus.DELETED,
    },
    MemoryStatus.SUPERSEDED: {MemoryStatus.ARCHIVED, MemoryStatus.DELETED},
    MemoryStatus.ARCHIVED: {MemoryStatus.CANDIDATE, MemoryStatus.DELETED},
    MemoryStatus.QUARANTINED: {
        MemoryStatus.CANDIDATE,
        MemoryStatus.ARCHIVED,
        MemoryStatus.DELETED,
    },
    MemoryStatus.DELETED: set(),
}


@dataclass(frozen=True)
class MemoryCreate:
    type: MemoryType
    content: str
    scope: str = "global"
    subject: str | None = None
    structured_payload_json: str = "{}"
    confidence: float = 0.8
    importance: float = 0.5
    utility_score: float = 0.0
    source_type: str | None = None
    source_id: str | None = None
    evidence: tuple[str, ...] = ()
    status: MemoryStatus = MemoryStatus.CANDIDATE
    valid_from: str | None = None
    valid_until: str | None = None
    supersedes: str | None = None

    def __post_init__(self) -> None:
        if not self.content.strip():
            raise ValueError("Memory content cannot be empty")
        if not self.scope.strip():
            raise ValueError("Memory scope cannot be empty")
        for name, value in (
            ("confidence", self.confidence),
            ("importance", self.importance),
            ("utility_score", self.utility_score),
        ):
            if not 0 <= value <= 1:
                raise ValueError(f"{name} must be between 0 and 1")
        payload = json.loads(self.structured_payload_json)
        if not isinstance(payload, (dict, list)):
            raise ValueError("structured payload must be a JSON object or array")
        if len(self.content) > 1_000_000:
            raise ValueError("Memory content exceeds the 1 MB limit")
        if len(self.structured_payload_json) > 1_000_000:
            raise ValueError("Structured payload exceeds the 1 MB limit")
        if self.valid_from is not None:
            parse_timestamp(self.valid_from)
        if self.valid_until is not None:
            parse_timestamp(self.valid_until)
        if (
            self.valid_from is not None
            and self.valid_until is not None
            and parse_timestamp(self.valid_until) <= parse_timestamp(self.valid_from)
        ):
            raise ValueError("valid_until must be later than valid_from")


@dataclass(frozen=True)
class MemoryPatch:
    content: str | None = None
    subject: str | None = None
    structured_payload_json: str | None = None
    confidence: float | None = None
    importance: float | None = None
    utility_score: float | None = None
    evidence: tuple[str, ...] | None = None
    expected_updated_at: str | None = None

    def __post_init__(self) -> None:
        if self.content is not None and not self.content.strip():
            raise ValueError("Memory content cannot be empty")
        if self.content is not None and len(self.content) > 1_000_000:
            raise ValueError("Memory content exceeds the 1 MB limit")
        if self.structured_payload_json is not None:
            payload = json.loads(self.structured_payload_json)
            if not isinstance(payload, (dict, list)):
                raise ValueError("structured payload must be a JSON object or array")
            if len(self.structured_payload_json) > 1_000_000:
                raise ValueError("Structured payload exceeds the 1 MB limit")
        for name, value in (
            ("confidence", self.confidence),
            ("importance", self.importance),
            ("utility_score", self.utility_score),
        ):
            if value is not None and not 0 <= value <= 1:
                raise ValueError(f"{name} must be between 0 and 1")


@dataclass(frozen=True)
class MemoryRecord:
    id: str
    type: MemoryType
    scope: str
    subject: str | None
    content: str
    structured_payload_json: str
    confidence: float
    importance: float
    utility_score: float
    source_type: str | None
    source_id: str | None
    evidence: tuple[str, ...]
    created_at: str
    updated_at: str
    valid_from: str
    valid_until: str | None
    last_accessed: str | None
    access_count: int
    successful_uses: int
    failed_uses: int
    supersedes: str | None
    superseded_by: str | None
    status: MemoryStatus
    token_cost: int


@dataclass(frozen=True)
class MemoryQuery:
    scope: str
    text: str | None = None
    types: tuple[MemoryType, ...] = ()
    statuses: tuple[MemoryStatus, ...] = (MemoryStatus.CONFIRMED,)
    subject: str | None = None
    valid_at: str | None = None
    minimum_confidence: float = 0.0
    minimum_utility: float = 0.0
    limit: int = 50
    cursor: str | None = None

    def __post_init__(self) -> None:
        if not 1 <= self.limit <= 200:
            raise ValueError("Memory query limit must be between 1 and 200")


@dataclass(frozen=True)
class MemoryPage:
    records: tuple[MemoryRecord, ...]
    next_cursor: str | None


class MemoryReader(Protocol):
    def get(self, memory_id: str) -> MemoryRecord | None: ...

    def search(self, query: MemoryQuery) -> MemoryPage: ...

    def subject_records(
        self,
        subject: str,
        *,
        scope: str,
        statuses: tuple[MemoryStatus, ...],
    ) -> tuple[MemoryRecord, ...]: ...


class MemoryStore(MemoryReader, Protocol):
    def create(self, memory: MemoryCreate) -> MemoryRecord: ...

    def update(self, memory_id: str, patch: MemoryPatch) -> MemoryRecord: ...

    def set_status(
        self, memory_id: str, status: MemoryStatus
    ) -> MemoryRecord: ...

    def supersede(
        self, old_id: str, new_id: str, *, effective_at: str | None = None
    ) -> None: ...

    def record_usage(self, memory_id: str, *, successful: bool) -> None: ...


class EmbeddingProvider(Protocol):
    def embed(self, texts: Sequence[str]) -> Sequence[Sequence[float]]: ...


class SQLiteMemoryStore:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    @staticmethod
    def _record(row: sqlite3.Row) -> MemoryRecord:
        return MemoryRecord(
            id=row["id"],
            type=MemoryType(row["type"]),
            scope=row["scope"],
            subject=row["subject"],
            content=row["content"],
            structured_payload_json=row["structured_payload_json"],
            confidence=row["confidence"],
            importance=row["importance"],
            utility_score=row["utility_score"],
            source_type=row["source_type"],
            source_id=row["source_id"],
            evidence=tuple(json.loads(row["evidence_json"])),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            valid_from=row["valid_from"],
            valid_until=row["valid_until"],
            last_accessed=row["last_accessed"],
            access_count=row["access_count"],
            successful_uses=row["successful_uses"],
            failed_uses=row["failed_uses"],
            supersedes=row["supersedes"],
            superseded_by=row["superseded_by"],
            status=MemoryStatus(row["status"]),
            token_cost=row["token_cost"],
        )

    def create(self, memory: MemoryCreate) -> MemoryRecord:
        memory_id = str(uuid.uuid4())
        now = utc_now()
        effective_from = (
            normalize_timestamp(memory.valid_from)
            if memory.valid_from is not None
            else now
        )
        valid_until = (
            normalize_timestamp(memory.valid_until)
            if memory.valid_until is not None
            else None
        )
        if (
            valid_until is not None
            and parse_timestamp(valid_until) <= parse_timestamp(effective_from)
        ):
            raise ValueError("valid_until must be later than valid_from")
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO memories (
                    id, type, scope, subject, content, structured_payload_json,
                    confidence, importance, utility_score, source_type, source_id,
                    evidence_json, created_at, updated_at, valid_from, valid_until,
                    last_accessed, access_count, successful_uses, failed_uses,
                    supersedes, superseded_by, status, token_cost
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 0, 0,
                          ?, NULL, ?, ?)
                """,
                (
                    memory_id,
                    memory.type.value,
                    memory.scope,
                    memory.subject,
                    memory.content,
                    memory.structured_payload_json,
                    memory.confidence,
                    memory.importance,
                    memory.utility_score,
                    memory.source_type,
                    memory.source_id,
                    json.dumps(memory.evidence),
                    now,
                    now,
                    effective_from,
                    valid_until,
                    now,
                    memory.supersedes,
                    memory.status.value,
                    estimate_tokens(memory.content),
                ),
            )
            if memory.supersedes:
                self._supersede_in_transaction(
                    memory.supersedes, memory_id, effective_from
                )
        record = self.get(memory_id)
        if record is None:
            raise RuntimeError("Created memory could not be reloaded")
        return record

    def get(self, memory_id: str) -> MemoryRecord | None:
        row = self.connection.execute(
            "SELECT * FROM memories WHERE id = ?", (memory_id,)
        ).fetchone()
        return self._record(row) if row else None

    def subject_records(
        self,
        subject: str,
        *,
        scope: str,
        statuses: tuple[MemoryStatus, ...],
    ) -> tuple[MemoryRecord, ...]:
        if not subject.strip():
            raise ValueError("Memory subject cannot be empty")
        if not statuses:
            return ()
        placeholders = ",".join("?" for _ in statuses)
        rows = self.connection.execute(
            f"""
            SELECT * FROM memories
            WHERE subject = ? COLLATE NOCASE
              AND (scope = ? OR scope = 'global')
              AND status IN ({placeholders})
            ORDER BY valid_from ASC, created_at ASC, id ASC
            """,
            (subject, scope, *(status.value for status in statuses)),
        ).fetchall()
        return tuple(self._record(row) for row in rows)

    @staticmethod
    def _encode_cursor(created_at: str, memory_id: str) -> str:
        raw = json.dumps([created_at, memory_id]).encode("utf-8")
        return base64.urlsafe_b64encode(raw).decode("ascii")

    @staticmethod
    def _decode_cursor(cursor: str) -> tuple[str, str]:
        try:
            created_at, memory_id = json.loads(
                base64.urlsafe_b64decode(cursor.encode("ascii")).decode("utf-8")
            )
        except Exception as error:
            raise ValueError("Invalid memory cursor") from error
        return str(created_at), str(memory_id)

    def search(self, query: MemoryQuery) -> MemoryPage:
        params: list[object] = []
        clauses = ["(m.scope = ? OR m.scope = 'global')"]
        params.append(query.scope)
        join = ""
        expression = fts_query(query.text or "")
        if expression:
            join = "JOIN memories_fts ON memories_fts.rowid = m.rowid"
            clauses.append("memories_fts MATCH ?")
            params.append(expression)
        if query.types:
            clauses.append(f"m.type IN ({','.join('?' for _ in query.types)})")
            params.extend(item.value for item in query.types)
        if query.statuses:
            clauses.append(f"m.status IN ({','.join('?' for _ in query.statuses)})")
            params.extend(item.value for item in query.statuses)
        if query.subject is not None:
            clauses.append("m.subject = ?")
            params.append(query.subject)
        valid_at = (
            normalize_timestamp(query.valid_at)
            if query.valid_at is not None
            else utc_now()
        )
        clauses.extend(
            [
                "julianday(m.valid_from) <= julianday(?)",
                "(m.valid_until IS NULL OR julianday(m.valid_until) > julianday(?))",
            ]
        )
        params.extend([valid_at, valid_at])
        clauses.extend(["m.confidence >= ?", "m.utility_score >= ?"])
        params.extend([query.minimum_confidence, query.minimum_utility])
        if query.cursor:
            created_at, memory_id = self._decode_cursor(query.cursor)
            clauses.append("(m.created_at < ? OR (m.created_at = ? AND m.id < ?))")
            params.extend([created_at, created_at, memory_id])
        params.append(query.limit + 1)
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
        has_more = len(rows) > query.limit
        rows = rows[: query.limit]
        records = tuple(self._record(row) for row in rows)
        next_cursor = (
            self._encode_cursor(records[-1].created_at, records[-1].id)
            if has_more and records
            else None
        )
        return MemoryPage(records, next_cursor)

    def update(self, memory_id: str, patch: MemoryPatch) -> MemoryRecord:
        current = self.get(memory_id)
        if current is None:
            raise KeyError(memory_id)
        if patch.expected_updated_at and patch.expected_updated_at != current.updated_at:
            raise RuntimeError("Memory was modified by another writer")
        values = {
            "content": patch.content,
            "subject": patch.subject,
            "structured_payload_json": patch.structured_payload_json,
            "confidence": patch.confidence,
            "importance": patch.importance,
            "utility_score": patch.utility_score,
            "evidence_json": (
                json.dumps(patch.evidence) if patch.evidence is not None else None
            ),
        }
        assignments = [f"{name} = ?" for name, value in values.items() if value is not None]
        parameters = [value for value in values.values() if value is not None]
        if patch.content is not None:
            assignments.append("token_cost = ?")
            parameters.append(estimate_tokens(patch.content))
        if not assignments:
            return current
        assignments.append("updated_at = ?")
        parameters.extend([utc_now(), memory_id])
        with self.connection:
            self.connection.execute(
                f"UPDATE memories SET {', '.join(assignments)} WHERE id = ?",
                parameters,
            )
        updated = self.get(memory_id)
        if updated is None:
            raise RuntimeError("Updated memory could not be reloaded")
        return updated

    def set_status(self, memory_id: str, status: MemoryStatus) -> MemoryRecord:
        current = self.get(memory_id)
        if current is None:
            raise KeyError(memory_id)
        if status == current.status:
            return current
        if status not in ALLOWED_STATUS_TRANSITIONS[current.status]:
            raise ValueError(
                f"Invalid memory status transition {current.status.value}->{status.value}"
            )
        with self.connection:
            self.connection.execute(
                "UPDATE memories SET status = ?, updated_at = ? WHERE id = ?",
                (status.value, utc_now(), memory_id),
            )
        updated = self.get(memory_id)
        if updated is None:
            raise RuntimeError("Memory status update could not be reloaded")
        return updated

    def _supersede_in_transaction(
        self, old_id: str, new_id: str, occurred_at: str
    ) -> None:
        if old_id == new_id:
            raise ValueError("A memory cannot supersede itself")
        old = self.get(old_id)
        new = self.get(new_id)
        if old is None or new is None:
            raise KeyError(old_id if old is None else new_id)
        if old.superseded_by is not None and old.superseded_by != new_id:
            raise ValueError("Memory already has a different replacement")
        if parse_timestamp(occurred_at) <= parse_timestamp(old.valid_from):
            raise ValueError(
                "A replacement must become effective after the old memory"
            )
        ancestor = old
        seen = {old_id}
        while ancestor.supersedes:
            if ancestor.supersedes == new_id or ancestor.supersedes in seen:
                raise ValueError("Memory supersession would create a cycle")
            seen.add(ancestor.supersedes)
            parent = self.get(ancestor.supersedes)
            if parent is None:
                break
            ancestor = parent
        future_change = parse_timestamp(occurred_at) > parse_timestamp(utc_now())
        old_status = old.status.value if future_change else "superseded"
        effective_until = occurred_at
        if (
            old.valid_until is not None
            and parse_timestamp(old.valid_until) < parse_timestamp(occurred_at)
        ):
            effective_until = old.valid_until
        self.connection.execute(
            """
            UPDATE memories
            SET status = ?, valid_until = ?, superseded_by = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (old_status, effective_until, new_id, utc_now(), old_id),
        )
        self.connection.execute(
            """
            UPDATE memories
            SET supersedes = ?, valid_from = ?, updated_at = ?
            WHERE id = ?
            """,
            (old_id, occurred_at, utc_now(), new_id),
        )

    def supersede(
        self, old_id: str, new_id: str, *, effective_at: str | None = None
    ) -> None:
        new = self.get(new_id)
        if new is None:
            raise KeyError(new_id)
        occurred_at = normalize_timestamp(effective_at or new.valid_from)
        with self.connection:
            self._supersede_in_transaction(old_id, new_id, occurred_at)

    def record_usage(self, memory_id: str, *, successful: bool) -> None:
        with self.connection:
            cursor = self.connection.execute(
                """
                UPDATE memories
                SET access_count = access_count + 1,
                    successful_uses = successful_uses + ?,
                    failed_uses = failed_uses + ?,
                    utility_score = CAST(successful_uses + ? AS REAL)
                        / (access_count + 1),
                    last_accessed = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    int(successful),
                    int(not successful),
                    int(successful),
                    utc_now(),
                    utc_now(),
                    memory_id,
                ),
            )
            if cursor.rowcount != 1:
                raise KeyError(memory_id)
