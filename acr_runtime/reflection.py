from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal

from .evaluation import EvaluationStore

ReflectionCategory = Literal[
    "what_worked",
    "what_failed",
    "unnecessary_context",
    "missing_information",
    "memory_impact",
    "skill_impact",
    "model_economy",
    "tool_economy",
    "reusable_experience",
]

REQUIRED_CATEGORIES: tuple[ReflectionCategory, ...] = (
    "what_worked",
    "what_failed",
    "unnecessary_context",
    "missing_information",
    "memory_impact",
    "skill_impact",
    "model_economy",
    "tool_economy",
    "reusable_experience",
)

AttributionOutcome = Literal["contributed", "ignored", "misled", "uncertain"]
SourceType = Literal["memory", "skill", "file", "tool", "other"]
Necessity = Literal["necessary", "unnecessary", "uncertain"]
ExperienceKind = Literal[
    "procedure", "failure", "decision", "preference", "environment"
]

MAX_INPUT_ITEMS = 128
MAX_EVIDENCE_ITEMS = 8
MAX_STRING_CHARS = 500


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _strict_text(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    if len(value) > MAX_STRING_CHARS:
        raise ValueError(f"{field} exceeds {MAX_STRING_CHARS} characters")
    return value


def _strict_string_tuple(
    value: object, *, field: str, required: bool = False
) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        raise ValueError(f"{field} must be a list")
    if len(value) > MAX_EVIDENCE_ITEMS:
        raise ValueError(f"{field} exceeds {MAX_EVIDENCE_ITEMS} items")
    result = tuple(
        _strict_text(item, field=f"{field} item") for item in value
    )
    if required and not result:
        raise ValueError(f"{field} cannot be empty")
    return result


def _strict_dict(payload: object, allowed: set[str], *, field: str) -> dict:
    if not isinstance(payload, dict):
        raise ValueError(f"{field} must be an object")
    unknown = set(payload) - allowed
    if unknown:
        raise ValueError(f"Unknown {field} fields: {sorted(unknown)}")
    return payload


@dataclass(frozen=True)
class ReflectionBudget:
    max_findings: int = 9
    max_output_tokens: int = 1_500
    max_input_items: int = MAX_INPUT_ITEMS
    max_passes: int = 1

    def __post_init__(self) -> None:
        if not 9 <= self.max_findings <= 32:
            raise ValueError("Reflection max_findings must be between 9 and 32")
        if not 256 <= self.max_output_tokens <= 4_000:
            raise ValueError(
                "Reflection max_output_tokens must be between 256 and 4000"
            )
        if not 9 <= self.max_input_items <= MAX_INPUT_ITEMS:
            raise ValueError(
                f"Reflection max_input_items must be between 9 and {MAX_INPUT_ITEMS}"
            )
        if self.max_passes != 1:
            raise ValueError("Reflection is limited to exactly one pass")

    @classmethod
    def from_dict(cls, payload: object) -> "ReflectionBudget":
        data = _strict_dict(
            payload,
            {
                "max_findings",
                "max_output_tokens",
                "max_input_items",
                "max_passes",
            },
            field="reflection budget",
        )
        return cls(
            max_findings=int(data.get("max_findings", 9)),
            max_output_tokens=int(data.get("max_output_tokens", 1_500)),
            max_input_items=int(data.get("max_input_items", MAX_INPUT_ITEMS)),
            max_passes=int(data.get("max_passes", 1)),
        )

    def as_dict(self) -> dict[str, int]:
        return {
            "max_findings": self.max_findings,
            "max_output_tokens": self.max_output_tokens,
            "max_input_items": self.max_input_items,
            "max_passes": self.max_passes,
        }


@dataclass(frozen=True)
class ReflectedContext:
    source_type: SourceType
    source_id: str
    tokens: int
    outcome: AttributionOutcome
    evidence: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.source_type not in {"memory", "skill", "file", "tool", "other"}:
            raise ValueError("Reflection context source_type is invalid")
        _strict_text(self.source_id, field="context source_id")
        if self.tokens < 0:
            raise ValueError("Reflection context tokens cannot be negative")
        if self.outcome not in {"contributed", "ignored", "misled", "uncertain"}:
            raise ValueError("Reflection context outcome is invalid")
        if self.outcome != "uncertain" and not self.evidence:
            raise ValueError("Attributed context requires evidence")

    @classmethod
    def from_dict(cls, payload: object) -> "ReflectedContext":
        data = _strict_dict(
            payload,
            {"source_type", "source_id", "tokens", "outcome", "evidence"},
            field="reflected context",
        )
        required = {"source_type", "source_id", "tokens", "outcome", "evidence"}
        if set(data) != required:
            raise ValueError("Reflected context requires all fields")
        return cls(
            source_type=str(data["source_type"]),  # type: ignore[arg-type]
            source_id=str(data["source_id"]),
            tokens=int(data["tokens"]),
            outcome=str(data["outcome"]),  # type: ignore[arg-type]
            evidence=_strict_string_tuple(
                data["evidence"], field="context evidence"
            ),
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "source_type": self.source_type,
            "source_id": self.source_id,
            "tokens": self.tokens,
            "outcome": self.outcome,
            "evidence": list(self.evidence),
        }


@dataclass(frozen=True)
class ReflectedToolCall:
    call_id: str
    tool: str
    necessity: Necessity
    success: bool
    latency_ms: int
    evidence: tuple[str, ...]

    def __post_init__(self) -> None:
        _strict_text(self.call_id, field="tool call_id")
        _strict_text(self.tool, field="tool name")
        if self.necessity not in {"necessary", "unnecessary", "uncertain"}:
            raise ValueError("Tool-call necessity is invalid")
        if type(self.success) is not bool:
            raise ValueError("Tool-call success must be boolean")
        if self.latency_ms < 0:
            raise ValueError("Tool-call latency cannot be negative")
        if self.necessity != "uncertain" and not self.evidence:
            raise ValueError("Tool-call necessity requires evidence")

    @classmethod
    def from_dict(cls, payload: object) -> "ReflectedToolCall":
        data = _strict_dict(
            payload,
            {"call_id", "tool", "necessity", "success", "latency_ms", "evidence"},
            field="reflected tool call",
        )
        if set(data) != {
            "call_id",
            "tool",
            "necessity",
            "success",
            "latency_ms",
            "evidence",
        }:
            raise ValueError("Reflected tool call requires all fields")
        if type(data["success"]) is not bool:
            raise ValueError("Tool-call success must be boolean")
        return cls(
            call_id=str(data["call_id"]),
            tool=str(data["tool"]),
            necessity=str(data["necessity"]),  # type: ignore[arg-type]
            success=data["success"],
            latency_ms=int(data["latency_ms"]),
            evidence=_strict_string_tuple(
                data["evidence"], field="tool-call evidence"
            ),
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "call_id": self.call_id,
            "tool": self.tool,
            "necessity": self.necessity,
            "success": self.success,
            "latency_ms": self.latency_ms,
            "evidence": list(self.evidence),
        }


@dataclass(frozen=True)
class CheaperModelEvidence:
    model: str
    current_cost: float
    candidate_cost: float
    capability_compatible: bool
    benchmark_passed: bool
    evidence: tuple[str, ...]

    def __post_init__(self) -> None:
        _strict_text(self.model, field="candidate model")
        if self.current_cost < 0 or self.candidate_cost < 0:
            raise ValueError("Model costs cannot be negative")
        if type(self.capability_compatible) is not bool:
            raise ValueError("capability_compatible must be boolean")
        if type(self.benchmark_passed) is not bool:
            raise ValueError("benchmark_passed must be boolean")
        if (self.capability_compatible or self.benchmark_passed) and not self.evidence:
            raise ValueError("Model compatibility claims require evidence")

    @classmethod
    def from_dict(cls, payload: object) -> "CheaperModelEvidence":
        data = _strict_dict(
            payload,
            {
                "model",
                "current_cost",
                "candidate_cost",
                "capability_compatible",
                "benchmark_passed",
                "evidence",
            },
            field="model evidence",
        )
        if len(data) != 6:
            raise ValueError("Model evidence requires all fields")
        for field in ("capability_compatible", "benchmark_passed"):
            if type(data.get(field)) is not bool:
                raise ValueError(f"{field} must be boolean")
        return cls(
            model=str(data["model"]),
            current_cost=float(data["current_cost"]),
            candidate_cost=float(data["candidate_cost"]),
            capability_compatible=data["capability_compatible"],
            benchmark_passed=data["benchmark_passed"],
            evidence=_strict_string_tuple(
                data["evidence"], field="model evidence"
            ),
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "model": self.model,
            "current_cost": self.current_cost,
            "candidate_cost": self.candidate_cost,
            "capability_compatible": self.capability_compatible,
            "benchmark_passed": self.benchmark_passed,
            "evidence": list(self.evidence),
        }


@dataclass(frozen=True)
class MissingInformation:
    key: str
    evidence: tuple[str, ...]

    def __post_init__(self) -> None:
        _strict_text(self.key, field="missing information key")
        if not self.evidence:
            raise ValueError("Missing-information claims require evidence")

    @classmethod
    def from_dict(cls, payload: object) -> "MissingInformation":
        data = _strict_dict(
            payload, {"key", "evidence"}, field="missing information"
        )
        if set(data) != {"key", "evidence"}:
            raise ValueError("Missing information requires key and evidence")
        return cls(
            key=str(data["key"]),
            evidence=_strict_string_tuple(
                data["evidence"],
                field="missing-information evidence",
                required=True,
            ),
        )

    def as_dict(self) -> dict[str, object]:
        return {"key": self.key, "evidence": list(self.evidence)}


@dataclass(frozen=True)
class ReusableExperience:
    experience_id: str
    kind: ExperienceKind
    significance: float
    novelty: float
    evidence: tuple[str, ...]

    def __post_init__(self) -> None:
        _strict_text(self.experience_id, field="experience_id")
        if self.kind not in {
            "procedure",
            "failure",
            "decision",
            "preference",
            "environment",
        }:
            raise ValueError("Reusable experience kind is invalid")
        if not 0 <= self.significance <= 1 or not 0 <= self.novelty <= 1:
            raise ValueError("Experience significance and novelty must be 0..1")
        if not self.evidence:
            raise ValueError("Reusable experience requires evidence")

    @classmethod
    def from_dict(cls, payload: object) -> "ReusableExperience":
        data = _strict_dict(
            payload,
            {"experience_id", "kind", "significance", "novelty", "evidence"},
            field="reusable experience",
        )
        if len(data) != 5:
            raise ValueError("Reusable experience requires all fields")
        return cls(
            experience_id=str(data["experience_id"]),
            kind=str(data["kind"]),  # type: ignore[arg-type]
            significance=float(data["significance"]),
            novelty=float(data["novelty"]),
            evidence=_strict_string_tuple(
                data["evidence"],
                field="experience evidence",
                required=True,
            ),
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "experience_id": self.experience_id,
            "kind": self.kind,
            "significance": self.significance,
            "novelty": self.novelty,
            "evidence": list(self.evidence),
        }


@dataclass(frozen=True)
class ReflectionRequest:
    task_id: str
    task_success: bool
    evaluation_run_id: str | None = None
    reflection_depth: int = 0
    context: tuple[ReflectedContext, ...] = ()
    tool_calls: tuple[ReflectedToolCall, ...] = ()
    model_candidates: tuple[CheaperModelEvidence, ...] = ()
    missing_information: tuple[MissingInformation, ...] = ()
    reusable_experience: tuple[ReusableExperience, ...] = ()
    budget: ReflectionBudget = ReflectionBudget()

    def __post_init__(self) -> None:
        _strict_text(self.task_id, field="reflection task_id")
        if type(self.task_success) is not bool:
            raise ValueError("Reflection task_success must be boolean")
        if self.reflection_depth != 0:
            raise ValueError("Reflection cannot reflect on another reflection")
        total = (
            len(self.context)
            + len(self.tool_calls)
            + len(self.model_candidates)
            + len(self.missing_information)
            + len(self.reusable_experience)
        )
        if total > self.budget.max_input_items:
            raise ValueError("Reflection input-item budget exceeded")
        ids = [item.source_id for item in self.context]
        if len(ids) != len(set(ids)):
            raise ValueError("Reflected context source IDs must be unique")
        call_ids = [item.call_id for item in self.tool_calls]
        if len(call_ids) != len(set(call_ids)):
            raise ValueError("Reflected tool call IDs must be unique")

    @classmethod
    def from_dict(cls, payload: object) -> "ReflectionRequest":
        data = _strict_dict(
            payload,
            {
                "task_id",
                "task_success",
                "evaluation_run_id",
                "reflection_depth",
                "context",
                "tool_calls",
                "model_candidates",
                "missing_information",
                "reusable_experience",
                "budget",
            },
            field="reflection request",
        )
        if "task_id" not in data or "task_success" not in data:
            raise ValueError("Reflection request requires task_id and task_success")
        if type(data["task_success"]) is not bool:
            raise ValueError("Reflection task_success must be boolean")
        return cls(
            task_id=str(data["task_id"]),
            task_success=data["task_success"],
            evaluation_run_id=(
                None
                if data.get("evaluation_run_id") is None
                else str(data["evaluation_run_id"])
            ),
            reflection_depth=int(data.get("reflection_depth", 0)),
            context=tuple(
                ReflectedContext.from_dict(item)
                for item in data.get("context", ())
            ),
            tool_calls=tuple(
                ReflectedToolCall.from_dict(item)
                for item in data.get("tool_calls", ())
            ),
            model_candidates=tuple(
                CheaperModelEvidence.from_dict(item)
                for item in data.get("model_candidates", ())
            ),
            missing_information=tuple(
                MissingInformation.from_dict(item)
                for item in data.get("missing_information", ())
            ),
            reusable_experience=tuple(
                ReusableExperience.from_dict(item)
                for item in data.get("reusable_experience", ())
            ),
            budget=ReflectionBudget.from_dict(data.get("budget", {})),
        )


@dataclass(frozen=True)
class ReflectionFinding:
    category: ReflectionCategory
    verdict: str
    subject_ids: tuple[str, ...]
    evidence: tuple[str, ...]
    metrics: dict[str, int | float | bool | str | None]

    def as_dict(self) -> dict[str, object]:
        return {
            "category": self.category,
            "verdict": self.verdict,
            "subject_ids": list(self.subject_ids),
            "evidence": list(self.evidence),
            "metrics": self.metrics,
        }


@dataclass(frozen=True)
class ReflectionRun:
    id: str
    task_id: str
    evaluation_run_id: str | None
    reflection_depth: int
    budget: ReflectionBudget
    findings: tuple[ReflectionFinding, ...]
    estimated_output_tokens: int
    created_at: str

    def as_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "task_id": self.task_id,
            "evaluation_run_id": self.evaluation_run_id,
            "reflection_depth": self.reflection_depth,
            "budget": self.budget.as_dict(),
            "findings": [item.as_dict() for item in self.findings],
            "estimated_output_tokens": self.estimated_output_tokens,
            "created_at": self.created_at,
        }


