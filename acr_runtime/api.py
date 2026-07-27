from __future__ import annotations

import hmac
import sqlite3
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .config import Settings
from .dashboard import DashboardReader, SERIES
from .memory import MemoryQuery, Sensitivity
from .service import AdaptiveRuntime


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


def create_app(
    database: str | Path,
    *,
    api_token: str | None = None,
) -> FastAPI:
    settings = Settings.from_env(database=database)
    if api_token is not None and not api_token:
        raise ValueError("API token cannot be empty")

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

    @app.exception_handler(LookupError)
    async def lookup_error(_: Request, error: LookupError):
        return JSONResponse(
            status_code=404,
            content={"detail": str(error).strip("'")},
        )

    @app.exception_handler(ValueError)
    async def value_error(_: Request, error: ValueError):
        return JSONResponse(status_code=422, content={"detail": str(error)})

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
        }

    return app
