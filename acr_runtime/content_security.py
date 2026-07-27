from __future__ import annotations

import hashlib
import html
import json
import re
import sqlite3
import unicodedata
import uuid
from dataclasses import dataclass
from typing import Literal

from .memory import utc_now
from .secret_management import detect_secret_material

ContentOrigin = Literal[
    "system_policy",
    "developer_instruction",
    "user_instruction",
    "skill_instruction",
    "retrieved_memory",
    "web_content",
    "document",
    "tool_output",
]
SensitiveAction = Literal[
    "memory.create", "skill.create", "agent.create", "permission.grant"
]

ORIGINS = frozenset({
    "system_policy", "developer_instruction", "user_instruction",
    "skill_instruction", "retrieved_memory", "web_content", "document",
    "tool_output",
})
EXTERNAL_ORIGINS = frozenset({
    "retrieved_memory", "web_content", "document", "tool_output",
})
INSTRUCTION_ORIGINS = frozenset({
    "system_policy", "developer_instruction", "user_instruction",
    "skill_instruction",
})
APPROVER_ORIGINS = frozenset({
    "system_policy", "developer_instruction", "user_instruction",
})
ACTIONS = frozenset({
    "memory.create", "skill.create", "agent.create", "permission.grant",
})
AUTHORITY = {
    "system_policy": "system",
    "developer_instruction": "developer",
    "user_instruction": "user",
    "skill_instruction": "scoped_skill",
    "retrieved_memory": "none",
    "web_content": "none",
    "document": "none",
    "tool_output": "none",
}

SUSPICIOUS_PATTERNS = {
    "authority_override": re.compile(
        r"\b(?:ignore|disregard|override|forget)\b.{0,48}"
        r"\b(?:previous|prior|system|developer|user|security|safety)\b.{0,24}"
        r"\b(?:instruction|message|policy|rule)s?\b",
        re.IGNORECASE | re.DOTALL,
    ),
    "policy_redefinition": re.compile(
        r"\b(?:new|replace|change|rewrite|disable|bypass)\b.{0,36}"
        r"\b(?:system|developer|security|safety)\b.{0,24}"
        r"\b(?:instruction|message|policy|rule)s?\b",
        re.IGNORECASE | re.DOTALL,
    ),
    "identity_override": re.compile(
        r"\b(?:you are now|act as|pretend to be)\b.{0,48}"
        r"\b(?:system|administrator|developer|root|unrestricted)\b",
        re.IGNORECASE | re.DOTALL,
    ),
    "secret_exfiltration": re.compile(
        r"\b(?:reveal|print|show|send|upload|transmit|exfiltrate)\b.{0,64}"
        r"\b(?:system prompt|developer message|secret|credential|api key|token)\b",
        re.IGNORECASE | re.DOTALL,
    ),
    "covert_action": re.compile(
        r"\b(?:do not|don't|never)\b.{0,32}\b(?:tell|notify|ask)\b.{0,24}"
        r"\b(?:user|operator|human)\b|\bwithout (?:user )?(?:approval|confirmation)\b",
        re.IGNORECASE | re.DOTALL,
    ),
    "security_mutation": re.compile(
        r"\b(?:grant|activate|create|change|remove|disable)\b.{0,48}"
        r"\b(?:permission|capability|memory|skill|agent|security policy)\b",
        re.IGNORECASE | re.DOTALL,
    ),
    "tool_coercion": re.compile(
        r"\b(?:call|invoke|run|execute)\b.{0,40}"
        r"\b(?:tool|shell|command|powershell|terminal)\b",
        re.IGNORECASE | re.DOTALL,
    ),
    "active_content": re.compile(
        r"<\s*(?:script|iframe)\b|javascript\s*:|data\s*:\s*text/html",
        re.IGNORECASE,
    ),
}


def _normalized_for_detection(content: str) -> str:
    normalized = unicodedata.normalize("NFKC", content)
    return re.sub(r"[\u200b-\u200f\u2060\ufeff]", "", normalized)


def detect_suspicious_instructions(content: str) -> tuple[str, ...]:
    normalized = _normalized_for_detection(content)
    findings = [
        name for name, pattern in SUSPICIOUS_PATTERNS.items()
        if pattern.search(normalized)
    ]
    if normalized != content and any(
        character in content
        for character in ("\u200b", "\u200c", "\u200d", "\u2060", "\ufeff")
    ):
        findings.append("invisible_characters")
    findings.extend(
        f"secret_material:{kind}"
        for kind in detect_secret_material(content)
    )
    return tuple(sorted(set(findings)))


def infer_content_origin(source_type: str | None) -> ContentOrigin:
    normalized = (source_type or "").casefold().replace("-", "_")
    if any(term in normalized for term in ("web", "http", "browser", "url")):
        return "web_content"
    if any(term in normalized for term in ("tool", "command_output")):
        return "tool_output"
    if any(term in normalized for term in ("file", "document", "pdf")):
        return "document"
    if "memory" in normalized:
        return "retrieved_memory"
    if "skill" in normalized:
        return "skill_instruction"
    return "user_instruction"


