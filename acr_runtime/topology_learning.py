from __future__ import annotations

import hashlib
import json
import math
import sqlite3
import uuid
from dataclasses import asdict, dataclass

from .agent_factory import AgentFactory, AgentFactoryRequest
from .agent_spec import _strict_strings
from .memory import utc_now


@dataclass(frozen=True)
class TopologyOutcomeCreate:
    plan_id: str
    models_used: tuple[str, ...]
    skills_used: tuple[str, ...]
    tokens: int
    latency_ms: int
    quality: float
    success: bool
    verification_passed: bool
    verification_evidence: tuple[str, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.plan_id, str) or not self.plan_id.strip():
            raise ValueError("plan_id is required")
        for name, values, required in (
            ("models_used", self.models_used, True),
            ("skills_used", self.skills_used, False),
            ("verification_evidence", self.verification_evidence, True),
        ):
            if type(values) is not tuple:
                raise ValueError(f"{name} must be an immutable tuple")
            if required and not values:
                raise ValueError(f"{name} is required")
            if len(set(values)) != len(values):
                raise ValueError(f"{name} contains duplicates")
            if any(
                not isinstance(value, str)
                or not value.strip()
                or len(value) > 512
                for value in values
            ):
                raise ValueError(f"{name} contains invalid text")
        if type(self.tokens) is not int or self.tokens < 0:
            raise ValueError("tokens must be a non-negative integer")
        if type(self.latency_ms) is not int or self.latency_ms < 0:
            raise ValueError("latency_ms must be a non-negative integer")
        if (
            isinstance(self.quality, bool)
            or not isinstance(self.quality, (int, float))
            or not math.isfinite(self.quality)
            or not 0 <= self.quality <= 1
        ):
            raise ValueError("quality must be finite and between 0 and 1")
        if type(self.success) is not bool:
            raise ValueError("success must be boolean")
        if type(self.verification_passed) is not bool:
            raise ValueError("verification_passed must be boolean")
    @classmethod
    def from_dict(cls, payload: dict[str, object]) -> "TopologyOutcomeCreate":
        expected = {
            "plan_id",
            "models_used",
            "skills_used",
            "tokens",
            "latency_ms",
            "quality",
            "success",
            "verification_passed",
            "verification_evidence",
        }
        if not isinstance(payload, dict) or set(payload) != expected:
            raise ValueError("topology outcome has an invalid shape")
        if not isinstance(payload["plan_id"], str):
            raise ValueError("plan_id must be text")
        return cls(
            plan_id=payload["plan_id"],
            models_used=_strict_strings(
                payload["models_used"],
                field="models_used",
                nonempty=True,
                identifiers=False,
            ),
            skills_used=_strict_strings(
                payload["skills_used"],
                field="skills_used",
                identifiers=False,
            ),
            tokens=payload["tokens"],
            latency_ms=payload["latency_ms"],
            quality=payload["quality"],
            success=payload["success"],
            verification_passed=payload["verification_passed"],
            verification_evidence=_strict_strings(
                payload["verification_evidence"],
                field="verification_evidence",
                nonempty=True,
                identifiers=False,
            ),
        )


@dataclass(frozen=True)
class TopologyOutcome:
    id: str
    plan_id: str
    task_class: str
    topology: str
    structure_hash: str
    worker_count: int
    models: tuple[str, ...]
    skills: tuple[str, ...]
    parallelism: float
    tokens: int
    latency_ms: int
    quality: float
    success: bool
    verification_passed: bool
    verification_evidence: tuple[str, ...]
    created_at: str

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class TopologyRecipe:
    id: str
    task_class: str
    topology: str
    structure_hash: str
    recipe: dict[str, object]
    worker_count: int
    models: tuple[str, ...]
    skills: tuple[str, ...]
    parallelism: float
    created_at: str

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class TopologyRecommendationCandidate:
    recipe: TopologyRecipe
    trials: int
    successes: int
    verified_successes: int
    success_rate: float
    average_quality: float
    average_tokens: float
    average_latency_ms: float
    score: float
    eligible: bool
    rejection_reasons: tuple[str, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            **asdict(self),
            "recipe": self.recipe.as_dict(),
        }


@dataclass(frozen=True)
class TopologyRecommendation:
    task_class: str
    available: bool
    selected_recipe_id: str | None
    reason: str
    candidates: tuple[TopologyRecommendationCandidate, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            **asdict(self),
            "candidates": [item.as_dict() for item in self.candidates],
        }


