from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Mapping

from .secret_management import SecretBoundaryError, assert_secret_free


GATE_NAMES = (
    "unit_tests",
    "security_checks",
    "benchmark_quality",
    "token_regression",
    "cost_regression",
    "latency_regression",
)
HARD_GATES = ("unit_tests", "security_checks")
TRADEOFF_GATES = (
    "benchmark_quality",
    "token_regression",
    "cost_regression",
    "latency_regression",
)
_REFERENCE = re.compile(r"^[a-z][a-z0-9_.-]{0,31}:[^\s]{1,240}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class ContinuousQualityError(ValueError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _text(value: object, field: str, maximum: int = 2_000) -> str:
    if not isinstance(value, str):
        raise ContinuousQualityError(f"{field} must be text")
    normalized = value.strip()
    if not 1 <= len(normalized) <= maximum:
        raise ContinuousQualityError(
            f"{field} must be 1..{maximum} characters"
        )
    try:
        assert_secret_free(normalized, f"continuous quality {field}")
    except SecretBoundaryError as exc:
        raise ContinuousQualityError(
            f"{field} contains secret material"
        ) from exc
    return normalized


def _reference(value: object, field: str) -> str:
    normalized = _text(value, field, 240)
    if not _REFERENCE.fullmatch(normalized):
        raise ContinuousQualityError(
            f"{field} must be a bounded type:value reference"
        )
    return normalized


def _evidence(value: object) -> tuple[str, ...]:
    if not isinstance(value, list) or not 1 <= len(value) <= 8:
        raise ContinuousQualityError("evidence must contain 1..8 references")
    result = tuple(_reference(item, "evidence") for item in value)
    if len(set(result)) != len(result):
        raise ContinuousQualityError("evidence references must be unique")
    return result


def _closed(
    payload: object, fields: set[str], label: str
) -> Mapping[str, object]:
    if not isinstance(payload, Mapping) or set(payload) != fields:
        raise ContinuousQualityError(
            f"{label} requires exactly {sorted(fields)}"
        )
    return payload


@dataclass(frozen=True)
class QualityGateMetrics:
    unit_tests_passed: bool
    security_checks_passed: bool
    benchmark_quality_micros: int
    minimum_quality_micros: int
    token_regression_bps: int
    maximum_token_regression_bps: int
    cost_regression_bps: int
    maximum_cost_regression_bps: int
    latency_regression_bps: int
    maximum_latency_regression_bps: int

    def __post_init__(self) -> None:
        if type(self.unit_tests_passed) is not bool:
            raise ContinuousQualityError("unit_tests_passed must be boolean")
        if type(self.security_checks_passed) is not bool:
            raise ContinuousQualityError(
                "security_checks_passed must be boolean"
            )
        if (
            type(self.benchmark_quality_micros) is not int
            or not 0 <= self.benchmark_quality_micros <= 1_000_000
        ):
            raise ContinuousQualityError(
                "benchmark_quality_micros must be 0..1000000"
            )
        if (
            type(self.minimum_quality_micros) is not int
            or not 0 <= self.minimum_quality_micros <= 2_000_000
        ):
            raise ContinuousQualityError(
                "minimum_quality_micros must be 0..2000000"
            )
        for field in (
            "token_regression_bps",
            "cost_regression_bps",
            "latency_regression_bps",
        ):
            value = getattr(self, field)
            if type(value) is not int or not -1_000_000 <= value <= 1_000_000:
                raise ContinuousQualityError(
                    f"{field} must be a bounded basis-point integer"
                )
        for field in (
            "maximum_token_regression_bps",
            "maximum_cost_regression_bps",
            "maximum_latency_regression_bps",
        ):
            value = getattr(self, field)
            if type(value) is not int or not 0 <= value <= 1_000_000:
                raise ContinuousQualityError(
                    f"{field} must be an integer from 0 to 1000000"
                )

    def as_dict(self) -> dict[str, object]:
        return {
            field: getattr(self, field)
            for field in self.__dataclass_fields__
        }


@dataclass(frozen=True)
class QualityGateApprovalCreate:
    run_id: str
    assessment_hash: str
    actor_ref: str
    decision: str
    justified_tradeoffs: tuple[str, ...]
    justification: str
    evidence: tuple[str, ...]

    @classmethod
    def from_dict(cls, payload: object) -> "QualityGateApprovalCreate":
        fields = {
            "schema_version",
            "run_id",
            "assessment_hash",
            "actor_ref",
            "decision",
            "justified_tradeoffs",
            "justification",
            "evidence",
        }
        data = _closed(payload, fields, "quality gate approval")
        if (
            data["schema_version"] != 1
            or not isinstance(data["justified_tradeoffs"], list)
            or not isinstance(data["evidence"], list)
        ):
            raise ContinuousQualityError(
                "quality gate approval requires version 1 lists"
            )
        assessment_hash = data["assessment_hash"]
        if not isinstance(assessment_hash, str) or not _SHA256.fullmatch(
            assessment_hash
        ):
            raise ContinuousQualityError(
                "assessment_hash must be a lowercase SHA-256"
            )
        actor_ref = _reference(data["actor_ref"], "actor_ref")
        if not actor_ref.startswith("human:"):
            raise ContinuousQualityError(
                "actor_ref must identify an explicit human approver"
            )
        decision = _text(data["decision"], "decision", 16)
        if decision not in {"approve", "reject"}:
            raise ContinuousQualityError(
                "decision must be approve or reject"
            )
        tradeoffs = tuple(
            _text(item, "justified_tradeoff", 64)
            for item in data["justified_tradeoffs"]
        )
        if (
            len(set(tradeoffs)) != len(tradeoffs)
            or any(item not in TRADEOFF_GATES for item in tradeoffs)
        ):
            raise ContinuousQualityError(
                "justified_tradeoffs contain an unsupported or duplicate gate"
            )
        if decision == "reject" and tradeoffs:
            raise ContinuousQualityError(
                "rejection cannot justify quantitative tradeoffs"
            )
        return cls(
            run_id=_text(data["run_id"], "run_id", 128),
            assessment_hash=assessment_hash,
            actor_ref=actor_ref,
            decision=decision,
            justified_tradeoffs=tradeoffs,
            justification=_text(
                data["justification"], "justification", 2_000
            ),
            evidence=_evidence(data["evidence"]),
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "run_id": self.run_id,
            "assessment_hash": self.assessment_hash,
            "actor_ref": self.actor_ref,
            "decision": self.decision,
            "justified_tradeoffs": list(self.justified_tradeoffs),
            "justification": self.justification,
            "evidence": list(self.evidence),
        }


class ContinuousQualityGate:
    """Retain conjunctive gate evidence and explicit human decisions."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def assess(
        self,
        *,
        run_id: str,
        target: str,
        candidate_version_id: str,
        metrics: QualityGateMetrics,
        benchmark_blockers: tuple[str, ...],
        evidence: tuple[str, ...],
    ) -> dict[str, object]:
        evidence = _evidence(list(evidence))
        if self.connection.execute(
            "SELECT 1 FROM continuous_quality_assessments WHERE run_id=?",
            (run_id,),
        ).fetchone():
            return self.assessment(run_id)
        if any(
            item not in {
                "incomplete_benchmark",
                "insufficient_cases",
                "hard_violation",
                "protected_regression",
                "insufficient_utility_improvement",
            }
            for item in benchmark_blockers
        ):
            raise ContinuousQualityError("unknown benchmark blocker")
        gates = {
            "unit_tests": {
                "passed": metrics.unit_tests_passed,
                "waivable": False,
            },
            "security_checks": {
                "passed": metrics.security_checks_passed,
                "waivable": False,
            },
            "benchmark_quality": {
                "passed": (
                    metrics.benchmark_quality_micros
                    >= metrics.minimum_quality_micros
                ),
                "actual": metrics.benchmark_quality_micros,
                "threshold": metrics.minimum_quality_micros,
                "waivable": True,
            },
            "token_regression": {
                "passed": (
                    metrics.token_regression_bps
                    <= metrics.maximum_token_regression_bps
                ),
                "actual": metrics.token_regression_bps,
                "threshold": metrics.maximum_token_regression_bps,
                "waivable": True,
            },
            "cost_regression": {
                "passed": (
                    metrics.cost_regression_bps
                    <= metrics.maximum_cost_regression_bps
                ),
                "actual": metrics.cost_regression_bps,
                "threshold": metrics.maximum_cost_regression_bps,
                "waivable": True,
            },
            "latency_regression": {
                "passed": (
                    metrics.latency_regression_bps
                    <= metrics.maximum_latency_regression_bps
                ),
                "actual": metrics.latency_regression_bps,
                "threshold": metrics.maximum_latency_regression_bps,
                "waivable": True,
            },
        }
        hard_failures = list(benchmark_blockers)
        hard_failures.extend(
            gate for gate in HARD_GATES if not gates[gate]["passed"]
        )
        tradeoff_failures = [
            gate for gate in TRADEOFF_GATES if not gates[gate]["passed"]
        ]
        payload = {
            "run_id": run_id,
            "target": target,
            "candidate_version_id": candidate_version_id,
            "metrics": metrics.as_dict(),
            "benchmark_blockers": list(benchmark_blockers),
            "gates": gates,
            "hard_failures": hard_failures,
            "tradeoff_failures": tradeoff_failures,
            "evidence": list(evidence),
        }
        assessment_hash = _digest(payload)
        assessment_id = str(uuid.uuid4())
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO continuous_quality_assessments(
                    id, run_id, target, candidate_version_id,
                    assessment_hash, metrics_json, gates_json,
                    hard_failures_json, tradeoff_failures_json,
                    evidence_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    assessment_id,
                    run_id,
                    target,
                    candidate_version_id,
                    assessment_hash,
                    json.dumps(
                        metrics.as_dict(), sort_keys=True, separators=(",", ":")
                    ),
                    json.dumps(gates, sort_keys=True, separators=(",", ":")),
                    json.dumps(hard_failures, separators=(",", ":")),
                    json.dumps(tradeoff_failures, separators=(",", ":")),
                    json.dumps(evidence, separators=(",", ":")),
                    _now(),
                ),
            )
        return self.assessment(run_id)

    def validate_approval(
        self, request: QualityGateApprovalCreate
    ) -> dict[str, object]:
        assessment = self.assessment(request.run_id)
        if assessment["assessment_hash"] != request.assessment_hash:
            raise ContinuousQualityError(
                "approval does not match the retained assessment"
            )
        if assessment["hard_failures"]:
            raise ContinuousQualityError(
                "hard quality gate failures cannot be approved"
            )
        expected = set(assessment["tradeoff_failures"])
        supplied = set(request.justified_tradeoffs)
        if request.decision == "approve" and supplied != expected:
            raise ContinuousQualityError(
                "approval must justify every and only failed quantitative gate"
            )
        return assessment

    def record_approval(
        self, request: QualityGateApprovalCreate
    ) -> dict[str, object]:
        assessment = self.validate_approval(request)
        existing = self.connection.execute(
            """
            SELECT * FROM continuous_quality_approvals
            WHERE assessment_id=?
            """,
            (assessment["id"],),
        ).fetchone()
        request_hash = _digest(request.as_dict())
        if existing is not None:
            if str(existing["request_hash"]) != request_hash:
                raise ContinuousQualityError(
                    "quality assessment already has a different approval"
                )
            return self.approval(str(existing["id"]))
        approval_id = str(uuid.uuid4())
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO continuous_quality_approvals(
                    id, assessment_id, request_hash, actor_hash, decision,
                    justified_tradeoffs_json, justification_hash,
                    evidence_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    approval_id,
                    assessment["id"],
                    request_hash,
                    _digest(request.actor_ref),
                    request.decision,
                    json.dumps(
                        request.justified_tradeoffs, separators=(",", ":")
                    ),
                    _digest(request.justification),
                    json.dumps(request.evidence, separators=(",", ":")),
                    _now(),
                ),
            )
        return self.approval(approval_id)

    def assessment(self, run_id: str) -> dict[str, object]:
        row = self.connection.execute(
            "SELECT * FROM continuous_quality_assessments WHERE run_id=?",
            (run_id,),
        ).fetchone()
        if row is None:
            raise ContinuousQualityError(
                f"unknown continuous quality assessment: {run_id}"
            )
        approval = self.connection.execute(
            """
            SELECT id FROM continuous_quality_approvals
            WHERE assessment_id=?
            """,
            (row["id"],),
        ).fetchone()
        return {
            "id": str(row["id"]),
            "run_id": str(row["run_id"]),
            "target": str(row["target"]),
            "candidate_version_id": str(row["candidate_version_id"]),
            "assessment_hash": str(row["assessment_hash"]),
            "metrics": json.loads(row["metrics_json"]),
            "gates": json.loads(row["gates_json"]),
            "hard_failures": json.loads(row["hard_failures_json"]),
            "tradeoff_failures": json.loads(row["tradeoff_failures_json"]),
            "evidence": json.loads(row["evidence_json"]),
            "approval_id": None if approval is None else str(approval["id"]),
            "approval_required": approval is None,
            "created_at": str(row["created_at"]),
        }

    def approval(self, approval_id: str) -> dict[str, object]:
        row = self.connection.execute(
            "SELECT * FROM continuous_quality_approvals WHERE id=?",
            (approval_id,),
        ).fetchone()
        if row is None:
            raise ContinuousQualityError(
                f"unknown quality gate approval: {approval_id}"
            )
        return {
            "id": str(row["id"]),
            "assessment_id": str(row["assessment_id"]),
            "request_hash": str(row["request_hash"]),
            "actor_hash": str(row["actor_hash"]),
            "decision": str(row["decision"]),
            "justified_tradeoffs": json.loads(
                row["justified_tradeoffs_json"]
            ),
            "justification_hash": str(row["justification_hash"]),
            "evidence": json.loads(row["evidence_json"]),
            "created_at": str(row["created_at"]),
        }
