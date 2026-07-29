from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Literal

from .capability_vocab import CAPABILITIES
from .content_security import ContentSecurityController
from .memory import utc_now
from .tool_registry import TOOL_ID

SubjectType = Literal["task", "agent", "skill"]
GrantorType = Literal["trusted_workflow", "task", "agent", "skill"]
SAFE_MODE_BLOCKED_CAPABILITIES = frozenset(
    {
        "filesystem.write",
        "network.write",
        "database.write",
        "memory.write",
        "shell.execute",
    }
)


def _instant(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("Capability timestamps must include a timezone")
    return parsed.astimezone(timezone.utc)


@dataclass(frozen=True)
class CapabilityGrantRequest:
    subject_type: SubjectType
    subject_id: str
    capability: str
    resource_scope: str
    expires_at: str
    delegable: bool
    grantor_type: GrantorType
    grantor_id: str
    reason: str
    evidence: tuple[str, ...]
    source_assessment_id: str | None = None
    workflow_approval_id: str | None = None

    def __post_init__(self) -> None:
        if self.subject_type not in ("task", "agent", "skill"):
            raise ValueError("Invalid capability subject type")
        if self.grantor_type not in ("trusted_workflow", "task", "agent", "skill"):
            raise ValueError("Invalid capability grantor type")
        if not TOOL_ID.fullmatch(self.subject_id) or not TOOL_ID.fullmatch(
            self.grantor_id
        ):
            raise ValueError("Capability subject/grantor ID is invalid")
        if self.capability not in CAPABILITIES:
            raise ValueError("Unknown capability; wildcard grants are forbidden")
        if (
            not self.resource_scope.strip()
            or self.resource_scope != self.resource_scope.strip()
            or self.resource_scope in {"*", "all", "global"}
            or len(self.resource_scope) > 512
        ):
            raise ValueError("Capability scope must be explicit and bounded")
        if type(self.delegable) is not bool:
            raise ValueError("delegable must be a boolean")
        if _instant(self.expires_at) <= datetime.now(timezone.utc):
            raise ValueError("Capability grant must expire in the future")
        if (
            not self.reason.strip()
            or self.reason != self.reason.strip()
            or len(self.reason) > 2_000
            or not isinstance(self.evidence, tuple)
            or not 1 <= len(self.evidence) <= 64
        ):
            raise ValueError("Capability grants require reason and evidence")
        if any(
            not item.strip()
            or item != item.strip()
            or len(item) > 512
            for item in self.evidence
        ):
            raise ValueError("Capability evidence cannot be empty")
        for field, value in (
            ("source_assessment_id", self.source_assessment_id),
            ("workflow_approval_id", self.workflow_approval_id),
        ):
            if value is not None and (
                not value.strip() or value != value.strip() or len(value) > 128
            ):
                raise ValueError(f"{field} must be bounded text or null")

    @property
    def target_ref(self) -> str:
        payload = json.dumps({
            "subject_type": self.subject_type,
            "subject_id": self.subject_id,
            "capability": self.capability,
            "resource_scope": self.resource_scope,
            "expires_at": _instant(self.expires_at).isoformat(),
            "delegable": self.delegable,
            "grantor_type": self.grantor_type,
            "grantor_id": self.grantor_id,
        }, sort_keys=True, separators=(",", ":"))
        return "capability:" + hashlib.sha256(
            payload.encode("utf-8")
        ).hexdigest()

    @classmethod
    def from_dict(cls, payload: object) -> "CapabilityGrantRequest":
        required = {
            "subject_type", "subject_id", "capability", "resource_scope",
            "expires_at", "delegable", "grantor_type", "grantor_id",
            "reason", "evidence",
        }
        optional = {"source_assessment_id", "workflow_approval_id"}
        if (
            not isinstance(payload, dict)
            or not required <= set(payload)
            or set(payload) - required - optional
        ):
            raise ValueError(
                f"Capability grant must contain {sorted(required)}"
            )
        if not isinstance(payload["delegable"], bool) or not isinstance(
            payload["evidence"], list
        ):
            raise ValueError("Capability grant types are invalid")
        text_fields = required - {"delegable", "evidence"}
        if any(not isinstance(payload[field], str) for field in text_fields):
            raise ValueError("Capability grant text fields must be strings")
        if any(not isinstance(item, str) for item in payload["evidence"]):
            raise ValueError("Capability evidence items must be strings")
        if any(
            payload.get(field) is not None
            and not isinstance(payload.get(field), str)
            for field in optional
        ):
            raise ValueError("Capability security references must be strings")
        return cls(
            subject_type=str(payload["subject_type"]),
            subject_id=str(payload["subject_id"]),
            capability=str(payload["capability"]),
            resource_scope=str(payload["resource_scope"]),
            expires_at=str(payload["expires_at"]),
            delegable=payload["delegable"],
            grantor_type=str(payload["grantor_type"]),
            grantor_id=str(payload["grantor_id"]),
            reason=str(payload["reason"]),
            evidence=tuple(str(item) for item in payload["evidence"]),
            source_assessment_id=payload.get("source_assessment_id"),
            workflow_approval_id=payload.get("workflow_approval_id"),
        )


@dataclass(frozen=True)
class CapabilityCheck:
    subject_type: SubjectType
    subject_id: str
    capability: str
    resource_scope: str

    def __post_init__(self) -> None:
        if self.subject_type not in ("task", "agent", "skill"):
            raise ValueError("Invalid capability subject type")
        if not TOOL_ID.fullmatch(self.subject_id):
            raise ValueError("Capability subject ID is invalid")
        if self.capability not in CAPABILITIES:
            raise ValueError("Unknown capability; wildcard checks are forbidden")
        if (
            not self.resource_scope.strip()
            or self.resource_scope != self.resource_scope.strip()
            or self.resource_scope in {"*", "all", "global"}
            or len(self.resource_scope) > 512
        ):
            raise ValueError("Capability scope must be explicit and bounded")

    @classmethod
    def from_dict(cls, payload: object) -> "CapabilityCheck":
        fields = {"subject_type", "subject_id", "capability", "resource_scope"}
        if not isinstance(payload, dict) or set(payload) != fields:
            raise ValueError(f"Capability check must contain {sorted(fields)} only")
        if any(not isinstance(value, str) for value in payload.values()):
            raise ValueError("Capability check fields must be strings")
        return cls(**payload)


class PermissionController:
    """Default-deny exact capabilities with bounded non-escalating delegation."""

    def __init__(
        self,
        connection: sqlite3.Connection,
        security: ContentSecurityController | None = None,
        *,
        safe_mode_provider: Callable[[], bool] | None = None,
    ) -> None:
        self.connection = connection
        self.security = security
        self.safe_mode_provider = safe_mode_provider

    def grant(self, request: CapabilityGrantRequest) -> dict[str, object]:
        if (
            self.safe_mode_provider is not None
            and self.safe_mode_provider()
            and request.capability in SAFE_MODE_BLOCKED_CAPABILITIES
        ):
            raise PermissionError("safe mode blocks write-capability grants")
        if request.grantor_type == "skill":
            raise PermissionError("Skills cannot issue capability grants")
        if request.source_assessment_id is not None:
            if self.security is None:
                raise RuntimeError(
                    "Content-derived grants require the security controller"
                )
            authorization = self.security.authorize_sensitive_action(
                assessment_id=request.source_assessment_id,
                action="permission.grant",
                target_ref=request.target_ref,
                approval_id=request.workflow_approval_id,
            )
            if not authorization["allowed"]:
                raise PermissionError(str(authorization["reason"]))
        parent_id: str | None = None
        if request.grantor_type in ("task", "agent"):
            parent = self.connection.execute(
                """
                SELECT * FROM capability_grants
                WHERE subject_type=? AND subject_id=? AND capability=?
                  AND resource_scope=? AND delegable=1 AND revoked_at IS NULL
                  AND expires_at > ?
                ORDER BY expires_at DESC LIMIT 1
                """,
                (
                    request.grantor_type, request.grantor_id,
                    request.capability, request.resource_scope, utc_now(),
                ),
            ).fetchone()
            if parent is None:
                raise PermissionError(
                    "Grantor lacks the same active delegable capability and scope"
                )
            if _instant(request.expires_at) > _instant(parent["expires_at"]):
                raise PermissionError("Delegated grant cannot outlive its parent")
            if request.delegable and not bool(parent["delegable"]):
                raise PermissionError("Delegation cannot add delegation authority")
            parent_id = parent["id"]
        grant_id = str(uuid.uuid4())
        self.connection.execute(
            """
            INSERT INTO capability_grants (
                id, subject_type, subject_id, capability, resource_scope,
                expires_at, delegable, grantor_type, grantor_id,
                parent_grant_id, reason, evidence_json, created_at,
                source_assessment_id, workflow_approval_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                grant_id, request.subject_type, request.subject_id,
                request.capability, request.resource_scope,
                _instant(request.expires_at).isoformat(),
                request.delegable, request.grantor_type, request.grantor_id,
                parent_id, request.reason, json.dumps(request.evidence), utc_now(),
                request.source_assessment_id, request.workflow_approval_id,
            ),
        )
        if request.source_assessment_id is not None:
            assert self.security is not None
            consumed = self.security.authorize_sensitive_action(
                assessment_id=request.source_assessment_id,
                action="permission.grant",
                target_ref=request.target_ref,
                approval_id=request.workflow_approval_id,
                consume=True,
                manage_transaction=False,
            )
            if not consumed["allowed"]:
                self.connection.rollback()
                raise PermissionError(str(consumed["reason"]))
        self.connection.commit()
        return self.get(grant_id)

    def check(self, request: CapabilityCheck) -> dict[str, object]:
        now = utc_now()
        safe_mode_denied = bool(
            self.safe_mode_provider is not None
            and self.safe_mode_provider()
            and request.capability in SAFE_MODE_BLOCKED_CAPABILITIES
        )
        row = self.connection.execute(
            """
            WITH RECURSIVE grant_chain(
                root_id, id, parent_grant_id, revoked_at, expires_at
            ) AS (
                SELECT id, id, parent_grant_id, revoked_at, expires_at
                FROM capability_grants
                WHERE subject_type=? AND subject_id=? AND capability=?
                  AND resource_scope=?
                UNION ALL
                SELECT c.root_id, p.id, p.parent_grant_id,
                       p.revoked_at, p.expires_at
                FROM grant_chain AS c
                JOIN capability_grants AS p ON p.id=c.parent_grant_id
            )
            SELECT root_id
            FROM grant_chain
            GROUP BY root_id
            HAVING SUM(
                CASE WHEN revoked_at IS NOT NULL OR expires_at <= ?
                     THEN 1 ELSE 0 END
            ) = 0
            ORDER BY MIN(expires_at) ASC
            LIMIT 1
            """,
            (
                request.subject_type, request.subject_id,
                request.capability, request.resource_scope, now,
            ),
        ).fetchone()
        grant_id = None if safe_mode_denied else (row["root_id"] if row else None)
        reason = (
            "safe_mode"
            if safe_mode_denied
            else ("active_exact_grant" if row else "default_deny")
        )
        decision_id = str(uuid.uuid4())
        self.connection.execute(
            """
            INSERT INTO capability_decisions (
                id, subject_type, subject_id, capability, resource_scope,
                allowed, grant_id, reason, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                decision_id, request.subject_type, request.subject_id,
                request.capability, request.resource_scope, bool(grant_id),
                grant_id, reason, utc_now(),
            ),
        )
        self.connection.commit()
        return {
            "id": decision_id, "allowed": bool(grant_id),
            "grant_id": grant_id, "reason": reason,
        }

    def revoke(self, grant_id: str, *, reason: str) -> dict[str, object]:
        if not reason.strip():
            raise ValueError("Revocation reason cannot be empty")
        row = self.connection.execute(
            "SELECT revoked_at FROM capability_grants WHERE id=?", (grant_id,)
        ).fetchone()
        if row is None:
            raise LookupError(f"Unknown capability grant: {grant_id}")
        if row["revoked_at"] is not None:
            raise ValueError("Capability grant is already revoked")
        now = utc_now()
        descendants = [
            item["id"] for item in self.connection.execute(
                """
                WITH RECURSIVE descendants(id) AS (
                    SELECT id FROM capability_grants WHERE id=?
                    UNION ALL
                    SELECT child.id
                    FROM capability_grants AS child
                    JOIN descendants AS parent
                      ON child.parent_grant_id=parent.id
                )
                SELECT id FROM descendants
                """,
                (grant_id,),
            )
        ]
        placeholders = ",".join("?" for _ in descendants)
        cursor = self.connection.execute(
            f"""
            UPDATE capability_grants
            SET revoked_at=?, revocation_reason=?
            WHERE id IN ({placeholders}) AND revoked_at IS NULL
            """,
            (now, reason, *descendants),
        )
        self.connection.commit()
        result = self.get(grant_id)
        result["cascade_revoked"] = cursor.rowcount
        return result

    def get(self, grant_id: str) -> dict[str, object]:
        row = self.connection.execute(
            "SELECT * FROM capability_grants WHERE id=?", (grant_id,)
        ).fetchone()
        if row is None:
            raise LookupError(f"Unknown capability grant: {grant_id}")
        result = dict(row)
        result["delegable"] = bool(result["delegable"])
        result["evidence"] = json.loads(result.pop("evidence_json"))
        return result

    def subject_grants(
        self, subject_type: str, subject_id: str
    ) -> list[dict[str, object]]:
        ids = [
            row["id"] for row in self.connection.execute(
                """
                SELECT id FROM capability_grants
                WHERE subject_type=? AND subject_id=?
                ORDER BY created_at
                """,
                (subject_type, subject_id),
            )
        ]
        return [self.get(grant_id) for grant_id in ids]
