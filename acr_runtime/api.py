from __future__ import annotations

import hmac
import sqlite3
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .config import Settings
from .dashboard import DashboardReader, SERIES
from .learning_dashboard import LearningDashboardReader
from .memory import LifecycleState, MemoryQuery, MemoryStatus, MemoryType, Sensitivity
from .memory_inspector import MemoryInspector
from .memory_inspector_actions import (
    MemoryInspectorActions,
    MemoryInspectorConflict,
)
from .service import AdaptiveRuntime
from .skill_lab import SkillLabReader
from .skill_lab_actions import SkillLabActions, SkillLabConflict
from .skill_benchmark import SkillBenchmarkRequest


class ClosedModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TaskCreateRequest(ClosedModel):
    objective: str = Field(min_length=1, max_length=8_000)
    scope: str = Field(default="global", min_length=1, max_length=128)
    token_budget: int = Field(default=4_000, ge=1, le=1_000_000)

    @field_validator("objective", "scope")
    @classmethod
    def reject_blank_text(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("must not be blank")
        return stripped


class TaskResponse(ClosedModel):
    id: str
    objective: str
    scope: str
    token_budget: int
    selected_tokens: int
    status: str
    critic_score: float | None
    duration_ms: int | None
    created_at: str
    completed_at: str | None


class MemorySearchRequest(ClosedModel):
    query: str = Field(min_length=1, max_length=8_000)
    scope: str = Field(min_length=1, max_length=128)
    limit: int = Field(default=20, ge=1, le=100)

    @field_validator("query", "scope")
    @classmethod
    def reject_blank_text(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("must not be blank")
        return stripped


class SkillSearchRequest(ClosedModel):
    query: str = Field(min_length=1, max_length=2_000)
    limit: int = Field(default=10, ge=1, le=50)


class ItemList(ClosedModel):
    items: list[dict[str, Any]]
    count: int


class HealthResponse(ClosedModel):
    status: str
    database: dict[str, Any]
    daemon_instance_id: str | None = None


class DashboardMetric(ClosedModel):
    status: str
    value: int | float | str | None
    unit: str | None
    sample_count: int
    coverage: float | None
    reason: str | None
    as_of: str


class DashboardCollectionResponse(ClosedModel):
    status: str
    items: list[dict[str, Any]]
    count: int
    reason: str | None
    as_of: str
    next_cursor: str | None = None


class DashboardOverviewResponse(ClosedModel):
    status: str
    as_of: str
    metrics: dict[str, DashboardMetric]
    task_states: dict[str, int]


class DashboardContextResponse(ClosedModel):
    status: str
    as_of: str
    reason: str | None
    metrics: dict[str, DashboardMetric]
    compression: list[dict[str, Any]]


class DashboardBenchmarksResponse(ClosedModel):
    status: str
    as_of: str
    local_model: DashboardCollectionResponse
    skill: DashboardCollectionResponse
    memory: DashboardMetric
    token: DashboardMetric


class DashboardSecurityResponse(ClosedModel):
    status: str
    as_of: str
    reason: str | None
    assessments: list[dict[str, Any]]
    capability_decisions: list[dict[str, Any]]
    privacy_decisions: list[dict[str, Any]]
    regression_alerts: list[dict[str, Any]]


class DashboardSeriesPoint(ClosedModel):
    key: str | None
    value: int | float | None
    sample_count: int
    coverage: float | None = None


class DashboardSeriesResponse(ClosedModel):
    metric: str
    status: str
    unit: str
    points: list[DashboardSeriesPoint]
    count: int
    reason: str | None
    as_of: str


class InspectorLifecycleRequest(ClosedModel):
    scope: str = Field(min_length=1, max_length=128)
    expected_updated_at: str = Field(min_length=1, max_length=128)
    action: Literal["pin", "archive", "restore"]
    reason: str | None = Field(default=None, max_length=2_000)


class InspectorCorrectionRequest(ClosedModel):
    scope: str = Field(min_length=1, max_length=128)
    expected_updated_at: str = Field(min_length=1, max_length=128)
    content: str = Field(min_length=1, max_length=1_000_000)
    evidence: list[str] = Field(min_length=1, max_length=20)
    reason: str = Field(min_length=1, max_length=2_000)

    @field_validator("evidence")
    @classmethod
    def validate_evidence(cls, values: list[str]) -> list[str]:
        if any(not value.strip() or len(value) > 2_000 for value in values):
            raise ValueError("evidence references must be nonblank and bounded")
        return [value.strip() for value in values]


class InspectorDeletePlanRequest(ClosedModel):
    scope: str = Field(min_length=1, max_length=128)
    expected_updated_at: str = Field(min_length=1, max_length=128)
    reason: str = Field(min_length=1, max_length=2_000)


class InspectorDeleteApprovalRequest(ClosedModel):
    scope: str = Field(min_length=1, max_length=128)
    confirmation: str = Field(min_length=1, max_length=128)


class SkillLabCompareRequest(ClosedModel):
    left_ref: str = Field(min_length=1, max_length=256)
    right_ref: str = Field(min_length=1, max_length=256)


class SkillLabLifecycleRequest(ClosedModel):
    action: Literal["activate", "quarantine", "retire"]
    expected_revision: str = Field(min_length=64, max_length=64)
    reason: str = Field(min_length=1, max_length=2_000)
    confirmation: str | None = Field(default=None, max_length=256)


class SkillLabRollbackRequest(ClosedModel):
    expected_source_revision: str = Field(min_length=64, max_length=64)
    expected_candidate_revision: str = Field(min_length=64, max_length=64)
    reason: str = Field(min_length=1, max_length=2_000)


class SkillLabBenchmarkRequest(ClosedModel):
    skill_name: str = Field(min_length=1, max_length=128)
    existing_ref: str = Field(min_length=1, max_length=256)
    candidate_ref: str = Field(min_length=1, max_length=256)
    trials: list[dict[str, Any]] = Field(min_length=1, max_length=300)


def create_app(
    database: str | Path,
    *,
    api_token: str | None = None,
    operator_id: str | None = None,
    daemon_instance_id: str | None = None,
) -> FastAPI:
    settings = Settings.from_env(database=database)
    if api_token is not None and not api_token:
        raise ValueError("API token cannot be empty")
    if daemon_instance_id is not None:
        try:
            canonical_daemon_id = str(uuid.UUID(daemon_instance_id))
        except (ValueError, AttributeError):
            raise ValueError("daemon_instance_id must be a UUID") from None
        if canonical_daemon_id != daemon_instance_id:
            raise ValueError("daemon_instance_id must be a canonical UUID")

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        with AdaptiveRuntime(settings=settings):
            pass
        yield

    app = FastAPI(
        title="Adaptive Cognitive Runtime API",
        version="0.1.0",
        description=(
            "Loopback-first structured API. Streaming is intentionally deferred."
        ),
        lifespan=lifespan,
    )

    async def authorize(
        supplied: str | None = Header(default=None, alias="X-ACR-Token"),
    ) -> None:
        if api_token is not None and (
            supplied is None or not hmac.compare_digest(supplied, api_token)
        ):
            raise HTTPException(status_code=401, detail="Unauthorized")

    async def runtime_dependency(
        _: None = Depends(authorize),
    ):
        runtime = AdaptiveRuntime(settings=settings)
        try:
            yield runtime
        finally:
            runtime.close()

    async def mutation_runtime_dependency(
        supplied: str | None = Header(default=None, alias="X-ACR-Token"),
    ):
        if api_token is None or operator_id is None:
            raise HTTPException(
                status_code=503,
                detail=(
                    "Governed actions require ACR_API_TOKEN and "
                    "ACR_API_OPERATOR_ID"
                ),
            )
        if supplied is None or not hmac.compare_digest(supplied, api_token):
            raise HTTPException(status_code=401, detail="Unauthorized")
        runtime = AdaptiveRuntime(settings=settings)
        try:
            yield runtime
        finally:
            runtime.close()

    @app.exception_handler(LookupError)
    async def lookup_error(_: Request, error: LookupError):
        return JSONResponse(
            status_code=404,
            content={"detail": str(error).strip("'")},
        )

    @app.exception_handler(ValueError)
    async def value_error(_: Request, error: ValueError):
        return JSONResponse(status_code=422, content={"detail": str(error)})

    @app.exception_handler(PermissionError)
    async def permission_error(_: Request, error: PermissionError):
        return JSONResponse(status_code=403, content={"detail": str(error)})

    @app.exception_handler(MemoryInspectorConflict)
    async def inspector_conflict(_: Request, error: MemoryInspectorConflict):
        return JSONResponse(status_code=409, content={"detail": str(error)})

    @app.exception_handler(SkillLabConflict)
    async def skill_lab_conflict(_: Request, error: SkillLabConflict):
        return JSONResponse(status_code=409, content={"detail": str(error)})

    @app.exception_handler(sqlite3.OperationalError)
    async def sqlite_operational_error(_: Request, error: sqlite3.OperationalError):
        busy = any(
            marker in str(error).lower() for marker in ("locked", "busy")
        )
        return JSONResponse(
            status_code=503,
            content={
                "detail": (
                    "Database temporarily busy"
                    if busy
                    else "Database temporarily unavailable"
                )
            },
            headers={"Retry-After": "1"} if busy else None,
        )

    @app.post("/tasks", response_model=TaskResponse, status_code=201)
    async def create_task(
        body: TaskCreateRequest,
        runtime: AdaptiveRuntime = Depends(runtime_dependency),
    ):
        task_id = runtime.db.create_task(
            objective=body.objective.strip(),
            scope=body.scope.strip(),
            token_budget=body.token_budget,
        )
        row = runtime.db.connection.execute(
            "SELECT * FROM tasks WHERE id=?", (task_id,)
        ).fetchone()
        return dict(row)

    @app.get("/tasks/{task_id}", response_model=TaskResponse)
    async def get_task(
        task_id: str,
        runtime: AdaptiveRuntime = Depends(runtime_dependency),
    ):
        row = runtime.db.connection.execute(
            "SELECT * FROM tasks WHERE id=?", (task_id,)
        ).fetchone()
        if row is None:
            raise LookupError(f"Unknown task: {task_id}")
        return dict(row)

    def safe_memories(
        runtime: AdaptiveRuntime,
        *,
        scope: str,
        query: str | None,
        limit: int,
    ) -> list[dict[str, Any]]:
        page = runtime.db.memories.search(MemoryQuery(
            scope=scope,
            text=query,
            include_global=False,
            sensitivities=(Sensitivity.PUBLIC, Sensitivity.INTERNAL),
            limit=limit,
        ))
        output = []
        for record in page.records:
            output.append({
                "id": record.id,
                "type": record.type.value,
                "scope": record.scope,
                "subject": record.subject,
                "content": record.content,
                "confidence": record.confidence,
                "importance": record.importance,
                "status": record.status.value,
                "sensitivity": record.sensitivity.value,
                "updated_at": record.updated_at,
                "valid_from": record.valid_from,
                "valid_until": record.valid_until,
                "observed_at": record.observed_at,
                "source_freshness": record.source_freshness.value,
                "source_class": (
                    record.source_class.value if record.source_class else None
                ),
                "expected_half_life_days": record.expected_half_life_days,
                "requires_refresh": record.requires_refresh,
            })
        return output

    @app.get("/memory", response_model=ItemList)
    async def list_memory(
        scope: Annotated[str, Query(min_length=1, max_length=128)],
        limit: Annotated[int, Query(ge=1, le=100)] = 20,
        runtime: AdaptiveRuntime = Depends(runtime_dependency),
    ):
        items = safe_memories(
            runtime, scope=scope, query=None, limit=limit
        )
        return {"items": items, "count": len(items)}

    @app.post("/memory/search", response_model=ItemList)
    async def search_memory(
        body: MemorySearchRequest,
        runtime: AdaptiveRuntime = Depends(runtime_dependency),
    ):
        items = safe_memories(
            runtime, scope=body.scope, query=body.query, limit=body.limit
        )
        return {"items": items, "count": len(items)}

    @app.get("/skills", response_model=ItemList)
    async def list_skills(
        runtime: AdaptiveRuntime = Depends(runtime_dependency),
    ):
        items = runtime.db.list_skills()
        return {"items": items, "count": len(items)}

    @app.post("/skills/search", response_model=dict[str, Any])
    async def search_skills(
        body: SkillSearchRequest,
        runtime: AdaptiveRuntime = Depends(runtime_dependency),
    ):
        return runtime.search_skills(body.query, limit=body.limit)

    @app.get("/agents", response_model=ItemList)
    async def list_agents(
        runtime: AdaptiveRuntime = Depends(runtime_dependency),
    ):
        items = [
            {
                key: value
                for key, value in item.items()
                if key != "objective"
            }
            for item in runtime.list_agent_specs()
        ]
        return {"items": items, "count": len(items)}

    @app.get("/models", response_model=ItemList)
    async def list_models(
        runtime: AdaptiveRuntime = Depends(runtime_dependency),
    ):
        rows = runtime.db.connection.execute(
            """
            SELECT id, provider, model, context_capacity, supports_tools,
                   input_cost_per_million, output_cost_per_million, active,
                   local, created_at
            FROM model_profiles ORDER BY provider, model
            """
        ).fetchall()
        items = [dict(row) for row in rows]
        return {"items": items, "count": len(items)}

    @app.get("/telemetry", response_model=dict[str, Any])
    async def telemetry(
        runtime: AdaptiveRuntime = Depends(runtime_dependency),
    ):
        return {
            "summary": runtime.telemetry(),
            "models": runtime.telemetry_models(),
            "skills": runtime.telemetry_skills(),
            "memory": runtime.telemetry_memory(),
            "token_economy": runtime.telemetry_token_economy(),
            "compression": runtime.telemetry_compression(),
        }

    @app.get(
        "/dashboard/v1/overview",
        response_model=DashboardOverviewResponse,
    )
    async def dashboard_overview(
        runtime: AdaptiveRuntime = Depends(runtime_dependency),
    ):
        return DashboardReader(runtime).overview()

    @app.get(
        "/dashboard/v1/tasks",
        response_model=DashboardCollectionResponse,
    )
    async def dashboard_tasks(
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
        cursor: Annotated[str | None, Query(max_length=2048)] = None,
        runtime: AdaptiveRuntime = Depends(runtime_dependency),
    ):
        return DashboardReader(runtime).tasks(limit=limit, cursor=cursor)

    @app.get(
        "/dashboard/v1/{section}",
        response_model=(
            DashboardCollectionResponse
            | DashboardContextResponse
            | DashboardBenchmarksResponse
            | DashboardSecurityResponse
        ),
    )
    async def dashboard_section(
        section: str,
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
        runtime: AdaptiveRuntime = Depends(runtime_dependency),
    ):
        reader = DashboardReader(runtime)
        bounded = {
            "skills": reader.skills,
            "agents": reader.agents,
            "models": reader.models,
            "tools": reader.tools,
            "costs": reader.costs,
            "benchmarks": reader.benchmarks,
        }
        unbounded = {
            "memory": reader.memory,
            "context": reader.context,
            "security": reader.security,
        }
        if section in bounded:
            return bounded[section](limit=limit)
        if section in unbounded:
            return unbounded[section]()
        raise LookupError(f"Unknown dashboard section: {section}")

    @app.get(
        "/dashboard/v1/series/{metric}",
        response_model=DashboardSeriesResponse,
    )
    async def dashboard_series(
        metric: str,
        limit: Annotated[int, Query(ge=1, le=100)] = 100,
        runtime: AdaptiveRuntime = Depends(runtime_dependency),
    ):
        if metric not in SERIES:
            raise LookupError(f"Unknown dashboard metric: {metric}")
        return DashboardReader(runtime).series(metric, limit=limit)

    @app.get("/memory-inspector/v1/search", response_model=dict[str, Any])
    async def inspector_search(
        scope: Annotated[str, Query(min_length=1, max_length=128)],
        text: Annotated[str | None, Query(max_length=8_000)] = None,
        memory_type: Annotated[list[MemoryType] | None, Query()] = None,
        status: Annotated[list[MemoryStatus] | None, Query()] = None,
        lifecycle: Annotated[list[LifecycleState] | None, Query()] = None,
        subject: Annotated[str | None, Query(max_length=512)] = None,
        minimum_confidence: Annotated[float, Query(ge=0, le=1)] = 0,
        minimum_utility: Annotated[float, Query(ge=0, le=1)] = 0,
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
        cursor: Annotated[str | None, Query(max_length=2_048)] = None,
        runtime: AdaptiveRuntime = Depends(runtime_dependency),
    ):
        return MemoryInspector(runtime).search(
            scope=scope,
            text=text,
            types=memory_type or (),
            statuses=status or (),
            lifecycle_states=lifecycle or (),
            subject=subject,
            minimum_confidence=minimum_confidence,
            minimum_utility=minimum_utility,
            limit=limit,
            cursor=cursor,
        )

    @app.get("/memory-inspector/v1/timeline", response_model=dict[str, Any])
    async def inspector_timeline(
        scope: Annotated[str, Query(min_length=1, max_length=128)],
        subject: Annotated[str, Query(min_length=1, max_length=512)],
        limit: Annotated[int, Query(ge=1, le=100)] = 100,
        runtime: AdaptiveRuntime = Depends(runtime_dependency),
    ):
        return MemoryInspector(runtime).timeline(
            subject, scope=scope, limit=limit
        )

    @app.get("/memory-inspector/v1/related", response_model=dict[str, Any])
    async def inspector_related(
        scope: Annotated[str, Query(min_length=1, max_length=128)],
        subject: Annotated[str, Query(min_length=1, max_length=512)],
        exclude_id: Annotated[str | None, Query(max_length=128)] = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
        runtime: AdaptiveRuntime = Depends(runtime_dependency),
    ):
        return MemoryInspector(runtime).related(
            subject,
            scope=scope,
            exclude_id=exclude_id,
            limit=limit,
        )

    @app.post(
        "/memory-inspector/v1/{memory_id}/lifecycle",
        response_model=dict[str, Any],
    )
    async def inspector_lifecycle(
        memory_id: str,
        body: InspectorLifecycleRequest,
        runtime: AdaptiveRuntime = Depends(mutation_runtime_dependency),
    ):
        return MemoryInspectorActions(
            runtime, operator_id=operator_id or ""
        ).lifecycle(
            memory_id,
            scope=body.scope,
            expected_updated_at=body.expected_updated_at,
            action=body.action,
            reason=body.reason,
        )

    @app.post(
        "/memory-inspector/v1/{memory_id}/correct",
        response_model=dict[str, Any],
        status_code=201,
    )
    async def inspector_correct(
        memory_id: str,
        body: InspectorCorrectionRequest,
        runtime: AdaptiveRuntime = Depends(mutation_runtime_dependency),
    ):
        return MemoryInspectorActions(
            runtime, operator_id=operator_id or ""
        ).correct(
            memory_id,
            scope=body.scope,
            expected_updated_at=body.expected_updated_at,
            content=body.content,
            evidence=tuple(body.evidence),
            reason=body.reason,
        )

    @app.post(
        "/memory-inspector/v1/{memory_id}/deletion-plan",
        response_model=dict[str, Any],
        status_code=201,
    )
    async def inspector_delete_plan(
        memory_id: str,
        body: InspectorDeletePlanRequest,
        runtime: AdaptiveRuntime = Depends(mutation_runtime_dependency),
    ):
        return MemoryInspectorActions(
            runtime, operator_id=operator_id or ""
        ).plan_delete(
            memory_id,
            scope=body.scope,
            expected_updated_at=body.expected_updated_at,
            reason=body.reason,
        )

    @app.post(
        "/memory-inspector/v1/deletion-requests/{request_id}/approve",
        response_model=dict[str, Any],
    )
    async def inspector_delete_approve(
        request_id: str,
        body: InspectorDeleteApprovalRequest,
        runtime: AdaptiveRuntime = Depends(mutation_runtime_dependency),
    ):
        return MemoryInspectorActions(
            runtime, operator_id=operator_id or ""
        ).approve_delete(
            request_id,
            scope=body.scope,
            confirmation=body.confirmation,
        )

    @app.get(
        "/memory-inspector/v1/{memory_id}",
        response_model=dict[str, Any],
    )
    async def inspector_detail(
        memory_id: str,
        scope: Annotated[str, Query(min_length=1, max_length=128)],
        runtime: AdaptiveRuntime = Depends(runtime_dependency),
    ):
        result = MemoryInspector(runtime).inspect(memory_id, scope=scope)
        if result is None:
            raise LookupError(f"Unknown memory: {memory_id}")
        return result

    @app.get("/skill-lab/v1/skills", response_model=dict[str, Any])
    async def skill_lab_list(
        limit: Annotated[int, Query(ge=1, le=100)] = 100,
        runtime: AdaptiveRuntime = Depends(runtime_dependency),
    ):
        return SkillLabReader(runtime).list(limit=limit)

    @app.get(
        "/learning-dashboard/v1/events",
        response_model=dict[str, Any],
    )
    async def learning_dashboard_events(
        response: Response,
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
        cursor: Annotated[
            str | None, Query(min_length=1, max_length=2048)
        ] = None,
        category: Annotated[
            str | None, Query(min_length=1, max_length=64)
        ] = None,
        autonomy: Annotated[
            str | None, Query(min_length=1, max_length=64)
        ] = None,
        runtime: AdaptiveRuntime = Depends(runtime_dependency),
    ):
        response.headers["Cache-Control"] = "no-store"
        return LearningDashboardReader(runtime).events(
            limit=limit,
            cursor=cursor,
            category=category,
            autonomy=autonomy,
        )

    @app.post("/skill-lab/v1/compare", response_model=dict[str, Any])
    async def skill_lab_compare(
        body: SkillLabCompareRequest,
        runtime: AdaptiveRuntime = Depends(runtime_dependency),
    ):
        return SkillLabReader(runtime).compare(
            body.left_ref, body.right_ref
        )

    @app.post(
        "/skill-lab/v1/skills/{reference}/lifecycle",
        response_model=dict[str, Any],
    )
    async def skill_lab_lifecycle(
        reference: str,
        body: SkillLabLifecycleRequest,
        idempotency_key: Annotated[
            str,
            Header(
                alias="Idempotency-Key",
                min_length=8,
                max_length=128,
            ),
        ],
        runtime: AdaptiveRuntime = Depends(mutation_runtime_dependency),
    ):
        return SkillLabActions(
            runtime, operator_id=operator_id or ""
        ).transition(
            reference,
            action=body.action,
            expected_revision=body.expected_revision,
            idempotency_key=idempotency_key,
            reason=body.reason,
            confirmation=body.confirmation,
        )

    @app.post(
        "/skill-lab/v1/evolutions/{run_id}/rollback",
        response_model=dict[str, Any],
    )
    async def skill_lab_rollback(
        run_id: str,
        body: SkillLabRollbackRequest,
        idempotency_key: Annotated[
            str,
            Header(
                alias="Idempotency-Key",
                min_length=8,
                max_length=128,
            ),
        ],
        runtime: AdaptiveRuntime = Depends(mutation_runtime_dependency),
    ):
        return SkillLabActions(
            runtime, operator_id=operator_id or ""
        ).rollback(
            run_id,
            expected_source_revision=body.expected_source_revision,
            expected_candidate_revision=body.expected_candidate_revision,
            idempotency_key=idempotency_key,
            reason=body.reason,
        )

    @app.post(
        "/skill-lab/v1/benchmark",
        response_model=dict[str, Any],
        status_code=201,
    )
    async def skill_lab_benchmark(
        body: SkillLabBenchmarkRequest,
        idempotency_key: Annotated[
            str,
            Header(
                alias="Idempotency-Key",
                min_length=8,
                max_length=128,
            ),
        ],
        runtime: AdaptiveRuntime = Depends(mutation_runtime_dependency),
    ):
        request = SkillBenchmarkRequest.from_dict(body.model_dump())
        return SkillLabActions(
            runtime, operator_id=operator_id or ""
        ).benchmark(request, idempotency_key=idempotency_key)

    @app.get(
        "/skill-lab/v1/skills/{reference}",
        response_model=dict[str, Any],
    )
    async def skill_lab_detail(
        reference: str,
        runtime: AdaptiveRuntime = Depends(runtime_dependency),
    ):
        return SkillLabReader(runtime).detail(reference)

    @app.get("/health", response_model=HealthResponse)
    async def health(
        runtime: AdaptiveRuntime = Depends(runtime_dependency),
    ):
        database_health = runtime.db.health()
        return {
            "status": (
                "ok" if database_health["quick_check"] == "ok" else "degraded"
            ),
            "database": database_health,
            "daemon_instance_id": daemon_instance_id,
        }

    return app
