from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from .attribution import (
    AttributionOutcome,
    AttributionSignals,
    ContextAttribution,
    ContextAttributor,
    EvaluatorJudgment,
)
from .evaluation import (
    EvaluationCase,
    EvaluationRun,
    EvaluationStore,
    default_deterministic_judges,
)
from .experience import DistillationPlan, ExperienceDistiller
from .models import ContextBlock, ContextBundle
from .skill_generator import SkillGenerator
from .confidence_calibration import ConfidenceCalibration

LEARNING_STAGES = (
    "evaluate",
    "attribute_context",
    "calculate_resource_efficiency",
    "distill_experience",
    "generate_memory_candidates",
    "update_memory_utility",
    "update_skill_utility",
    "identify_skill_candidate",
    "identify_routing_improvements",
    "detect_regression",
)

SOURCE_TYPES = {
    "system_rule",
    "memory",
    "skill",
    "file",
    "tool",
    "agent_state",
    "observation",
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _strict_object(
    payload: object, allowed: set[str], *, field: str
) -> dict[str, object]:
    if not isinstance(payload, dict):
        raise ValueError(f"{field} must be an object")
    unknown = set(payload) - allowed
    if unknown:
        raise ValueError(f"Unknown {field} fields: {sorted(unknown)}")
    return payload


def _source_ref(payload: object) -> tuple[str, str]:
    data = _strict_object(
        payload, {"source_type", "source_id"}, field="source reference"
    )
    if set(data) != {"source_type", "source_id"}:
        raise ValueError("Source reference requires source_type and source_id")
    source_type = str(data["source_type"])
    source_id = str(data["source_id"])
    if source_type not in SOURCE_TYPES or not source_id.strip():
        raise ValueError("Source reference is invalid")
    return source_type, source_id


@dataclass(frozen=True)
class RegressionBaseline:
    quality_floor: float = 0.7
    max_total_tokens: int | None = None
    max_duration_ms: int | None = None
    max_estimated_cost: float | None = None

    def __post_init__(self) -> None:
        if not 0 <= self.quality_floor <= 1:
            raise ValueError("Regression quality_floor must be 0..1")
        for value, field in (
            (self.max_total_tokens, "max_total_tokens"),
            (self.max_duration_ms, "max_duration_ms"),
        ):
            if value is not None and value < 0:
                raise ValueError(f"Regression {field} cannot be negative")
        if self.max_estimated_cost is not None and self.max_estimated_cost < 0:
            raise ValueError("Regression max_estimated_cost cannot be negative")

    @classmethod
    def from_dict(cls, payload: object) -> "RegressionBaseline":
        data = _strict_object(
            payload,
            {
                "quality_floor",
                "max_total_tokens",
                "max_duration_ms",
                "max_estimated_cost",
            },
            field="regression baseline",
        )
        return cls(
            quality_floor=float(data.get("quality_floor", 0.7)),
            max_total_tokens=(
                None
                if data.get("max_total_tokens") is None
                else int(data["max_total_tokens"])
            ),
            max_duration_ms=(
                None
                if data.get("max_duration_ms") is None
                else int(data["max_duration_ms"])
            ),
            max_estimated_cost=(
                None
                if data.get("max_estimated_cost") is None
                else float(data["max_estimated_cost"])
            ),
        )

    def as_dict(self) -> dict[str, int | float | None]:
        return {
            "quality_floor": self.quality_floor,
            "max_total_tokens": self.max_total_tokens,
            "max_duration_ms": self.max_duration_ms,
            "max_estimated_cost": self.max_estimated_cost,
        }


@dataclass(frozen=True)
class LearningRequest:
    execution_run_id: str
    evaluation_case: EvaluationCase
    attribution_signals: AttributionSignals = AttributionSignals()
    experience_trace_id: str | None = None
    skill_scope: str | None = None
    task_class: str = "general"
    model: str = ""
    baseline: RegressionBaseline = RegressionBaseline()

    def __post_init__(self) -> None:
        if not self.execution_run_id.strip():
            raise ValueError("Learning execution_run_id cannot be empty")
        if not self.task_class.strip():
            raise ValueError("Learning task_class cannot be empty")
        if self.skill_scope is not None and not self.skill_scope.strip():
            raise ValueError("Learning skill_scope cannot be empty")

    @classmethod
    def from_dict(cls, payload: object) -> "LearningRequest":
        data = _strict_object(
            payload,
            {
                "execution_run_id",
                "evaluation_case",
                "attribution_signals",
                "experience_trace_id",
                "skill_scope",
                "task_class",
                "model",
                "baseline",
            },
            field="learning request",
        )
        if "execution_run_id" not in data or "evaluation_case" not in data:
            raise ValueError(
                "Learning request requires execution_run_id and evaluation_case"
            )
        signals_data = _strict_object(
            data.get("attribution_signals", {}),
            {
                "model_sources",
                "execution_sources",
                "tool_dependencies",
                "ignored_sources",
                "misled_sources",
                "evaluator_judgments",
            },
            field="attribution signals",
        )

        def refs(field: str) -> tuple[tuple[str, str], ...]:
            value = signals_data.get(field, [])
            if not isinstance(value, list):
                raise ValueError(f"{field} must be a list")
            return tuple(_source_ref(item) for item in value)

        judgments_value = signals_data.get("evaluator_judgments", [])
        if not isinstance(judgments_value, list):
            raise ValueError("evaluator_judgments must be a list")
        judgments: list[EvaluatorJudgment] = []
        for item in judgments_value:
            judgment = _strict_object(
                item,
                {"source_type", "source_id", "score"},
                field="evaluator judgment",
            )
            if set(judgment) != {"source_type", "source_id", "score"}:
                raise ValueError("Evaluator judgment requires all fields")
            source_type, source_id = _source_ref(
                {
                    "source_type": judgment["source_type"],
                    "source_id": judgment["source_id"],
                }
            )
            judgments.append(
                EvaluatorJudgment(
                    source_type=source_type,  # type: ignore[arg-type]
                    source_id=source_id,
                    score=float(judgment["score"]),
                )
            )
        return cls(
            execution_run_id=str(data["execution_run_id"]),
            evaluation_case=EvaluationCase.from_dict(data["evaluation_case"]),
            attribution_signals=AttributionSignals(
                model_sources=refs("model_sources"),  # type: ignore[arg-type]
                execution_sources=refs("execution_sources"),  # type: ignore[arg-type]
                tool_dependencies=refs("tool_dependencies"),  # type: ignore[arg-type]
                ignored_sources=refs("ignored_sources"),  # type: ignore[arg-type]
                misled_sources=refs("misled_sources"),  # type: ignore[arg-type]
                evaluator_judgments=tuple(judgments),
            ),
            experience_trace_id=(
                None
                if data.get("experience_trace_id") is None
                else str(data["experience_trace_id"])
            ),
            skill_scope=(
                None
                if data.get("skill_scope") is None
                else str(data["skill_scope"])
            ),
            task_class=str(data.get("task_class", "general")),
            model=str(data.get("model", "")),
            baseline=RegressionBaseline.from_dict(data.get("baseline", {})),
        )


@dataclass(frozen=True)
class LearningStage:
    stage: str
    status: str
    details: dict[str, object]

    def as_dict(self) -> dict[str, object]:
        return {
            "stage": self.stage,
            "status": self.status,
            "details": self.details,
        }


@dataclass(frozen=True)
class LearningRun:
    id: str
    execution_run_id: str
    task_id: str
    evaluation_run_id: str
    experience_distillation_id: str | None
    skill_generation_run_id: str
    resource_efficiency: dict[str, int | float | None]
    stages: tuple[LearningStage, ...]
    memory_candidate_count: int
    skill_candidate_count: int
    routing_improvement_count: int
    regression_count: int
    created_at: str

    def as_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "execution_run_id": self.execution_run_id,
            "task_id": self.task_id,
            "evaluation_run_id": self.evaluation_run_id,
            "experience_distillation_id": self.experience_distillation_id,
            "skill_generation_run_id": self.skill_generation_run_id,
            "resource_efficiency": self.resource_efficiency,
            "stages": [item.as_dict() for item in self.stages],
            "memory_candidate_count": self.memory_candidate_count,
            "skill_candidate_count": self.skill_candidate_count,
            "routing_improvement_count": self.routing_improvement_count,
            "regression_count": self.regression_count,
            "created_at": self.created_at,
        }


