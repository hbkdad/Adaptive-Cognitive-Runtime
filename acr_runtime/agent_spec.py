from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
from dataclasses import asdict, dataclass

from .memory import utc_now
from .skill_format import SEMVER, SkillPackageLoader
from .skill_registry import SkillRegistry


AGENT_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{1,127}$")
CAPABILITY_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")
COMMUNICATION_MODES = frozenset({"none", "manager_only", "allowlist"})
TERMINATION_KINDS = frozenset(
    {
        "objective_met",
        "verification_failed",
        "budget_exhausted",
        "time_exhausted",
        "cancelled",
        "blocked",
    }
)
REQUIRED_TERMINATION_KINDS = frozenset(
    {
        "objective_met",
        "verification_failed",
        "budget_exhausted",
        "time_exhausted",
        "cancelled",
    }
)


def _strict_strings(
    values: object,
    *,
    field: str,
    nonempty: bool = False,
    identifiers: bool = True,
) -> tuple[str, ...]:
    if not isinstance(values, list):
        raise ValueError(f"{field} must be a list")
    if nonempty and not values:
        raise ValueError(f"{field} cannot be empty")
    if len(values) > 64:
        raise ValueError(f"{field} exceeds 64 items")
    result: list[str] = []
    for value in values:
        if not isinstance(value, str) or not value.strip() or len(value) > 512:
            raise ValueError(f"{field} contains invalid text")
        normalized = value.strip()
        if identifiers and not CAPABILITY_ID.fullmatch(normalized):
            raise ValueError(f"{field} contains an invalid identifier")
        result.append(normalized)
    if len(set(result)) != len(result):
        raise ValueError(f"{field} contains duplicates")
    return tuple(result)


@dataclass(frozen=True)
class ModelPolicy:
    allowed_models: tuple[str, ...]
    preferred_model: str
    local_only: bool
    allow_fallback: bool

    def __post_init__(self) -> None:
        if not self.allowed_models:
            raise ValueError("allowed_models cannot be empty")
        if len(set(self.allowed_models)) != len(self.allowed_models):
            raise ValueError("allowed_models contains duplicates")
        if any(
            not CAPABILITY_ID.fullmatch(model) for model in self.allowed_models
        ):
            raise ValueError("allowed_models contains an invalid identifier")
        if self.preferred_model not in self.allowed_models:
            raise ValueError("preferred_model must be in allowed_models")
        if type(self.local_only) is not bool or type(self.allow_fallback) is not bool:
            raise ValueError("model policy flags must be booleans")

    @classmethod
    def from_dict(cls, payload: object) -> "ModelPolicy":
        if not isinstance(payload, dict) or set(payload) != {
            "allowed_models",
            "preferred_model",
            "local_only",
            "allow_fallback",
        }:
            raise ValueError("model_policy has an invalid shape")
        preferred = payload["preferred_model"]
        if not isinstance(preferred, str):
            raise ValueError("preferred_model must be text")
        return cls(
            allowed_models=_strict_strings(
                payload["allowed_models"],
                field="allowed_models",
                nonempty=True,
            ),
            preferred_model=preferred,
            local_only=payload["local_only"],
            allow_fallback=payload["allow_fallback"],
        )


