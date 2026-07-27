from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
import uuid
from dataclasses import asdict, dataclass, replace

from .agent_factory import (
    AgentFactory,
    AgentFactoryRequest,
    FactoryWorkstream,
)
from .agent_spec import ModelPolicy, _strict_strings
from .memory import utc_now
from .skill_registry import SkillRegistry
from .skill_router import SkillRouter
from .topology_learning import TopologyLearner


PLAN_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{0,127}$")
PLAN_PHASES = frozenset(
    {"proposed", "blocked", "executing", "completed", "cancelled"}
)
NODE_STATUSES = frozenset(
    {"ready", "waiting", "blocked", "completed", "cancelled"}
)
NODE_KINDS = frozenset({"macro", "action"})
DECOMPOSITION_STATES = frozenset({"leaf", "expandable", "expanded"})


@dataclass(frozen=True)
class PlanPrerequisite:
    id: str
    description: str
    satisfied: bool
    evidence: tuple[str, ...]

    def __post_init__(self) -> None:
        if not PLAN_ID.fullmatch(self.id):
            raise ValueError("prerequisite id is invalid")
        if not self.description.strip() or len(self.description) > 1_000:
            raise ValueError("prerequisite description must be bounded")
        if type(self.satisfied) is not bool:
            raise ValueError("prerequisite satisfied must be boolean")
        if self.satisfied and not self.evidence:
            raise ValueError("satisfied prerequisites require evidence")
        if any(not item.strip() or len(item) > 512 for item in self.evidence):
            raise ValueError("prerequisite evidence is invalid")

    @classmethod
    def from_dict(cls, payload: object) -> "PlanPrerequisite":
        if not isinstance(payload, dict) or set(payload) != {
            "id", "description", "satisfied", "evidence"
        }:
            raise ValueError("prerequisite has an invalid shape")
        if not isinstance(payload["id"], str) or not isinstance(
            payload["description"], str
        ):
            raise ValueError("prerequisite identity must be text")
        return cls(
            id=payload["id"],
            description=payload["description"],
            satisfied=payload["satisfied"],
            evidence=_strict_strings(
                payload["evidence"],
                field="evidence",
                identifiers=False,
            ),
        )


@dataclass(frozen=True)
class PlanWorkHint:
    id: str
    objective: str
    depends_on: tuple[str, ...]
    task_scope: tuple[str, ...]
    memory_scope: tuple[str, ...]
    required_tools: tuple[str, ...]
    required_skills: tuple[str, ...]
    complexity: float
    parallelizable: bool
    verification_requirements: tuple[str, ...]

    def __post_init__(self) -> None:
        if not PLAN_ID.fullmatch(self.id):
            raise ValueError("work hint id is invalid")
        if not self.objective.strip() or len(self.objective) > 2_000:
            raise ValueError("work hint objective must be bounded")
        if self.id in self.depends_on:
            raise ValueError("work hint cannot depend on itself")
        if (
            isinstance(self.complexity, bool)
            or not isinstance(self.complexity, (int, float))
            or not math.isfinite(self.complexity)
            or not 0 <= self.complexity <= 1
        ):
            raise ValueError("work hint complexity must be 0..1")
        if type(self.parallelizable) is not bool:
            raise ValueError("parallelizable must be boolean")
        if not self.task_scope or not self.memory_scope:
            raise ValueError("work hint scopes are required")
        if not self.verification_requirements:
            raise ValueError("work hint verification is required")
        if any(
            "@" not in reference for reference in self.required_skills
        ):
            raise ValueError("required skills must use exact versions")

    @classmethod
    def from_dict(cls, payload: object) -> "PlanWorkHint":
        expected = {
            "id", "objective", "depends_on", "task_scope", "memory_scope",
            "required_tools", "required_skills", "complexity",
            "parallelizable", "verification_requirements",
        }
        if not isinstance(payload, dict) or set(payload) != expected:
            raise ValueError("work hint has an invalid shape")
        if not isinstance(payload["id"], str) or not isinstance(
            payload["objective"], str
        ):
            raise ValueError("work hint identity must be text")
        return cls(
            id=payload["id"],
            objective=payload["objective"],
            depends_on=_strict_strings(
                payload["depends_on"], field="depends_on"
            ),
            task_scope=_strict_strings(
                payload["task_scope"], field="task_scope", nonempty=True
            ),
            memory_scope=_strict_strings(
                payload["memory_scope"], field="memory_scope", nonempty=True
            ),
            required_tools=_strict_strings(
                payload["required_tools"], field="required_tools"
            ),
            required_skills=_strict_strings(
                payload["required_skills"],
                field="required_skills",
                identifiers=False,
            ),
            complexity=payload["complexity"],
            parallelizable=payload["parallelizable"],
            verification_requirements=_strict_strings(
                payload["verification_requirements"],
                field="verification_requirements",
                nonempty=True,
                identifiers=False,
            ),
        )


