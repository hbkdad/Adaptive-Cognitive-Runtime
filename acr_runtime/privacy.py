from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable, Literal

from .memory import (
    LifecycleState,
    MemoryRecord,
    MemoryStatus,
    Sensitivity,
    parse_timestamp,
    timestamp_after,
    utc_now,
)
from .scoring import fts_query
from .secret_management import assert_secret_free

DeletionRequirement = Literal["standard", "secure"]
DELETED_CONTENT = "[deleted by privacy engine]"
IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:@/-]{0,127}")
SENSITIVITY_RANK = {
    Sensitivity.PUBLIC: 0,
    Sensitivity.INTERNAL: 1,
    Sensitivity.PERSONAL: 2,
    Sensitivity.CONFIDENTIAL: 3,
    Sensitivity.SECRET: 4,
}


@dataclass(frozen=True)
class PrivacyPolicy:
    classification: Sensitivity
    allowed_providers: tuple[str, ...]
    retention_days: int | None
    exportable: bool
    deletion_requirement: DeletionRequirement
    version: int
    reason: str
    updated_at: str

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "PrivacyPolicy":
        return cls(
            classification=Sensitivity(row["classification"]),
            allowed_providers=tuple(json.loads(row["allowed_providers_json"])),
            retention_days=row["retention_days"],
            exportable=bool(row["exportable"]),
            deletion_requirement=row["deletion_requirement"],
            version=row["version"],
            reason=row["reason"],
            updated_at=row["updated_at"],
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "classification": self.classification.value,
            "allowed_providers": list(self.allowed_providers),
            "retention_days": self.retention_days,
            "exportable": self.exportable,
            "deletion_requirement": self.deletion_requirement,
            "version": self.version,
            "reason": self.reason,
            "updated_at": self.updated_at,
        }


