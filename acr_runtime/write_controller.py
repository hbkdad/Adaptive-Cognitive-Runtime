from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import uuid
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from enum import Enum

from .content_security import (
    ContentAssessmentRequest,
    ContentSecurityController,
    detect_suspicious_instructions,
    infer_content_origin,
)
from .memory import (
    MemoryCreate,
    MemoryPatch,
    MemoryQuery,
    MemoryRecord,
    MemoryStatus,
    MemoryStore,
    MemoryType,
    normalize_timestamp,
    parse_timestamp,
    utc_now,
)
from .temporal import TemporalMemory
from .secret_management import detect_secret_material

CALCULATION_RE = re.compile(r"^[\d\s+\-*/().=,%]+$")
RISK_PATTERNS = {
    "prompt_injection": (
        "ignore previous",
        "ignore all previous",
        "reveal the system prompt",
        "system prompt",
    ),
    "exfiltration": ("exfiltrate", "send secrets", "upload credentials"),
    "active_content": ("<script", "javascript:"),
}
GREETINGS = {
    "hello",
    "hi",
    "hey",
    "good morning",
    "good afternoon",
    "good evening",
    "thanks",
    "thank you",
}


def content_risk_flags(content: str) -> tuple[str, ...]:
    lowered = content.casefold()
    flags = list(
        flag
        for flag, patterns in RISK_PATTERNS.items()
        if any(pattern in lowered for pattern in patterns)
    )
    signals = detect_suspicious_instructions(content)
    secret_types = detect_secret_material(content)
    if secret_types:
        flags.append("secret_material")
        flags.extend(f"secret_material:{item}" for item in secret_types)
    if signals and "prompt_injection" not in flags:
        flags.append("prompt_injection")
    flags.extend(f"prompt_injection:{signal}" for signal in signals)
    return tuple(dict.fromkeys(flags))


class WriteOutcome(str, Enum):
    IGNORE = "ignore"
    STORE_TEMPORARY = "store_temporary"
    STORE_CANDIDATE = "store_candidate"
    STORE_CONFIRMED = "store_confirmed"
    UPDATE_EXISTING = "update_existing"
    SUPERSEDE_EXISTING = "supersede_existing"
    REQUEST_VERIFICATION = "request_verification"
    QUARANTINE = "quarantine"


