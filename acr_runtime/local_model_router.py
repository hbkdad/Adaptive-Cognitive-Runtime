from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Literal, Protocol

from .benchmark import BenchmarkDataset, BenchmarkRunner
from .model_router import (
    ModelOutcome,
    ModelProfile,
    ModelRoute,
    ModelRouter,
    RouteRequest,
)
from .providers import ModelMetadata, ModelProvider

if TYPE_CHECKING:
    from .privacy import PrivacyEngine

RiskLevel = Literal["low", "medium", "high"]
LOCAL_BENCHMARK_CLASSES = frozenset({
    "classification",
    "summarization",
    "memory_extraction",
    "simple_planning",
    "code_analysis",
})


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class LocalDiscoveryProvider(ModelProvider, Protocol):
    def inspect_model(self, model: str) -> ModelMetadata: ...


@dataclass(frozen=True)
class LocalRouteRequest:
    route: RouteRequest
    risk_level: RiskLevel
    contains_sensitive_context: bool
    cloud_escalation_configured: bool = False
    external_permission_reference: str | None = None
    memory_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.risk_level not in ("low", "medium", "high"):
            raise ValueError("risk_level must be low, medium, or high")
        if (
            self.external_permission_reference is not None
            and not self.external_permission_reference.strip()
        ):
            raise ValueError("external_permission_reference cannot be blank")
        if (
            not isinstance(self.memory_ids, tuple)
            or len(self.memory_ids) > 1_000
            or len(set(self.memory_ids)) != len(self.memory_ids)
            or any(not item.strip() for item in self.memory_ids)
        ):
            raise ValueError("memory_ids must be a bounded unique tuple")

    @classmethod
    def from_dict(cls, payload: object) -> "LocalRouteRequest":
        if not isinstance(payload, dict):
            raise ValueError("Local route request must be an object")
        fields = {
            "route", "risk_level", "contains_sensitive_context",
            "cloud_escalation_configured", "external_permission_reference",
            "memory_ids",
        }
        required = {"route", "risk_level", "contains_sensitive_context"}
        if not required <= set(payload) or set(payload) - fields:
            raise ValueError(f"Local route request requires {sorted(required)}")
        for name in ("contains_sensitive_context", "cloud_escalation_configured"):
            if name in payload and not isinstance(payload[name], bool):
                raise ValueError(f"{name} must be a boolean")
        if "memory_ids" in payload and (
            not isinstance(payload["memory_ids"], list)
            or any(not isinstance(item, str) for item in payload["memory_ids"])
        ):
            raise ValueError("memory_ids must be a string list")
        return cls(
            route=RouteRequest.from_dict(payload["route"]),
            risk_level=str(payload["risk_level"]),
            contains_sensitive_context=payload["contains_sensitive_context"],
            cloud_escalation_configured=payload.get(
                "cloud_escalation_configured", False
            ),
            external_permission_reference=(
                None if payload.get("external_permission_reference") is None
                else str(payload["external_permission_reference"])
            ),
            memory_ids=tuple(payload.get("memory_ids", ())),
        )