class ReflectionEngine:
    """One-pass, evidence-driven post-task reflection with no mutation path."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection
        self.evaluations = EvaluationStore(connection)

    @staticmethod
    def _evidence(items: tuple[object, ...]) -> tuple[str, ...]:
        values: list[str] = []
        for item in items:
            for value in getattr(item, "evidence"):
                if value not in values:
                    values.append(value)
                if len(values) == MAX_EVIDENCE_ITEMS:
                    return tuple(values)
        return tuple(values)

    def reflect(self, request: ReflectionRequest) -> ReflectionRun:
        evaluation_passed: bool | None = None
        if request.evaluation_run_id is not None:
            evaluation = self.evaluations.get(request.evaluation_run_id)
            if evaluation.task_id not in {None, request.task_id}:
                raise ValueError("Evaluation belongs to a different task")
            evaluation_passed = evaluation.result.passed

        succeeded = request.task_success and evaluation_passed is not False
        failed_reasons = int(not request.task_success) + int(
            evaluation_passed is False
        )
        ignored = tuple(
            item for item in request.context if item.outcome == "ignored"
        )
        uncertain_context = sum(
            item.outcome == "uncertain" for item in request.context
        )
        memory = tuple(
            item for item in request.context if item.source_type == "memory"
        )
        skill = tuple(
            item for item in request.context if item.source_type == "skill"
        )
        cheaper = tuple(
            item
            for item in request.model_candidates
            if item.candidate_cost < item.current_cost
            and item.capability_compatible
            and item.benchmark_passed
        )
        unnecessary_tools = tuple(
            item
            for item in request.tool_calls
            if item.necessity == "unnecessary"
        )
        reusable = tuple(
            item
            for item in request.reusable_experience
            if item.significance >= 0.7 and item.novelty >= 0.5
        )

        findings = (
            ReflectionFinding(
                "what_worked",
                (
                    "task_and_evaluation_passed"
                    if succeeded and evaluation_passed is True
                    else "task_passed"
                    if succeeded
                    else "not_confirmed"
                ),
                (request.task_id,),
                (
                    (f"evaluation:{request.evaluation_run_id}",)
                    if request.evaluation_run_id
                    else ()
                ),
                {
                    "task_success": request.task_success,
                    "evaluation_passed": evaluation_passed,
                },
            ),
            ReflectionFinding(
                "what_failed",
                "failure_observed" if failed_reasons else "no_failure_observed",
                (request.task_id,) if failed_reasons else (),
                (
                    (f"evaluation:{request.evaluation_run_id}",)
                    if evaluation_passed is False
                    else ()
                ),
                {"failure_signal_count": failed_reasons},
            ),
            ReflectionFinding(
                "unnecessary_context",
                "unnecessary_context_observed" if ignored else "none_proven",
                tuple(item.source_id for item in ignored),
                self._evidence(ignored),
                {
                    "ignored_count": len(ignored),
                    "ignored_tokens": sum(item.tokens for item in ignored),
                    "uncertain_count": uncertain_context,
                },
            ),
            ReflectionFinding(
                "missing_information",
                "missing_information_observed"
                if request.missing_information
                else "none_observed",
                tuple(item.key for item in request.missing_information),
                self._evidence(request.missing_information),
                {"missing_count": len(request.missing_information)},
            ),
            self._impact_finding("memory_impact", memory),
            self._impact_finding("skill_impact", skill),
            ReflectionFinding(
                "model_economy",
                "verified_cheaper_model"
                if cheaper
                else "no_verified_cheaper_model",
                tuple(item.model for item in cheaper),
                self._evidence(cheaper),
                {
                    "verified_candidate_count": len(cheaper),
                    "max_estimated_savings": max(
                        (
                            item.current_cost - item.candidate_cost
                            for item in cheaper
                        ),
                        default=0.0,
                    ),
                },
            ),
            ReflectionFinding(
                "tool_economy",
                "unnecessary_tool_calls_observed"
                if unnecessary_tools
                else "none_proven",
                tuple(item.call_id for item in unnecessary_tools),
                self._evidence(unnecessary_tools),
                {
                    "unnecessary_count": len(unnecessary_tools),
                    "avoidable_latency_ms": sum(
                        item.latency_ms for item in unnecessary_tools
                    ),
                    "uncertain_count": sum(
                        item.necessity == "uncertain"
                        for item in request.tool_calls
                    ),
                },
            ),
            ReflectionFinding(
                "reusable_experience",
                "reusable_candidate_observed" if reusable else "none_qualified",
                tuple(item.experience_id for item in reusable),
                self._evidence(reusable),
                {"qualified_count": len(reusable)},
            ),
        )
        if len(findings) > request.budget.max_findings:
            raise ValueError("Reflection finding budget exceeded")
        estimated_tokens = max(
            1,
            (
                len(
                    json.dumps(
                        [item.as_dict() for item in findings],
                        sort_keys=True,
                        separators=(",", ":"),
                    ).encode("utf-8")
                )
                + 3
            )
            // 4,
        )
        if estimated_tokens > request.budget.max_output_tokens:
            raise ValueError("Reflection output-token budget exceeded")
        return self._persist(request, findings, estimated_tokens)

    def _impact_finding(
        self,
        category: Literal["memory_impact", "skill_impact"],
        items: tuple[ReflectedContext, ...],
    ) -> ReflectionFinding:
        helped = tuple(item for item in items if item.outcome == "contributed")
        harmed = tuple(
            item for item in items if item.outcome in {"ignored", "misled"}
        )
        if helped:
            verdict = "helped"
        elif harmed:
            verdict = "did_not_help"
        else:
            verdict = "unknown"
        return ReflectionFinding(
            category,
            verdict,
            tuple(item.source_id for item in (*helped, *harmed)),
            self._evidence((*helped, *harmed)),
            {
                "helped_count": len(helped),
                "did_not_help_count": len(harmed),
                "uncertain_count": sum(
                    item.outcome == "uncertain" for item in items
                ),
            },
        )

    def _persist(
        self,
        request: ReflectionRequest,
        findings: tuple[ReflectionFinding, ...],
        estimated_tokens: int,
    ) -> ReflectionRun:
        run_id = str(uuid.uuid4())
        created_at = _utc_now()
        input_metadata = {
            "task_id_sha256": _hash(request.task_id),
            "context_count": len(request.context),
            "context_tokens": sum(item.tokens for item in request.context),
            "tool_call_count": len(request.tool_calls),
            "model_candidate_count": len(request.model_candidates),
            "missing_information_count": len(request.missing_information),
            "reusable_experience_count": len(request.reusable_experience),
        }
        try:
            self.connection.execute("BEGIN IMMEDIATE")
            self.connection.execute(
                """
                INSERT INTO reflection_runs (
                    id, task_id, evaluation_run_id, reflection_depth,
                    budget_json, input_metadata_json, finding_count,
                    estimated_output_tokens, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    request.task_id,
                    request.evaluation_run_id,
                    1,
                    json.dumps(request.budget.as_dict(), sort_keys=True),
                    json.dumps(input_metadata, sort_keys=True),
                    len(findings),
                    estimated_tokens,
                    created_at,
                ),
            )
            for sequence, finding in enumerate(findings, start=1):
                self.connection.execute(
                    """
                    INSERT INTO reflection_findings (
                        id, run_id, sequence, category, verdict,
                        subject_ids_json, evidence_json, metrics_json, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(uuid.uuid4()),
                        run_id,
                        sequence,
                        finding.category,
                        finding.verdict,
                        json.dumps(finding.subject_ids),
                        json.dumps(finding.evidence),
                        json.dumps(finding.metrics, sort_keys=True),
                        created_at,
                    ),
                )
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise
        return ReflectionRun(
            run_id,
            request.task_id,
            request.evaluation_run_id,
            1,
            request.budget,
            findings,
            estimated_tokens,
            created_at,
        )

    def get(self, run_id: str) -> ReflectionRun:
        row = self.connection.execute(
            "SELECT * FROM reflection_runs WHERE id = ?", (run_id,)
        ).fetchone()
        if row is None:
            raise KeyError(run_id)
        finding_rows = self.connection.execute(
            """
            SELECT * FROM reflection_findings
            WHERE run_id = ? ORDER BY sequence
            """,
            (run_id,),
        ).fetchall()
        findings = tuple(
            ReflectionFinding(
                category=item["category"],
                verdict=item["verdict"],
                subject_ids=tuple(json.loads(item["subject_ids_json"])),
                evidence=tuple(json.loads(item["evidence_json"])),
                metrics=json.loads(item["metrics_json"]),
            )
            for item in finding_rows
        )
        if tuple(item.category for item in findings) != REQUIRED_CATEGORIES:
            raise ValueError("Persisted reflection is incomplete or out of order")
        return ReflectionRun(
            id=row["id"],
            task_id=row["task_id"],
            evaluation_run_id=row["evaluation_run_id"],
            reflection_depth=int(row["reflection_depth"]),
            budget=ReflectionBudget.from_dict(json.loads(row["budget_json"])),
            findings=findings,
            estimated_output_tokens=int(row["estimated_output_tokens"]),
            created_at=row["created_at"],
        )