@dataclass(frozen=True)
class PlanningRequest:
    objective: str
    task_class: str
    constraints: tuple[str, ...]
    prerequisites: tuple[PlanPrerequisite, ...]
    work_hints: tuple[PlanWorkHint, ...]
    available_tools: tuple[str, ...]
    permissions: tuple[str, ...]
    model_policy: ModelPolicy
    token_budget: int
    money_budget: float
    time_budget: int
    estimated_single_agent_tokens: int
    estimated_single_agent_seconds: int
    estimated_context_tokens: int
    estimated_cost_per_1k_tokens: float
    uncertainty: float
    research_breadth: float
    requires_critique: bool
    requires_synthesis: bool
    value_score: float
    max_agents: int

    def __post_init__(self) -> None:
        if not self.objective.strip() or len(self.objective) > 2_000:
            raise ValueError("planning objective must be bounded")
        if not self.task_class.strip() or len(self.task_class) > 128:
            raise ValueError("task_class must be bounded")
        if len(self.work_hints) > 12:
            raise ValueError("at most 12 coarse work hints are allowed")
        for name in (
            "token_budget", "time_budget", "estimated_single_agent_tokens",
            "estimated_single_agent_seconds", "estimated_context_tokens",
            "max_agents",
        ):
            value = getattr(self, name)
            minimum = 0 if name == "estimated_context_tokens" else 1
            if type(value) is not int or value < minimum:
                raise ValueError(f"{name} must be an integer")
        if not 1 <= self.max_agents <= 8:
            raise ValueError("max_agents must be 1..8")
        for name in (
            "money_budget", "estimated_cost_per_1k_tokens",
            "uncertainty", "research_breadth", "value_score",
        ):
            value = getattr(self, name)
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
            ):
                raise ValueError(f"{name} must be finite")
        if self.money_budget < 0 or self.estimated_cost_per_1k_tokens < 0:
            raise ValueError("money values must be non-negative")
        if any(
            not 0 <= getattr(self, name) <= 1
            for name in ("uncertainty", "research_breadth", "value_score")
        ):
            raise ValueError("planning scores must be 0..1")
        if type(self.requires_critique) is not bool or type(
            self.requires_synthesis
        ) is not bool:
            raise ValueError("planning flags must be boolean")
        ids = [item.id for item in self.work_hints]
        if len(ids) != len(set(ids)):
            raise ValueError("work hint IDs must be unique")
        known = set(ids)
        if any(not set(item.depends_on) <= known for item in self.work_hints):
            raise ValueError("work hint dependency is unknown")
        dependencies = {
            item.id: item.depends_on for item in self.work_hints
        }
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(identifier: str) -> None:
            if identifier in visiting:
                raise ValueError("work hint dependency graph contains a cycle")
            if identifier in visited:
                return
            visiting.add(identifier)
            for dependency in dependencies[identifier]:
                visit(dependency)
            visiting.remove(identifier)
            visited.add(identifier)

        for identifier in ids:
            visit(identifier)
        prereq_ids = [item.id for item in self.prerequisites]
        if len(prereq_ids) != len(set(prereq_ids)):
            raise ValueError("prerequisite IDs must be unique")
        if self.estimated_single_agent_tokens > self.token_budget:
            raise ValueError("single-agent estimate exceeds token budget")
        if self.estimated_single_agent_seconds > self.time_budget:
            raise ValueError("single-agent estimate exceeds time budget")
        if (
            self.estimated_single_agent_tokens
            * self.estimated_cost_per_1k_tokens
            / 1_000
            > self.money_budget
        ):
            raise ValueError("single-agent estimate exceeds money budget")

    def as_dict(self) -> dict[str, object]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, object]) -> "PlanningRequest":
        expected = {
            "objective", "task_class", "constraints", "prerequisites",
            "work_hints", "available_tools", "permissions", "model_policy",
            "token_budget", "money_budget", "time_budget",
            "estimated_single_agent_tokens", "estimated_single_agent_seconds",
            "estimated_context_tokens", "estimated_cost_per_1k_tokens",
            "uncertainty", "research_breadth", "requires_critique",
            "requires_synthesis", "value_score", "max_agents",
        }
        if not isinstance(payload, dict) or set(payload) != expected:
            raise ValueError("PlanningRequest has an invalid shape")
        if not isinstance(payload["objective"], str) or not isinstance(
            payload["task_class"], str
        ):
            raise ValueError("planning identity must be text")
        prerequisites = payload["prerequisites"]
        work_hints = payload["work_hints"]
        if not isinstance(prerequisites, list) or not isinstance(
            work_hints, list
        ):
            raise ValueError("planning collections must be lists")
        return cls(
            objective=payload["objective"],
            task_class=payload["task_class"],
            constraints=_strict_strings(
                payload["constraints"],
                field="constraints",
                identifiers=False,
            ),
            prerequisites=tuple(
                PlanPrerequisite.from_dict(item) for item in prerequisites
            ),
            work_hints=tuple(
                PlanWorkHint.from_dict(item) for item in work_hints
            ),
            available_tools=_strict_strings(
                payload["available_tools"], field="available_tools"
            ),
            permissions=_strict_strings(
                payload["permissions"], field="permissions"
            ),
            model_policy=ModelPolicy.from_dict(payload["model_policy"]),
            token_budget=payload["token_budget"],
            money_budget=payload["money_budget"],
            time_budget=payload["time_budget"],
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
            uncertainty=payload["uncertainty"],
            research_breadth=payload["research_breadth"],
            requires_critique=payload["requires_critique"],
            requires_synthesis=payload["requires_synthesis"],
            value_score=payload["value_score"],
            max_agents=payload["max_agents"],
        )