@dataclass(frozen=True)
class CommunicationPolicy:
    mode: str
    allowed_peers: tuple[str, ...]
    max_messages: int

    def __post_init__(self) -> None:
        if self.mode not in COMMUNICATION_MODES:
            raise ValueError("invalid communication mode")
        if type(self.max_messages) is not int or not 0 <= self.max_messages <= 100:
            raise ValueError("max_messages must be an integer from 0 to 100")
        if any(not AGENT_ID.fullmatch(peer) for peer in self.allowed_peers):
            raise ValueError("allowed_peers contains an invalid agent ID")
        if len(set(self.allowed_peers)) != len(self.allowed_peers):
            raise ValueError("allowed_peers contains duplicates")
        if self.mode == "none" and (self.allowed_peers or self.max_messages):
            raise ValueError("none communication must have no peers or messages")
        if self.mode == "manager_only" and (
            len(self.allowed_peers) != 1 or self.max_messages < 1
        ):
            raise ValueError("manager_only requires one peer and message budget")
        if self.mode == "allowlist" and (
            not self.allowed_peers or self.max_messages < 1
        ):
            raise ValueError("allowlist requires peers and a message budget")

    @classmethod
    def from_dict(cls, payload: object) -> "CommunicationPolicy":
        if not isinstance(payload, dict) or set(payload) != {
            "mode",
            "allowed_peers",
            "max_messages",
        }:
            raise ValueError("communication has an invalid shape")
        mode = payload["mode"]
        if not isinstance(mode, str):
            raise ValueError("communication mode must be text")
        return cls(
            mode=mode,
            allowed_peers=_strict_strings(
                payload["allowed_peers"],
                field="allowed_peers",
                identifiers=False,
            ),
            max_messages=payload["max_messages"],
        )


@dataclass(frozen=True)
class AgentContextItem:
    source_id: str
    task_scope: str
    memory_scope: str | None
    content: str

    def __post_init__(self) -> None:
        if not CAPABILITY_ID.fullmatch(self.source_id):
            raise ValueError("context source_id is invalid")
        if not CAPABILITY_ID.fullmatch(self.task_scope):
            raise ValueError("context task_scope is invalid")
        if (
            self.memory_scope is not None
            and not CAPABILITY_ID.fullmatch(self.memory_scope)
        ):
            raise ValueError("context memory_scope is invalid")
        if not self.content.strip():
            raise ValueError("context content cannot be empty")


