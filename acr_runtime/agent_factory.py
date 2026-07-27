from __future__ import annotations

import json
import math
import sqlite3
import uuid
from dataclasses import asdict, dataclass

from .agent_spec import (
    AgentSpec,
    AgentSpecRegistry,
    CommunicationPolicy,
    ModelPolicy,
    _strict_strings,
)
from .memory import utc_now


TOPOLOGIES = (
    "single_agent",
    "multi_agent",
    "parallel_workers",
    "specialist_critic",
    "researchers_synthesizer",
)


@dataclass(frozen=True)
class FactoryWorkstream:
    id: str
    objective: str
    task_scope: tuple[str, ...]
    memory_scope: tuple[str, ...]

    @classmethod
    def from_dict(cls, payload: object) -> "FactoryWorkstream":
        if not isinstance(payload, dict) or set(payload) != {
            "id", "objective", "task_scope", "memory_scope"
        }:
            raise ValueError("workstream has an invalid shape")
        if not isinstance(payload["id"], str) or not isinstance(
            payload["objective"], str
        ):
            raise ValueError("workstream identity must be text")
        if not payload["id"].strip() or len(payload["id"]) > 128:
            raise ValueError("workstream id must be bounded non-empty text")
        if (
            not payload["objective"].strip()
            or len(payload["objective"]) > 2_000
        ):
            raise ValueError("workstream objective must be bounded non-empty text")
        return cls(
            id=payload["id"],
            objective=payload["objective"].strip(),
            task_scope=_strict_strings(
                payload["task_scope"], field="task_scope", nonempty=True
            ),
            memory_scope=_strict_strings(
                payload["memory_scope"], field="memory_scope", nonempty=True
            ),
        )