class TopologyLearner:
    """Append-only outcomes and conservative, advisory recipe retrieval."""

    MIN_TRIALS = 3
    MIN_VERIFIED_SUCCESSES = 2
    MIN_SUCCESS_RATE = 2 / 3
    MIN_AVERAGE_QUALITY = 0.70

    def __init__(
        self, connection: sqlite3.Connection, factory: AgentFactory
    ) -> None:
        self.connection = connection
        self.factory = factory

    @staticmethod
    def _structure(
        plan,
        models_used: tuple[str, ...],
        skills_used: tuple[str, ...],
    ) -> tuple[dict[str, object], str]:
        workers = [
            {
                "role": worker.spec.role,
                "responsibility": (
                    worker.responsibility
                    if worker.responsibility
                    in {"primary", "specialist", "critic", "synthesizer"}
                    else "workstream"
                ),
                "model_policy": asdict(worker.spec.model_policy),
                "skills": list(worker.spec.skills),
                "communication_mode": worker.spec.communication.mode,
                "max_messages": worker.spec.communication.max_messages,
            }
            for worker in plan.workers
        ]
        recipe = {
            "topology": plan.selected_topology,
            "models_used": list(models_used),
            "skills_used": list(skills_used),
            "workers": workers,
        }
        encoded = json.dumps(
            recipe, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        return recipe, hashlib.sha256(encoded).hexdigest()

    @staticmethod
    def _dependencies(plan) -> tuple[tuple[str, ...], tuple[str, ...]]:
        models = tuple(
            sorted(
                {
                    model
                    for worker in plan.workers
                    for model in worker.spec.model_policy.allowed_models
                }
            )
        )
        skills = tuple(
            sorted(
                {
                    skill
                    for worker in plan.workers
                    for skill in worker.spec.skills
                }
            )
        )
        return models, skills

    def record(self, create: TopologyOutcomeCreate) -> TopologyOutcome:
        plan = self.factory.load(create.plan_id)
        allowed_models, assigned_skills = self._dependencies(plan)
        if not set(create.models_used) <= set(allowed_models):
            raise ValueError("used model is outside the plan allowlist")
        if not set(create.skills_used) <= set(assigned_skills):
            raise ValueError("used skill is outside the plan")
        models = tuple(sorted(create.models_used))
        skills = tuple(sorted(create.skills_used))
        recipe, structure_hash = self._structure(plan, models, skills)
        now = utc_now()
        outcome_id = str(uuid.uuid4())
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO agent_topology_outcomes(
                    id, plan_id, task_class, topology, structure_hash,
                    worker_count, models_json, skills_json, parallelism,
                    tokens, latency_ms, quality, success,
                    verification_passed, verification_evidence_json,
                    created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    outcome_id,
                    plan.id,
                    plan.request.task_class,
                    plan.selected_topology,
                    structure_hash,
                    plan.worker_count,
                    json.dumps(models),
                    json.dumps(skills),
                    plan.selected_estimate.parallelism_benefit,
                    create.tokens,
                    create.latency_ms,
                    create.quality,
                    int(create.success),
                    int(create.verification_passed),
                    json.dumps(create.verification_evidence),
                    now,
                ),
            )
            if create.success and create.verification_passed:
                self.connection.execute(
                    """
                    INSERT INTO agent_topology_recipes(
                        id, task_class, topology, structure_hash, recipe_json,
                        worker_count, models_json, skills_json, parallelism,
                        created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(task_class, structure_hash) DO NOTHING
                    """,
                    (
                        str(uuid.uuid4()),
                        plan.request.task_class,
                        plan.selected_topology,
                        structure_hash,
                        json.dumps(recipe, sort_keys=True),
                        plan.worker_count,
                        json.dumps(models),
                        json.dumps(skills),
                        plan.selected_estimate.parallelism_benefit,
                        now,
                    ),
                )
        return self.outcome(outcome_id)

    def outcome(self, outcome_id: str) -> TopologyOutcome:
        row = self.connection.execute(
            "SELECT * FROM agent_topology_outcomes WHERE id = ?",
            (outcome_id,),
        ).fetchone()
        if row is None:
            raise KeyError(outcome_id)
        return TopologyOutcome(
            id=row["id"],
            plan_id=row["plan_id"],
            task_class=row["task_class"],
            topology=row["topology"],
            structure_hash=row["structure_hash"],
            worker_count=row["worker_count"],
            models=tuple(json.loads(row["models_json"])),
            skills=tuple(json.loads(row["skills_json"])),
            parallelism=row["parallelism"],
            tokens=row["tokens"],
            latency_ms=row["latency_ms"],
            quality=row["quality"],
            success=bool(row["success"]),
            verification_passed=bool(row["verification_passed"]),
            verification_evidence=tuple(
                json.loads(row["verification_evidence_json"])
            ),
            created_at=row["created_at"],
        )

    def recipes(
        self, *, task_class: str | None = None
    ) -> tuple[TopologyRecipe, ...]:
        if task_class is None:
            rows = self.connection.execute(
                """
                SELECT * FROM agent_topology_recipes
                ORDER BY task_class, created_at, id
                """
            ).fetchall()
        else:
            rows = self.connection.execute(
                """
                SELECT * FROM agent_topology_recipes
                WHERE task_class = ? ORDER BY created_at, id
                """,
                (task_class,),
            ).fetchall()
        return tuple(self._recipe(row) for row in rows)

    @staticmethod
    def _recipe(row: sqlite3.Row) -> TopologyRecipe:
        return TopologyRecipe(
            id=row["id"],
            task_class=row["task_class"],
            topology=row["topology"],
            structure_hash=row["structure_hash"],
            recipe=json.loads(row["recipe_json"]),
            worker_count=row["worker_count"],
            models=tuple(json.loads(row["models_json"])),
            skills=tuple(json.loads(row["skills_json"])),
            parallelism=row["parallelism"],
            created_at=row["created_at"],
        )

    def recommend(
        self, request: AgentFactoryRequest
    ) -> TopologyRecommendation:
        rows = self.connection.execute(
            """
            SELECT r.*, COUNT(o.id) AS trials,
                   SUM(o.success) AS successes,
                   SUM(o.success * o.verification_passed) AS verified_successes,
                   AVG(o.quality) AS average_quality,
                   AVG(o.tokens) AS average_tokens,
                   AVG(o.latency_ms) AS average_latency_ms
            FROM agent_topology_recipes r
            JOIN agent_topology_outcomes o
              ON o.task_class = r.task_class
             AND o.structure_hash = r.structure_hash
             AND o.verification_passed = 1
            WHERE r.task_class = ?
            GROUP BY r.id
            ORDER BY r.created_at, r.id
            """,
            (request.task_class,),
        ).fetchall()
        candidates: list[TopologyRecommendationCandidate] = []
        for row in rows:
            recipe = self._recipe(row)
            trials = int(row["trials"])
            successes = int(row["successes"])
            verified = int(row["verified_successes"])
            success_rate = successes / trials
            quality = float(row["average_quality"])
            tokens = float(row["average_tokens"])
            latency = float(row["average_latency_ms"])
            reasons: list[str] = []
            if trials < self.MIN_TRIALS:
                reasons.append("insufficient_trials")
            if verified < self.MIN_VERIFIED_SUCCESSES:
                reasons.append("insufficient_verified_successes")
            if success_rate < self.MIN_SUCCESS_RATE:
                reasons.append("low_success_rate")
            if quality < self.MIN_AVERAGE_QUALITY:
                reasons.append("low_quality")
            if recipe.worker_count > request.max_agents:
                reasons.append("agent_limit")
            if tokens > request.token_budget:
                reasons.append("token_budget")
            if latency > request.time_budget * 1_000:
                reasons.append("time_budget")
            if not set(recipe.models) <= set(
                request.model_policy.allowed_models
            ):
                reasons.append("model_mismatch")
            if not set(recipe.skills) <= set(request.skills):
                reasons.append("skill_mismatch")
            score = (
                0.55 * quality
                + 0.25 * success_rate
                + 0.10 * (1 - min(1.0, tokens / request.token_budget))
                + 0.10
                * (
                    1
                    - min(
                        1.0,
                        latency / (request.time_budget * 1_000),
                    )
                )
            )
            candidates.append(
                TopologyRecommendationCandidate(
                    recipe=recipe,
                    trials=trials,
                    successes=successes,
                    verified_successes=verified,
                    success_rate=round(success_rate, 6),
                    average_quality=round(quality, 6),
                    average_tokens=round(tokens, 3),
                    average_latency_ms=round(latency, 3),
                    score=round(score, 6),
                    eligible=not reasons,
                    rejection_reasons=tuple(reasons),
                )
            )
        eligible = [item for item in candidates if item.eligible]
        selected = (
            max(
                eligible,
                key=lambda item: (
                    item.score,
                    -item.recipe.worker_count,
                    item.recipe.id,
                ),
            )
            if eligible
            else None
        )
        return TopologyRecommendation(
            task_class=request.task_class,
            available=selected is not None,
            selected_recipe_id=(
                selected.recipe.id if selected is not None else None
            ),
            reason=(
                "historical_recipe_available"
                if selected is not None
                else "insufficient_compatible_evidence"
            ),
            candidates=tuple(candidates),
        )
