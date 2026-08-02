from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
import uuid
from dataclasses import dataclass

from .execution import PlanningAdvice, PlanningAdvisor, Task
from .memory import (
    LifecycleState,
    MemoryCreate,
    MemoryPatch,
    MemoryRecord,
    MemoryStatus,
    MemoryStore,
    MemoryType,
    utc_now,
)
from .secret_management import assert_secret_free

WORD = re.compile(r"[a-z0-9][a-z0-9_.-]*")
SPACE = re.compile(r"\s+")
MAX_FIELD = 16_000
MAX_ERROR_MESSAGE = 4_000
NEGATIVE_PROCEDURE_MIN_OCCURRENCES = 3
NEGATIVE_PROCEDURE_MIN_EVIDENCE = 3
NEGATIVE_PROCEDURE_MIN_CONFIDENCE = 0.95
NEGATIVE_PROCEDURE_MAX_SOURCE_FAILURES = 500


def _normalized(value: str) -> str:
    return SPACE.sub(" ", value.strip().lower())


def _tokens(value: str) -> set[str]:
    return set(WORD.findall(value.lower()))


def _similarity(left: str, right: str) -> float:
    left_tokens = _tokens(left)
    right_tokens = _tokens(right)
    if not left_tokens or not right_tokens:
        return 0.0
    return len(left_tokens & right_tokens) / len(left_tokens | right_tokens)


def _environment_json(value: str) -> str:
    assert_secret_free(value, "failure environment")
    payload = json.loads(value)
    if not isinstance(payload, dict):
        raise ValueError("environment_json must be a JSON object")
    if len(value) > MAX_FIELD:
        raise ValueError("environment_json exceeds the 16 KB limit")
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


@dataclass(frozen=True)
class FailureCreate:
    task_class: str
    strategy_attempted: str
    symptoms: tuple[str, ...]
    failed_action: str
    evidence: tuple[str, ...]
    scope: str = "global"
    environment_json: str = "{}"
    root_cause: str | None = None
    error_type: str | None = None
    error_message: str | None = None
    avoidance_rule: str | None = None
    confidence: float = 0.7
    deterministic: bool = False

    def __post_init__(self) -> None:
        required = (
            ("task_class", self.task_class),
            ("strategy_attempted", self.strategy_attempted),
            ("failed_action", self.failed_action),
            ("scope", self.scope),
        )
        for name, value in required:
            if not value.strip():
                raise ValueError(f"{name} cannot be empty")
        if not self.symptoms or any(not item.strip() for item in self.symptoms):
            raise ValueError("At least one non-empty symptom is required")
        if not self.evidence or any(not item.strip() for item in self.evidence):
            raise ValueError("At least one evidence reference is required")
        if not self.error_type and not self.error_message:
            raise ValueError("error_type or error_message is required")
        if not 0 <= self.confidence <= 1:
            raise ValueError("confidence must be between 0 and 1")
        _environment_json(self.environment_json)
        values = (
            self.task_class,
            self.strategy_attempted,
            *self.symptoms,
            self.failed_action,
            *(value for value in (
                self.root_cause,
                self.error_type,
                self.error_message,
                self.avoidance_rule,
            ) if value),
        )
        if any(len(value) > MAX_FIELD for value in values):
            raise ValueError("Failure field exceeds the 16 KB limit")
        if self.error_message and len(self.error_message) > MAX_ERROR_MESSAGE:
            raise ValueError("error_message exceeds the 4 KB safety limit")


@dataclass(frozen=True)
class FailureRecord:
    id: str
    memory_id: str
    scope: str
    task_class: str
    strategy_attempted: str
    environment_json: str
    symptoms: tuple[str, ...]
    root_cause: str | None
    failed_action: str
    error_type: str | None
    error_message: str | None
    resolution: str | None
    avoidance_rule: str | None
    confidence: float
    evidence: tuple[str, ...]
    deterministic: bool
    occurrence_count: int
    status: str
    remediation_memory_id: str | None
    first_seen_at: str
    last_seen_at: str
    resolved_at: str | None


@dataclass(frozen=True)
class FailureQuery:
    task: str
    task_class: str = "general"
    scope: str = "global"
    strategy: str | None = None
    environment_json: str = "{}"
    limit: int = 5
    minimum_weight: float = 0.15

    def __post_init__(self) -> None:
        if not self.task.strip() or not self.task_class.strip() or not self.scope.strip():
            raise ValueError("Failure query task, task_class, and scope are required")
        if not 1 <= self.limit <= 50:
            raise ValueError("Failure query limit must be between 1 and 50")
        if not 0 <= self.minimum_weight <= 1:
            raise ValueError("minimum_weight must be between 0 and 1")
        _environment_json(self.environment_json)