@dataclass(frozen=True)
class ContentAssessmentRequest:
    origin: ContentOrigin
    source_id: str
    content: str
    provenance: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.origin not in ORIGINS:
            raise ValueError("Unknown content origin")
        if (
            not self.source_id.strip()
            or self.source_id != self.source_id.strip()
            or len(self.source_id) > 512
        ):
            raise ValueError("Content source_id must be bounded non-empty text")
        if not self.content.strip() or len(self.content) > 1_000_000:
            raise ValueError("Assessed content must contain 1..1,000,000 characters")
        if (
            not isinstance(self.provenance, tuple)
            or len(self.provenance) > 64
            or any(
                not isinstance(item, str)
                or not item.strip()
                or item != item.strip()
                or len(item) > 512
                for item in self.provenance
            )
        ):
            raise ValueError("Content provenance must be a bounded string tuple")

    @classmethod
    def from_dict(cls, payload: object) -> "ContentAssessmentRequest":
        fields = {"origin", "source_id", "content", "provenance"}
        if not isinstance(payload, dict) or set(payload) != fields:
            raise ValueError(f"Content assessment requires {sorted(fields)}")
        if not all(
            isinstance(payload[field], str)
            for field in ("origin", "source_id", "content")
        ) or not isinstance(payload["provenance"], list):
            raise ValueError("Content assessment field types are invalid")
        if any(not isinstance(item, str) for item in payload["provenance"]):
            raise ValueError("Content provenance items must be strings")
        return cls(
            origin=payload["origin"],
            source_id=payload["source_id"],
            content=payload["content"],
            provenance=tuple(payload["provenance"]),
        )


@dataclass(frozen=True)
class TrustedWorkflowApprovalRequest:
    assessment_id: str
    action: SensitiveAction
    target_ref: str
    approver_origin: ContentOrigin
    approver_id: str
    reason: str
    evidence: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.action not in ACTIONS:
            raise ValueError("Unknown sensitive action")
        if self.approver_origin not in APPROVER_ORIGINS:
            raise PermissionError(
                "Only system, developer, or user instruction channels "
                "can approve sensitive derivation"
            )
        for field, value, limit in (
            ("assessment_id", self.assessment_id, 128),
            ("target_ref", self.target_ref, 512),
            ("approver_id", self.approver_id, 128),
            ("reason", self.reason, 2_000),
        ):
            if (
                not value.strip()
                or value != value.strip()
                or len(value) > limit
            ):
                raise ValueError(f"{field} must be bounded non-empty text")
        if (
            not isinstance(self.evidence, tuple)
            or not 1 <= len(self.evidence) <= 64
            or any(
                not isinstance(item, str)
                or not item.strip()
                or item != item.strip()
                or len(item) > 512
                for item in self.evidence
            )
        ):
            raise ValueError("Approval requires bounded evidence")

    @classmethod
    def from_dict(cls, payload: object) -> "TrustedWorkflowApprovalRequest":
        fields = {
            "assessment_id", "action", "target_ref", "approver_origin",
            "approver_id", "reason", "evidence",
        }
        if not isinstance(payload, dict) or set(payload) != fields:
            raise ValueError(f"Security approval requires {sorted(fields)}")
        if any(
            not isinstance(payload[field], str)
            for field in fields - {"evidence"}
        ) or not isinstance(payload["evidence"], list):
            raise ValueError("Security approval field types are invalid")
        if any(not isinstance(item, str) for item in payload["evidence"]):
            raise ValueError("Security approval evidence must be strings")
        return cls(
            assessment_id=payload["assessment_id"],
            action=payload["action"],
            target_ref=payload["target_ref"],
            approver_origin=payload["approver_origin"],
            approver_id=payload["approver_id"],
            reason=payload["reason"],
            evidence=tuple(payload["evidence"]),
        )


