from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Callable

from .secret_management import assert_secret_free


MICRO = 1_000_000
MAX_FINDINGS = 500
MAX_EXPECTED_FUTURE_USES = 100
MAX_COST_MICROS = 100_000_000


class VerificationActionKind(str, Enum):
    ASK_USER = "ask_user"
    INSPECT_REPOSITORY = "inspect_repository"
    CONSULT_OFFICIAL_DOCUMENTATION = "consult_official_documentation"
    RUN_LOCAL_DIAGNOSTIC = "run_local_diagnostic"
    COMPARE_PRIMARY_SOURCES = "compare_primary_sources"


ACTION_CAPABILITIES: dict[VerificationActionKind, str | None] = {
    VerificationActionKind.ASK_USER: None,
    VerificationActionKind.INSPECT_REPOSITORY: "filesystem.read",
    VerificationActionKind.CONSULT_OFFICIAL_DOCUMENTATION: "network.read",
    VerificationActionKind.RUN_LOCAL_DIAGNOSTIC: "shell.execute",
    VerificationActionKind.COMPARE_PRIMARY_SOURCES: "network.read",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _text(value: object, field: str, maximum: int = 500) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be text")
    normalized = " ".join(value.split())
    if not normalized or len(normalized) > maximum:
        raise ValueError(f"{field} must be 1..{maximum} characters")
    assert_secret_free(normalized, f"active learning {field}")
    return normalized


def _micros(value: object, field: str, maximum: int = MICRO) -> int:
    if type(value) is not int or not 0 <= value <= maximum:
        raise ValueError(f"{field} must be an integer between 0 and {maximum}")
    return value


def _hash(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")
    ).hexdigest()


def _task_hash(task_id: str) -> str:
    return hashlib.sha256(task_id.encode("utf-8")).hexdigest()


def _normalized_key(value: str) -> str:
    return " ".join(value.casefold().split())


@dataclass(frozen=True)
class ActiveLearningRequest:
    scope: str
    task_class: str
    uncertainty_key: str
    action_kind: VerificationActionKind
    target_ref: str
    expected_future_uses: int
    impact_micros: int
    resolution_probability_micros: int
    interruption_cost_micros: int
    verification_cost_micros: int
    evidence: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "scope", _text(self.scope, "scope", 128))
        object.__setattr__(
            self, "task_class", _text(self.task_class, "task_class", 128)
        )
        object.__setattr__(
            self,
            "uncertainty_key",
            _normalized_key(_text(self.uncertainty_key, "uncertainty_key")),
        )
        object.__setattr__(
            self, "target_ref", _text(self.target_ref, "target_ref")
        )
        if not isinstance(self.action_kind, VerificationActionKind):
            raise ValueError("action_kind must use the closed vocabulary")
        if (
            type(self.expected_future_uses) is not int
            or not 1
            <= self.expected_future_uses
            <= MAX_EXPECTED_FUTURE_USES
        ):
            raise ValueError(
                "expected_future_uses must be an integer between 1 and 100"
            )
        _micros(self.impact_micros, "impact_micros")
        _micros(
            self.resolution_probability_micros,
            "resolution_probability_micros",
        )
        _micros(
            self.interruption_cost_micros,
            "interruption_cost_micros",
            MAX_COST_MICROS,
        )
        _micros(
            self.verification_cost_micros,
            "verification_cost_micros",
            MAX_COST_MICROS,
        )
        if self.impact_micros == 0:
            raise ValueError("impact_micros must be positive")
        if self.resolution_probability_micros == 0:
            raise ValueError("resolution_probability_micros must be positive")
        if self.interruption_cost_micros + self.verification_cost_micros == 0:
            raise ValueError("A verification action must declare non-zero cost")
        if not self.evidence or len(self.evidence) > 8:
            raise ValueError("active learning evidence must contain 1..8 references")
        normalized_evidence = tuple(
            _text(item, "evidence reference") for item in self.evidence
        )
        if len(set(normalized_evidence)) != len(normalized_evidence):
            raise ValueError("active learning evidence must be unique")
        object.__setattr__(self, "evidence", normalized_evidence)

    @classmethod
    def from_dict(cls, payload: object) -> "ActiveLearningRequest":
        if not isinstance(payload, dict):
            raise ValueError("active learning request must be an object")
        fields = {
            "schema_version",
            "scope",
            "task_class",
            "uncertainty_key",
            "action_kind",
            "target_ref",
            "expected_future_uses",
            "impact_micros",
            "resolution_probability_micros",
            "interruption_cost_micros",
            "verification_cost_micros",
            "evidence",
        }
        if set(payload) != fields or payload.get("schema_version") != 1:
            raise ValueError("active learning request must use the exact v1 schema")
        evidence = payload["evidence"]
        if not isinstance(evidence, list) or not all(
            isinstance(item, str) for item in evidence
        ):
            raise ValueError("active learning evidence must be a string list")
        for field in (
            "scope",
            "task_class",
            "uncertainty_key",
            "action_kind",
            "target_ref",
        ):
            if not isinstance(payload[field], str):
                raise ValueError(f"{field} must be text")
        try:
            action_kind = VerificationActionKind(payload["action_kind"])
        except ValueError as error:
            raise ValueError("action_kind must use the closed vocabulary") from error
        return cls(
            scope=payload["scope"],
            task_class=payload["task_class"],
            uncertainty_key=payload["uncertainty_key"],
            action_kind=action_kind,
            target_ref=payload["target_ref"],
            expected_future_uses=payload["expected_future_uses"],  # type: ignore[arg-type]
            impact_micros=payload["impact_micros"],  # type: ignore[arg-type]
            resolution_probability_micros=payload[
                "resolution_probability_micros"
            ],  # type: ignore[arg-type]
            interruption_cost_micros=payload[
                "interruption_cost_micros"
            ],  # type: ignore[arg-type]
            verification_cost_micros=payload[
                "verification_cost_micros"
            ],  # type: ignore[arg-type]
            evidence=tuple(evidence),
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "scope": self.scope,
            "task_class": self.task_class,
            "uncertainty_key": self.uncertainty_key,
            "action_kind": self.action_kind.value,
            "target_ref": self.target_ref,
            "expected_future_uses": self.expected_future_uses,
            "impact_micros": self.impact_micros,
            "resolution_probability_micros": (
                self.resolution_probability_micros
            ),
            "interruption_cost_micros": self.interruption_cost_micros,
            "verification_cost_micros": self.verification_cost_micros,
            "evidence": list(self.evidence),
        }


