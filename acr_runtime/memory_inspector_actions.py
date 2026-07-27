from __future__ import annotations

import hashlib
import sqlite3
from typing import Any

from .memory import MemoryCreate, MemoryRecord, MemoryStatus
from .permissions import CapabilityCheck
from .secret_management import assert_secret_free
from .service import AdaptiveRuntime


class MemoryInspectorConflict(RuntimeError):
    pass


class MemoryInspectorActions:
    """Authorized, optimistic memory actions used by the operator UI."""

    def __init__(self, runtime: AdaptiveRuntime, *, operator_id: str) -> None:
        if not operator_id.strip():
            raise ValueError("Memory inspector operator ID cannot be empty")
        self.runtime = runtime
        self.operator_id = operator_id

    def authorize(self, scope: str) -> None:
        decision = self.runtime.permissions.check(CapabilityCheck(
            subject_type="agent",
            subject_id=self.operator_id,
            capability="memory.write",
            resource_scope=f"memory:{scope}",
        ))
        if not decision["allowed"]:
            raise PermissionError(
                "Operator lacks an active memory.write grant for this exact scope"
            )

    def _visible(self, memory_id: str, scope: str) -> MemoryRecord:
        record = self.runtime.db.memories.get(memory_id)
        if (
            record is None
            or record.scope != scope
            or record.sensitivity.value not in {"public", "internal"}
            or record.status is MemoryStatus.DELETED
            or record.lifecycle_state.value == "deleted"
        ):
            raise LookupError(f"Unknown memory: {memory_id}")
        return record

    def _current(
        self, memory_id: str, scope: str, expected_updated_at: str
    ) -> MemoryRecord:
        record = self._visible(memory_id, scope)
        if record.updated_at != expected_updated_at:
            raise MemoryInspectorConflict(
                "Memory changed; refresh before trying again"
            )
        return record

    def lifecycle(
        self,
        memory_id: str,
        *,
        scope: str,
        expected_updated_at: str,
        action: str,
        reason: str | None = None,
    ) -> dict[str, Any]:
        self.authorize(scope)
        connection = self.runtime.db.connection
        connection.execute("BEGIN IMMEDIATE")
        try:
            record = self._current(memory_id, scope, expected_updated_at)
            if action == "pin":
                bounded_reason = reason.strip() if reason else None
                if bounded_reason and len(bounded_reason) > 2_000:
                    raise ValueError("Pin reason is too long")
                if bounded_reason:
                    assert_secret_free(bounded_reason, "pin reason")
                updated = self.runtime.lifecycle.pin(
                    record.id, reason=bounded_reason
                )
            elif action == "archive":
                updated = self.runtime.lifecycle.archive(record.id)
            elif action == "restore":
                updated = self.runtime.lifecycle.restore(record.id)
            else:
                raise ValueError("Unsupported memory lifecycle action")
        except Exception:
            if connection.in_transaction:
                connection.rollback()
            raise
        else:
            if connection.in_transaction:
                connection.commit()
        return {
            "action": action,
            "memory_id": updated.id,
            "updated_at": updated.updated_at,
            "lifecycle_state": updated.lifecycle_state.value,
            "status": updated.status.value,
            "pinned": updated.pinned,
        }

    def correct(
        self,
        memory_id: str,
        *,
        scope: str,
        expected_updated_at: str,
        content: str,
        evidence: tuple[str, ...],
        reason: str,
    ) -> dict[str, Any]:
        self.authorize(scope)
        if not reason.strip() or len(reason) > 2_000:
            raise ValueError("Correction requires a bounded reason")
        if not evidence:
            raise ValueError("Correction requires at least one evidence reference")
        assert_secret_free(reason, "correction reason")
        connection = self.runtime.db.connection
        connection.execute("BEGIN IMMEDIATE")
        try:
            prior = self._current(memory_id, scope, expected_updated_at)
            replacement = self.runtime.db.memories.create(MemoryCreate(
                type=prior.type,
                content=content,
                scope=prior.scope,
                subject=prior.subject,
                structured_payload_json=prior.structured_payload_json,
                confidence=prior.confidence,
                importance=prior.importance,
                utility_score=prior.utility_score,
                source_type="operator_correction",
                source_id=self.operator_id,
                evidence=evidence,
                retention_reasons=(
                    "operator_correction",
                    "reason_sha256:"
                    + hashlib.sha256(reason.strip().encode("utf-8")).hexdigest(),
                ),
                status=MemoryStatus.CONFIRMED,
                supersedes=prior.id,
                sensitivity=prior.sensitivity,
            ))
        except Exception:
            if connection.in_transaction:
                connection.rollback()
            raise
        else:
            if connection.in_transaction:
                connection.commit()
        return {
            "action": "correct",
            "memory_id": replacement.id,
            "supersedes": prior.id,
            "updated_at": replacement.updated_at,
            "status": replacement.status.value,
        }

    def plan_delete(
        self,
        memory_id: str,
        *,
        scope: str,
        expected_updated_at: str,
        reason: str,
    ) -> dict[str, object]:
        self.authorize(scope)
        self._current(memory_id, scope, expected_updated_at)
        return self.runtime.privacy.plan_deletion(
            memory_id,
            requested_by=self.operator_id,
            reason=reason,
        )

    def approve_delete(
        self,
        request_id: str,
        *,
        scope: str,
        confirmation: str,
    ) -> dict[str, object]:
        request = self.runtime.privacy.deletion_request(request_id)
        memory_id = str(request["memory_id"])
        self.authorize(scope)
        self._visible(memory_id, scope)
        if confirmation != memory_id:
            raise ValueError("Deletion confirmation must exactly match the memory ID")
        try:
            return self.runtime.privacy.approve_deletion(request_id)
        except sqlite3.Error:
            raise