class LocalModelRouter:
    """Local discovery, benchmark evidence, and cloud-transmission policy."""

    def __init__(
        self,
        connection: sqlite3.Connection,
        model_router: ModelRouter,
        privacy: "PrivacyEngine | None" = None,
    ) -> None:
        self.connection = connection
        self.model_router = model_router
        self.privacy = privacy

    def discover(self, provider: LocalDiscoveryProvider) -> dict[str, object]:
        discovery_id = str(uuid.uuid4())
        discovered: list[dict[str, object]] = []
        try:
            models = provider.list_models()
            for listed in models:
                try:
                    metadata = provider.inspect_model(listed.model)
                    context = metadata.capabilities.context_window
                    if not context:
                        discovered.append({
                            "model": listed.model, "registered": False,
                            "reason": "context_capacity_unreported",
                        })
                        continue
                    profile = ModelProfile(
                        provider=metadata.provider, model=metadata.model,
                        context_capacity=context,
                        supports_tools=metadata.capabilities.tool_calling,
                        input_cost_per_million=0.0,
                        output_cost_per_million=0.0,
                        local=True,
                    )
                    self.model_router.register(profile)
                    discovered.append({
                        **profile.as_dict(),
                        "registered": True,
                        "chat": metadata.capabilities.chat,
                        "embeddings": metadata.capabilities.embeddings,
                        "vision": metadata.capabilities.vision,
                    })
                except Exception as error:
                    discovered.append({
                        "model": listed.model, "registered": False,
                        "reason": type(error).__name__,
                    })
            status, error_kind = "completed", None
        except Exception as error:
            status, error_kind = "failed", type(error).__name__
        self.connection.execute(
            """
            INSERT INTO local_model_discoveries (
                id, provider, status, models_json, error_kind, created_at
            ) VALUES (?, 'ollama', ?, ?, ?, ?)
            """,
            (discovery_id, status, json.dumps(discovered), error_kind, _utc_now()),
        )
        self.connection.commit()
        return {
            "id": discovery_id, "status": status, "models": discovered,
            "error_kind": error_kind,
        }

    def benchmark(
        self,
        provider: ModelProvider,
        dataset: BenchmarkDataset,
        *,
        model: str,
        seed: int = 0,
        discovery_id: str | None = None,
    ) -> dict[str, object]:
        categories = {case.category for case in dataset.cases}
        missing = LOCAL_BENCHMARK_CLASSES - categories
        if missing:
            raise ValueError(
                f"Local routing benchmark missing task classes: {sorted(missing)}"
            )
        model_id = f"{provider.name}:{model}"
        profile = self.connection.execute(
            "SELECT local FROM model_profiles WHERE id=?", (model_id,)
        ).fetchone()
        if profile is None or not bool(profile["local"]):
            raise ValueError("Local benchmark model must be discovered and local")
        report = BenchmarkRunner(provider, model=model).run(dataset, seed=seed)
        outcome_ids: list[str] = []
        try:
            self.connection.execute("BEGIN IMMEDIATE")
            for result in report.cases:
                outcome_ids.append(self.model_router.record_outcome(
                    ModelOutcome(
                        model_id=model_id, task_class=result.category,
                        success=not result.failed and result.quality == 1.0,
                        quality=result.quality, latency_ms=result.latency_ms,
                        input_tokens=result.input_tokens,
                        output_tokens=result.output_tokens,
                        tool_attempts=result.tool_calls,
                        tool_successes=result.tool_calls if not result.failed else 0,
                        evidence=(
                            f"benchmark:{dataset.name}:v{dataset.version}:"
                            f"{result.case_id}:seed:{seed}",
                        ),
                        input_cost=0.0, output_cost=0.0,
                    ),
                    commit=False,
                ))
            run_id = str(uuid.uuid4())
            self.connection.execute(
                """
                INSERT INTO local_benchmark_runs (
                    id, discovery_id, model_id, dataset, dataset_version, seed,
                    case_count, outcome_ids_json, summary_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (run_id, discovery_id, model_id, dataset.name, dataset.version,
                 seed, len(report.cases), json.dumps(outcome_ids),
                 json.dumps(report.summary), _utc_now()),
            )
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise
        return {
            "id": run_id, "model_id": model_id,
            "outcome_ids": outcome_ids, "report": report.to_dict(),
        }

    def route(self, request: LocalRouteRequest) -> ModelRoute:
        profiles = self.connection.execute(
            "SELECT id, provider, local FROM model_profiles WHERE active=1"
        ).fetchall()
        local_ids = frozenset(row["id"] for row in profiles if row["local"])
        cloud_ids = frozenset(row["id"] for row in profiles if not row["local"])
        permission = request.external_permission_reference
        cloud_allowed = (
            request.cloud_escalation_configured
            and (not request.contains_sensitive_context or bool(permission))
        )
        allowed = local_ids | (cloud_ids if cloud_allowed else frozenset())
        privacy_blocked: set[str] = set()
        if request.memory_ids:
            if self.privacy is None:
                allowed = frozenset()
                privacy_blocked.update(row["id"] for row in profiles)
            else:
                for row in profiles:
                    decision = self.privacy.authorize_provider(
                        request.memory_ids,
                        provider=row["provider"],
                        local=bool(row["local"]),
                    )
                    if not decision["allowed"]:
                        privacy_blocked.add(row["id"])
                allowed = frozenset(allowed - privacy_blocked)
        if request.memory_ids and self.privacy is None:
            reason = "memory_privacy_policy_unavailable"
        elif privacy_blocked:
            reason = "memory_classification_blocks_provider"
        elif request.contains_sensitive_context and not permission:
            reason = "sensitive_context_requires_external_policy_permission"
        elif not request.cloud_escalation_configured:
            reason = "cloud_escalation_not_configured"
        elif cloud_allowed:
            reason = "local_preferred_cloud_escalation_policy_permitted"
        else:
            reason = "cloud_candidates_blocked"
        permission_hash = (
            hashlib.sha256(permission.encode("utf-8")).hexdigest()
            if permission else None
        )
        try:
            self.connection.execute("BEGIN IMMEDIATE")
            route = self.model_router.route(
                request.route,
                allowed_model_ids=allowed,
                preferred_model_ids=local_ids,
                commit=False,
            )
            self.connection.execute(
                """
                INSERT INTO local_route_policies (
                    route_id, risk_level, contains_sensitive_context,
                    cloud_escalation_configured,
                    external_permission_reference_hash,
                    local_candidate_count, cloud_candidate_count,
                    cloud_candidates_allowed, decision_reason, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (route.id, request.risk_level,
                 request.contains_sensitive_context,
                 request.cloud_escalation_configured, permission_hash,
                 len(local_ids), len(cloud_ids), cloud_allowed, reason,
                 _utc_now()),
            )
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise
        return route

    def policy(self, route_id: str) -> dict[str, object]:
        row = self.connection.execute(
            "SELECT * FROM local_route_policies WHERE route_id=?", (route_id,)
        ).fetchone()
        if row is None:
            raise LookupError(f"No local routing policy for route: {route_id}")
        return dict(row)