@dataclass(frozen=True)
class AgentSpec:
    id: str
    role: str
    objective: str
    task_scope: tuple[str, ...]
    tools: tuple[str, ...]
    skills: tuple[str, ...]
    memory_scope: tuple[str, ...]
    model_policy: ModelPolicy
    token_budget: int
    money_budget: float
    time_budget: int
    permissions: tuple[str, ...]
    communication: CommunicationPolicy
    termination_conditions: tuple[str, ...]
    verification_requirements: tuple[str, ...]

    def __post_init__(self) -> None:
        if not AGENT_ID.fullmatch(self.id):
            raise ValueError("AgentSpec id is invalid")
        for name, value in (("role", self.role), ("objective", self.objective)):
            if not value.strip() or len(value) > 2_000:
                raise ValueError(f"{name} must be bounded non-empty text")
        for name, values, required in (
            ("task_scope", self.task_scope, True),
            ("memory_scope", self.memory_scope, True),
            ("tools", self.tools, False),
            ("permissions", self.permissions, False),
        ):
            if required and not values:
                raise ValueError(f"{name} cannot be empty")
            if len(set(values)) != len(values):
                raise ValueError(f"{name} contains duplicates")
            if any(not CAPABILITY_ID.fullmatch(value) for value in values):
                raise ValueError(f"{name} contains an invalid identifier")
            if any(value.lower() in {"*", "all"} for value in values):
                raise ValueError(f"{name} cannot use a wildcard scope")
        if len(set(self.skills)) != len(self.skills):
            raise ValueError("skills contains duplicates")
        for reference in self.skills:
            if "@" not in reference:
                raise ValueError("skills must use exact stable-id@version references")
            identifier, version = reference.rsplit("@", 1)
            if not AGENT_ID.fullmatch(identifier) or not SEMVER.fullmatch(version):
                raise ValueError("skills contains an invalid exact reference")
        if type(self.token_budget) is not int or self.token_budget < 1:
            raise ValueError("token_budget must be a positive integer")
        if (
            isinstance(self.money_budget, bool)
            or not isinstance(self.money_budget, (int, float))
            or not math.isfinite(self.money_budget)
            or self.money_budget < 0
        ):
            raise ValueError("money_budget must be finite and non-negative")
        if type(self.time_budget) is not int or self.time_budget < 1:
            raise ValueError("time_budget must be positive integer seconds")
        termination = set(self.termination_conditions)
        if (
            len(termination) != len(self.termination_conditions)
            or not termination <= TERMINATION_KINDS
            or not REQUIRED_TERMINATION_KINDS <= termination
        ):
            raise ValueError("termination_conditions are incomplete or invalid")
        if not self.verification_requirements or any(
            not requirement.strip() or len(requirement) > 512
            for requirement in self.verification_requirements
        ):
            raise ValueError("verification_requirements must be bounded and non-empty")
        if self.id in self.communication.allowed_peers:
            raise ValueError("AgentSpec cannot communicate with itself")

    def as_dict(self) -> dict[str, object]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, object]) -> "AgentSpec":
        expected = {
            "id",
            "role",
            "objective",
            "task_scope",
            "tools",
            "skills",
            "memory_scope",
            "model_policy",
            "token_budget",
            "money_budget",
            "time_budget",
            "permissions",
            "communication",
            "termination_conditions",
            "verification_requirements",
        }
        if set(payload) != expected:
            raise ValueError("AgentSpec must contain exactly the Prompt 24 fields")
        if not all(isinstance(payload[key], str) for key in ("id", "role", "objective")):
            raise ValueError("AgentSpec identity fields must be text")
        return cls(
            id=payload["id"],
            role=payload["role"],
            objective=payload["objective"],
            task_scope=_strict_strings(
                payload["task_scope"], field="task_scope", nonempty=True
            ),
            tools=_strict_strings(payload["tools"], field="tools"),
            skills=_strict_strings(
                payload["skills"], field="skills", identifiers=False
            ),
            memory_scope=_strict_strings(
                payload["memory_scope"], field="memory_scope", nonempty=True
            ),
            model_policy=ModelPolicy.from_dict(payload["model_policy"]),
            token_budget=payload["token_budget"],
            money_budget=payload["money_budget"],
            time_budget=payload["time_budget"],
            permissions=_strict_strings(
                payload["permissions"], field="permissions"
            ),
            communication=CommunicationPolicy.from_dict(
                payload["communication"]
            ),
            termination_conditions=_strict_strings(
                payload["termination_conditions"],
                field="termination_conditions",
                nonempty=True,
            ),
            verification_requirements=_strict_strings(
                payload["verification_requirements"],
                field="verification_requirements",
                nonempty=True,
                identifiers=False,
            ),
        )

    def filter_context(
        self, items: tuple[AgentContextItem, ...]
    ) -> tuple[AgentContextItem, ...]:
        return tuple(
            item
            for item in items
            if item.task_scope in self.task_scope
            and (
                item.memory_scope is None
                or item.memory_scope in self.memory_scope
            )
        )


@dataclass(frozen=True)
class StoredAgentSpec:
    spec: AgentSpec
    resolved_skills: tuple[dict[str, object], ...]
    content_hash: str
    status: str
    created_at: str

    def as_dict(self) -> dict[str, object]:
        return {
            **self.spec.as_dict(),
            "resolved_skills": list(self.resolved_skills),
            "content_hash": self.content_hash,
            "status": self.status,
            "created_at": self.created_at,
        }