@dataclass(frozen=True)
class CandidateFact:
    type: MemoryType
    content: str
    scope: str | None
    subject: str | None = None
    confidence: float = 0.5
    importance: float = 0.5
    usefulness: float = 0.5
    stability: float = 0.5
    evidence: tuple[str, ...] = ()
    source_type: str | None = None
    source_id: str | None = None
    structured_payload_json: str = "{}"
    trusted_source: bool = False
    temporary: bool = False
    privacy_risk: bool = False
    security_risk: bool = False
    valid_from: str | None = None
    valid_until: str | None = None
    content_origin: str | None = None
    provenance: tuple[str, ...] = ()
    security_assessment_id: str | None = None
    workflow_approval_id: str | None = None

    def __post_init__(self) -> None:
        if not self.content.strip():
            raise ValueError("Candidate content cannot be empty")
        for name, value in (
            ("confidence", self.confidence),
            ("importance", self.importance),
            ("usefulness", self.usefulness),
            ("stability", self.stability),
        ):
            if not 0 <= value <= 1:
                raise ValueError(f"{name} must be between 0 and 1")
        payload = json.loads(self.structured_payload_json)
        if not isinstance(payload, (dict, list)):
            raise ValueError("structured payload must be a JSON object or array")
        if self.valid_from is not None:
            normalize_timestamp(self.valid_from)
        if self.valid_until is not None:
            normalize_timestamp(self.valid_until)
        if (
            self.valid_from is not None
            and self.valid_until is not None
            and parse_timestamp(self.valid_until) <= parse_timestamp(self.valid_from)
        ):
            raise ValueError("valid_until must be later than valid_from")
        if any(not item.strip() for item in self.provenance):
            raise ValueError("Candidate provenance cannot contain empty references")

    @property
    def fingerprint(self) -> str:
        identity = json.dumps(
            {
                "type": self.type.value,
                "scope": self.scope,
                "subject": self.subject,
                "content": self.content,
            },
            sort_keys=True,
            ensure_ascii=False,
        )
        return hashlib.sha256(identity.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class WritePolicy:
    minimum_usefulness: float = 0.30
    temporary_stability: float = 0.35
    confirmed_confidence: float = 0.90
    confirmed_usefulness: float = 0.70
    confirmed_stability: float = 0.75
    temporary_ttl_hours: int = 24

    def __post_init__(self) -> None:
        values = (
            self.minimum_usefulness,
            self.temporary_stability,
            self.confirmed_confidence,
            self.confirmed_usefulness,
            self.confirmed_stability,
        )
        if any(not 0 <= value <= 1 for value in values):
            raise ValueError("Write policy thresholds must be between 0 and 1")
        if self.temporary_ttl_hours < 1:
            raise ValueError("temporary_ttl_hours must be positive")


@dataclass(frozen=True)
class WriteDecision:
    id: str
    outcome: WriteOutcome
    candidate_hash: str
    reasons: tuple[str, ...]
    risk_flags: tuple[str, ...]
    memory: MemoryRecord | None
    matched_memory_id: str | None
    created_at: str
    security_assessment_id: str | None = None


class SQLiteWriteDecisionAudit:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def record(self, decision: WriteDecision, candidate: CandidateFact) -> None:
        self.connection.execute(
            """
            INSERT INTO memory_write_decisions (
                id, candidate_hash, outcome, memory_id, matched_memory_id,
                reasons_json, risk_flags_json, scope, memory_type, confidence,
                evidence_count, created_at, security_assessment_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                decision.id,
                decision.candidate_hash,
                decision.outcome.value,
                decision.memory.id if decision.memory else None,
                decision.matched_memory_id,
                json.dumps(decision.reasons),
                json.dumps(decision.risk_flags),
                candidate.scope,
                candidate.type.value,
                candidate.confidence,
                len(candidate.evidence),
                decision.created_at,
                decision.security_assessment_id,
            ),
        )
        self.connection.commit()

    def recent(self, *, limit: int = 100) -> list[dict[str, object]]:
        if not 1 <= limit <= 500:
            raise ValueError("Audit limit must be between 1 and 500")
        rows = self.connection.execute(
            """
            SELECT id, candidate_hash, outcome, memory_id, matched_memory_id,
                   reasons_json, risk_flags_json, scope, memory_type, confidence,
                   evidence_count, created_at
                   , security_assessment_id
            FROM memory_write_decisions
            ORDER BY created_at DESC, id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        decisions: list[dict[str, object]] = []
        for row in rows:
            decision = dict(row)
            decision["reasons"] = json.loads(str(decision.pop("reasons_json")))
            decision["risk_flags"] = json.loads(
                str(decision.pop("risk_flags_json"))
            )
            decisions.append(decision)
        return decisions


@dataclass(frozen=True)
class _Plan:
    outcome: WriteOutcome
    reasons: tuple[str, ...]
    risks: tuple[str, ...] = ()
    matched: MemoryRecord | None = None
    security_assessment_id: str | None = None


class MemoryWriteController:
    def __init__(
        self,
        store: MemoryStore,
        audit: SQLiteWriteDecisionAudit,
        *,
        policy: WritePolicy | None = None,
        security: ContentSecurityController | None = None,
    ) -> None:
        self.store = store
        self.audit = audit
        self.policy = policy or WritePolicy()
        self.temporal = TemporalMemory(store)
        self.security = security

    def _security_assessment(
        self, candidate: CandidateFact
    ) -> dict[str, object] | None:
        if self.security is None:
            return None
        origin = candidate.content_origin or infer_content_origin(
            candidate.source_type
        )
        if candidate.security_assessment_id is not None:
            assessment = self.security.get(candidate.security_assessment_id)
            expected_hash = hashlib.sha256(
                candidate.content.encode("utf-8")
            ).hexdigest()
            if (
                assessment["content_hash"] != expected_hash
                or assessment["origin"] != origin
            ):
                raise ValueError(
                    "Candidate content does not match its security assessment"
                )
            return assessment
        return self.security.assess(ContentAssessmentRequest(
            origin=origin,
            source_id=candidate.source_id or candidate.fingerprint,
            content=candidate.content,
            provenance=tuple(dict.fromkeys((
                f"memory-candidate:{candidate.fingerprint}",
                *candidate.provenance,
            ))),
        ))

    @staticmethod
    def _normalized(text: str) -> str:
        return " ".join(text.casefold().split())

    @staticmethod
    def _risk_flags(candidate: CandidateFact) -> tuple[str, ...]:
        flags: list[str] = []
        if candidate.privacy_risk:
            flags.append("privacy_risk")
        if candidate.security_risk:
            flags.append("security_risk")
        flags.extend(content_risk_flags(candidate.content))
        flags.extend(content_risk_flags(candidate.structured_payload_json))
        return tuple(flags)

    def _find_existing(
        self, candidate: CandidateFact
    ) -> tuple[MemoryRecord | None, MemoryRecord | None]:
        if candidate.scope is None:
            return None, None
        exact: MemoryRecord | None = None
        conflict: MemoryRecord | None = None
        if candidate.subject:
            resolution = self.temporal.current(
                candidate.subject, scope=candidate.scope
            )
            current_records = (
                (resolution.preferred,) + resolution.alternatives
                if resolution.preferred
                else ()
            )
            pending_records = self.store.subject_records(
                candidate.subject,
                scope=candidate.scope,
                statuses=(
                    MemoryStatus.CANDIDATE,
                    MemoryStatus.QUARANTINED,
                ),
            )
            for record in (*current_records, *pending_records):
                if self._normalized(record.content) == self._normalized(
                    candidate.content
                ):
                    exact = record
                    break
            if (
                exact is None
                and resolution.preferred is not None
                and resolution.preferred.scope == candidate.scope
            ):
                conflict = resolution.preferred
        else:
            records = self.store.search(
                MemoryQuery(
                    scope=candidate.scope,
                    statuses=(
                        MemoryStatus.CANDIDATE,
                        MemoryStatus.CONFIRMED,
                        MemoryStatus.QUARANTINED,
                    ),
                    limit=200,
                )
            ).records
            exact = next(
                (
                    record
                    for record in records
                    if self._normalized(record.content)
                    == self._normalized(candidate.content)
                ),
                None,
            )
        return exact, conflict

    def _qualifies_confirmed(self, candidate: CandidateFact) -> bool:
        return (
            candidate.trusted_source
            and candidate.confidence >= self.policy.confirmed_confidence
            and candidate.usefulness >= self.policy.confirmed_usefulness
            and candidate.stability >= self.policy.confirmed_stability
            and bool(candidate.evidence)
        )

    def evaluate(self, candidate: CandidateFact) -> _Plan:
        assessment = self._security_assessment(candidate)
        if assessment is not None:
            authorization = self.security.authorize_sensitive_action(
                assessment_id=str(assessment["id"]),
                action="memory.create",
                target_ref=candidate.fingerprint,
                approval_id=candidate.workflow_approval_id,
            )
            if not authorization["allowed"]:
                return _Plan(
                    WriteOutcome.QUARANTINE,
                    ("external_content_requires_trusted_workflow",),
                    ("untrusted_content_derivation",),
                    security_assessment_id=str(assessment["id"]),
                )
        risks = self._risk_flags(candidate)
        if risks:
            return _Plan(
                WriteOutcome.QUARANTINE,
                ("unsafe_content_requires_review",),
                risks,
                security_assessment_id=(
                    str(assessment["id"]) if assessment is not None else None
                ),
            )
        normalized = self._normalized(candidate.content).strip("!,.? ")
        if normalized in GREETINGS:
            return _Plan(WriteOutcome.IGNORE, ("ephemeral_greeting",))
        if CALCULATION_RE.fullmatch(candidate.content.strip()):
            return _Plan(WriteOutcome.IGNORE, ("one_off_calculation",))
        if candidate.scope is None or not candidate.scope.strip():
            return _Plan(
                WriteOutcome.REQUEST_VERIFICATION,
                ("scope_unknown",),
            )
        if candidate.usefulness < self.policy.minimum_usefulness:
            return _Plan(WriteOutcome.IGNORE, ("low_expected_future_utility",))

        exact, conflict = self._find_existing(candidate)
        if exact is not None:
            if (
                exact.status is MemoryStatus.CANDIDATE
                and self._qualifies_confirmed(candidate)
            ):
                return _Plan(
                    WriteOutcome.UPDATE_EXISTING,
                    ("candidate_verified_by_trusted_evidence",),
                    matched=exact,
                )
            evidence_added = bool(set(candidate.evidence) - set(exact.evidence))
            quality_improved = (
                candidate.confidence > exact.confidence
                or candidate.importance > exact.importance
            )
            if evidence_added or quality_improved:
                return _Plan(
                    WriteOutcome.UPDATE_EXISTING,
                    ("duplicate_claim_with_better_evidence_or_quality",),
                    matched=exact,
                )
            return _Plan(
                WriteOutcome.IGNORE,
                ("duplicate_without_new_evidence",),
                matched=exact,
            )

        if candidate.temporary or candidate.stability < self.policy.temporary_stability:
            return _Plan(
                WriteOutcome.STORE_TEMPORARY,
                ("useful_but_short_lived",),
            )

        qualifies_confirmed = self._qualifies_confirmed(candidate)
        if conflict is not None:
            if qualifies_confirmed:
                return _Plan(
                    WriteOutcome.SUPERSEDE_EXISTING,
                    ("trusted_evidence_changes_current_subject",),
                    matched=conflict,
                )
            return _Plan(
                WriteOutcome.REQUEST_VERIFICATION,
                ("unresolved_contradiction",),
                matched=conflict,
            )
        if qualifies_confirmed:
            return _Plan(
                WriteOutcome.STORE_CONFIRMED,
                ("stable_high_value_evidence_from_trusted_source",),
            )
        if not candidate.evidence and candidate.confidence >= self.policy.confirmed_confidence:
            return _Plan(
                WriteOutcome.REQUEST_VERIFICATION,
                ("high_confidence_claim_lacks_evidence",),
            )
        return _Plan(
            WriteOutcome.STORE_CANDIDATE,
            ("potential_future_value_pending_confirmation",),
        )

    def _create(
        self,
        candidate: CandidateFact,
        *,
        status: MemoryStatus,
        memory_type: MemoryType | None = None,
        valid_until: str | None = None,
        supersedes: str | None = None,
        reasons: tuple[str, ...],
    ) -> MemoryRecord:
        return self.store.create(
            MemoryCreate(
                type=memory_type or candidate.type,
                content=candidate.content,
                scope=candidate.scope or "global",
                subject=candidate.subject,
                structured_payload_json=candidate.structured_payload_json,
                confidence=candidate.confidence,
                importance=candidate.importance,
                source_type=candidate.source_type,
                source_id=candidate.source_id,
                evidence=candidate.evidence,
                retention_reasons=reasons,
                status=status,
                valid_from=candidate.valid_from,
                valid_until=valid_until or candidate.valid_until,
                supersedes=supersedes,
            )
        )

    def consider(self, candidate: CandidateFact) -> WriteDecision:
        plan = self.evaluate(candidate)
        assessment = self._security_assessment(candidate)
        if assessment is not None and plan.security_assessment_id is None:
            plan = replace(
                plan, security_assessment_id=str(assessment["id"])
            )
        memory: MemoryRecord | None = None
        mutating_outcomes = {
            WriteOutcome.STORE_TEMPORARY,
            WriteOutcome.STORE_CANDIDATE,
            WriteOutcome.STORE_CONFIRMED,
            WriteOutcome.UPDATE_EXISTING,
            WriteOutcome.SUPERSEDE_EXISTING,
        }
        if (
            plan.outcome in mutating_outcomes
            and plan.security_assessment_id is not None
            and self.security is not None
        ):
            authorization = self.security.authorize_sensitive_action(
                assessment_id=plan.security_assessment_id,
                action="memory.create",
                target_ref=candidate.fingerprint,
                approval_id=candidate.workflow_approval_id,
                consume=True,
            )
            if not authorization["allowed"]:
                plan = _Plan(
                    WriteOutcome.QUARANTINE,
                    ("trusted_workflow_approval_unavailable",),
                    ("untrusted_content_derivation",),
                    security_assessment_id=plan.security_assessment_id,
                )
        if plan.outcome is WriteOutcome.STORE_TEMPORARY:
            valid_from = (
                normalize_timestamp(candidate.valid_from)
                if candidate.valid_from
                else utc_now()
            )
            expires = candidate.valid_until
            if expires is None:
                start = datetime.fromisoformat(valid_from)
                expires = (
                    start + timedelta(hours=self.policy.temporary_ttl_hours)
                ).astimezone(timezone.utc).isoformat()
            memory = self._create(
                candidate,
                status=MemoryStatus.CONFIRMED,
                memory_type=MemoryType.TEMPORARY,
                valid_until=expires,
                reasons=plan.reasons,
            )
        elif plan.outcome is WriteOutcome.STORE_CANDIDATE:
            memory = self._create(
                candidate,
                status=MemoryStatus.CANDIDATE,
                reasons=plan.reasons,
            )
        elif plan.outcome is WriteOutcome.STORE_CONFIRMED:
            memory = self._create(
                candidate,
                status=MemoryStatus.CONFIRMED,
                reasons=plan.reasons,
            )
        elif plan.outcome is WriteOutcome.UPDATE_EXISTING:
            assert plan.matched is not None
            memory = self.store.update(
                plan.matched.id,
                MemoryPatch(
                    confidence=max(plan.matched.confidence, candidate.confidence),
                    importance=max(plan.matched.importance, candidate.importance),
                    evidence=tuple(
                        dict.fromkeys((*plan.matched.evidence, *candidate.evidence))
                    ),
                    retention_reasons=plan.reasons,
                    expected_updated_at=plan.matched.updated_at,
                ),
            )
            if plan.reasons == ("candidate_verified_by_trusted_evidence",):
                memory = self.store.set_status(
                    memory.id, MemoryStatus.CONFIRMED
                )
        elif plan.outcome is WriteOutcome.SUPERSEDE_EXISTING:
            assert plan.matched is not None
            memory = self._create(
                candidate,
                status=MemoryStatus.CONFIRMED,
                supersedes=plan.matched.id,
                reasons=plan.reasons,
            )

        decision = WriteDecision(
            id=str(uuid.uuid4()),
            outcome=plan.outcome,
            candidate_hash=candidate.fingerprint,
            reasons=plan.reasons,
            risk_flags=plan.risks,
            memory=memory,
            matched_memory_id=plan.matched.id if plan.matched else None,
            created_at=utc_now(),
            security_assessment_id=plan.security_assessment_id,
        )
        self.audit.record(decision, candidate)
        return decision