@dataclass(frozen=True)
class PlanNode:
    id: str
    parent_id: str | None
    sequence: int
    depth: int
    kind: str
    objective: str
    depends_on: tuple[str, ...]
    status: str
    decomposition: str
    task_scope: tuple[str, ...]
    memory_scope: tuple[str, ...]
    selected_tools: tuple[str, ...]
    selected_skills: tuple[str, ...]
    assigned_agents: tuple[str, ...]
    token_budget: int
    money_budget: float
    time_budget: int
    verification_requirements: tuple[str, ...]
    planning_evidence: dict[str, object]

    def __post_init__(self) -> None:
        if not PLAN_ID.fullmatch(self.id):
            raise ValueError("plan node id is invalid")
        if self.parent_id is not None and not PLAN_ID.fullmatch(self.parent_id):
            raise ValueError("plan node parent is invalid")
        if type(self.sequence) is not int or self.sequence < 1:
            raise ValueError("plan node sequence must be positive")
        if type(self.depth) is not int or not 0 <= self.depth <= 4:
            raise ValueError("plan node depth must be 0..4")
        if self.kind not in NODE_KINDS or self.status not in NODE_STATUSES:
            raise ValueError("plan node kind or status is invalid")
        if self.decomposition not in DECOMPOSITION_STATES:
            raise ValueError("plan node decomposition is invalid")
        if not self.objective.strip() or len(self.objective) > 2_000:
            raise ValueError("plan node objective must be bounded")
        if self.id in self.depends_on:
            raise ValueError("plan node cannot depend on itself")
        if (
            type(self.token_budget) is not int
            or self.token_budget < 1
            or type(self.time_budget) is not int
            or self.time_budget < 1
            or isinstance(self.money_budget, bool)
            or not isinstance(self.money_budget, (int, float))
            or not math.isfinite(self.money_budget)
            or self.money_budget < 0
        ):
            raise ValueError("plan node budgets are invalid")
        if not self.task_scope or not self.memory_scope:
            raise ValueError("plan node scopes are required")
        if not self.verification_requirements:
            raise ValueError("plan node verification is required")

    def as_dict(self) -> dict[str, object]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: object) -> "PlanNode":
        expected = {
            "id", "parent_id", "sequence", "depth", "kind", "objective",
            "depends_on", "status", "decomposition", "task_scope",
            "memory_scope", "selected_tools", "selected_skills",
            "assigned_agents", "token_budget", "money_budget", "time_budget",
            "verification_requirements", "planning_evidence",
        }
        if not isinstance(payload, dict) or set(payload) != expected:
            raise ValueError("PlanNode has an invalid shape")
        if not all(
            isinstance(payload[key], str)
            for key in ("id", "kind", "objective", "status", "decomposition")
        ) or (
            payload["parent_id"] is not None
            and not isinstance(payload["parent_id"], str)
        ):
            raise ValueError("PlanNode identity has invalid types")
        if not isinstance(payload["planning_evidence"], dict):
            raise ValueError("planning_evidence must be an object")
        return cls(
            id=payload["id"],
            parent_id=payload["parent_id"],
            sequence=payload["sequence"],
            depth=payload["depth"],
            kind=payload["kind"],
            objective=payload["objective"],
            depends_on=_strict_strings(
                payload["depends_on"], field="depends_on"
            ),
            status=payload["status"],
            decomposition=payload["decomposition"],
            task_scope=_strict_strings(
                payload["task_scope"], field="task_scope", nonempty=True
            ),
            memory_scope=_strict_strings(
                payload["memory_scope"], field="memory_scope", nonempty=True
            ),
            selected_tools=_strict_strings(
                payload["selected_tools"], field="selected_tools"
            ),
            selected_skills=_strict_strings(
                payload["selected_skills"],
                field="selected_skills",
                identifiers=False,
            ),
            assigned_agents=_strict_strings(
                payload["assigned_agents"],
                field="assigned_agents",
                identifiers=False,
            ),
            token_budget=payload["token_budget"],
            money_budget=payload["money_budget"],
            time_budget=payload["time_budget"],
            verification_requirements=_strict_strings(
                payload["verification_requirements"],
                field="verification_requirements",
                nonempty=True,
                identifiers=False,
            ),
            planning_evidence=dict(payload["planning_evidence"]),
        )


@dataclass(frozen=True)
class PlanSnapshot:
    phase: str
    objective_summary: str
    constraints: tuple[str, ...]
    prerequisites: tuple[PlanPrerequisite, ...]
    missing_prerequisites: tuple[str, ...]
    nodes: tuple[PlanNode, ...]
    agent_factory_plan_id: str | None
    orchestration_evidence: dict[str, object]

    def as_dict(self) -> dict[str, object]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: object) -> "PlanSnapshot":
        expected = {
            "phase", "objective_summary", "constraints", "prerequisites",
            "missing_prerequisites", "nodes", "agent_factory_plan_id",
            "orchestration_evidence",
        }
        legacy_expected = expected - {"orchestration_evidence"}
        if (
            not isinstance(payload, dict)
            or frozenset(payload) not in {
                frozenset(expected),
                frozenset(legacy_expected),
            }
        ):
            raise ValueError("PlanSnapshot has an invalid shape")
        if not isinstance(payload["phase"], str) or not isinstance(
            payload["objective_summary"], str
        ):
            raise ValueError("snapshot identity must be text")
        if (
            payload["agent_factory_plan_id"] is not None
            and not isinstance(payload["agent_factory_plan_id"], str)
        ):
            raise ValueError("agent_factory_plan_id must be text or null")
        if not isinstance(payload.get("orchestration_evidence", {}), dict):
            raise ValueError("orchestration_evidence must be an object")
        if not isinstance(payload["prerequisites"], list) or not isinstance(
            payload["nodes"], list
        ):
            raise ValueError("snapshot collections must be lists")
        return cls(
            phase=payload["phase"],
            objective_summary=payload["objective_summary"],
            constraints=_strict_strings(
                payload["constraints"],
                field="constraints",
                identifiers=False,
            ),
            prerequisites=tuple(
                PlanPrerequisite.from_dict(item)
                for item in payload["prerequisites"]
            ),
            missing_prerequisites=_strict_strings(
                payload["missing_prerequisites"],
                field="missing_prerequisites",
                identifiers=False,
            ),
            nodes=tuple(PlanNode.from_dict(item) for item in payload["nodes"]),
            agent_factory_plan_id=payload["agent_factory_plan_id"],
            orchestration_evidence=dict(
                payload.get("orchestration_evidence", {})
            ),
        )


@dataclass(frozen=True)
class PlanRevision:
    id: str
    plan_id: str
    revision: int
    parent_revision_id: str | None
    change_kind: str
    change_reason: str
    snapshot: PlanSnapshot
    content_hash: str
    created_at: str

    def as_dict(self) -> dict[str, object]:
        return {
            **asdict(self),
            "snapshot": self.snapshot.as_dict(),
        }


