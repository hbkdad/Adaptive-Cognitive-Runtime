from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Literal

from .memory import parse_timestamp
from .memory_scope import MemoryScopeRegistry
from .secret_management import detect_secret_material

CacheOutcome = Literal[
    "hit",
    "miss",
    "bypass",
    "fill",
    "expired",
    "invalidated",
    "error",
    "pruned",
]
MAX_CACHE_PAYLOAD_BYTES = 262_144
MAX_CACHE_ENTRIES = 1_000
MAX_CACHE_EVENTS = 10_000
RETRIEVAL_CACHE_VERSION = "retrieval-v3-source-class"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )


@dataclass(frozen=True)
class CacheEntry:
    id: str
    key_hash: str
    scope: str
    algorithm_version: str
    source_generation: int
    payload: dict[str, object]
    compute_duration_ms: int
    created_at: str
    expires_at: str
    hit_count: int


class SafeCache:
    """Exact local cache that stores no raw request or memory content."""

    def __init__(
        self,
        connection: sqlite3.Connection,
        *,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        self.connection = connection
        self.clock = clock
        self.scopes = MemoryScopeRegistry(connection)

    def generation(self, namespace: str = "memory_retrieval") -> int:
        row = self.connection.execute(
            "SELECT generation FROM cache_generations WHERE namespace = ?",
            (namespace,),
        ).fetchone()
        if row is None:
            raise RuntimeError(f"Missing cache generation: {namespace}")
        return int(row["generation"])

    def _now(self) -> datetime:
        return self.clock().astimezone(timezone.utc)

    def _finish_write(self, outer_transaction: bool) -> None:
        if not outer_transaction:
            self.connection.commit()

    def retrieval_key(
        self,
        envelope: dict[str, object],
        *,
        scope: str,
        include_ancestors: bool,
    ) -> str | None:
        visible_scopes = self.scopes.visible_scope_ids(
            scope, include_ancestors=include_ancestors
        )
        material = _canonical_json(
            {
                "cache_schema": RETRIEVAL_CACHE_VERSION,
                "cache_type": "retrieval",
                "scope": scope,
                "visible_scopes": visible_scopes,
                "request": envelope,
            }
        )
        if detect_secret_material(material):
            return None
        return hashlib.sha256(material.encode("utf-8")).hexdigest()

    @staticmethod
    def _entry(row: sqlite3.Row) -> CacheEntry:
        encoded = row["payload_json"]
        encoded_bytes = len(encoded.encode("utf-8"))
        if (
            encoded_bytes != int(row["payload_bytes"])
            or encoded_bytes > MAX_CACHE_PAYLOAD_BYTES
        ):
            raise ValueError("cache payload size metadata is invalid")
        payload = json.loads(encoded)
        if not isinstance(payload, dict):
            raise ValueError("cache payload must be an object")
        return CacheEntry(
            id=row["id"],
            key_hash=row["key_hash"],
            scope=row["scope"],
            algorithm_version=row["algorithm_version"],
            source_generation=int(row["source_generation"]),
            payload=payload,
            compute_duration_ms=int(row["compute_duration_ms"]),
            created_at=row["created_at"],
            expires_at=row["expires_at"],
            hit_count=int(row["hit_count"]),
        )

    def event(
        self,
        outcome: CacheOutcome,
        reason: str,
        *,
        entry_id: str | None = None,
        saved_duration_ms: int = 0,
    ) -> None:
        if not reason.strip() or len(reason) > 128:
            raise ValueError("cache event reason must be bounded")
        outer = self.connection.in_transaction
        self.connection.execute(
            """
            INSERT INTO cache_events (
                id, cache_type, entry_id, outcome, reason,
                saved_duration_ms, created_at
            ) VALUES (?, 'retrieval', ?, ?, ?, ?, ?)
            """,
            (
                str(uuid.uuid4()),
                entry_id,
                outcome,
                reason,
                saved_duration_ms,
                self._now().isoformat(),
            ),
        )
        self.connection.execute(
            """
            DELETE FROM cache_events
            WHERE id NOT IN (
                SELECT id FROM cache_events
                ORDER BY created_at DESC, id DESC
                LIMIT ?
            )
            """,
            (MAX_CACHE_EVENTS,),
        )
        self._finish_write(outer)

    def probe(self, key_hash: str) -> CacheEntry | None:
        row = self.connection.execute(
            """
            SELECT * FROM cache_entries
            WHERE cache_type = 'retrieval' AND key_hash = ?
            """,
            (key_hash,),
        ).fetchone()
        if row is None:
            return None
        now = self._now()
        if now >= parse_timestamp(row["expires_at"]):
            outer = self.connection.in_transaction
            self.connection.execute(
                "DELETE FROM cache_entries WHERE id = ?", (row["id"],)
            )
            self._finish_write(outer)
            self.event("expired", "ttl_elapsed", entry_id=row["id"])
            return None
        if int(row["source_generation"]) != self.generation():
            self.invalidate(row["id"], reason="source_generation_changed")
            return None
        try:
            return self._entry(row)
        except (json.JSONDecodeError, TypeError, ValueError):
            self.invalidate(row["id"], reason="invalid_payload")
            return None

    def confirm_hit(self, entry: CacheEntry) -> bool:
        now = self._now().isoformat()
        outer = self.connection.in_transaction
        cursor = self.connection.execute(
            """
            UPDATE cache_entries
            SET hit_count = hit_count + 1, last_hit_at = ?
            WHERE id = ?
              AND source_generation = (
                  SELECT generation FROM cache_generations
                  WHERE namespace = 'memory_retrieval'
              )
              AND julianday(expires_at) > julianday(?)
            """,
            (now, entry.id, now),
        )
        if cursor.rowcount != 1:
            self._finish_write(outer)
            return False
        self.connection.execute(
            """
            INSERT INTO cache_events (
                id, cache_type, entry_id, outcome, reason,
                saved_duration_ms, created_at
            ) VALUES (?, 'retrieval', ?, 'hit', 'fresh_exact_match', ?, ?)
            """,
            (
                str(uuid.uuid4()),
                entry.id,
                entry.compute_duration_ms,
                now,
            ),
        )
        self.connection.execute(
            """
            DELETE FROM cache_events
            WHERE id NOT IN (
                SELECT id FROM cache_events
                ORDER BY created_at DESC, id DESC
                LIMIT ?
            )
            """,
            (MAX_CACHE_EVENTS,),
        )
        self._finish_write(outer)
        return True

    def put(
        self,
        *,
        key_hash: str,
        scope: str,
        source_generation: int,
        payload: dict[str, object],
        compute_duration_ms: int,
        expires_at: datetime,
    ) -> CacheEntry:
        encoded = _canonical_json(payload)
        payload_bytes = len(encoded.encode("utf-8"))
        if payload_bytes > MAX_CACHE_PAYLOAD_BYTES:
            raise ValueError("cache payload exceeds 262144 bytes")
        if detect_secret_material(encoded):
            raise ValueError("cache payload contains secret material")
        if compute_duration_ms < 0:
            raise ValueError("compute_duration_ms cannot be negative")
        now = self._now()
        if expires_at.tzinfo is None or expires_at <= now:
            raise ValueError("cache expiry must be timezone-aware and future")
        entry_id = str(uuid.uuid4())
        outer = self.connection.in_transaction
        self.connection.execute(
            """
            INSERT INTO cache_entries (
                id, cache_type, key_hash, scope, algorithm_version,
                source_generation, payload_json, payload_bytes,
                compute_duration_ms, created_at, expires_at
            ) VALUES (
                ?, 'retrieval', ?, ?, ?, ?, ?, ?, ?, ?, ?
            )
            ON CONFLICT(cache_type, key_hash) DO UPDATE SET
                id = excluded.id,
                scope = excluded.scope,
                algorithm_version = excluded.algorithm_version,
                source_generation = excluded.source_generation,
                payload_json = excluded.payload_json,
                payload_bytes = excluded.payload_bytes,
                compute_duration_ms = excluded.compute_duration_ms,
                created_at = excluded.created_at,
                expires_at = excluded.expires_at,
                last_hit_at = NULL,
                hit_count = 0
            """,
            (
                entry_id,
                key_hash,
                scope,
                RETRIEVAL_CACHE_VERSION,
                source_generation,
                encoded,
                payload_bytes,
                compute_duration_ms,
                now.isoformat(),
                expires_at.astimezone(timezone.utc).isoformat(),
            ),
        )
        self.connection.execute(
            """
            DELETE FROM cache_entries
            WHERE cache_type = 'retrieval'
              AND id NOT IN (
                  SELECT id FROM cache_entries
                  WHERE cache_type = 'retrieval'
                  ORDER BY created_at DESC, id DESC
                  LIMIT ?
              )
            """,
            (MAX_CACHE_ENTRIES,),
        )
        self._finish_write(outer)
        row = self.connection.execute(
            """
            SELECT * FROM cache_entries
            WHERE cache_type = 'retrieval' AND key_hash = ?
            """,
            (key_hash,),
        ).fetchone()
        if row is None:
            raise RuntimeError("cache fill was not retained")
        entry = self._entry(row)
        self.event("fill", "successful_retrieval", entry_id=entry.id)
        return entry

    def invalidate(self, entry_id: str, *, reason: str) -> None:
        outer = self.connection.in_transaction
        self.connection.execute(
            "DELETE FROM cache_entries WHERE id = ?", (entry_id,)
        )
        self._finish_write(outer)
        self.event("invalidated", reason, entry_id=entry_id)

    def prune(self) -> int:
        now = self._now().isoformat()
        outer = self.connection.in_transaction
        cursor = self.connection.execute(
            """
            DELETE FROM cache_entries
            WHERE julianday(expires_at) <= julianday(?)
            """,
            (now,),
        )
        removed = max(0, cursor.rowcount)
        self._finish_write(outer)
        if removed:
            self.event("pruned", "expired_entries", saved_duration_ms=0)
        return removed

    def status(self) -> dict[str, object]:
        entries = self.connection.execute(
            """
            SELECT COUNT(*) AS entries,
                   COALESCE(SUM(payload_bytes), 0) AS payload_bytes
            FROM cache_entries WHERE cache_type = 'retrieval'
            """
        ).fetchone()
        outcomes = self.connection.execute(
            """
            SELECT outcome, COUNT(*) AS events,
                   COALESCE(SUM(saved_duration_ms), 0) AS saved_duration_ms
            FROM cache_events
            WHERE cache_type = 'retrieval'
            GROUP BY outcome ORDER BY outcome
            """
        ).fetchall()
        return {
            "cache_type": "retrieval",
            "generation": self.generation(),
            "entries": int(entries["entries"]),
            "payload_bytes": int(entries["payload_bytes"]),
            "hits": sum(
                int(row["events"])
                for row in outcomes
                if row["outcome"] == "hit"
            ),
            "events": [
                {
                    "outcome": row["outcome"],
                    "events": int(row["events"]),
                    "estimated_gross_duration_avoided_ms": int(
                        row["saved_duration_ms"]
                    ),
                }
                for row in outcomes
            ],
            "stored_request_content": False,
            "stale_if_error_enabled": False,
        }

    def next_retrieval_transition(
        self,
        *,
        scope: str,
        include_ancestors: bool,
        after: datetime,
    ) -> datetime | None:
        visible = self.scopes.visible_scope_ids(
            scope, include_ancestors=include_ancestors
        )
        placeholders = ",".join("?" for _ in visible)
        after_iso = after.astimezone(timezone.utc).isoformat()
        row = self.connection.execute(
            f"""
            SELECT MIN(value) AS transition_at
            FROM (
                SELECT valid_from AS value FROM memories
                WHERE scope IN ({placeholders})
                  AND lifecycle_state IN ('active', 'cold')
                  AND valid_from > ?
                UNION ALL
                SELECT valid_until AS value FROM memories
                WHERE scope IN ({placeholders})
                  AND lifecycle_state IN ('active', 'cold')
                  AND valid_until IS NOT NULL AND valid_until > ?
                UNION ALL
                SELECT retention_until AS value FROM memories
                WHERE scope IN ({placeholders})
                  AND lifecycle_state IN ('active', 'cold')
                  AND retention_until IS NOT NULL AND retention_until > ?
            )
            """,
            (
                *visible,
                after_iso,
                *visible,
                after_iso,
                *visible,
                after_iso,
            ),
        ).fetchone()
        if row is None or row["transition_at"] is None:
            return None
        return parse_timestamp(row["transition_at"])