@dataclass(frozen=True)
class LearningReadinessPlan:
    """Content-minimized, read-only preparation for one learning request."""

    task_id: str
    execution_run_id: str | None
    status: str
    structurally_eligible: bool
    checks: tuple[dict[str, object], ...]
    terminal_execution_runs: tuple[dict[str, object], ...]
    context_sources: tuple[dict[str, object], ...]
    experience_traces: tuple[dict[str, object], ...]
    request_draft: dict[str, object] | None
    required_inputs: tuple[str, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "task_id": self.task_id,
            "execution_run_id": self.execution_run_id,
            "status": self.status,
            "structurally_eligible": self.structurally_eligible,
            "mutates_state": False,
            "checks": list(self.checks),
            "terminal_execution_runs": list(self.terminal_execution_runs),
            "context_sources": list(self.context_sources),
            "experience_traces": list(self.experience_traces),
            "request_draft": self.request_draft,
            "required_inputs": list(self.required_inputs),
            "next_command": (
                None
                if self.request_draft is None
                else "acr learn run <completed-learning-request.json>"
            ),
        }


class LearningController:
    """Atomic post-execution learning; never edits the execution result."""

    def __init__(
        self,
        connection: sqlite3.Connection,
        evaluations: EvaluationStore,
        attributor: ContextAttributor,
        experiences: ExperienceDistiller,
        skills: SkillGenerator,
    ) -> None:
        self.connection = connection
        self.evaluations = evaluations
        self.attributor = attributor
        self.experiences = experiences
        self.skills = skills

    def plan(
        self,
        task_id: str,
        *,
        execution_run_id: str | None = None,
    ) -> LearningReadinessPlan:
        """Inspect retained evidence without starting or authorizing learning."""
        task = self.connection.execute(
            "SELECT id, scope FROM tasks WHERE id = ?", (task_id,)
        ).fetchone()
        if task is None:
            raise KeyError(task_id)

        terminal_rows = self.connection.execute(
            """
            SELECT run_id, state, event_count, step_count, action_count,
                   duration_ms, verification_score, evaluation_score,
                   failure_kind, started_at, completed_at
            FROM execution_runs
            WHERE task_id = ? AND state IN ('completed', 'failed')
            ORDER BY completed_at DESC, run_id
            """,
            (task_id,),
        ).fetchall()
        terminal_runs = tuple(dict(row) for row in terminal_rows)
        selected: sqlite3.Row | None = None
        if execution_run_id is not None:
            selected = self.connection.execute(
                """
                SELECT run_id, task_id, state
                FROM execution_runs WHERE run_id = ?
                """,
                (execution_run_id,),
            ).fetchone()
            if selected is None:
                raise KeyError(execution_run_id)
            if selected["task_id"] != task_id:
                raise ValueError("Execution run belongs to another task")
        elif len(terminal_rows) == 1:
            selected = terminal_rows[0]

        checks: list[dict[str, object]] = [
            {
                "name": "task_retained",
                "passed": True,
                "reason": "task metadata is retained",
            }
        ]
        if selected is None:
            reason = (
                "no terminal execution run is retained"
                if not terminal_rows
                else "multiple terminal runs require --run-id"
            )
            checks.append(
                {
                    "name": "terminal_execution_selected",
                    "passed": False,
                    "reason": reason,
                }
            )
        else:
            terminal = selected["state"] in {"completed", "failed"}
            checks.append(
                {
                    "name": "terminal_execution_selected",
                    "passed": terminal,
                    "reason": (
                        "selected execution is terminal"
                        if terminal
                        else "selected execution is not terminal"
                    ),
                }
            )

        learned = False
        if selected is not None:
            learned = (
                self.connection.execute(
                    """
                    SELECT 1 FROM learning_runs WHERE execution_run_id = ?
                    """,
                    (selected["run_id"],),
                ).fetchone()
                is not None
            )
        checks.append(
            {
                "name": "not_previously_learned",
                "passed": selected is not None and not learned,
                "reason": (
                    "execution has no learning transaction"
                    if selected is not None and not learned
                    else (
                        "execution already has a learning transaction"
                        if learned
                        else "select a terminal execution first"
                    )
                ),
            }
        )

        legacy_attribution = (
            self.connection.execute(
                "SELECT 1 FROM context_attributions WHERE task_id = ? LIMIT 1",
                (task_id,),
            ).fetchone()
            is not None
        )
        checks.append(
            {
                "name": "no_legacy_attribution",
                "passed": not legacy_attribution,
                "reason": (
                    "no prior task attribution exists"
                    if not legacy_attribution
                    else "prior task attribution prevents double learning"
                ),
            }
        )

        context_rows = self.connection.execute(
            """
            SELECT source_type, source_id, tokens
            FROM context_uses
            WHERE task_id = ?
            ORDER BY source_type, source_id
            """,
            (task_id,),
        ).fetchall()
        trace_rows = self.connection.execute(
            """
            SELECT id, scope, task_class, outcome, significance_score,
                   raw_tokens, event_count, created_at
            FROM experience_traces
            WHERE task_id = ?
            ORDER BY created_at DESC, id
            """,
            (task_id,),
        ).fetchall()
        traces = tuple(
            {
                **dict(row),
                "distillation_eligible": (
                    float(row["significance_score"])
                    >= self.experiences.config.minimum_significance
                ),
            }
            for row in trace_rows
        )

        structurally_eligible = all(bool(item["passed"]) for item in checks)
        selected_run_id = None if selected is None else str(selected["run_id"])
        request_draft = None
        required_inputs: tuple[str, ...] = ()
        if structurally_eligible:
            request_draft = {
                "execution_run_id": selected_run_id,
                "attribution_signals": {
                    "model_sources": [],
                    "execution_sources": [],
                    "tool_dependencies": [],
                    "ignored_sources": [],
                    "misled_sources": [],
                    "evaluator_judgments": [],
                },
                "experience_trace_id": None,
                "skill_scope": str(task["scope"]),
                "task_class": "general",
                "model": "",
                "baseline": RegressionBaseline().as_dict(),
            }
            required_inputs = (
                "evaluation_case.objective",
                "evaluation_case.actual",
                "at least one deterministic evaluation reference "
                "(expected, required_elements, constraints, evidence, or schema)",
                "review attribution_signals against the listed context source IDs",
                "optionally select one eligible experience_trace_id",
            )
        status = (
            "ready_for_operator_inputs"
            if structurally_eligible
            else "ineligible"
        )
        return LearningReadinessPlan(
            task_id=task_id,
            execution_run_id=selected_run_id,
            status=status,
            structurally_eligible=structurally_eligible,
            checks=tuple(checks),
            terminal_execution_runs=terminal_runs,
            context_sources=tuple(dict(row) for row in context_rows),
            experience_traces=traces,
            request_draft=request_draft,
            required_inputs=required_inputs,
        )

    def learn(
        self,
        request: LearningRequest,
        *,
        _fail_after_stage: str | None = None,
    ) -> LearningRun:
        if _fail_after_stage is not None and _fail_after_stage not in LEARNING_STAGES:
            raise ValueError("Unknown injected learning failure stage")
        execution = self.connection.execute(
            "SELECT * FROM execution_runs WHERE run_id = ?",
            (request.execution_run_id,),
        ).fetchone()
        if execution is None:
            raise KeyError(request.execution_run_id)
        if execution["state"] not in {"completed", "failed"}:
            raise ValueError("Learning requires a terminal execution run")
        task_id = execution["task_id"]
        task = self.connection.execute(
            "SELECT * FROM tasks WHERE id = ?", (task_id,)
        ).fetchone()
        if task is None:
            raise ValueError("Learning requires retained task context")
        if self.connection.execute(
            "SELECT 1 FROM learning_runs WHERE execution_run_id = ?",
            (request.execution_run_id,),
        ).fetchone():
            raise ValueError("Execution run has already been learned")
        context_rows = self.connection.execute(
            """
            SELECT source_type, source_id, tokens, utility, roi,
                   compression_strategy, original_tokens, exact_preserved
            FROM context_uses WHERE task_id = ?
            ORDER BY source_type, source_id
            """,
            (task_id,),
        ).fetchall()
        if self.connection.execute(
            "SELECT 1 FROM context_attributions WHERE task_id = ?",
            (task_id,),
        ).fetchone():
            raise ValueError(
                "Task already has legacy attribution; refusing double learning"
            )
        bundle = self._bundle(task, context_rows)
        run_id = str(uuid.uuid4())
        created_at = _utc_now()
        stages: list[LearningStage] = []
        try:
            self.connection.execute("BEGIN IMMEDIATE")
            evaluation = self.evaluations.evaluate(
                request.evaluation_case,
                default_deterministic_judges(),
                task_id=task_id,
                manage_transaction=False,
            )
            self._stage(
                stages,
                "evaluate",
                "completed",
                {"passed": evaluation.result.passed, "score": evaluation.result.score},
                _fail_after_stage,
            )

            execution_succeeded = execution["state"] == "completed"
            attributions = self.attributor.attribute(
                bundle,
                signals=request.attribution_signals,
                success=execution_succeeded,
                critic_score=evaluation.result.score,
            )
            self._insert_attributions(attributions, created_at)
            self._stage(
                stages,
                "attribute_context",
                "completed",
                {
                    "source_count": len(attributions),
                    "conclusive_count": sum(
                        item.outcome is not AttributionOutcome.UNCERTAIN
                        for item in attributions
                    ),
                },
                _fail_after_stage,
            )

            resource = self._resource_efficiency(
                execution, request.evaluation_case, context_rows, attributions
            )
            self._stage(
                stages,
                "calculate_resource_efficiency",
                "completed",
                resource,
                _fail_after_stage,
            )

            distillation: DistillationPlan | None = None
            if request.experience_trace_id is not None:
                trace = self.experiences.get_trace(request.experience_trace_id)
                if trace is None:
                    raise KeyError(request.experience_trace_id)
                if trace.task_id not in {None, task_id}:
                    raise ValueError("Experience trace belongs to another task")
                distillation = self.experiences.plan(
                    request.experience_trace_id,
                    manage_transaction=False,
                )
                distill_status = "completed"
                distill_details = {"candidate_count": len(distillation.items)}
            else:
                distill_status = "skipped"
                distill_details = {"reason": "no_experience_trace"}
            self._stage(
                stages,
                "distill_experience",
                distill_status,
                distill_details,
                _fail_after_stage,
            )

            memory_count = self._memory_candidates(
                run_id, distillation, created_at
            )
            self._stage(
                stages,
                "generate_memory_candidates",
                "completed" if distillation else "skipped",
                {"candidate_count": memory_count},
                _fail_after_stage,
            )

            learned_success = execution_succeeded and evaluation.result.passed
            memory_updates = self._update_memory_utility(
                attributions, learned_success, created_at
            )
            self._stage(
                stages,
                "update_memory_utility",
                "completed",
                {"updated_count": memory_updates},
                _fail_after_stage,
            )

            skill_updates = self._update_skill_utility(
                attributions,
                learned_success,
                request,
                execution,
                context_rows,
                created_at,
            )
            self._stage(
                stages,
                "update_skill_utility",
                "completed",
                {"updated_count": skill_updates},
                _fail_after_stage,
            )

            skill_plan = self.skills.plan(
                scope=request.skill_scope,
                manage_transaction=False,
            )
            self._stage(
                stages,
                "identify_skill_candidate",
                "completed",
                {"candidate_count": len(skill_plan.candidates)},
                _fail_after_stage,
            )

            routing_count = self._routing_improvements(
                run_id, attributions, created_at
            )
            self._stage(
                stages,
                "identify_routing_improvements",
                "completed",
                {"candidate_count": routing_count},
                _fail_after_stage,
            )

            regression_count = self._regressions(
                run_id,
                evaluation,
                resource,
                request.baseline,
                created_at,
            )
            self._stage(
                stages,
                "detect_regression",
                "completed",
                {"regression_count": regression_count},
                _fail_after_stage,
            )

            self.connection.execute(
                """
                INSERT INTO learning_runs (
                    id, execution_run_id, task_id, evaluation_run_id,
                    experience_distillation_id, skill_generation_run_id,
                    resource_efficiency_json, baseline_json, stage_count,
                    memory_candidate_count, skill_candidate_count,
                    routing_improvement_count, regression_count,
                    status, created_at, completed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 10, ?, ?, ?, ?,
                          'completed', ?, ?)
                """,
                (
                    run_id,
                    request.execution_run_id,
                    task_id,
                    evaluation.id,
                    distillation.id if distillation else None,
                    skill_plan.id,
                    json.dumps(resource, sort_keys=True),
                    json.dumps(request.baseline.as_dict(), sort_keys=True),
                    memory_count,
                    len(skill_plan.candidates),
                    routing_count,
                    regression_count,
                    created_at,
                    _utc_now(),
                ),
            )
            for sequence, stage in enumerate(stages, start=1):
                self.connection.execute(
                    """
                    INSERT INTO learning_stage_results (
                        id, run_id, sequence, stage, status,
                        details_json, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(uuid.uuid4()),
                        run_id,
                        sequence,
                        stage.stage,
                        stage.status,
                        json.dumps(stage.details, sort_keys=True),
                        created_at,
                    ),
                )
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise
        from .utility_governance import UtilityGovernor

        with self.connection:
            UtilityGovernor(self.connection).observe_context_task(task_id)
        from .skill_coevolution import MemorySkillCoevolution

        coevolution = MemorySkillCoevolution(self.connection)
        for skill_id in {
            item.source_id
            for item in attributions
            if item.source_type == "skill"
            and item.outcome is not AttributionOutcome.UNCERTAIN
        }:
            coevolution.refresh(skill_id)
        return LearningRun(
            id=run_id,
            execution_run_id=request.execution_run_id,
            task_id=task_id,
            evaluation_run_id=evaluation.id,
            experience_distillation_id=(
                distillation.id if distillation else None
            ),
            skill_generation_run_id=skill_plan.id,
            resource_efficiency=resource,
            stages=tuple(stages),
            memory_candidate_count=memory_count,
            skill_candidate_count=len(skill_plan.candidates),
            routing_improvement_count=routing_count,
            regression_count=regression_count,
            created_at=created_at,
        )

    @staticmethod
    def _stage(
        stages: list[LearningStage],
        name: str,
        status: str,
        details: dict[str, object],
        fail_after: str | None,
    ) -> None:
        stages.append(LearningStage(name, status, details))
        if fail_after == name:
            raise RuntimeError(f"Injected learning failure after {name}")

    @staticmethod
    def _bundle(task: sqlite3.Row, rows: list[sqlite3.Row]) -> ContextBundle:
        blocks = [
            ContextBlock(
                source_type=row["source_type"],
                source_id=row["source_id"],
                label=row["source_id"],
                content="retained-context-reference",
                tokens=int(row["tokens"]),
                relevance_score=0.0,
                confidence=1.0,
                expected_utility=float(row["utility"]),
                required=False,
                reason_selected="retained_context_use",
                roi=float(row["roi"]),
                compression_strategy=row["compression_strategy"],
                original_tokens=row["original_tokens"],
                exact_preserved=bool(row["exact_preserved"]),
            )
            for row in rows
        ]
        return ContextBundle(
            task_id=task["id"],
            task=task["objective"],
            scope=task["scope"],
            token_budget=int(task["token_budget"]),
            task_tokens=0,
            selected_tokens=sum(item.tokens for item in blocks),
            blocks=blocks,
        )

    def _insert_attributions(
        self, items: tuple[ContextAttribution, ...], created_at: str
    ) -> None:
        for item in items:
            useful = {
                AttributionOutcome.CONTRIBUTED: 1,
                AttributionOutcome.IGNORED: 0,
                AttributionOutcome.MISLED: 0,
                AttributionOutcome.UNCERTAIN: None,
            }[item.outcome]
            self.connection.execute(
                """
                INSERT INTO context_attributions (
                    id, task_id, source_type, source_id, role, outcome,
                    impact_score, confidence, approximate_roi, model_score,
                    execution_score, dependency_score, evaluator_score,
                    evidence_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item.id,
                    item.task_id,
                    item.source_type,
                    item.source_id,
                    item.role,
                    item.outcome.value,
                    item.impact_score,
                    item.confidence,
                    item.approximate_roi,
                    item.model_score,
                    item.execution_score,
                    item.dependency_score,
                    item.evaluator_score,
                    item.evidence_json,
                    created_at,
                ),
            )
            self.connection.execute(
                """
                UPDATE context_uses SET useful = ?
                WHERE task_id = ? AND source_type = ? AND source_id = ?
                """,
                (useful, item.task_id, item.source_type, item.source_id),
            )

    def _resource_efficiency(
        self,
        execution: sqlite3.Row,
        case: EvaluationCase,
        context_rows: list[sqlite3.Row],
        attributions: tuple[ContextAttribution, ...],
    ) -> dict[str, int | float | None]:
        total_tokens = case.input_tokens + case.output_tokens
        selected_tokens = sum(int(row["tokens"]) for row in context_rows)
        ignored = {
            (item.source_type, item.source_id)
            for item in attributions
            if item.outcome in {AttributionOutcome.IGNORED, AttributionOutcome.MISLED}
        }
        unnecessary_context_tokens = sum(
            int(row["tokens"])
            for row in context_rows
            if (row["source_type"], row["source_id"]) in ignored
        )
        estimated_cost = float(
            self.connection.execute(
                """
                SELECT COALESCE(SUM(estimated_cost), 0)
                FROM telemetry_events WHERE run_id = ?
                """,
                (execution["run_id"],),
            ).fetchone()[0]
        )
        budget_efficiency = (
            None
            if case.token_budget is None
            else min(1.0, case.token_budget / max(1, total_tokens))
        )
        context_efficiency = (
            1.0
            if selected_tokens == 0
            else 1.0 - unnecessary_context_tokens / selected_tokens
        )
        return {
            "total_tokens": total_tokens,
            "selected_context_tokens": selected_tokens,
            "unnecessary_context_tokens": unnecessary_context_tokens,
            "context_efficiency": round(context_efficiency, 6),
            "budget_efficiency": (
                None if budget_efficiency is None else round(budget_efficiency, 6)
            ),
            "duration_ms": int(execution["duration_ms"]),
            "estimated_cost": estimated_cost,
        }

    def _memory_candidates(
        self,
        run_id: str,
        plan: DistillationPlan | None,
        created_at: str,
    ) -> int:
        if plan is None:
            return 0
        candidates = [
            item for item in plan.items if item.kind.value != "candidate_skill"
        ]
        for item in candidates:
            self.connection.execute(
                """
                INSERT INTO learning_memory_candidates (
                    id, run_id, distilled_item_id, memory_type,
                    content_hash, candidate_json, status, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, 'proposed', ?)
                """,
                (
                    str(uuid.uuid4()),
                    run_id,
                    item.id,
                    item.kind.value,
                    hashlib.sha256(item.content.encode("utf-8")).hexdigest(),
                    json.dumps(
                        {
                            "content": item.content,
                            "evidence": list(item.evidence),
                            "confidence": item.confidence,
                            "importance": item.importance,
                        },
                        sort_keys=True,
                    ),
                    created_at,
                ),
            )
        return len(candidates)

    def _update_memory_utility(
        self,
        attributions: tuple[ContextAttribution, ...],
        success: bool,
        now: str,
    ) -> int:
        updated = 0
        for item in attributions:
            if (
                item.source_type != "memory"
                or item.outcome is AttributionOutcome.UNCERTAIN
            ):
                continue
            positive = item.outcome is AttributionOutcome.CONTRIBUTED and success
            ConfidenceCalibration(self.connection).resolve(
                "memory",
                f"{item.task_id}:{item.source_id}",
                positive,
                evidence=(f"learning_attribution:{item.id}",),
                commit=False,
            )
            cursor = self.connection.execute(
                """
                UPDATE memories
                SET access_count = access_count + 1,
                    successful_uses = successful_uses + ?,
                    failed_uses = failed_uses + ?,
                    utility_score = CAST(successful_uses + ? AS REAL)
                        / (access_count + 1),
                    last_accessed = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    int(positive),
                    int(not positive),
                    int(positive),
                    now,
                    now,
                    item.source_id,
                ),
            )
            if cursor.rowcount != 1:
                raise KeyError(item.source_id)
            updated += 1
        return updated

    def _update_skill_utility(
        self,
        attributions: tuple[ContextAttribution, ...],
        success: bool,
        request: LearningRequest,
        execution: sqlite3.Row,
        context_rows: list[sqlite3.Row],
        now: str,
    ) -> int:
        tokens = {
            (row["source_type"], row["source_id"]): int(row["tokens"])
            for row in context_rows
        }
        updated = 0
        for item in attributions:
            if (
                item.source_type != "skill"
                or item.outcome is AttributionOutcome.UNCERTAIN
            ):
                continue
            positive = item.outcome is AttributionOutcome.CONTRIBUTED and success
            skill_tokens = tokens[(item.source_type, item.source_id)]
            cursor = self.connection.execute(
                """
                UPDATE skills
                SET use_count = use_count + 1,
                    success_count = success_count + ?,
                    failure_count = failure_count + ?,
                    total_tokens = total_tokens + ?,
                    total_latency_ms = total_latency_ms + ?,
                    last_used = ?
                WHERE id = ?
                """,
                (
                    int(positive),
                    int(not positive),
                    skill_tokens,
                    int(execution["duration_ms"]),
                    now,
                    item.source_id,
                ),
            )
            if cursor.rowcount != 1:
                raise KeyError(item.source_id)
            self.connection.execute(
                """
                INSERT INTO skill_performance (
                    skill_id, task_class, model, uses, successful_uses,
                    failures, total_tokens, total_cost, total_latency_ms,
                    last_used
                ) VALUES (?, ?, ?, 1, ?, ?, ?, 0, ?, ?)
                ON CONFLICT(skill_id, task_class, model) DO UPDATE SET
                    uses = uses + 1,
                    successful_uses =
                        successful_uses + excluded.successful_uses,
                    failures = failures + excluded.failures,
                    total_tokens = total_tokens + excluded.total_tokens,
                    total_latency_ms =
                        total_latency_ms + excluded.total_latency_ms,
                    last_used = excluded.last_used
                """,
                (
                    item.source_id,
                    request.task_class,
                    request.model,
                    int(positive),
                    int(not positive),
                    skill_tokens,
                    int(execution["duration_ms"]),
                    now,
                ),
            )
            updated += 1
        return updated

    def _routing_improvements(
        self,
        run_id: str,
        attributions: tuple[ContextAttribution, ...],
        created_at: str,
    ) -> int:
        skills = [item for item in attributions if item.source_type == "skill"]
        for item in skills:
            recommendation = {
                AttributionOutcome.CONTRIBUTED: "reinforce",
                AttributionOutcome.IGNORED: "review_reduce",
                AttributionOutcome.MISLED: "quarantine_review",
                AttributionOutcome.UNCERTAIN: "collect_evidence",
            }[item.outcome]
            self.connection.execute(
                """
                INSERT INTO learning_routing_improvements (
                    id, run_id, skill_id, attribution_outcome,
                    recommendation, evidence_json, status, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, 'proposed', ?)
                """,
                (
                    str(uuid.uuid4()),
                    run_id,
                    item.source_id,
                    item.outcome.value,
                    recommendation,
                    item.evidence_json,
                    created_at,
                ),
            )
        return len(skills)

    def _regressions(
        self,
        run_id: str,
        evaluation: EvaluationRun,
        resource: dict[str, int | float | None],
        baseline: RegressionBaseline,
        created_at: str,
    ) -> int:
        checks = [
            (
                "quality",
                baseline.quality_floor,
                evaluation.result.score,
                evaluation.result.score < baseline.quality_floor,
            ),
            (
                "total_tokens",
                baseline.max_total_tokens,
                resource["total_tokens"],
                baseline.max_total_tokens is not None
                and int(resource["total_tokens"] or 0)
                > baseline.max_total_tokens,
            ),
            (
                "duration_ms",
                baseline.max_duration_ms,
                resource["duration_ms"],
                baseline.max_duration_ms is not None
                and int(resource["duration_ms"] or 0)
                > baseline.max_duration_ms,
            ),
            (
                "estimated_cost",
                baseline.max_estimated_cost,
                resource["estimated_cost"],
                baseline.max_estimated_cost is not None
                and float(resource["estimated_cost"] or 0)
                > baseline.max_estimated_cost,
            ),
        ]
        regressions = [item for item in checks if item[3]]
        for metric, expected, observed, _ in regressions:
            self.connection.execute(
                """
                INSERT INTO learning_regressions (
                    id, run_id, metric, baseline_value, observed_value,
                    delta, severity, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, 'review', ?)
                """,
                (
                    str(uuid.uuid4()),
                    run_id,
                    metric,
                    expected,
                    observed,
                    float(observed or 0) - float(expected or 0),
                    created_at,
                ),
            )
        return len(regressions)

    def get(self, run_id: str) -> LearningRun:
        row = self.connection.execute(
            "SELECT * FROM learning_runs WHERE id = ?", (run_id,)
        ).fetchone()
        if row is None:
            raise KeyError(run_id)
        stages = tuple(
            LearningStage(
                stage=item["stage"],
                status=item["status"],
                details=json.loads(item["details_json"]),
            )
            for item in self.connection.execute(
                """
                SELECT * FROM learning_stage_results
                WHERE run_id = ? ORDER BY sequence
                """,
                (run_id,),
            ).fetchall()
        )
        if tuple(item.stage for item in stages) != LEARNING_STAGES:
            raise ValueError("Persisted learning pipeline is incomplete")
        return LearningRun(
            id=row["id"],
            execution_run_id=row["execution_run_id"],
            task_id=row["task_id"],
            evaluation_run_id=row["evaluation_run_id"],
            experience_distillation_id=row["experience_distillation_id"],
            skill_generation_run_id=row["skill_generation_run_id"],
            resource_efficiency=json.loads(row["resource_efficiency_json"]),
            stages=stages,
            memory_candidate_count=int(row["memory_candidate_count"]),
            skill_candidate_count=int(row["skill_candidate_count"]),
            routing_improvement_count=int(row["routing_improvement_count"]),
            regression_count=int(row["regression_count"]),
            created_at=row["created_at"],
        )
