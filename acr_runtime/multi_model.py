from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal

from .model_router import ModelRouter, RouteRequest
from .secret_management import assert_secret_free

ModelTier = Literal["small", "medium", "strong"]
WorkflowRole = Literal[
    "classification",
    "memory_extraction",
    "routing",
    "implementation",
    "summarization",
    "architecture",
    "complex_debugging",
    "critique",
]

ROLE_TIERS: dict[str, ModelTier] = {
    "classification": "small",
    "memory_extraction": "small",
    "routing": "small",
    "implementation": "medium",
    "summarization": "medium",
    "architecture": "strong",
    "complex_debugging": "strong",
    "critique": "strong",
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _nonempty(value: object, field: str) -> str:
    text = str(value).strip()
    if not text:
        raise ValueError(f"{field} cannot be empty")
    if len(text) > 255:
        raise ValueError(f"{field} exceeds 255 characters")
    assert_secret_free(text, f"multi-model {field}")
    return text


def _bounded(value: object, field: str) -> float:
    number = float(value)
    if not 0 <= number <= 1:
        raise ValueError(f"{field} must be between 0 and 1")
    return number


@dataclass(frozen=True)
class WorkflowStageRequest:
    id: str
    role: WorkflowRole
    route: RouteRequest
    dependencies: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _nonempty(self.id, "stage id")
        if self.role not in ROLE_TIERS:
            raise ValueError("Unsupported multi-model workflow role")
        if any(not item.strip() for item in self.dependencies):
            raise ValueError("Stage dependencies cannot be empty")
        _nonempty(self.route.task_class, "stage task_class")

    @classmethod
    def from_dict(cls, payload: object) -> "WorkflowStageRequest":
        if not isinstance(payload, dict):
            raise ValueError("Workflow stage must be an object")
        required = {"id", "role", "route"}
        optional = {"dependencies"}
        if not required <= set(payload) or set(payload) - required - optional:
            raise ValueError(
                "Workflow stage requires id, role, route, and optional dependencies"
            )
        dependencies = payload.get("dependencies", [])
        if not isinstance(dependencies, list):
            raise ValueError("Stage dependencies must be a list")
        return cls(
            id=_nonempty(payload["id"], "stage id"),
            role=str(payload["role"]),
            route=RouteRequest.from_dict(payload["route"]),
            dependencies=tuple(str(item) for item in dependencies),
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "role": self.role,
            "route": self.route.as_dict(),
            "dependencies": list(self.dependencies),
        }


@dataclass(frozen=True)
class MultiModelWorkflowRequest:
    workflow_class: str
    baseline_model_id: str
    stages: tuple[WorkflowStageRequest, ...]

    def __post_init__(self) -> None:
        _nonempty(self.workflow_class, "workflow_class")
        _nonempty(self.baseline_model_id, "baseline_model_id")
        if not 2 <= len(self.stages) <= 12:
            raise ValueError("A multi-model workflow requires 2..12 stages")
        ids: set[str] = set()
        for stage in self.stages:
            if stage.id in ids:
                raise ValueError("Workflow stage ids must be unique")
            unknown = set(stage.dependencies) - ids
            if unknown:
                raise ValueError(
                    "Stage dependencies must reference earlier stages: "
                    + ", ".join(sorted(unknown))
                )
            ids.add(stage.id)

    @classmethod
    def from_dict(cls, payload: object) -> "MultiModelWorkflowRequest":
        if not isinstance(payload, dict):
            raise ValueError("Multi-model workflow request must be an object")
        required = {"workflow_class", "baseline_model_id", "stages"}
        if set(payload) != required or not isinstance(payload["stages"], list):
            raise ValueError(
                "Workflow request requires workflow_class, baseline_model_id, stages"
            )
        return cls(
            workflow_class=_nonempty(payload["workflow_class"], "workflow_class"),
            baseline_model_id=_nonempty(
                payload["baseline_model_id"], "baseline_model_id"
            ),
            stages=tuple(
                WorkflowStageRequest.from_dict(item) for item in payload["stages"]
            ),
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "workflow_class": self.workflow_class,
            "baseline_model_id": self.baseline_model_id,
            "stages": [stage.as_dict() for stage in self.stages],
        }


@dataclass(frozen=True)
class BaselineWorkflowOutcome:
    success: bool
    quality: float
    latency_ms: int
    input_tokens: int
    output_tokens: int
    cost: float
    evidence: tuple[str, ...]

    def __post_init__(self) -> None:
        _bounded(self.quality, "quality")
        if min(
            self.latency_ms, self.input_tokens, self.output_tokens
        ) < 0 or self.cost < 0:
            raise ValueError("Baseline metrics cannot be negative")
        if not self.evidence or any(not item.strip() for item in self.evidence):
            raise ValueError("Baseline evidence cannot be empty")
        for item in self.evidence:
            assert_secret_free(item, "multi-model baseline evidence")

    @classmethod
    def from_dict(cls, payload: object) -> "BaselineWorkflowOutcome":
        if not isinstance(payload, dict):
            raise ValueError("Baseline workflow outcome must be an object")
        required = {
            "success",
            "quality",
            "latency_ms",
            "input_tokens",
            "output_tokens",
            "cost",
            "evidence",
        }
        if set(payload) != required:
            raise ValueError(f"Baseline outcome requires {sorted(required)}")
        if not isinstance(payload["success"], bool):
            raise ValueError("Baseline success must be a boolean")
        if not isinstance(payload["evidence"], list):
            raise ValueError("Baseline evidence must be a list")
        return cls(
            success=payload["success"],
            quality=float(payload["quality"]),
            latency_ms=int(payload["latency_ms"]),
            input_tokens=int(payload["input_tokens"]),
            output_tokens=int(payload["output_tokens"]),
            cost=float(payload["cost"]),
            evidence=tuple(str(item) for item in payload["evidence"]),
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "success": self.success,
            "quality": self.quality,
            "latency_ms": self.latency_ms,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.input_tokens + self.output_tokens,
            "cost": self.cost,
        }


@dataclass(frozen=True)
class MultiModelWorkflow:
    id: str
    workflow_class: str
    baseline_model_id: str
    state: Literal["planned", "unavailable", "evaluated"]
    reasons: tuple[str, ...]
    stages: tuple[dict[str, object], ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "workflow_class": self.workflow_class,
            "baseline_model_id": self.baseline_model_id,
            "state": self.state,
            "reasons": list(self.reasons),
            "stages": list(self.stages),
        }


class MultiModelCoordinator:
    """Plans role-specialized routes and measures paired workflow benefit."""

    def __init__(
        self, connection: sqlite3.Connection, model_router: ModelRouter
    ) -> None:
        self.connection = connection
        self.model_router = model_router

    def _tier_models(self, tier: ModelTier) -> tuple[frozenset[str], frozenset[str]]:
        rows = self.connection.execute(
            """
            SELECT id, local FROM model_profiles
            WHERE active=1 AND tier=? ORDER BY id
            """,
            (tier,),
        ).fetchall()
        return (
            frozenset(str(row["id"]) for row in rows),
            frozenset(str(row["id"]) for row in rows if bool(row["local"])),
        )

    def plan(self, request: MultiModelWorkflowRequest) -> MultiModelWorkflow:
        baseline = self.connection.execute(
            "SELECT active FROM model_profiles WHERE id=?",
            (request.baseline_model_id,),
        ).fetchone()
        if baseline is None or not bool(baseline["active"]):
            raise LookupError("Baseline model must be an active registered profile")
        workflow_id = str(uuid.uuid4())
        now = _utc_now()
        reasons: list[str] = []
        planned: list[tuple[WorkflowStageRequest, str, str | None]] = []
        try:
            self.connection.execute("BEGIN IMMEDIATE")
            self.connection.execute(
                """
                INSERT INTO multi_model_workflows(
                    id, workflow_class, baseline_model_id, request_json,
                    state, reasons_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, 'planned', '[]', ?, ?)
                """,
                (
                    workflow_id,
                    request.workflow_class,
                    request.baseline_model_id,
                    json.dumps(request.as_dict()),
                    now,
                    now,
                ),
            )
            for sequence, stage in enumerate(request.stages, start=1):
                tier = ROLE_TIERS[stage.role]
                allowed, local = self._tier_models(tier)
                route = self.model_router.route(
                    stage.route,
                    allowed_model_ids=allowed,
                    preferred_model_ids=local if tier == "small" else frozenset(),
                    commit=False,
                )
                if route.selected_model_id is None:
                    reasons.append(f"stage_unavailable:{stage.id}")
                planned.append((stage, route.id, route.selected_model_id))
                self.connection.execute(
                    """
                    INSERT INTO multi_model_stages(
                        id, workflow_id, stage_key, sequence, role, required_tier,
                        route_id, selected_model_id, dependencies_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(uuid.uuid4()),
                        workflow_id,
                        stage.id,
                        sequence,
                        stage.role,
                        tier,
                        route.id,
                        route.selected_model_id,
                        json.dumps(stage.dependencies),
                    ),
                )
            selected = {
                model_id for _, _, model_id in planned if model_id is not None
            }
            if len(selected) < 2:
                reasons.append("no_distinct_model_specialization")
            state = "unavailable" if reasons else "planned"
            self.connection.execute(
                """
                UPDATE multi_model_workflows
                SET state=?, reasons_json=?, updated_at=? WHERE id=?
                """,
                (state, json.dumps(reasons), _utc_now(), workflow_id),
            )
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise
        return self.get(workflow_id)

    def get(self, workflow_id: str) -> MultiModelWorkflow:
        row = self.connection.execute(
            "SELECT * FROM multi_model_workflows WHERE id=?", (workflow_id,)
        ).fetchone()
        if row is None:
            raise LookupError(f"Unknown multi-model workflow: {workflow_id}")
        stages = self.connection.execute(
            """
            SELECT stage_key AS id, sequence, role, required_tier, route_id,
                   selected_model_id, dependencies_json
            FROM multi_model_stages
            WHERE workflow_id=? ORDER BY sequence
            """,
            (workflow_id,),
        ).fetchall()
        return MultiModelWorkflow(
            id=row["id"],
            workflow_class=row["workflow_class"],
            baseline_model_id=row["baseline_model_id"],
            state=row["state"],
            reasons=tuple(json.loads(row["reasons_json"])),
            stages=tuple(
                {
                    "id": stage["id"],
                    "sequence": stage["sequence"],
                    "role": stage["role"],
                    "required_tier": stage["required_tier"],
                    "route_id": stage["route_id"],
                    "selected_model_id": stage["selected_model_id"],
                    "dependencies": json.loads(stage["dependencies_json"]),
                }
                for stage in stages
            ),
        )

    def record_outcome(
        self, workflow_id: str, baseline: BaselineWorkflowOutcome
    ) -> dict[str, object]:
        workflow = self.get(workflow_id)
        if workflow.state != "planned":
            raise ValueError("Only a planned workflow can record an outcome")
        attempts: list[dict[str, object]] = []
        for stage in workflow.stages:
            route = self.model_router.get(str(stage["route_id"]))
            if route.state != "completed" or not route.attempts:
                raise ValueError("Every specialized stage must complete verification")
            attempts.append(dict(route.attempts[-1]))
        specialized = {
            "success": True,
            "quality": sum(float(item["quality"]) for item in attempts)
            / len(attempts),
            "latency_ms": sum(int(item["latency_ms"]) for item in attempts),
            "input_tokens": sum(int(item["input_tokens"]) for item in attempts),
            "output_tokens": sum(int(item["output_tokens"]) for item in attempts),
            "cost": sum(
                float(item["input_cost"]) + float(item["output_cost"])
                for item in attempts
            ),
            "models": [
                str(stage["selected_model_id"]) for stage in workflow.stages
            ],
            "stage_route_ids": [
                str(stage["route_id"]) for stage in workflow.stages
            ],
        }
        specialized["total_tokens"] = (
            int(specialized["input_tokens"])
            + int(specialized["output_tokens"])
        )
        baseline_data = baseline.as_dict()
        baseline_data["model_id"] = workflow.baseline_model_id
        deltas = {
            "quality_delta": float(specialized["quality"]) - baseline.quality,
            "success_delta": 1 - int(baseline.success),
            "latency_saved_ms": baseline.latency_ms
            - int(specialized["latency_ms"]),
            "tokens_saved": int(baseline_data["total_tokens"])
            - int(specialized["total_tokens"]),
            "cost_saved": baseline.cost - float(specialized["cost"]),
        }
        outcome_id = str(uuid.uuid4())
        try:
            self.connection.execute("BEGIN IMMEDIATE")
            self.connection.execute(
                """
                INSERT INTO multi_model_outcomes(
                    id, workflow_id, workflow_class, specialized_json,
                    baseline_json, quality_delta, success_delta,
                    latency_saved_ms, tokens_saved, cost_saved,
                    evidence_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    outcome_id,
                    workflow_id,
                    workflow.workflow_class,
                    json.dumps(specialized),
                    json.dumps(baseline_data),
                    deltas["quality_delta"],
                    deltas["success_delta"],
                    deltas["latency_saved_ms"],
                    deltas["tokens_saved"],
                    deltas["cost_saved"],
                    json.dumps(baseline.evidence),
                    _utc_now(),
                ),
            )
            self.connection.execute(
                """
                UPDATE multi_model_workflows
                SET state='evaluated', updated_at=? WHERE id=?
                """,
                (_utc_now(), workflow_id),
            )
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise
        return {
            "id": outcome_id,
            "workflow_id": workflow_id,
            "specialized": specialized,
            "baseline": baseline_data,
            **deltas,
        }

    def benefit_report(
        self, workflow_class: str, *, minimum_pairs: int = 3
    ) -> dict[str, object]:
        if not 1 <= minimum_pairs <= 100:
            raise ValueError("minimum_pairs must be between 1 and 100")
        rows = self.connection.execute(
            """
            SELECT * FROM multi_model_outcomes
            WHERE workflow_class=? ORDER BY created_at, id
            """,
            (_nonempty(workflow_class, "workflow_class"),),
        ).fetchall()
        pairs = len(rows)
        if not rows:
            return {
                "workflow_class": workflow_class,
                "pairs": 0,
                "status": "insufficient_evidence",
            }
        average = lambda field: sum(float(row[field]) for row in rows) / pairs
        quality_delta = average("quality_delta")
        specialized_success = sum(
            int(json.loads(row["specialized_json"])["success"]) for row in rows
        ) / pairs
        baseline_success = sum(
            int(json.loads(row["baseline_json"])["success"]) for row in rows
        ) / pairs
        metrics = {
            "quality_delta": quality_delta,
            "success_rate_delta": specialized_success - baseline_success,
            "latency_saved_ms": average("latency_saved_ms"),
            "tokens_saved": average("tokens_saved"),
            "cost_saved": average("cost_saved"),
        }
        if pairs < minimum_pairs:
            status = "insufficient_evidence"
        else:
            quality_or_reliability = (
                quality_delta >= 0.02
                or metrics["success_rate_delta"] >= 0.05
            )
            no_material_regression = (
                metrics["tokens_saved"] >= 0
                and metrics["cost_saved"] >= 0
                and metrics["latency_saved_ms"] >= 0
            )
            status = (
                "beneficial"
                if quality_or_reliability and no_material_regression
                else "not_beneficial"
            )
        return {
            "workflow_class": workflow_class,
            "pairs": pairs,
            "minimum_pairs": minimum_pairs,
            "status": status,
            "metrics": metrics,
        }