class PrivacyEngine:
    """Classification policy, provider/export gates, retention, and erasure."""

    def __init__(
        self,
        connection: sqlite3.Connection,
        *,
        mutation_guard: Callable[[str], None] | None = None,
    ) -> None:
        self.connection = connection
        self.mutation_guard = mutation_guard

    def policy(self, classification: Sensitivity | str) -> PrivacyPolicy:
        label = Sensitivity(classification)
        row = self.connection.execute(
            "SELECT * FROM privacy_policies WHERE classification=?",
            (label.value,),
        ).fetchone()
        if row is None:
            raise RuntimeError(f"Missing privacy policy: {label.value}")
        return PrivacyPolicy.from_row(row)

    def policies(self) -> list[dict[str, object]]:
        return [
            self.policy(classification).as_dict()
            for classification in Sensitivity
        ]

    @staticmethod
    def _validate_provider(provider: str) -> str:
        if not IDENTIFIER.fullmatch(provider) or provider == "cloud":
            raise ValueError(
                "Provider must be an exact bounded identifier; cloud wildcard is forbidden"
            )
        return provider

    def update_policy(
        self,
        classification: Sensitivity | str,
        *,
        allowed_providers: tuple[str, ...],
        retention_days: int | None,
        exportable: bool,
        deletion_requirement: DeletionRequirement,
        actor: str,
        reason: str,
    ) -> PrivacyPolicy:
        label = Sensitivity(classification)
        if not isinstance(allowed_providers, tuple) or any(
            not isinstance(provider, str) for provider in allowed_providers
        ):
            raise ValueError("Allowed providers must be unique exact identifiers")
        if (
            len(set(allowed_providers)) != len(allowed_providers)
            or any(
                self._validate_provider(provider) != provider
                for provider in allowed_providers
            )
        ):
            raise ValueError("Allowed providers must be unique exact identifiers")
        if retention_days is not None and not 1 <= retention_days <= 36_500:
            raise ValueError("Retention days must be null or between 1 and 36500")
        if type(exportable) is not bool:
            raise ValueError("exportable must be a boolean")
        if deletion_requirement not in ("standard", "secure"):
            raise ValueError("Invalid deletion requirement")
        if not IDENTIFIER.fullmatch(actor):
            raise ValueError("Privacy policy actor is invalid")
        if not reason.strip() or len(reason) > 2_000:
            raise ValueError("Privacy policy requires a bounded reason")
        assert_secret_free(reason, "privacy policy reason")
        current = self.policy(label)
        version = current.version + 1
        now = utc_now()
        payload = {
            "allowed_providers": list(allowed_providers),
            "retention_days": retention_days,
            "exportable": exportable,
            "deletion_requirement": deletion_requirement,
        }
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO privacy_policy_events (
                    id, classification, version, policy_json, actor, reason,
                    created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(uuid.uuid4()), label.value, version,
                    json.dumps(payload, sort_keys=True), actor, reason, now,
                ),
            )
            self.connection.execute(
                """
                UPDATE privacy_policies
                SET allowed_providers_json=?, retention_days=?, exportable=?,
                    deletion_requirement=?, version=?, reason=?, updated_at=?
                WHERE classification=?
                """,
                (
                    json.dumps(allowed_providers), retention_days,
                    int(exportable), deletion_requirement, version, reason,
                    now, label.value,
                ),
            )
        return self.policy(label)

    def _records(self, memory_ids: tuple[str, ...]) -> tuple[MemoryRecord, ...]:
        if not memory_ids or len(memory_ids) > 1_000:
            raise ValueError("Memory IDs must contain 1..1000 entries")
        if len(set(memory_ids)) != len(memory_ids):
            raise ValueError("Memory IDs must be unique")
        placeholders = ",".join("?" for _ in memory_ids)
        rows = self.connection.execute(
            f"SELECT * FROM memories WHERE id IN ({placeholders})",
            memory_ids,
        ).fetchall()
        found = {row["id"] for row in rows}
        missing = sorted(set(memory_ids) - found)
        if missing:
            raise KeyError(f"Unknown memory IDs: {missing}")
        from .memory import SQLiteMemoryStore

        by_id = {
            row["id"]: SQLiteMemoryStore._record(row) for row in rows
        }
        return tuple(by_id[memory_id] for memory_id in memory_ids)

    def _decision(
        self,
        action: str,
        records: tuple[MemoryRecord, ...],
        *,
        allowed: bool,
        reason: str,
        provider: str | None = None,
    ) -> str:
        decision_id = str(uuid.uuid4())
        self.connection.execute(
            """
            INSERT INTO privacy_decisions (
                id, action, memory_ids_json, classifications_json, provider,
                allowed, reason, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                decision_id, action,
                json.dumps([record.id for record in records]),
                json.dumps(sorted({
                    record.sensitivity.value for record in records
                })),
                provider, int(allowed), reason, utc_now(),
            ),
        )
        self.connection.commit()
        return decision_id

    def classify(
        self,
        memory_id: str,
        classification: Sensitivity | str,
        *,
        actor: str,
        reason: str,
        allow_downgrade: bool = False,
    ) -> MemoryRecord:
        record = self._records((memory_id,))[0]
        target = Sensitivity(classification)
        if record.lifecycle_state is LifecycleState.DELETED:
            raise ValueError("Deleted memory cannot be reclassified")
        if not IDENTIFIER.fullmatch(actor) or not reason.strip():
            raise ValueError("Classification requires actor and reason")
        assert_secret_free(reason, "classification reason")
        if (
            SENSITIVITY_RANK[target] < SENSITIVITY_RANK[record.sensitivity]
            and not allow_downgrade
        ):
            self._decision(
                "classify", (record,), allowed=False,
                reason="downgrade_requires_explicit_approval",
            )
            raise PermissionError("Sensitivity downgrade requires explicit approval")
        policy = self.policy(target)
        now = timestamp_after(record.updated_at)
        retention_until = (
            None
            if policy.retention_days is None
            else (
                parse_timestamp(now) + timedelta(days=policy.retention_days)
            ).isoformat()
        )
        with self.connection:
            self.connection.execute(
                """
                UPDATE memories
                SET sensitivity=?, retention_until=?,
                    privacy_policy_version=?, updated_at=?
                WHERE id=?
                """,
                (
                    target.value, retention_until, policy.version, now,
                    memory_id,
                ),
            )
        updated = self._records((memory_id,))[0]
        self._decision(
            "classify", (updated,), allowed=True,
            reason=f"classification_updated_by:{actor}",
        )
        return updated

    def authorize_provider(
        self,
        memory_ids: tuple[str, ...],
        *,
        provider: str,
        local: bool,
    ) -> dict[str, object]:
        provider = self._validate_provider(provider)
        records = self._records(memory_ids)
        identity = "local" if local else provider
        blocked = sorted({
            record.sensitivity.value
            for record in records
            if identity not in self.policy(record.sensitivity).allowed_providers
            or record.lifecycle_state is LifecycleState.DELETED
        })
        allowed = not blocked
        reason = (
            "provider_allowed_by_all_classifications"
            if allowed else "provider_blocked_by_classification"
        )
        decision_id = self._decision(
            "provider", records, allowed=allowed, reason=reason,
            provider=provider,
        )
        return {
            "id": decision_id,
            "allowed": allowed,
            "provider": provider,
            "local": local,
            "blocked_classifications": blocked,
            "reason": reason,
        }

    def export(self, memory_ids: tuple[str, ...]) -> dict[str, object]:
        records = self._records(memory_ids)
        blocked = [
            record.id for record in records
            if not self.policy(record.sensitivity).exportable
            or record.lifecycle_state is LifecycleState.DELETED
        ]
        decision_id = self._decision(
            "export", records, allowed=not blocked,
            reason=(
                "all_memories_exportable"
                if not blocked else "policy_blocks_export"
            ),
        )
        if blocked:
            raise PermissionError(
                f"Privacy policy blocks export; decision={decision_id}"
            )
        return {
            "decision_id": decision_id,
            "memories": [
                {
                    "id": record.id,
                    "type": record.type.value,
                    "scope": record.scope,
                    "subject": record.subject,
                    "content": record.content,
                    "structured_payload": json.loads(
                        record.structured_payload_json
                    ),
                    "evidence": list(record.evidence),
                    "created_at": record.created_at,
                    "updated_at": record.updated_at,
                    "sensitivity": record.sensitivity.value,
                    "retention_until": record.retention_until,
                }
                for record in records
            ],
        }

    def retention_due(self, *, at: str | None = None) -> list[dict[str, object]]:
        instant = parse_timestamp(at) if at else datetime.now(timezone.utc)
        rows = self.connection.execute(
            """
            SELECT id, sensitivity, retention_until FROM memories
            WHERE retention_until IS NOT NULL
              AND julianday(retention_until) <= julianday(?)
              AND lifecycle_state != 'deleted'
            ORDER BY retention_until, id
            """,
            (instant.isoformat(),),
        ).fetchall()
        return [dict(row) for row in rows]

    def plan_deletion(
        self, memory_id: str, *, requested_by: str, reason: str
    ) -> dict[str, object]:
        record = self._records((memory_id,))[0]
        if record.lifecycle_state is LifecycleState.DELETED:
            raise ValueError("Memory is already deleted")
        if not IDENTIFIER.fullmatch(requested_by):
            raise ValueError("Deletion requester is invalid")
        if not reason.strip() or len(reason) > 2_000:
            raise ValueError("Deletion requires a bounded reason")
        assert_secret_free(reason, "deletion reason")
        policy = self.policy(record.sensitivity)
        request_id = str(uuid.uuid4())
        created_at = utc_now()
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO memory_deletion_requests (
                    id, memory_id, classification, expected_updated_at,
                    deletion_requirement, requested_by, reason, status,
                    created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'planned', ?)
                """,
                (
                    request_id, memory_id, record.sensitivity.value,
                    record.updated_at, policy.deletion_requirement,
                    requested_by,
                    "sha256:" + hashlib.sha256(
                        reason.encode("utf-8")
                    ).hexdigest(),
                    created_at,
                ),
            )
        return self.deletion_request(request_id)

    def deletion_request(self, request_id: str) -> dict[str, object]:
        row = self.connection.execute(
            "SELECT * FROM memory_deletion_requests WHERE id=?",
            (request_id,),
        ).fetchone()
        if row is None:
            raise LookupError(f"Unknown deletion request: {request_id}")
        payload = dict(row)
        payload["verification"] = json.loads(payload.pop("verification_json"))
        return payload

    def approve_deletion(self, request_id: str) -> dict[str, object]:
        if self.mutation_guard is not None:
            self.mutation_guard("memory_deletion")
        request = self.deletion_request(request_id)
        if request["status"] != "planned":
            raise ValueError("Only a planned deletion can be approved")
        record = self._records((str(request["memory_id"]),))[0]
        if record.updated_at != request["expected_updated_at"]:
            raise RuntimeError("Memory changed after deletion was planned")
        old_expression = fts_query(
            " ".join(filter(None, (record.subject, record.content)))
        )
        now = timestamp_after(record.updated_at)
        self.connection.execute("PRAGMA secure_delete = ON")
        self.connection.execute(
            """
            INSERT INTO memories_fts(memories_fts, rank)
            VALUES('secure-delete', 1)
            """
        )
        with self.connection:
            self.connection.execute(
                """
                UPDATE memories
                SET scope='deleted', subject=NULL, content=?,
                    structured_payload_json='{}', confidence=0, importance=0,
                    utility_score=0, source_type=NULL, source_id=NULL,
                    evidence_json='[]',
                    retention_reason_json='["privacy_erasure_completed"]',
                    updated_at=?, valid_until=?, last_accessed=NULL,
                    access_count=0, successful_uses=0, failed_uses=0,
                    status='deleted', token_cost=0,
                    lifecycle_state='deleted', pinned=0, pinned_at=NULL,
                    pin_reason=NULL, lifecycle_updated_at=?, archived_at=NULL,
                    deleted_at=?, retention_until=?
                WHERE id=?
                """,
                (
                    DELETED_CONTENT, now, now, now, now, now,
                    record.id,
                ),
            )
        residual_fts_rows = 0
        if old_expression:
            residual_fts_rows = int(self.connection.execute(
                """
                SELECT COUNT(*) FROM memories_fts
                WHERE memories_fts MATCH ? AND rowid=(
                    SELECT rowid FROM memories WHERE id=?
                )
                """,
                (old_expression, record.id),
            ).fetchone()[0])
        try:
            self.connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            if request["deletion_requirement"] == "secure":
                self.connection.execute("VACUUM")
        except Exception as error:
            with self.connection:
                self.connection.execute(
                    """
                    UPDATE memory_deletion_requests
                    SET status='failed', verification_json=?, completed_at=?
                    WHERE id=?
                    """,
                    (
                        json.dumps({"error_type": type(error).__name__}),
                        utc_now(), request_id,
                    ),
                )
            raise
        erased = self._records((record.id,))[0]
        verified = (
            erased.content == DELETED_CONTENT
            and erased.subject is None
            and erased.structured_payload_json == "{}"
            and not erased.evidence
            and erased.lifecycle_state is LifecycleState.DELETED
            and erased.status is MemoryStatus.DELETED
            and residual_fts_rows == 0
        )
        verification = {
            "content_fields_erased": verified,
            "fts_residual_rows": residual_fts_rows,
            "core_secure_delete": True,
            "fts_secure_delete": True,
            "vacuum_completed": (
                request["deletion_requirement"] == "secure"
            ),
            "backup_cleanup_required": True,
        }
        with self.connection:
            self.connection.execute(
                """
                UPDATE memory_deletion_requests
                SET status=?, verification_json=?, completed_at=?
                WHERE id=?
                """,
                (
                    "completed" if verified else "failed",
                    json.dumps(verification, sort_keys=True),
                    utc_now(), request_id,
                ),
            )
        self._decision(
            "delete", (erased,), allowed=verified,
            reason=(
                "erasure_verified"
                if verified else "erasure_verification_failed"
            ),
        )
        if not verified:
            raise RuntimeError("Memory erasure verification failed")
        return self.deletion_request(request_id)