@dataclass(frozen=True)
class HierarchicalPlan:
    id: str
    request: PlanningRequest
    current_revision: int
    status: str
    agent_factory_plan_id: str | None
    revision: PlanRevision
    created_at: str
    updated_at: str

    def as_dict(self) -> dict[str, object]:
        return {
            **asdict(self),
            "request": self.request.as_dict(),
            "revision": self.revision.as_dict(),
        }


class HierarchicalPlanner:
    """Coarse-to-fine planner with retained, optimistic-lock revisions."""

    MAX_NODES = 50
    DECOMPOSE_THRESHOLD = 0.45

    def __init__(
        self,
        connection: sqlite3.Connection,
        skill_router: SkillRouter,
        skill_registry: SkillRegistry,
        agent_factory: AgentFactory,
        topology_learner: TopologyLearner,
    ) -> None:
        self.connection = connection
        self.skill_router = skill_router
        self.skill_registry = skill_registry
        self.agent_factory = agent_factory
        self.topology_learner = topology_learner

    def _capabilities(
        self,
        request: PlanningRequest,
        hint: PlanWorkHint,
        skill_budget: int,
    ) -> tuple[tuple[str, ...], tuple[str, ...], list[str], dict[str, object]]:
        route = self.skill_router.route(
            hint.objective,
            task_class=request.task_class,
            token_budget=skill_budget,
        )
        references = [
            f"{item.manifest_id}@{item.version}" for item in route.selected
        ]
        for reference in hint.required_skills:
            if reference not in references:
                references.append(reference)
        tools = list(hint.required_tools)
        valid_references: list[str] = []
        missing: list[str] = []
        for reference in references:
            skill = self.skill_registry.inspect(reference)
            if skill["lifecycle_status"] != "active":
                if reference in hint.required_skills:
                    raise ValueError(
                        f"required skill is not active: {reference}"
                    )
                continue
            task_classes = set(skill["task_classes"])
            if task_classes and not task_classes & set(hint.task_scope):
                if reference in hint.required_skills:
                    raise ValueError(
                        f"required skill is outside task scope: {reference}"
                    )
                continue
            valid_references.append(reference)
            for tool in skill["manifest"]["tools"]:
                if tool not in tools:
                    tools.append(tool)
            for permission in skill["permissions"]:
                if permission not in request.permissions:
                    missing.append(f"permission:{permission}")
        for tool in tools:
            if tool not in request.available_tools:
                missing.append(f"tool:{tool}")
        return (
            tuple(tools),
            tuple(valid_references),
            missing,
            {"skill_route": route.as_dict()},
        )

    @staticmethod
    def _allocate(total: int, weights: list[float]) -> list[int]:
        if not weights:
            return []
        minimum = len(weights)
        if total < minimum:
            raise ValueError("budget cannot give each plan leaf a minimum")
        remaining = total - minimum
        denominator = sum(weights) or len(weights)
        values = [1 + int(remaining * weight / denominator) for weight in weights]
        for index in range(total - sum(values)):
            values[index % len(values)] += 1
        return values

    @staticmethod
    def _allocate_money(total: float, weights: list[float]) -> list[float]:
        denominator = sum(weights) or len(weights)
        values = [
            round(total * weight / denominator, 8) for weight in weights
        ]
        if values:
            values[-1] = round(total - sum(values[:-1]), 8)
        return values

    def _should_decompose(self, request: PlanningRequest) -> bool:
        if len(request.work_hints) < 2:
            return False
        average = sum(item.complexity for item in request.work_hints) / len(
            request.work_hints
        )
        has_dependencies = any(item.depends_on for item in request.work_hints)
        return average >= self.DECOMPOSE_THRESHOLD or has_dependencies

    def create(self, request: PlanningRequest) -> HierarchicalPlan:
        hints = request.work_hints
        decomposed = self._should_decompose(request)
        if not hints:
            hints = (
                PlanWorkHint(
                    id="objective",
                    objective=request.objective,
                    depends_on=(),
                    task_scope=(request.task_class,),
                    memory_scope=("global",),
                    required_tools=(),
                    required_skills=(),
                    complexity=max(
                        (item.complexity for item in request.work_hints),
                        default=0.2,
                    ),
                    parallelizable=False,
                    verification_requirements=(
                        "Verify the requested output against the objective.",
                    ),
                ),
            )
        elif not decomposed:
            hints = (
                PlanWorkHint(
                    id="objective",
                    objective=request.objective,
                    depends_on=(),
                    task_scope=tuple(
                        dict.fromkeys(
                            scope
                            for hint in request.work_hints
                            for scope in hint.task_scope
                        )
                    ),
                    memory_scope=tuple(
                        dict.fromkeys(
                            scope
                            for hint in request.work_hints
                            for scope in hint.memory_scope
                        )
                    ),
                    required_tools=tuple(
                        dict.fromkeys(
                            tool
                            for hint in request.work_hints
                            for tool in hint.required_tools
                        )
                    ),
                    required_skills=tuple(
                        dict.fromkeys(
                            skill
                            for hint in request.work_hints
                            for skill in hint.required_skills
                        )
                    ),
                    complexity=max(
                        item.complexity for item in request.work_hints
                    ),
                    parallelizable=False,
                    verification_requirements=tuple(
                        dict.fromkeys(
                            requirement
                            for hint in request.work_hints
                            for requirement in hint.verification_requirements
                        )
                    ),
                ),
            )
        weights = [max(0.05, item.complexity) for item in hints]
        token_budgets = self._allocate(request.token_budget, weights)
        time_budgets = self._allocate(request.time_budget, weights)
        money_budgets = self._allocate_money(request.money_budget, weights)
        node_capabilities: dict[
            str, tuple[tuple[str, ...], tuple[str, ...], list[str], dict[str, object]]
        ] = {}
        prerequisite_blockers = [
            f"prerequisite:{item.id}"
            for item in request.prerequisites
            if not item.satisfied
        ]
        capability_blockers: list[str] = []
        for hint, budget in zip(hints, token_budgets):
            node_capabilities[hint.id] = self._capabilities(
                request, hint, max(0, budget // 5)
            )
            capability_blockers.extend(node_capabilities[hint.id][2])
        capability_blockers = list(dict.fromkeys(capability_blockers))
        blockers = list(
            dict.fromkeys((*prerequisite_blockers, *capability_blockers))
        )

        factory_plan_id: str | None = None
        factory_workers: tuple[object, ...] = ()
        orchestration_evidence: dict[str, object] = {
            "historical_recommendation": {
                "available": False,
                "reason": "capability_prerequisites_missing",
                "candidates": [],
            }
        }
        all_tools = tuple(
            dict.fromkeys(
                tool
                for hint in hints
                for tool in node_capabilities[hint.id][0]
            )
        )
        all_skills = tuple(
            dict.fromkeys(
                skill
                for hint in hints
                for skill in node_capabilities[hint.id][1]
            )
        )
        if not capability_blockers:
            factory_request = AgentFactoryRequest(
                objective=request.objective,
                task_class=request.task_class,
                workstreams=tuple(
                    FactoryWorkstream(
                        id=hint.id,
                        objective=hint.objective,
                        task_scope=hint.task_scope,
                        memory_scope=hint.memory_scope,
                    )
                    for hint in hints
                ),
                tools=all_tools,
                skills=all_skills,
                model_policy=request.model_policy,
                token_budget=request.token_budget,
                money_budget=request.money_budget,
                time_budget=request.time_budget,
                permissions=request.permissions,
                verification_requirements=tuple(
                    dict.fromkeys(
                        requirement
                        for hint in hints
                        for requirement in hint.verification_requirements
                    )
                ),
                estimated_single_agent_tokens=request.estimated_single_agent_tokens,
                estimated_single_agent_seconds=request.estimated_single_agent_seconds,
                estimated_context_tokens=request.estimated_context_tokens,
                estimated_cost_per_1k_tokens=request.estimated_cost_per_1k_tokens,
                complexity=max(item.complexity for item in hints),
                uncertainty=request.uncertainty,
                research_breadth=request.research_breadth,
                parallelizable=all(item.parallelizable for item in hints),
                requires_critique=request.requires_critique,
                requires_synthesis=request.requires_synthesis,
                value_score=request.value_score,
                max_agents=request.max_agents,
            )
            factory_plan = self.agent_factory.plan(factory_request)
            factory_plan_id = factory_plan.id
            factory_workers = factory_plan.workers
            recommendation = self.topology_learner.recommend(factory_request)
            orchestration_evidence = {
                "historical_recommendation": recommendation.as_dict(),
                "factory_selected_topology": factory_plan.selected_topology,
                "factory_plan_id": factory_plan.id,
            }

        leaf_nodes: list[PlanNode] = []
        node_ids = {
            hint.id: f"step-{index + 1}"
            for index, hint in enumerate(hints)
        }
        for index, hint in enumerate(hints):
            tools, skills, local_blockers, evidence = node_capabilities[hint.id]
            specialized = tuple(
                worker.spec.id
                for worker in factory_workers
                if worker.responsibility == hint.id
            )
            agents = specialized or tuple(
                worker.spec.id
                for worker in factory_workers
                if (
                    set(worker.spec.task_scope) & set(hint.task_scope)
                    and worker.responsibility
                    in {"primary", "specialist", "critic"}
                )
            )
            depends = tuple(
                node_ids[dependency] if decomposed else dependency
                for dependency in hint.depends_on
            )
            status = (
                "blocked"
                if blockers or local_blockers
                else ("waiting" if depends else "ready")
            )
            leaf_nodes.append(
                PlanNode(
                    id=node_ids[hint.id] if decomposed else "objective",
                    parent_id="root" if decomposed else None,
                    sequence=index + (2 if decomposed else 1),
                    depth=1 if decomposed else 0,
                    kind="action",
                    objective=hint.objective,
                    depends_on=depends,
                    status=status,
                    decomposition=(
                        "expandable" if hint.complexity >= 0.75 else "leaf"
                    ),
                    task_scope=hint.task_scope,
                    memory_scope=hint.memory_scope,
                    selected_tools=tools,
                    selected_skills=skills,
                    assigned_agents=agents,
                    token_budget=token_budgets[index],
                    money_budget=money_budgets[index],
                    time_budget=time_budgets[index],
                    verification_requirements=hint.verification_requirements,
                    planning_evidence={
                        **evidence,
                        "complexity": hint.complexity,
                        "capability_blockers": local_blockers,
                    },
                )
            )
        if decomposed:
            nodes = (
                PlanNode(
                    id="root",
                    parent_id=None,
                    sequence=1,
                    depth=0,
                    kind="macro",
                    objective=request.objective,
                    depends_on=(),
                    status="blocked" if blockers else "ready",
                    decomposition="expanded",
                    task_scope=tuple(
                        dict.fromkeys(
                            scope for hint in hints for scope in hint.task_scope
                        )
                    ),
                    memory_scope=tuple(
                        dict.fromkeys(
                            scope for hint in hints for scope in hint.memory_scope
                        )
                    ),
                    selected_tools=all_tools,
                    selected_skills=all_skills,
                    assigned_agents=tuple(
                        worker.spec.id for worker in factory_workers
                    ),
                    token_budget=request.token_budget,
                    money_budget=request.money_budget,
                    time_budget=request.time_budget,
                    verification_requirements=tuple(
                        dict.fromkeys(
                            item
                            for hint in hints
                            for item in hint.verification_requirements
                        )
                    ),
                    planning_evidence={
                        "progressive_decomposition": True,
                        "coarse_work_items": len(hints),
                    },
                ),
                *leaf_nodes,
            )
        else:
            nodes = tuple(leaf_nodes)
        snapshot = PlanSnapshot(
            phase="blocked" if blockers else "proposed",
            objective_summary=request.objective.strip(),
            constraints=request.constraints,
            prerequisites=request.prerequisites,
            missing_prerequisites=tuple(blockers),
            nodes=nodes,
            agent_factory_plan_id=factory_plan_id,
            orchestration_evidence=orchestration_evidence,
        )
        self._validate_snapshot(request, snapshot)
        return self._insert_initial(request, snapshot)

    def _validate_snapshot(
        self,
        request: PlanningRequest,
        snapshot: PlanSnapshot,
        *,
        previous: PlanSnapshot | None = None,
    ) -> None:
        if snapshot.phase not in PLAN_PHASES:
            raise ValueError("plan phase is invalid")
        if (
            not snapshot.objective_summary.strip()
            or len(snapshot.objective_summary) > 2_000
        ):
            raise ValueError("objective summary must be bounded")
        if not snapshot.nodes or len(snapshot.nodes) > self.MAX_NODES:
            raise ValueError("plan must contain 1..50 nodes")
        ids = {item.id for item in snapshot.nodes}
        if len(ids) != len(snapshot.nodes):
            raise ValueError("plan node IDs must be unique")
        roots = [item for item in snapshot.nodes if item.parent_id is None]
        if len(roots) != 1:
            raise ValueError("plan must have exactly one root")
        by_id = {item.id: item for item in snapshot.nodes}
        sequences = [item.sequence for item in snapshot.nodes]
        if len(sequences) != len(set(sequences)):
            raise ValueError("plan node sequences must be unique")
        for node in snapshot.nodes:
            if node.parent_id is not None:
                parent = by_id.get(node.parent_id)
                if parent is None or node.depth != parent.depth + 1:
                    raise ValueError("plan parent/depth relationship is invalid")
            if not set(node.depends_on) <= ids:
                raise ValueError("plan dependency is unknown")
        allowed_task_scopes = {
            scope
            for hint in request.work_hints
            for scope in hint.task_scope
        } or {request.task_class}
        allowed_memory_scopes = {
            scope
            for hint in request.work_hints
            for scope in hint.memory_scope
        } or {"global"}
        if any(
            not set(node.task_scope) <= allowed_task_scopes
            or not set(node.memory_scope) <= allowed_memory_scopes
            for node in snapshot.nodes
        ):
            raise ValueError("plan revision expands its context scope")
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(node_id: str) -> None:
            if node_id in visiting:
                raise ValueError("plan dependency graph contains a cycle")
            if node_id in visited:
                return
            visiting.add(node_id)
            for dependency in by_id[node_id].depends_on:
                visit(dependency)
            visiting.remove(node_id)
            visited.add(node_id)

        for node_id in ids:
            visit(node_id)
        children = {item.parent_id for item in snapshot.nodes if item.parent_id}
        for node in snapshot.nodes:
            has_children = node.id in children
            if (node.decomposition == "expanded") != has_children:
                raise ValueError("plan decomposition state conflicts with children")
        leaves = [item for item in snapshot.nodes if item.id not in children]
        if sum(item.token_budget for item in leaves) > request.token_budget:
            raise ValueError("leaf token allocations exceed plan budget")
        if sum(item.money_budget for item in leaves) > request.money_budget + 1e-8:
            raise ValueError("leaf money allocations exceed plan budget")
        if sum(item.time_budget for item in leaves) > request.time_budget:
            raise ValueError("leaf time allocations exceed plan budget")
        expected_prereqs = {item.id for item in request.prerequisites}
        actual_prereqs = {item.id for item in snapshot.prerequisites}
        if not expected_prereqs <= actual_prereqs:
            raise ValueError("plan revisions cannot remove initial prerequisites")
        if previous is None and expected_prereqs != actual_prereqs:
            raise ValueError("initial plan prerequisites changed")
        previous_prereqs = (
            {item.id: item for item in previous.prerequisites}
            if previous is not None
            else {}
        )
        if previous is not None and not set(previous_prereqs) <= actual_prereqs:
            raise ValueError("plan revisions cannot remove prerequisites")
        initial_prereqs = {item.id: item for item in request.prerequisites}
        for prerequisite in snapshot.prerequisites:
            original = previous_prereqs.get(
                prerequisite.id, initial_prereqs.get(prerequisite.id)
            )
            if original is not None and (
                prerequisite.description != original.description
                or (original.satisfied and not prerequisite.satisfied)
            ):
                raise ValueError("prerequisite identity or truth was rewritten")
        expected_missing = {
            f"prerequisite:{item.id}"
            for item in snapshot.prerequisites
            if not item.satisfied
        }
        for hint in request.work_hints:
            for tool in hint.required_tools:
                if tool not in request.available_tools:
                    expected_missing.add(f"tool:{tool}")
        for node in snapshot.nodes:
            for tool in node.selected_tools:
                if tool not in request.available_tools:
                    expected_missing.add(f"tool:{tool}")
            for reference in node.selected_skills:
                skill = self.skill_registry.inspect(reference)
                if skill["lifecycle_status"] != "active":
                    expected_missing.add(f"inactive_skill:{reference}")
                required_tools = set(skill["manifest"]["tools"])
                if not required_tools <= set(node.selected_tools):
                    raise ValueError("plan node omits a selected skill tool")
                for permission in skill["permissions"]:
                    if permission not in request.permissions:
                        expected_missing.add(f"permission:{permission}")
        if set(snapshot.missing_prerequisites) != expected_missing:
            raise ValueError("missing prerequisites do not match plan evidence")
        if snapshot.phase == "executing" and snapshot.missing_prerequisites:
            raise ValueError("blocked plans cannot enter execution")
        if snapshot.phase == "blocked" and not snapshot.missing_prerequisites:
            raise ValueError("blocked phase requires a missing prerequisite")
        if snapshot.phase == "proposed" and snapshot.missing_prerequisites:
            raise ValueError("plans with missing prerequisites must be blocked")
        if snapshot.phase in {"proposed", "executing"} and any(
            node.status == "blocked" for node in snapshot.nodes
        ):
            raise ValueError("unblocked plans cannot retain blocked nodes")
        if snapshot.phase == "completed" and any(
            node.status != "completed"
            for node in snapshot.nodes
            if node.kind == "action"
        ):
            raise ValueError("completed plans require completed action nodes")
        if snapshot.agent_factory_plan_id is not None:
            factory_plan = self.agent_factory.load(
                snapshot.agent_factory_plan_id
            )
            allowed_agents = {item.spec.id for item in factory_plan.workers}
            allowed_skills = {
                skill
                for item in factory_plan.workers
                for skill in item.spec.skills
            }
            allowed_tools = {
                tool
                for item in factory_plan.workers
                for tool in item.spec.tools
            }
            if any(
                not set(node.assigned_agents) <= allowed_agents
                for node in snapshot.nodes
            ):
                raise ValueError("plan assigns an unknown temporary agent")
            if any(
                not set(node.selected_skills) <= allowed_skills
                or not set(node.selected_tools) <= allowed_tools
                for node in snapshot.nodes
            ):
                raise ValueError("plan expands its approved capabilities")

    @staticmethod
    def _encode(snapshot: PlanSnapshot) -> tuple[str, str]:
        encoded = json.dumps(
            snapshot.as_dict(), sort_keys=True, separators=(",", ":")
        )
        return encoded, hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    def _insert_initial(
        self, request: PlanningRequest, snapshot: PlanSnapshot
    ) -> HierarchicalPlan:
        plan_id = str(uuid.uuid4())
        revision_id = str(uuid.uuid4())
        encoded, digest = self._encode(snapshot)
        now = utc_now()
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO hierarchical_plans(
                    id, request_json, current_revision, status,
                    agent_factory_plan_id, created_at, updated_at
                ) VALUES (?, ?, 1, ?, ?, ?, ?)
                """,
                (
                    plan_id,
                    json.dumps(request.as_dict(), sort_keys=True),
                    snapshot.phase,
                    snapshot.agent_factory_plan_id,
                    now,
                    now,
                ),
            )
            self.connection.execute(
                """
                INSERT INTO hierarchical_plan_revisions(
                    id, plan_id, revision, parent_revision_id, change_kind,
                    change_reason, snapshot_json, content_hash, created_at
                ) VALUES (?, ?, 1, NULL, 'initial', ?, ?, ?, ?)
                """,
                (
                    revision_id,
                    plan_id,
                    "Initial progressive plan",
                    encoded,
                    digest,
                    now,
                ),
            )
        return self.load(plan_id)

    def revise(
        self,
        plan_id: str,
        *,
        expected_revision: int,
        snapshot: PlanSnapshot,
        reason: str,
        change_kind: str = "edit",
    ) -> HierarchicalPlan:
        current = self.load(plan_id)
        if current.current_revision != expected_revision:
            raise ValueError("stale plan revision")
        if current.status in {"completed", "cancelled"}:
            raise ValueError("terminal plans cannot be edited")
        if change_kind not in {"refinement", "edit", "phase"}:
            raise ValueError("revision change kind is invalid")
        if not reason.strip() or len(reason) > 1_000:
            raise ValueError("revision reason must be bounded")
        if change_kind != "phase" and not (
            snapshot.phase == current.status
            or (
                current.status == "blocked"
                and snapshot.phase == "proposed"
                and not snapshot.missing_prerequisites
            )
            or (
                current.status == "executing"
                and snapshot.phase == "blocked"
                and bool(snapshot.missing_prerequisites)
            )
        ):
            raise ValueError("plan phase changes require a valid transition")
        if (
            snapshot.agent_factory_plan_id
            != current.agent_factory_plan_id
        ):
            raise ValueError("plan revisions cannot replace the agent proposal")
        self._validate_snapshot(
            current.request,
            snapshot,
            previous=current.revision.snapshot,
        )
        revision = expected_revision + 1
        revision_id = str(uuid.uuid4())
        encoded, digest = self._encode(snapshot)
        now = utc_now()
        try:
            with self.connection:
                changed = self.connection.execute(
                    """
                    UPDATE hierarchical_plans
                    SET current_revision = ?, status = ?,
                        agent_factory_plan_id = ?, updated_at = ?
                    WHERE id = ? AND current_revision = ?
                    """,
                    (
                        revision,
                        snapshot.phase,
                        snapshot.agent_factory_plan_id,
                        now,
                        plan_id,
                        expected_revision,
                    ),
                ).rowcount
                if changed != 1:
                    raise ValueError("stale plan revision")
                self.connection.execute(
                    """
                    INSERT INTO hierarchical_plan_revisions(
                        id, plan_id, revision, parent_revision_id, change_kind,
                        change_reason, snapshot_json, content_hash, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        revision_id,
                        plan_id,
                        revision,
                        current.revision.id,
                        change_kind,
                        reason,
                        encoded,
                        digest,
                        now,
                    ),
                )
        except sqlite3.IntegrityError as error:
            raise ValueError("stale or invalid plan revision") from error
        return self.load(plan_id)

    def refine(
        self,
        plan_id: str,
        *,
        expected_revision: int,
        target_node_id: str,
        children: tuple[PlanWorkHint, ...],
        reason: str,
    ) -> HierarchicalPlan:
        current = self.load(plan_id)
        if current.current_revision != expected_revision:
            raise ValueError("stale plan revision")
        snapshot = current.revision.snapshot
        by_id = {node.id: node for node in snapshot.nodes}
        target = by_id.get(target_node_id)
        if target is None:
            raise KeyError(target_node_id)
        if target.decomposition != "expandable":
            raise ValueError("target node is not expandable")
        if target.status in {"completed", "cancelled"}:
            raise ValueError("terminal nodes cannot be refined")
        if target.depth >= 4:
            raise ValueError("maximum progressive decomposition depth reached")
        if not 2 <= len(children) <= 6:
            raise ValueError("refinement requires 2..6 bounded children")
        child_keys = [child.id for child in children]
        if len(child_keys) != len(set(child_keys)):
            raise ValueError("refinement child IDs must be unique")
        if any(
            not set(child.depends_on) <= set(child_keys)
            for child in children
        ):
            raise ValueError("refinement dependency is outside the child set")
        if any(
            not set(child.required_tools) <= set(target.selected_tools)
            or not set(child.required_skills) <= set(target.selected_skills)
            or not set(child.task_scope) <= set(target.task_scope)
            or not set(child.memory_scope) <= set(target.memory_scope)
            for child in children
        ):
            raise ValueError(
                "refinement cannot expand the parent capability or context scope"
            )
        weights = [max(0.05, child.complexity) for child in children]
        tokens = self._allocate(target.token_budget, weights)
        times = self._allocate(target.time_budget, weights)
        money = self._allocate_money(target.money_budget, weights)
        identifiers = {
            child.id: f"{target.id}-{index + 1}"
            for index, child in enumerate(children)
        }
        next_sequence = max(node.sequence for node in snapshot.nodes) + 1
        child_nodes: list[PlanNode] = []
        for index, child in enumerate(children):
            internal_dependencies = tuple(
                identifiers[dependency] for dependency in child.depends_on
            )
            dependencies = tuple(
                dict.fromkeys((*target.depends_on, *internal_dependencies))
            )
            child_nodes.append(
                PlanNode(
                    id=identifiers[child.id],
                    parent_id=target.id,
                    sequence=next_sequence + index,
                    depth=target.depth + 1,
                    kind="action",
                    objective=child.objective,
                    depends_on=dependencies,
                    status=(
                        "blocked"
                        if snapshot.missing_prerequisites
                        else ("waiting" if dependencies else "ready")
                    ),
                    decomposition=(
                        "expandable"
                        if child.complexity >= 0.75 and target.depth + 1 < 4
                        else "leaf"
                    ),
                    task_scope=child.task_scope,
                    memory_scope=child.memory_scope,
                    selected_tools=target.selected_tools,
                    selected_skills=target.selected_skills,
                    assigned_agents=target.assigned_agents,
                    token_budget=tokens[index],
                    money_budget=money[index],
                    time_budget=times[index],
                    verification_requirements=child.verification_requirements,
                    planning_evidence={
                        "refined_from": target.id,
                        "complexity": child.complexity,
                        "capabilities_inherited": True,
                    },
                )
            )
        updated_target = replace(
            target,
            kind="macro",
            decomposition="expanded",
        )
        revised_nodes = tuple(
            updated_target if node.id == target.id else node
            for node in snapshot.nodes
        ) + tuple(child_nodes)
        revised_snapshot = replace(snapshot, nodes=revised_nodes)
        return self.revise(
            plan_id,
            expected_revision=expected_revision,
            snapshot=revised_snapshot,
            reason=reason,
            change_kind="refinement",
        )

    def transition(
        self,
        plan_id: str,
        *,
        expected_revision: int,
        phase: str,
        reason: str,
    ) -> HierarchicalPlan:
        current = self.load(plan_id)
        allowed = {
            "proposed": {"executing", "cancelled"},
            "blocked": {"cancelled"},
            "executing": {"completed", "cancelled"},
        }
        if phase not in allowed.get(current.status, set()):
            raise ValueError("invalid plan phase transition")
        nodes = current.revision.snapshot.nodes
        if phase == "completed" and any(
            node.status != "completed"
            for node in nodes
            if node.kind == "action"
        ):
            raise ValueError("all action nodes must complete first")
        snapshot = replace(current.revision.snapshot, phase=phase)
        return self.revise(
            plan_id,
            expected_revision=expected_revision,
            snapshot=snapshot,
            reason=reason,
            change_kind="phase",
        )

    def load(
        self, plan_id: str, *, revision: int | None = None
    ) -> HierarchicalPlan:
        row = self.connection.execute(
            "SELECT * FROM hierarchical_plans WHERE id = ?", (plan_id,)
        ).fetchone()
        if row is None:
            raise KeyError(plan_id)
        selected_revision = revision or row["current_revision"]
        revision_row = self.connection.execute(
            """
            SELECT * FROM hierarchical_plan_revisions
            WHERE plan_id = ? AND revision = ?
            """,
            (plan_id, selected_revision),
        ).fetchone()
        if revision_row is None:
            raise KeyError(f"{plan_id}@{selected_revision}")
        plan_revision = PlanRevision(
            id=revision_row["id"],
            plan_id=plan_id,
            revision=revision_row["revision"],
            parent_revision_id=revision_row["parent_revision_id"],
            change_kind=revision_row["change_kind"],
            change_reason=revision_row["change_reason"],
            snapshot=PlanSnapshot.from_dict(
                json.loads(revision_row["snapshot_json"])
            ),
            content_hash=revision_row["content_hash"],
            created_at=revision_row["created_at"],
        )
        return HierarchicalPlan(
            id=row["id"],
            request=PlanningRequest.from_dict(json.loads(row["request_json"])),
            current_revision=row["current_revision"],
            status=row["status"],
            agent_factory_plan_id=row["agent_factory_plan_id"],
            revision=plan_revision,
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def history(self, plan_id: str) -> tuple[PlanRevision, ...]:
        current = self.load(plan_id)
        rows = self.connection.execute(
            """
            SELECT revision FROM hierarchical_plan_revisions
            WHERE plan_id = ? ORDER BY revision
            """,
            (plan_id,),
        ).fetchall()
        return tuple(
            self.load(plan_id, revision=row["revision"]).revision
            for row in rows
        )