@dataclass(frozen=True)
class AgentFactoryRequest:
    objective: str
    task_class: str
    workstreams: tuple[FactoryWorkstream, ...]
    tools: tuple[str, ...]
    skills: tuple[str, ...]
    model_policy: ModelPolicy
    token_budget: int
    money_budget: float
    time_budget: int
    permissions: tuple[str, ...]
    verification_requirements: tuple[str, ...]
    estimated_single_agent_tokens: int
    estimated_single_agent_seconds: int
    estimated_context_tokens: int
    estimated_cost_per_1k_tokens: float
    complexity: float
    uncertainty: float
    research_breadth: float
    parallelizable: bool
    requires_critique: bool
    requires_synthesis: bool
    value_score: float
    max_agents: int

    def __post_init__(self) -> None:
        if (
            not self.objective.strip()
            or len(self.objective) > 2_000
            or not self.task_class.strip()
            or len(self.task_class) > 128
        ):
            raise ValueError("factory objective and task_class are required")
        if not 1 <= len(self.workstreams) <= 6:
            raise ValueError("factory requires 1..6 workstreams")
        if len({item.id for item in self.workstreams}) != len(self.workstreams):
            raise ValueError("workstream IDs must be unique")
        for name in (
            "token_budget", "time_budget", "estimated_single_agent_tokens",
            "estimated_single_agent_seconds", "estimated_context_tokens",
            "max_agents",
        ):
            value = getattr(self, name)
            minimum = 0 if name == "estimated_context_tokens" else 1
            if type(value) is not int or value < minimum:
                raise ValueError(f"{name} must be a bounded integer")
        if not 1 <= self.max_agents <= 8:
            raise ValueError("max_agents must be 1..8")
        for name in (
            "complexity", "uncertainty", "research_breadth", "value_score"
        ):
            value = getattr(self, name)
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
            ):
                raise ValueError(f"{name} must be numeric")
            if not 0 <= value <= 1:
                raise ValueError(f"{name} must be 0..1")
        for name in (
            "money_budget", "estimated_cost_per_1k_tokens"
        ):
            value = getattr(self, name)
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or value < 0
            ):
                raise ValueError(f"{name} must be non-negative")
        for name in (
            "parallelizable", "requires_critique", "requires_synthesis"
        ):
            if type(getattr(self, name)) is not bool:
                raise ValueError(f"{name} must be boolean")
        if self.estimated_single_agent_tokens > self.token_budget:
            raise ValueError("single-agent estimate exceeds the token budget")
        if self.estimated_single_agent_seconds > self.time_budget:
            raise ValueError("single-agent estimate exceeds the time budget")
        estimated_money = (
            self.estimated_single_agent_tokens
            * self.estimated_cost_per_1k_tokens
            / 1_000
        )
        if estimated_money > self.money_budget:
            raise ValueError("single-agent estimate exceeds the money budget")

    def as_dict(self) -> dict[str, object]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, object]) -> "AgentFactoryRequest":
        expected = {
            "objective", "task_class", "workstreams", "tools", "skills",
            "model_policy", "token_budget", "money_budget", "time_budget",
            "permissions", "verification_requirements",
            "estimated_single_agent_tokens", "estimated_single_agent_seconds",
            "estimated_context_tokens", "estimated_cost_per_1k_tokens",
            "complexity", "uncertainty", "research_breadth",
            "parallelizable", "requires_critique", "requires_synthesis",
            "value_score", "max_agents",
        }
        if set(payload) != expected:
            raise ValueError("Agent factory request has an invalid shape")
        workstreams = payload["workstreams"]
        if not isinstance(workstreams, list):
            raise ValueError("workstreams must be a list")
        if not isinstance(payload["objective"], str) or not isinstance(
            payload["task_class"], str
        ):
            raise ValueError("factory identity fields must be text")
        return cls(
            objective=payload["objective"],
            task_class=payload["task_class"],
            workstreams=tuple(
                FactoryWorkstream.from_dict(item) for item in workstreams
            ),
            tools=_strict_strings(payload["tools"], field="tools"),
            skills=_strict_strings(
                payload["skills"], field="skills", identifiers=False
            ),
            model_policy=ModelPolicy.from_dict(payload["model_policy"]),
            token_budget=payload["token_budget"],
            money_budget=payload["money_budget"],
            time_budget=payload["time_budget"],
            permissions=_strict_strings(
                payload["permissions"], field="permissions"
            ),
            verification_requirements=_strict_strings(
                payload["verification_requirements"],
                field="verification_requirements",
                nonempty=True,
                identifiers=False,
            ),
            estimated_single_agent_tokens=payload[
                "estimated_single_agent_tokens"
            ],
            estimated_single_agent_seconds=payload[
                "estimated_single_agent_seconds"
            ],
            estimated_context_tokens=payload["estimated_context_tokens"],
            estimated_cost_per_1k_tokens=payload[
                "estimated_cost_per_1k_tokens"
            ],
            complexity=payload["complexity"],
            uncertainty=payload["uncertainty"],
            research_breadth=payload["research_breadth"],
            parallelizable=payload["parallelizable"],
            requires_critique=payload["requires_critique"],
            requires_synthesis=payload["requires_synthesis"],
            value_score=payload["value_score"],
            max_agents=payload["max_agents"],
        )


@dataclass(frozen=True)
class FactoryEstimate:
    topology: str
    worker_count: int
    expected_quality_gain: float
    parallelism_benefit: float
    coordination_overhead: float
    additional_token_cost: int
    estimated_total_tokens: int
    estimated_total_money: float
    estimated_wall_time_seconds: int
    net_benefit: float
    feasible: bool
    selected: bool
    rejection_reasons: tuple[str, ...]

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class FactoryWorker:
    id: str
    sequence: int
    responsibility: str
    spec: AgentSpec
    context_scope: dict[str, object]
    status: str

    def as_dict(self) -> dict[str, object]:
        return {
            **asdict(self),
            "spec": self.spec.as_dict(),
        }


@dataclass(frozen=True)
class AgentFactoryPlan:
    id: str
    request: AgentFactoryRequest
    selected_topology: str
    selected_estimate: FactoryEstimate
    worker_count: int
    status: str
    candidates: tuple[FactoryEstimate, ...]
    workers: tuple[FactoryWorker, ...]
    created_at: str

    def as_dict(self) -> dict[str, object]:
        return {
            **asdict(self),
            "request": self.request.as_dict(),
            "selected_estimate": self.selected_estimate.as_dict(),
            "candidates": [item.as_dict() for item in self.candidates],
            "workers": [item.as_dict() for item in self.workers],
        }