@dataclass(frozen=True)
class ActiveLearningAssessment:
    id: str
    request: ActiveLearningRequest
    status: str
    reasons: tuple[str, ...]
    occurrence_count: int
    distinct_task_count: int
    total_reflected_task_count: int
    recurrence_micros: int
    expected_benefit_micros: int
    total_cost_micros: int
    expected_net_value_micros: int
    observation_refs: tuple[dict[str, str], ...]
    created_at: str

    def as_dict(self) -> dict[str, object]:
        proposed_action = None
        if self.status == "suggested":
            proposed_action = {
                "kind": self.request.action_kind.value,
                "target_ref": self.request.target_ref,
                "required_capability": ACTION_CAPABILITIES[
                    self.request.action_kind
                ],
                "execution_authority": False,
            }
        return {
            "id": self.id,
            "status": self.status,
            "scope": self.request.scope,
            "task_class": self.request.task_class,
            "uncertainty_key": self.request.uncertainty_key,
            "reasons": list(self.reasons),
            "metrics": {
                "occurrence_count": self.occurrence_count,
                "distinct_task_count": self.distinct_task_count,
                "total_reflected_task_count": self.total_reflected_task_count,
                "recurrence_micros": self.recurrence_micros,
                "expected_future_uses": self.request.expected_future_uses,
                "impact_micros": self.request.impact_micros,
                "resolution_probability_micros": (
                    self.request.resolution_probability_micros
                ),
                "expected_benefit_micros": self.expected_benefit_micros,
                "interruption_cost_micros": (
                    self.request.interruption_cost_micros
                ),
                "verification_cost_micros": (
                    self.request.verification_cost_micros
                ),
                "total_cost_micros": self.total_cost_micros,
                "expected_net_value_micros": self.expected_net_value_micros,
            },
            "proposed_action": proposed_action,
            "observation_refs": list(self.observation_refs),
            "evidence": list(self.request.evidence),
            "created_at": self.created_at,
        }