class ContentSecurityController:
    """Separates instruction authority from retained untrusted data."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def assess(self, request: ContentAssessmentRequest) -> dict[str, object]:
        content_hash = hashlib.sha256(request.content.encode("utf-8")).hexdigest()
        assessment_hash = hashlib.sha256(json.dumps({
            "origin": request.origin,
            "source_id": request.source_id,
            "content_hash": content_hash,
            "provenance": request.provenance,
        }, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
        existing = self.connection.execute(
            "SELECT id FROM content_security_assessments WHERE assessment_hash=?",
            (assessment_hash,),
        ).fetchone()
        if existing is not None:
            return self.get(existing["id"])
        signals = detect_suspicious_instructions(request.content)
        contains_secret = any(
            signal.startswith("secret_material:") for signal in signals
        )
        if contains_secret:
            disposition = "quarantine"
        elif request.origin in EXTERNAL_ORIGINS:
            disposition = "quarantine" if signals else "data_only"
        elif request.origin == "skill_instruction":
            disposition = "quarantine" if signals else "scoped_instruction"
        else:
            disposition = "trusted_instruction"
        assessment_id = str(uuid.uuid4())
        self.connection.execute(
            """
            INSERT INTO content_security_assessments (
                id, assessment_hash, origin, source_id, content_hash,
                authority, disposition, suspicious_signals_json,
                provenance_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                assessment_id, assessment_hash, request.origin,
                request.source_id, content_hash, AUTHORITY[request.origin],
                disposition, json.dumps(signals),
                json.dumps(request.provenance), utc_now(),
            ),
        )
        self.connection.commit()
        return self.get(assessment_id)

    def get(self, assessment_id: str) -> dict[str, object]:
        row = self.connection.execute(
            "SELECT * FROM content_security_assessments WHERE id=?",
            (assessment_id,),
        ).fetchone()
        if row is None:
            raise LookupError(f"Unknown content assessment: {assessment_id}")
        result = dict(row)
        result["suspicious_signals"] = json.loads(
            result.pop("suspicious_signals_json")
        )
        result["provenance"] = json.loads(result.pop("provenance_json"))
        return result

    def approve(
        self, request: TrustedWorkflowApprovalRequest
    ) -> dict[str, object]:
        self.get(request.assessment_id)
        approval_id = str(uuid.uuid4())
        self.connection.execute(
            """
            INSERT INTO trusted_workflow_approvals (
                id, assessment_id, action, target_ref, approver_origin,
                approver_id, reason, evidence_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                approval_id, request.assessment_id, request.action,
                request.target_ref, request.approver_origin,
                request.approver_id, request.reason,
                json.dumps(request.evidence), utc_now(),
            ),
        )
        self.connection.commit()
        return self.approval(approval_id)

    def approval(self, approval_id: str) -> dict[str, object]:
        row = self.connection.execute(
            "SELECT * FROM trusted_workflow_approvals WHERE id=?",
            (approval_id,),
        ).fetchone()
        if row is None:
            raise LookupError(f"Unknown trusted workflow approval: {approval_id}")
        result = dict(row)
        result["evidence"] = json.loads(result.pop("evidence_json"))
        return result

    def authorize_sensitive_action(
        self,
        *,
        assessment_id: str,
        action: SensitiveAction,
        target_ref: str,
        approval_id: str | None = None,
        consume: bool = False,
        manage_transaction: bool = True,
    ) -> dict[str, object]:
        assessment = self.get(assessment_id)
        if action not in ACTIONS:
            raise ValueError("Unknown sensitive action")
        if assessment["origin"] in APPROVER_ORIGINS:
            return {
                "allowed": True,
                "reason": "trusted_instruction_origin",
                "approval_id": None,
            }
        if approval_id is None:
            return {
                "allowed": False,
                "reason": "trusted_workflow_approval_required",
                "approval_id": None,
            }
        approval = self.approval(approval_id)
        if (
            approval["assessment_id"] != assessment_id
            or approval["action"] != action
            or approval["target_ref"] != target_ref
        ):
            return {
                "allowed": False,
                "reason": "approval_scope_mismatch",
                "approval_id": approval_id,
            }
        if approval["consumed_at"] is not None:
            return {
                "allowed": False,
                "reason": "approval_already_consumed",
                "approval_id": approval_id,
            }
        if consume:
            cursor = self.connection.execute(
                """
                UPDATE trusted_workflow_approvals
                SET consumed_at=?
                WHERE id=? AND consumed_at IS NULL
                """,
                (utc_now(), approval_id),
            )
            if manage_transaction:
                self.connection.commit()
            if cursor.rowcount != 1:
                return {
                    "allowed": False,
                    "reason": "approval_already_consumed",
                    "approval_id": approval_id,
                }
        return {
            "allowed": True,
            "reason": "explicit_trusted_workflow_approval",
            "approval_id": approval_id,
        }

    @staticmethod
    def frame_untrusted(
        request: ContentAssessmentRequest,
        assessment: dict[str, object],
    ) -> str:
        if request.origin not in EXTERNAL_ORIGINS:
            return request.content
        return (
            f'<untrusted_data origin="{request.origin}" '
            f'source="{html.escape(request.source_id, quote=True)}" '
            f'ref="{str(assessment["content_hash"])[:12]}">\n'
            + html.escape(request.content, quote=False)
            + "\n</untrusted_data>"
        )

    @staticmethod
    def frame_scoped_skill(
        request: ContentAssessmentRequest,
        assessment: dict[str, object],
    ) -> str:
        if request.origin != "skill_instruction":
            return request.content
        return (
            f'<skill_instruction authority="scoped_skill" '
            f'source="{html.escape(request.source_id, quote=True)}" '
            f'ref="{str(assessment["content_hash"])[:12]}">\n'
            + html.escape(request.content, quote=False)
            + "\n</skill_instruction>"
        )