class AgentFactory:
    """Costed topology planner that emits proposed temporary workers only."""

    CREATION_TOKENS = 120
    COORDINATION_TOKENS = 80
    MINIMUM_NET_QUALITY_GAIN = 0.05

    def __init__(
        self,
        connection: sqlite3.Connection,
        agent_specs: AgentSpecRegistry,
    ) -> None:
        self.connection = connection
        self.agent_specs = agent_specs

    @staticmethod
    def _topology_shapes(
        request: AgentFactoryRequest,
    ) -> list[tuple[str, int, int, float]]:
        count = len(request.workstreams)
        shapes = [("single_agent", 1, 0, 0.0)]
        if count >= 2:
            shapes.append(("multi_agent", count + 1, count, 0.10))
        if count >= 2 and request.parallelizable:
            shapes.append(
                (
                    "parallel_workers",
                    count,
                    0,
                    min(0.65, 0.16 * count),
                )
            )
        if request.requires_critique:
            shapes.append(("specialist_critic", 2, 1, 0.0))
        if (
            count >= 2
            and request.requires_synthesis
            and request.research_breadth >= 0.5
        ):
            shapes.append(
                (
                    "researchers_synthesizer",
                    count + 1,
                    count,
                    min(0.55, 0.12 * count),
                )
            )
        return shapes

    def _estimate(
        self,
        request: AgentFactoryRequest,
        topology: str,
        workers: int,
        edges: int,
        parallelism: float,
    ) -> FactoryEstimate:
        raw_quality = {
            "single_agent": 0.0,
            "multi_agent": 0.08 * request.complexity
            + 0.05 * request.uncertainty,
            "parallel_workers": 0.06 * request.complexity
            + 0.04 * min(1.0, len(request.workstreams) / 4),
            "specialist_critic": 0.10 * request.uncertainty
            + 0.06 * request.complexity,
            "researchers_synthesizer": 0.12 * request.research_breadth
            + 0.06 * request.uncertainty
            + 0.04 * request.complexity,
        }[topology]
        overhead = 0.015 * (workers - 1) + 0.005 * edges
        quality_gain = max(0.0, raw_quality - overhead)
        additional_tokens = (
            (workers - 1)
            * (self.CREATION_TOKENS + request.estimated_context_tokens)
            + edges * self.COORDINATION_TOKENS
        )
        total_tokens = request.estimated_single_agent_tokens + additional_tokens
        total_money = (
            total_tokens * request.estimated_cost_per_1k_tokens / 1_000
        )
        wall_time = max(
            1,
            round(
                request.estimated_single_agent_seconds * (1 - parallelism)
                + edges * 10
            ),
        )
        token_ratio = additional_tokens / max(
            1, request.estimated_single_agent_tokens
        )
        net_benefit = (
            request.value_score * quality_gain
            + 0.05 * parallelism
            - 0.05 * token_ratio
            - 0.02 * edges
        )
        reasons: list[str] = []
        if workers > request.max_agents:
            reasons.append("agent_limit")
        if total_tokens > request.token_budget:
            reasons.append("token_budget")
        if total_money > request.money_budget:
            reasons.append("money_budget")
        if wall_time > request.time_budget:
            reasons.append("time_budget")
        if topology != "single_agent" and quality_gain < self.MINIMUM_NET_QUALITY_GAIN:
            reasons.append("insufficient_quality_gain")
        if topology != "single_agent" and net_benefit <= 0:
            reasons.append("non_positive_net_benefit")
        if (
            request.requires_synthesis
            and len(request.workstreams) > 1
            and topology in {"parallel_workers", "specialist_critic"}
        ):
            reasons.append("missing_synthesizer")
        if (
            request.requires_critique
            and topology not in {"single_agent", "specialist_critic"}
        ):
            reasons.append("missing_independent_critic")
        feasible = not reasons
        return FactoryEstimate(
            topology=topology,
            worker_count=workers,
            expected_quality_gain=round(quality_gain, 6),
            parallelism_benefit=round(parallelism, 6),
            coordination_overhead=round(overhead, 6),
            additional_token_cost=additional_tokens,
            estimated_total_tokens=total_tokens,
            estimated_total_money=round(total_money, 8),
            estimated_wall_time_seconds=wall_time,
            net_benefit=round(net_benefit, 6),
            feasible=feasible,
            selected=False,
            rejection_reasons=tuple(reasons),
        )

    @staticmethod
    def _union(
        workstreams: tuple[FactoryWorkstream, ...], field: str
    ) -> tuple[str, ...]:
        return tuple(
            dict.fromkeys(
                value
                for workstream in workstreams
                for value in getattr(workstream, field)
            )
        )

    def _spec(
        self,
        *,
        worker_id: str,
        role: str,
        objective: str,
        workstreams: tuple[FactoryWorkstream, ...],
        request: AgentFactoryRequest,
        worker_count: int,
        communication: CommunicationPolicy,
    ) -> AgentSpec:
        task_scope = self._union(workstreams, "task_scope")
        applicable_skills: list[str] = []
        for reference in request.skills:
            skill = self.agent_specs.skill_registry.inspect(reference)
            skill_tasks = set(skill["task_classes"])
            if not skill_tasks or skill_tasks & set(task_scope):
                applicable_skills.append(reference)
        spec = AgentSpec(
            id=worker_id,
            role=role,
            objective=objective,
            task_scope=task_scope,
            tools=request.tools,
            skills=tuple(applicable_skills),
            memory_scope=self._union(workstreams, "memory_scope"),
            model_policy=request.model_policy,
            token_budget=max(1, request.token_budget // worker_count),
            money_budget=request.money_budget / worker_count,
            time_budget=max(1, request.time_budget // worker_count),
            permissions=request.permissions,
            communication=communication,
            termination_conditions=(
                "objective_met",
                "verification_failed",
                "budget_exhausted",
                "time_exhausted",
                "cancelled",
            ),
            verification_requirements=request.verification_requirements,
        )
        self.agent_specs.validate_dependencies(spec)
        return spec

    def _workers(
        self,
        plan_id: str,
        topology: str,
        request: AgentFactoryRequest,
        worker_count: int,
    ) -> tuple[FactoryWorker, ...]:
        prefix = f"temp-{plan_id[:8]}"
        streams = request.workstreams
        definitions: list[
            tuple[str, str, tuple[FactoryWorkstream, ...], str]
        ] = []
        if topology == "single_agent":
            definitions = [
                (
                    "Primary worker",
                    request.objective,
                    streams,
                    "primary",
                )
            ]
        elif topology == "specialist_critic":
            definitions = [
                ("Specialist", request.objective, streams, "specialist"),
                (
                    "Independent critic",
                    "Verify the specialist output against the requirements.",
                    streams,
                    "critic",
                ),
            ]
        else:
            for stream in streams:
                definitions.append(
                    (
                        "Focused specialist",
                        stream.objective,
                        (stream,),
                        stream.id,
                    )
                )
            if topology in {"multi_agent", "researchers_synthesizer"}:
                definitions.append(
                    (
                        "Synthesizer",
                        "Synthesize the bounded worker results for the objective.",
                        streams,
                        "synthesizer",
                    )
                )
        ids = [
            f"{prefix}-{index}"
            for index in range(1, len(definitions) + 1)
        ]
        workers: list[FactoryWorker] = []
        for index, (role, objective, assigned, responsibility) in enumerate(
            definitions, 1
        ):
            if topology in {"multi_agent", "researchers_synthesizer"}:
                synthesizer_id = ids[-1]
                if index == len(definitions):
                    communication = CommunicationPolicy(
                        "allowlist", tuple(ids[:-1]), len(ids[:-1])
                    )
                else:
                    communication = CommunicationPolicy(
                        "manager_only", (synthesizer_id,), 1
                    )
            elif topology == "specialist_critic":
                communication = CommunicationPolicy(
                    "manager_only", (ids[1 if index == 1 else 0],), 1
                )
            else:
                communication = CommunicationPolicy("none", (), 0)
            spec = self._spec(
                worker_id=ids[index - 1],
                role=role,
                objective=objective,
                workstreams=assigned,
                request=request,
                worker_count=worker_count,
                communication=communication,
            )
            workers.append(
                FactoryWorker(
                    id=str(uuid.uuid4()),
                    sequence=index,
                    responsibility=responsibility,
                    spec=spec,
                    context_scope={
                        "task_scope": list(spec.task_scope),
                        "memory_scope": list(spec.memory_scope),
                    },
                    status="proposed",
                )
            )
        return tuple(workers)

    def plan(self, request: AgentFactoryRequest) -> AgentFactoryPlan:
        estimates = [
            self._estimate(request, *shape)
            for shape in self._topology_shapes(request)
        ]
        single = estimates[0]
        alternatives = [
            item for item in estimates
            if item.topology != "single_agent" and item.feasible
        ]
        if alternatives:
            chosen = min(
                alternatives,
                key=lambda item: (
                    item.worker_count,
                    -item.net_benefit,
                    item.topology,
                ),
            )
        else:
            chosen = single
        selected = tuple(
            FactoryEstimate(
                **{
                    **asdict(item),
                    "selected": item.topology == chosen.topology,
                }
            )
            for item in estimates
        )
        chosen = next(item for item in selected if item.selected)
        plan_id = str(uuid.uuid4())
        workers = self._workers(
            plan_id,
            chosen.topology,
            request,
            chosen.worker_count,
        )
        now = utc_now()
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO agent_factory_plans(
                    id, request_json, selected_topology,
                    selected_estimate_json, worker_count, status, created_at
                ) VALUES (?, ?, ?, ?, ?, 'proposed', ?)
                """,
                (
                    plan_id,
                    json.dumps(request.as_dict(), sort_keys=True),
                    chosen.topology,
                    json.dumps(chosen.as_dict(), sort_keys=True),
                    chosen.worker_count,
                    now,
                ),
            )
            for item in selected:
                self.connection.execute(
                    """
                    INSERT INTO agent_factory_candidates(
                        id, plan_id, topology, worker_count, estimate_json,
                        feasible, selected, rejection_reasons_json, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(uuid.uuid4()),
                        plan_id,
                        item.topology,
                        item.worker_count,
                        json.dumps(item.as_dict(), sort_keys=True),
                        int(item.feasible),
                        int(item.selected),
                        json.dumps(item.rejection_reasons),
                        now,
                    ),
                )
            for worker in workers:
                self.connection.execute(
                    """
                    INSERT INTO agent_factory_workers(
                        id, plan_id, sequence, responsibility, spec_json,
                        context_scope_json, status, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, 'proposed', ?)
                    """,
                    (
                        worker.id,
                        plan_id,
                        worker.sequence,
                        worker.responsibility,
                        json.dumps(worker.spec.as_dict(), sort_keys=True),
                        json.dumps(worker.context_scope, sort_keys=True),
                        now,
                    ),
                )
        return self.load(plan_id)

    def load(self, plan_id: str) -> AgentFactoryPlan:
        row = self.connection.execute(
            "SELECT * FROM agent_factory_plans WHERE id = ?", (plan_id,)
        ).fetchone()
        if row is None:
            raise KeyError(plan_id)
        candidate_rows = self.connection.execute(
            """
            SELECT estimate_json FROM agent_factory_candidates
            WHERE plan_id = ? ORDER BY worker_count, topology
            """,
            (plan_id,),
        ).fetchall()
        candidates = tuple(
            FactoryEstimate(
                **{
                    **json.loads(item["estimate_json"]),
                    "rejection_reasons": tuple(
                        json.loads(item["estimate_json"])[
                            "rejection_reasons"
                        ]
                    ),
                }
            )
            for item in candidate_rows
        )
        worker_rows = self.connection.execute(
            """
            SELECT * FROM agent_factory_workers
            WHERE plan_id = ? ORDER BY sequence
            """,
            (plan_id,),
        ).fetchall()
        workers = tuple(
            FactoryWorker(
                id=item["id"],
                sequence=item["sequence"],
                responsibility=item["responsibility"],
                spec=AgentSpec.from_dict(json.loads(item["spec_json"])),
                context_scope=json.loads(item["context_scope_json"]),
                status=item["status"],
            )
            for item in worker_rows
        )
        selected_payload = json.loads(row["selected_estimate_json"])
        selected_payload["rejection_reasons"] = tuple(
            selected_payload["rejection_reasons"]
        )
        return AgentFactoryPlan(
            id=row["id"],
            request=AgentFactoryRequest.from_dict(
                json.loads(row["request_json"])
            ),
            selected_topology=row["selected_topology"],
            selected_estimate=FactoryEstimate(**selected_payload),
            worker_count=row["worker_count"],
            status=row["status"],
            candidates=candidates,
            workers=workers,
            created_at=row["created_at"],
        )