class ActiveLearningEngine:
    """Propose, but never execute, high-value verification actions."""

    def __init__(
        self,
        connection: sqlite3.Connection,
        *,
        mutation_guard: Callable[[str], None] | None = None,
    ) -> None:
        self.connection = connection
        self.mutation_guard = mutation_guard

    def _observations(
        self, request: ActiveLearningRequest
    ) -> tuple[tuple[dict[str, str], ...], int]:
        rows = self.connection.execute(
            """
            SELECT rf.run_id, rr.task_id, rf.subject_ids_json, rf.evidence_json
            FROM reflection_findings AS rf
            JOIN reflection_runs AS rr ON rr.id = rf.run_id
            JOIN tasks AS t ON t.id = rr.task_id
            WHERE rf.category = 'missing_information'
              AND rf.verdict = 'missing_information_observed'
              AND t.scope = ?
            ORDER BY rf.created_at, rf.id
            LIMIT ?
            """,
            (request.scope, MAX_FINDINGS + 1),
        ).fetchall()
        if len(rows) > MAX_FINDINGS:
            raise ValueError("active learning finding scan exceeds safe bound")
        total_reflected_task_count = int(
            self.connection.execute(
                """
                SELECT COUNT(DISTINCT rr.task_id)
                FROM reflection_runs AS rr
                JOIN tasks AS t ON t.id = rr.task_id
                WHERE t.scope = ?
                """,
                (request.scope,),
            ).fetchone()[0]
        )
        if total_reflected_task_count > MAX_FINDINGS:
            raise ValueError("active learning task scan exceeds safe bound")
        observations: list[dict[str, str]] = []
        for row in rows:
            keys = json.loads(row["subject_ids_json"])
            if not isinstance(keys, list) or not all(
                isinstance(item, str) for item in keys
            ):
                raise ValueError("persisted missing-information keys are invalid")
            if request.uncertainty_key not in {
                _normalized_key(item) for item in keys
            }:
                continue
            evidence = json.loads(row["evidence_json"])
            if not isinstance(evidence, list):
                raise ValueError("persisted missing-information evidence is invalid")
            observations.append(
                {
                    "reflection_run_id": str(row["run_id"]),
                    "task_id_sha256": _task_hash(str(row["task_id"])),
                    "evidence_sha256": _hash(evidence),
                }
            )
        return tuple(observations), total_reflected_task_count

    def assess(
        self, request: ActiveLearningRequest
    ) -> ActiveLearningAssessment:
        if self.mutation_guard is not None:
            self.mutation_guard("autonomous_optimization")
        observations, total_tasks = self._observations(request)
        distinct_tasks = len(
            {item["task_id_sha256"] for item in observations}
        )
        recurrence_micros = (
            0
            if total_tasks == 0
            else distinct_tasks * MICRO // total_tasks
        )
        expected_benefit = (
            request.impact_micros
            * recurrence_micros
            * request.resolution_probability_micros
            * request.expected_future_uses
            // (MICRO * MICRO)
        )
        total_cost = (
            request.interruption_cost_micros
            + request.verification_cost_micros
        )
        net_value = expected_benefit - total_cost
        reasons: list[str] = []
        if len(observations) < 3:
            reasons.append("insufficient_occurrences")
        if distinct_tasks < 3:
            reasons.append("insufficient_distinct_tasks")
        if expected_benefit <= total_cost:
            reasons.append("interruption_or_cost_not_justified")
        status = "deferred" if reasons else "suggested"
        if not reasons:
            reasons.append(
                "expected_future_utility_exceeds_interruption_and_cost"
            )

        observation_hash = _hash(observations)
        request_hash = _hash(request.as_dict())
        existing = self.connection.execute(
            """
            SELECT id FROM active_learning_runs
            WHERE request_hash=? AND observation_set_hash=?
            """,
            (request_hash, observation_hash),
        ).fetchone()
        if existing is not None:
            return self.get(str(existing["id"]))

        assessment_id = str(uuid.uuid4())
        created_at = _now()
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO active_learning_runs (
                    id, request_hash, observation_set_hash, scope, task_class,
                    uncertainty_key, action_kind, target_ref,
                    required_capability, status, reasons_json,
                    occurrence_count, distinct_task_count,
                    total_reflected_task_count, recurrence_micros,
                    expected_future_uses, impact_micros,
                    resolution_probability_micros,
                    expected_benefit_micros, interruption_cost_micros,
                    verification_cost_micros, expected_net_value_micros,
                    observation_refs_json, evidence_json, created_at
                ) VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?
                )
                """,
                (
                    assessment_id,
                    request_hash,
                    observation_hash,
                    request.scope,
                    request.task_class,
                    request.uncertainty_key,
                    request.action_kind.value,
                    request.target_ref,
                    ACTION_CAPABILITIES[request.action_kind],
                    status,
                    json.dumps(reasons),
                    len(observations),
                    distinct_tasks,
                    total_tasks,
                    recurrence_micros,
                    request.expected_future_uses,
                    request.impact_micros,
                    request.resolution_probability_micros,
                    expected_benefit,
                    request.interruption_cost_micros,
                    request.verification_cost_micros,
                    net_value,
                    json.dumps(observations, sort_keys=True),
                    json.dumps(request.evidence),
                    created_at,
                ),
            )
        return self.get(assessment_id)

    def get(self, assessment_id: str) -> ActiveLearningAssessment:
        row = self.connection.execute(
            "SELECT * FROM active_learning_runs WHERE id=?",
            (assessment_id,),
        ).fetchone()
        if row is None:
            raise KeyError(assessment_id)
        request = ActiveLearningRequest(
            scope=row["scope"],
            task_class=row["task_class"],
            uncertainty_key=row["uncertainty_key"],
            action_kind=VerificationActionKind(row["action_kind"]),
            target_ref=row["target_ref"],
            expected_future_uses=int(row["expected_future_uses"]),
            impact_micros=int(row["impact_micros"]),
            resolution_probability_micros=int(
                row["resolution_probability_micros"]
            ),
            interruption_cost_micros=int(row["interruption_cost_micros"]),
            verification_cost_micros=int(row["verification_cost_micros"]),
            evidence=tuple(json.loads(row["evidence_json"])),
        )
        return ActiveLearningAssessment(
            id=row["id"],
            request=request,
            status=row["status"],
            reasons=tuple(json.loads(row["reasons_json"])),
            occurrence_count=int(row["occurrence_count"]),
            distinct_task_count=int(row["distinct_task_count"]),
            total_reflected_task_count=int(row["total_reflected_task_count"]),
            recurrence_micros=int(row["recurrence_micros"]),
            expected_benefit_micros=int(row["expected_benefit_micros"]),
            total_cost_micros=(
                int(row["interruption_cost_micros"])
                + int(row["verification_cost_micros"])
            ),
            expected_net_value_micros=int(row["expected_net_value_micros"]),
            observation_refs=tuple(json.loads(row["observation_refs_json"])),
            created_at=row["created_at"],
        )