@dataclass(frozen=True)
class FailureMatch:
    failure: FailureRecord
    analogy_score: float
    avoidance_weight: float
    repetition_weight: float
    absolute_prohibition: bool
    explanation: str


@dataclass(frozen=True)
class NegativeProcedure:
    """Authority-free, evidence-backed projection of a repeated failure."""

    id: str
    scope: str
    task_class: str
    failed_action: str
    applicability_environment_json: str
    avoidance_rule: str
    source_failure_id: str
    source_memory_id: str
    occurrence_count: int
    evidence_count: int
    confidence: float
    authority: str = "planning_constraint_only"


@dataclass(frozen=True)
class NegativeProcedureAssessment:
    failure_id: str
    eligible: bool
    rejection_reasons: tuple[str, ...]
    procedure: NegativeProcedure | None


class FailureIntelligence:
    """Structured, evidence-weighted failure memory and analogy retrieval."""

    def __init__(self, connection: sqlite3.Connection, memories: MemoryStore) -> None:
        self.connection = connection
        self.memories = memories

    @staticmethod
    def _fingerprint(candidate: FailureCreate) -> str:
        stable = {
            "task_class": _normalized(candidate.task_class),
            "strategy": _normalized(candidate.strategy_attempted),
            "environment": json.loads(_environment_json(candidate.environment_json)),
            "failed_action": _normalized(candidate.failed_action),
            "error_type": _normalized(candidate.error_type or ""),
            "symptoms": sorted(_normalized(item) for item in candidate.symptoms),
        }
        return hashlib.sha256(
            json.dumps(stable, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()

    @staticmethod
    def _content(candidate: FailureCreate) -> str:
        cause = candidate.root_cause or candidate.error_type or "unknown cause"
        avoidance = candidate.avoidance_rule or "review the evidence before retrying"
        return (
            f"Failure in {candidate.task_class}: {candidate.symptoms[0]}. "
            f"Strategy attempted: {candidate.strategy_attempted}. "
            f"Cause: {cause}. Avoidance: {avoidance}."
        )

    def _record(self, row: sqlite3.Row) -> FailureRecord:
        return FailureRecord(
            id=row["id"],
            memory_id=row["memory_id"],
            scope=row["scope"],
            task_class=row["task_class"],
            strategy_attempted=row["strategy_attempted"],
            environment_json=row["environment_json"],
            symptoms=tuple(json.loads(row["symptoms_json"])),
            root_cause=row["root_cause"],
            failed_action=row["failed_action"],
            error_type=row["error_type"],
            error_message=row["error_message"],
            resolution=row["resolution"],
            avoidance_rule=row["avoidance_rule"],
            confidence=row["confidence"],
            evidence=tuple(json.loads(row["evidence_json"])),
            deterministic=bool(row["deterministic"]),
            occurrence_count=row["occurrence_count"],
            status=row["failure_status"],
            remediation_memory_id=row["remediation_memory_id"],
            first_seen_at=row["first_seen_at"],
            last_seen_at=row["last_seen_at"],
            resolved_at=row["resolved_at"],
        )

    @staticmethod
    def _select_sql(where: str) -> str:
        return f"""
            SELECT f.*, m.confidence, m.evidence_json,
                   f.status AS failure_status
            FROM failure_records f
            JOIN memories m ON m.id = f.memory_id
            WHERE {where}
        """

    def get(self, failure_id: str) -> FailureRecord | None:
        row = self.connection.execute(
            self._select_sql("f.id = ?"), (failure_id,)
        ).fetchone()
        return self._record(row) if row else None

    @staticmethod
    def _assess_negative_procedure(
        failure: FailureRecord,
    ) -> NegativeProcedureAssessment:
        reasons = []
        if _normalized(failure.scope) == "global":
            reasons.append("global_scope_requires_cross_scope_evidence")
        if failure.status != "unresolved":
            reasons.append("failure_is_resolved")
        if not failure.deterministic:
            reasons.append("failure_is_not_deterministic")
        if failure.confidence < NEGATIVE_PROCEDURE_MIN_CONFIDENCE:
            reasons.append("confidence_below_threshold")
        if failure.occurrence_count < NEGATIVE_PROCEDURE_MIN_OCCURRENCES:
            reasons.append("insufficient_occurrences")
        if len(failure.evidence) < NEGATIVE_PROCEDURE_MIN_EVIDENCE:
            reasons.append("insufficient_distinct_evidence")
        if not failure.avoidance_rule or not failure.avoidance_rule.strip():
            reasons.append("avoidance_rule_missing")
        if reasons:
            return NegativeProcedureAssessment(
                failure_id=failure.id,
                eligible=False,
                rejection_reasons=tuple(reasons),
                procedure=None,
            )
        identity = {
            "scope": _normalized(failure.scope),
            "task_class": _normalized(failure.task_class),
            "failed_action": _normalized(failure.failed_action),
            "environment": json.loads(failure.environment_json),
            "avoidance_rule": _normalized(failure.avoidance_rule or ""),
            "source_failure_id": failure.id,
        }
        procedure_id = "negative-" + hashlib.sha256(
            json.dumps(identity, sort_keys=True, separators=(",", ":")).encode(
                "utf-8"
            )
        ).hexdigest()[:24]
        return NegativeProcedureAssessment(
            failure_id=failure.id,
            eligible=True,
            rejection_reasons=(),
            procedure=NegativeProcedure(
                id=procedure_id,
                scope=failure.scope,
                task_class=failure.task_class,
                failed_action=failure.failed_action,
                applicability_environment_json=failure.environment_json,
                avoidance_rule=failure.avoidance_rule or "",
                source_failure_id=failure.id,
                source_memory_id=failure.memory_id,
                occurrence_count=failure.occurrence_count,
                evidence_count=len(failure.evidence),
                confidence=failure.confidence,
            ),
        )

    def assess_negative_procedures(
        self,
        *,
        scope: str,
        task_class: str,
        limit: int = 50,
    ) -> tuple[NegativeProcedureAssessment, ...]:
        """Assess exact-scope failures without creating skills or new authority."""
        if not scope.strip() or not task_class.strip():
            raise ValueError("scope and task_class are required")
        if not 1 <= limit <= 50:
            raise ValueError("limit must be between 1 and 50")
        rows = self.connection.execute(
            self._select_sql("f.scope = ?")
            + " ORDER BY f.last_seen_at DESC, f.id ASC LIMIT ?",
            (scope, NEGATIVE_PROCEDURE_MAX_SOURCE_FAILURES + 1),
        ).fetchall()
        if len(rows) > NEGATIVE_PROCEDURE_MAX_SOURCE_FAILURES:
            raise ValueError(
                "Negative procedure source exceeds the 500-record scan limit"
            )
        failures = (
            self._record(row)
            for row in rows
            if _normalized(row["task_class"]) == _normalized(task_class)
        )
        assessments = tuple(
            self._assess_negative_procedure(failure) for failure in failures
        )
        return tuple(
            sorted(
                assessments,
                key=lambda item: (
                    item.eligible,
                    (
                        item.procedure.occurrence_count
                        if item.procedure is not None
                        else 0
                    ),
                    item.failure_id,
                ),
                reverse=True,
            )[:limit]
        )

    def record(self, candidate: FailureCreate) -> FailureRecord:
        fingerprint = self._fingerprint(candidate)
        existing = self.connection.execute(
            self._select_sql("f.scope = ? AND f.fingerprint = ?"),
            (candidate.scope, fingerprint),
        ).fetchone()
        now = utc_now()
        if existing:
            current = self._record(existing)
            memory = self.memories.get(current.memory_id)
            if memory is None:
                raise RuntimeError("Failure memory link is broken")
            evidence = tuple(dict.fromkeys((*current.evidence, *candidate.evidence)))
            self.memories.update(
                memory.id,
                MemoryPatch(
                    confidence=max(current.confidence, candidate.confidence),
                    importance=round(
                        min(
                            1.0,
                            max(
                                memory.importance,
                                0.70 + 0.05 * (current.occurrence_count + 1),
                            ),
                        ),
                        6,
                    ),
                    evidence=evidence,
                    expected_updated_at=memory.updated_at,
                ),
            )
            with self.connection:
                self.connection.execute(
                    """
                    UPDATE failure_records
                    SET occurrence_count = occurrence_count + 1,
                        last_seen_at = ?,
                        root_cause = COALESCE(root_cause, ?),
                        avoidance_rule = COALESCE(avoidance_rule, ?),
                        deterministic = MAX(deterministic, ?),
                        status = 'unresolved',
                        resolved_at = NULL
                    WHERE id = ?
                    """,
                    (
                        now,
                        candidate.root_cause,
                        candidate.avoidance_rule,
                        int(candidate.deterministic),
                        current.id,
                    ),
                )
            repeated = self.get(current.id)
            if repeated is None:
                raise RuntimeError("Repeated failure could not be reloaded")
            return repeated

        failure_id = str(uuid.uuid4())
        memory = self.memories.create(
            MemoryCreate(
                type=MemoryType.FAILURE,
                content=self._content(candidate),
                scope=candidate.scope,
                subject=candidate.task_class,
                structured_payload_json=json.dumps(
                    {"failure_record_id": failure_id}, sort_keys=True
                ),
                confidence=candidate.confidence,
                importance=0.9 if candidate.deterministic else 0.7,
                source_type="failure-intelligence",
                source_id=failure_id,
                evidence=candidate.evidence,
                retention_reasons=("explicit_failure_record",),
                status=MemoryStatus.CONFIRMED,
            )
        )
        try:
            with self.connection:
                self.connection.execute(
                    """
                    INSERT INTO failure_records(
                        id, memory_id, scope, task_class, strategy_attempted,
                        environment_json, symptoms_json, root_cause,
                        failed_action, error_type, error_message, resolution,
                        avoidance_rule, deterministic, occurrence_count,
                        status, remediation_memory_id, fingerprint,
                        first_seen_at, last_seen_at, resolved_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, 1,
                              'unresolved', NULL, ?, ?, ?, NULL)
                    """,
                    (
                        failure_id,
                        memory.id,
                        candidate.scope,
                        candidate.task_class,
                        candidate.strategy_attempted,
                        _environment_json(candidate.environment_json),
                        json.dumps(candidate.symptoms),
                        candidate.root_cause,
                        candidate.failed_action,
                        candidate.error_type,
                        candidate.error_message,
                        candidate.avoidance_rule,
                        int(candidate.deterministic),
                        fingerprint,
                        now,
                        now,
                    ),
                )
        except Exception:
            self.memories.set_lifecycle(
                memory.id, LifecycleState.ARCHIVED, reason="orphaned_failure_record"
            )
            raise
        created = self.get(failure_id)
        if created is None:
            raise RuntimeError("Failure record could not be reloaded")
        return created

    def resolve(
        self,
        failure_id: str,
        *,
        resolution: str,
        remediation_memory_id: str,
    ) -> FailureRecord:
        if not resolution.strip():
            raise ValueError("resolution cannot be empty")
        if len(resolution) > MAX_FIELD:
            raise ValueError("resolution exceeds the 16 KB limit")
        failure = self.get(failure_id)
        if failure is None:
            raise KeyError(failure_id)
        remediation = self.memories.get(remediation_memory_id)
        if remediation is None:
            raise KeyError(remediation_memory_id)
        if (
            remediation.status is not MemoryStatus.CONFIRMED
            or remediation.lifecycle_state
            not in (LifecycleState.ACTIVE, LifecycleState.COLD)
            or remediation.type
            not in (MemoryType.PROCEDURAL, MemoryType.DECISION, MemoryType.SEMANTIC)
            or not remediation.evidence
        ):
            raise ValueError(
                "Remediation must be a live, confirmed, evidence-backed "
                "procedure, decision, or fact"
            )
        with self.connection:
            self.connection.execute(
                """
                UPDATE failure_records
                SET resolution = ?, status = 'resolved',
                    remediation_memory_id = ?, resolved_at = ?
                WHERE id = ?
                """,
                (resolution.strip(), remediation_memory_id, utc_now(), failure_id),
            )
        resolved = self.get(failure_id)
        if resolved is None:
            raise RuntimeError("Resolved failure could not be reloaded")
        return resolved

    @staticmethod
    def _environment_similarity(left_json: str, right_json: str) -> float:
        left = json.loads(left_json)
        right = json.loads(right_json)
        if not right:
            return 0.5
        keys = set(left) | set(right)
        if not keys:
            return 0.5
        return sum(left.get(key) == right.get(key) for key in keys) / len(keys)

    def query(self, query: FailureQuery) -> tuple[FailureMatch, ...]:
        rows = self.connection.execute(
            self._select_sql(
                """
                (f.scope = ? OR f.scope = 'global')
                AND m.status = 'confirmed'
                AND m.lifecycle_state IN ('active', 'cold')
                """
            ),
            (query.scope,),
        ).fetchall()
        matches: list[FailureMatch] = []
        requested_environment = _environment_json(query.environment_json)
        for row in rows:
            failure = self._record(row)
            class_score = (
                1.0
                if _normalized(failure.task_class) == _normalized(query.task_class)
                else _similarity(failure.task_class, query.task_class)
            )
            history_text = " ".join(
                (
                    failure.task_class,
                    *failure.symptoms,
                    failure.root_cause or "",
                    failure.failed_action,
                    failure.error_type or "",
                )
            )
            objective_score = _similarity(history_text, query.task)
            strategy_score = (
                _similarity(failure.strategy_attempted, query.strategy)
                if query.strategy
                else 0.5
            )
            environment_score = self._environment_similarity(
                failure.environment_json, requested_environment
            )
            analogy = (
                0.40 * class_score
                + 0.30 * objective_score
                + 0.20 * strategy_score
                + 0.10 * environment_score
            )
            repetition = 1.0 - math.exp(-failure.occurrence_count / 3)
            avoidance = analogy * failure.confidence * (0.7 + 0.3 * repetition)
            if failure.status == "resolved":
                avoidance *= 0.35
            avoidance = max(0.0, min(1.0, avoidance))
            absolute = bool(
                failure.deterministic
                and _normalized(failure.scope) != "global"
                and failure.status == "unresolved"
                and failure.confidence >= 0.95
                and failure.occurrence_count >= NEGATIVE_PROCEDURE_MIN_OCCURRENCES
                and len(failure.evidence) >= NEGATIVE_PROCEDURE_MIN_EVIDENCE
                and analogy >= 0.75
                and failure.avoidance_rule
            )
            if avoidance < query.minimum_weight:
                continue
            cause = failure.root_cause or failure.error_type or "an unknown cause"
            matches.append(
                FailureMatch(
                    failure=failure,
                    analogy_score=round(analogy, 6),
                    avoidance_weight=round(avoidance, 6),
                    repetition_weight=round(repetition, 6),
                    absolute_prohibition=absolute,
                    explanation=(
                        f"An analogous approach previously failed because {cause}. "
                        f"Avoidance weight {avoidance:.3f}; "
                        f"{failure.occurrence_count} occurrence(s)."
                    ),
                )
            )
        matches.sort(
            key=lambda item: (
                item.absolute_prohibition,
                item.avoidance_weight,
                item.analogy_score,
                item.failure.last_seen_at,
                item.failure.id,
            ),
            reverse=True,
        )
        return tuple(matches[: query.limit])


class FailurePlanningAdvisor(PlanningAdvisor):
    def __init__(
        self,
        intelligence: FailureIntelligence,
        *,
        minimum_weight: float = 0.25,
        limit: int = 3,
    ) -> None:
        self.intelligence = intelligence
        self.minimum_weight = minimum_weight
        self.limit = limit

    def advise(self, task: Task) -> PlanningAdvice:
        matches = self.intelligence.query(
            FailureQuery(
                task=task.objective,
                task_class=task.task_class,
                scope=task.scope,
                strategy=task.strategy,
                environment_json=task.environment_json,
                limit=self.limit,
                minimum_weight=self.minimum_weight,
            )
        )
        constraints = []
        for match in matches:
            if match.failure.status == "resolved":
                guidance = (
                    f"Prior resolution: {match.failure.resolution}; "
                    f"remediation memory {match.failure.remediation_memory_id}"
                )
            else:
                guidance = (
                    match.failure.avoidance_rule
                    or match.failure.root_cause
                    or match.failure.error_type
                    or "review the prior failure evidence"
                )
            qualifier = (
                "Deterministic prohibition"
                if match.absolute_prohibition
                else f"Weighted warning {match.avoidance_weight:.3f}"
            )
            constraints.append(
                f"{qualifier} from failure {match.failure.id}: {guidance}"
            )
        return PlanningAdvice(
            constraints=tuple(constraints),
            source_ids=tuple(match.failure.id for match in matches),
            weights=tuple(match.avoidance_weight for match in matches),
            blocked=any(match.absolute_prohibition for match in matches),
        )