class AgentSpecRegistry:
    """Immutable AgentSpec definitions; no worker creation or execution."""

    def __init__(
        self,
        connection: sqlite3.Connection,
        skill_registry: SkillRegistry,
        *,
        loader: SkillPackageLoader | None = None,
    ) -> None:
        self.connection = connection
        self.skill_registry = skill_registry
        self.loader = loader or SkillPackageLoader()

    def _resolve_skills(
        self, spec: AgentSpec
    ) -> tuple[dict[str, object], ...]:
        resolved: list[dict[str, object]] = []
        for reference in spec.skills:
            skill = self.skill_registry.inspect(reference)
            if skill["lifecycle_status"] != "active":
                raise ValueError("AgentSpec skills must be active and validated")
            package = self.loader.load(skill["package_path"])
            if package.content_hash != skill["content_hash"]:
                raise ValueError("AgentSpec skill package changed after validation")
            missing_tools = set(skill["manifest"]["tools"]) - set(spec.tools)
            missing_permissions = set(skill["permissions"]) - set(spec.permissions)
            if missing_tools:
                raise ValueError("AgentSpec omits a required skill tool")
            if missing_permissions:
                raise ValueError("AgentSpec omits a required skill permission")
            task_classes = set(skill["task_classes"])
            if task_classes and not task_classes & set(spec.task_scope):
                raise ValueError("AgentSpec skill is outside its task scope")
            skill_models = set(skill["models"])
            if (
                "any" not in skill_models
                and not skill_models & set(spec.model_policy.allowed_models)
            ):
                raise ValueError("AgentSpec model policy cannot run a skill")
            resolved.append(
                {
                    "reference": reference,
                    "skill_id": skill["id"],
                    "manifest_id": skill["manifest_id"],
                    "version": skill["version"],
                    "content_hash": skill["content_hash"],
                }
            )
        return tuple(resolved)

    def validate_dependencies(
        self, spec: AgentSpec
    ) -> tuple[dict[str, object], ...]:
        return self._resolve_skills(spec)

    def define(self, spec: AgentSpec) -> StoredAgentSpec:
        payload = spec.as_dict()
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        if len(encoded.encode("utf-8")) > 64_000:
            raise ValueError("AgentSpec exceeds 64 KB")
        digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
        existing = self.connection.execute(
            "SELECT content_hash FROM agent_specs WHERE id = ?", (spec.id,)
        ).fetchone()
        if existing is not None:
            if existing["content_hash"] == digest:
                return self.inspect(spec.id)
            raise ValueError("AgentSpec IDs are immutable")
        resolved = self._resolve_skills(spec)
        created_at = utc_now()
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO agent_specs(
                    id, role, objective, task_scope_json, memory_scope_json,
                    tools_json, skills_json, permissions_json, spec_json,
                    resolved_skills_json, content_hash, status, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'defined', ?)
                """,
                (
                    spec.id,
                    spec.role,
                    spec.objective,
                    json.dumps(spec.task_scope),
                    json.dumps(spec.memory_scope),
                    json.dumps(spec.tools),
                    json.dumps(spec.skills),
                    json.dumps(spec.permissions),
                    encoded,
                    json.dumps(resolved, sort_keys=True),
                    digest,
                    created_at,
                ),
            )
        return self.inspect(spec.id)

    def inspect(self, agent_id: str) -> StoredAgentSpec:
        row = self.connection.execute(
            "SELECT * FROM agent_specs WHERE id = ?", (agent_id,)
        ).fetchone()
        if row is None:
            raise KeyError(agent_id)
        return StoredAgentSpec(
            spec=AgentSpec.from_dict(json.loads(row["spec_json"])),
            resolved_skills=tuple(
                json.loads(row["resolved_skills_json"])
            ),
            content_hash=row["content_hash"],
            status=row["status"],
            created_at=row["created_at"],
        )

    def list(self) -> tuple[dict[str, object], ...]:
        rows = self.connection.execute(
            """
            SELECT id, role, objective, task_scope_json, memory_scope_json,
                   content_hash, status, created_at
            FROM agent_specs ORDER BY created_at, id
            """
        ).fetchall()
        return tuple(
            {
                "id": row["id"],
                "role": row["role"],
                "objective": row["objective"],
                "task_scope": json.loads(row["task_scope_json"]),
                "memory_scope": json.loads(row["memory_scope_json"]),
                "content_hash": row["content_hash"],
                "status": row["status"],
                "created_at": row["created_at"],
            }
            for row in rows
        )
