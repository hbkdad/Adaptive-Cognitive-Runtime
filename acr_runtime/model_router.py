from __future__ import annotations

import json
import math
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal

from .confidence_calibration import ConfidenceCalibration

RouteState = Literal["selected", "escalation_recommended", "completed", "exhausted"]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _bounded(value: object, field: str) -> float:
    number = float(value)
    if not 0.0 <= number <= 1.0:
        raise ValueError(f"{field} must be between 0 and 1")
    return number


def _nonempty(value: object, field: str) -> str:
    text = str(value).strip()
    if not text:
        raise ValueError(f"{field} cannot be empty")
    return text


def _wilson_lower(successes: float, trials: int, z: float) -> float:
    if trials <= 0:
        return 0.0
    proportion = successes / trials
    denominator = 1.0 + z * z / trials
    centre = proportion + z * z / (2.0 * trials)
    margin = z * math.sqrt(
        proportion * (1.0 - proportion) / trials
        + z * z / (4.0 * trials * trials)
    )
    return max(0.0, (centre - margin) / denominator)


@dataclass(frozen=True)
class ModelProfile:
    provider: str
    model: str
    context_capacity: int
    supports_tools: bool
    input_cost_per_million: float
    output_cost_per_million: float
    active: bool = True
    local: bool = False
    tier: Literal["small", "medium", "strong"] = "medium"

    def __post_init__(self) -> None:
        _nonempty(self.provider, "provider")
        _nonempty(self.model, "model")
        if self.context_capacity < 1:
            raise ValueError("context_capacity must be positive")
        if self.input_cost_per_million < 0 or self.output_cost_per_million < 0:
            raise ValueError("model costs cannot be negative")
        if self.tier not in ("small", "medium", "strong"):
            raise ValueError("model tier must be small, medium, or strong")

    @property
    def id(self) -> str:
        return f"{self.provider}:{self.model}"

    @classmethod
    def from_dict(cls, payload: object) -> "ModelProfile":
        if not isinstance(payload, dict):
            raise ValueError("Model profile must be an object")
        required = {
            "provider", "model", "context_capacity", "supports_tools",
            "input_cost_per_million", "output_cost_per_million",
        }
        if not required <= set(payload) or set(payload) - required - {
            "active", "local", "tier"
        }:
            raise ValueError(
                f"Model profile requires {sorted(required)} and optional "
                "active/local/tier"
            )
        if not isinstance(payload["supports_tools"], bool):
            raise ValueError("supports_tools must be a boolean")
        if "active" in payload and not isinstance(payload["active"], bool):
            raise ValueError("active must be a boolean")
        if "local" in payload and not isinstance(payload["local"], bool):
            raise ValueError("local must be a boolean")
        return cls(
            provider=_nonempty(payload["provider"], "provider"),
            model=_nonempty(payload["model"], "model"),
            context_capacity=int(payload["context_capacity"]),
            supports_tools=payload["supports_tools"],
            input_cost_per_million=float(payload["input_cost_per_million"]),
            output_cost_per_million=float(payload["output_cost_per_million"]),
            active=payload.get("active", True),
            local=payload.get("local", False),
            tier=str(payload.get("tier", "medium")),
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "id": self.id, "provider": self.provider, "model": self.model,
            "context_capacity": self.context_capacity,
            "supports_tools": self.supports_tools,
            "input_cost_per_million": self.input_cost_per_million,
            "output_cost_per_million": self.output_cost_per_million,
            "active": self.active,
            "local": self.local,
            "tier": self.tier,
        }


@dataclass(frozen=True)
class ModelOutcome:
    model_id: str
    task_class: str
    success: bool
    quality: float
    latency_ms: int
    input_tokens: int
    output_tokens: int
    tool_attempts: int
    tool_successes: int
    evidence: tuple[str, ...]
    input_cost: float = 0.0
    output_cost: float = 0.0

    def __post_init__(self) -> None:
        _nonempty(self.model_id, "model_id")
        _nonempty(self.task_class, "task_class")
        _bounded(self.quality, "quality")
        if min(self.latency_ms, self.input_tokens, self.output_tokens,
               self.tool_attempts, self.tool_successes) < 0:
            raise ValueError("Outcome counts cannot be negative")
        if self.tool_successes > self.tool_attempts:
            raise ValueError("tool_successes cannot exceed tool_attempts")
        if self.input_cost < 0 or self.output_cost < 0:
            raise ValueError("Outcome costs cannot be negative")
        if not self.evidence or any(not item.strip() for item in self.evidence):
            raise ValueError("Verified outcome evidence cannot be empty")

    @classmethod
    def from_dict(cls, payload: object) -> "ModelOutcome":
        if not isinstance(payload, dict):
            raise ValueError("Model outcome must be an object")
        fields = {
            "model_id", "task_class", "success", "quality", "latency_ms",
            "input_tokens", "output_tokens", "tool_attempts",
            "tool_successes", "evidence",
        }
        optional = {"input_cost", "output_cost"}
        if not fields <= set(payload) or set(payload) - fields - optional:
            raise ValueError(
                f"Model outcome requires {sorted(fields)} and optional costs"
            )
        if not isinstance(payload["success"], bool):
            raise ValueError("success must be a boolean")
        evidence = payload["evidence"]
        if not isinstance(evidence, list):
            raise ValueError("evidence must be a list")
        return cls(
            model_id=_nonempty(payload["model_id"], "model_id"),
            task_class=_nonempty(payload["task_class"], "task_class"),
            success=payload["success"], quality=_bounded(payload["quality"], "quality"),
            latency_ms=int(payload["latency_ms"]),
            input_tokens=int(payload["input_tokens"]),
            output_tokens=int(payload["output_tokens"]),
            tool_attempts=int(payload["tool_attempts"]),
            tool_successes=int(payload["tool_successes"]),
            evidence=tuple(str(item) for item in evidence),
            input_cost=float(payload.get("input_cost", 0.0)),
            output_cost=float(payload.get("output_cost", 0.0)),
        )


@dataclass(frozen=True)
class RouteRequest:
    task_class: str
    quality_threshold: float
    minimum_success_rate: float
    estimated_input_tokens: int
    estimated_output_tokens: int
    required_context: int
    requires_tools: bool = False
    minimum_tool_reliability: float = 0.0
    minimum_samples: int = 3
    confidence_z: float = 1.0
    attempt_confidence_threshold: float = 0.7

    def __post_init__(self) -> None:
        _nonempty(self.task_class, "task_class")
        for field in ("quality_threshold", "minimum_success_rate",
                      "minimum_tool_reliability", "attempt_confidence_threshold"):
            _bounded(getattr(self, field), field)
        if min(self.estimated_input_tokens, self.estimated_output_tokens,
               self.required_context) < 0:
            raise ValueError("Token and context estimates cannot be negative")
        if self.minimum_samples < 1:
            raise ValueError("minimum_samples must be positive")
        if self.confidence_z < 0:
            raise ValueError("confidence_z cannot be negative")

    @classmethod
    def from_dict(cls, payload: object) -> "RouteRequest":
        if not isinstance(payload, dict):
            raise ValueError("Route request must be an object")
        required = {
            "task_class", "quality_threshold", "minimum_success_rate",
            "estimated_input_tokens", "estimated_output_tokens", "required_context",
        }
        optional = {
            "requires_tools", "minimum_tool_reliability", "minimum_samples",
            "confidence_z", "attempt_confidence_threshold",
        }
        if not required <= set(payload) or set(payload) - required - optional:
            raise ValueError(f"Route request requires {sorted(required)}")
        if "requires_tools" in payload and not isinstance(
            payload["requires_tools"], bool
        ):
            raise ValueError("requires_tools must be a boolean")
        return cls(
            task_class=_nonempty(payload["task_class"], "task_class"),
            quality_threshold=float(payload["quality_threshold"]),
            minimum_success_rate=float(payload["minimum_success_rate"]),
            estimated_input_tokens=int(payload["estimated_input_tokens"]),
            estimated_output_tokens=int(payload["estimated_output_tokens"]),
            required_context=int(payload["required_context"]),
            requires_tools=payload.get("requires_tools", False),
            minimum_tool_reliability=float(
                payload.get("minimum_tool_reliability", 0.0)
            ),
            minimum_samples=int(payload.get("minimum_samples", 3)),
            confidence_z=float(payload.get("confidence_z", 1.0)),
            attempt_confidence_threshold=float(
                payload.get("attempt_confidence_threshold", 0.7)
            ),
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "task_class": self.task_class,
            "quality_threshold": self.quality_threshold,
            "minimum_success_rate": self.minimum_success_rate,
            "estimated_input_tokens": self.estimated_input_tokens,
            "estimated_output_tokens": self.estimated_output_tokens,
            "required_context": self.required_context,
            "requires_tools": self.requires_tools,
            "minimum_tool_reliability": self.minimum_tool_reliability,
            "minimum_samples": self.minimum_samples,
            "confidence_z": self.confidence_z,
            "attempt_confidence_threshold": self.attempt_confidence_threshold,
        }


@dataclass(frozen=True)
class RouteAttempt:
    model_id: str
    verification_passed: bool
    confidence: float
    quality: float
    latency_ms: int
    input_tokens: int
    output_tokens: int
    tool_attempts: int
    tool_successes: int
    evidence: tuple[str, ...]
    input_cost: float = 0.0
    output_cost: float = 0.0

    def __post_init__(self) -> None:
        _nonempty(self.model_id, "model_id")
        _bounded(self.confidence, "confidence")
        _bounded(self.quality, "quality")
        if min(self.latency_ms, self.input_tokens, self.output_tokens,
               self.tool_attempts, self.tool_successes) < 0:
            raise ValueError("Attempt counts cannot be negative")
        if self.tool_successes > self.tool_attempts:
            raise ValueError("tool_successes cannot exceed tool_attempts")
        if self.input_cost < 0 or self.output_cost < 0:
            raise ValueError("Attempt costs cannot be negative")
        if not self.evidence or any(not item.strip() for item in self.evidence):
            raise ValueError("Attempt evidence cannot be empty")

    @classmethod
    def from_dict(cls, payload: object) -> "RouteAttempt":
        if not isinstance(payload, dict):
            raise ValueError("Route attempt must be an object")
        fields = {
            "model_id", "verification_passed", "confidence", "quality",
            "latency_ms", "input_tokens", "output_tokens", "tool_attempts",
            "tool_successes", "evidence",
        }
        optional = {"input_cost", "output_cost"}
        if not fields <= set(payload) or set(payload) - fields - optional:
            raise ValueError(
                f"Route attempt requires {sorted(fields)} and optional costs"
            )
        if not isinstance(payload["verification_passed"], bool):
            raise ValueError("verification_passed must be a boolean")
        if not isinstance(payload["evidence"], list):
            raise ValueError("evidence must be a list")
        return cls(
            model_id=_nonempty(payload["model_id"], "model_id"),
            verification_passed=payload["verification_passed"],
            confidence=_bounded(payload["confidence"], "confidence"),
            quality=_bounded(payload["quality"], "quality"),
            latency_ms=int(payload["latency_ms"]),
            input_tokens=int(payload["input_tokens"]),
            output_tokens=int(payload["output_tokens"]),
            tool_attempts=int(payload["tool_attempts"]),
            tool_successes=int(payload["tool_successes"]),
            evidence=tuple(str(item) for item in payload["evidence"]),
            input_cost=float(payload.get("input_cost", 0.0)),
            output_cost=float(payload.get("output_cost", 0.0)),
        )


@dataclass(frozen=True)
class ModelRoute:
    id: str
    request: dict[str, object]
    candidates: tuple[dict[str, object], ...]
    selected_model_id: str | None
    escalation_model_id: str | None
    state: RouteState
    escalation_improved: bool | None
    attempts: tuple[dict[str, object], ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "id": self.id, "request": self.request,
            "candidates": list(self.candidates),
            "selected_model_id": self.selected_model_id,
            "escalation_model_id": self.escalation_model_id,
            "state": self.state,
            "escalation_improved": self.escalation_improved,
            "attempts": list(self.attempts),
        }


class ModelRouter:
    """Evidence-backed cheapest-qualified routing with one measured escalation."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def register(self, profile: ModelProfile) -> ModelProfile:
        self.connection.execute(
            """
            INSERT INTO model_profiles (
                id, provider, model, context_capacity, supports_tools,
                input_cost_per_million, output_cost_per_million, active,
                created_at, local, tier
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                context_capacity=excluded.context_capacity,
                supports_tools=excluded.supports_tools,
                input_cost_per_million=excluded.input_cost_per_million,
                output_cost_per_million=excluded.output_cost_per_million,
                active=excluded.active,
                local=excluded.local,
                tier=excluded.tier
            """,
            (profile.id, profile.provider, profile.model, profile.context_capacity,
             profile.supports_tools, profile.input_cost_per_million,
             profile.output_cost_per_million, profile.active, _utc_now(),
             profile.local, profile.tier),
        )
        self.connection.commit()
        return profile

    def record_outcome(self, outcome: ModelOutcome, *, commit: bool = True) -> str:
        if self.connection.execute(
            "SELECT 1 FROM model_profiles WHERE id = ?", (outcome.model_id,)
        ).fetchone() is None:
            raise LookupError(f"Unknown model profile: {outcome.model_id}")
        outcome_id = str(uuid.uuid4())
        self.connection.execute(
            """
            INSERT INTO model_outcomes (
                id, model_id, task_class, success, quality, latency_ms,
                input_tokens, output_tokens, tool_attempts, tool_successes,
                input_cost, output_cost, evidence_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (outcome_id, outcome.model_id, outcome.task_class, outcome.success,
             outcome.quality, outcome.latency_ms, outcome.input_tokens,
             outcome.output_tokens, outcome.tool_attempts, outcome.tool_successes,
             outcome.input_cost, outcome.output_cost,
             json.dumps(outcome.evidence), _utc_now()),
        )
        if commit:
            self.connection.commit()
        return outcome_id

    def _candidates(
        self,
        request: RouteRequest,
        *,
        allowed_model_ids: frozenset[str] | None = None,
    ) -> list[dict[str, object]]:
        profiles = self.connection.execute(
            "SELECT * FROM model_profiles WHERE active = 1 ORDER BY id"
        ).fetchall()
        candidates: list[dict[str, object]] = []
        for row in profiles:
            if allowed_model_ids is not None and row["id"] not in allowed_model_ids:
                continue
            outcomes = self.connection.execute(
                "SELECT * FROM model_outcomes WHERE model_id=? AND task_class=?",
                (row["id"], request.task_class),
            ).fetchall()
            samples = len(outcomes)
            successes = sum(int(item["success"]) for item in outcomes)
            quality_sum = sum(float(item["quality"]) for item in outcomes)
            tool_attempts = sum(int(item["tool_attempts"]) for item in outcomes)
            tool_successes = sum(int(item["tool_successes"]) for item in outcomes)
            quality_lcb = _wilson_lower(quality_sum, samples, request.confidence_z)
            success_lcb = _wilson_lower(successes, samples, request.confidence_z)
            tool_lcb = _wilson_lower(
                tool_successes, tool_attempts, request.confidence_z
            )
            reasons: list[str] = []
            if int(row["context_capacity"]) < request.required_context:
                reasons.append("insufficient_context")
            if request.requires_tools and not bool(row["supports_tools"]):
                reasons.append("tools_unsupported")
            if samples < request.minimum_samples:
                reasons.append("insufficient_verified_samples")
            if quality_lcb < request.quality_threshold:
                reasons.append("quality_below_threshold")
            if success_lcb < request.minimum_success_rate:
                reasons.append("success_below_threshold")
            if request.requires_tools and tool_lcb < request.minimum_tool_reliability:
                reasons.append("tool_reliability_below_threshold")
            expected_cost = (
                request.estimated_input_tokens
                * float(row["input_cost_per_million"])
                + request.estimated_output_tokens
                * float(row["output_cost_per_million"])
            ) / 1_000_000.0
            candidates.append({
                "model_id": row["id"], "eligible": not reasons,
                "rejection_reasons": reasons, "samples": samples,
                "success_rate": successes / samples if samples else 0.0,
                "success_lower_bound": success_lcb,
                "mean_quality": quality_sum / samples if samples else 0.0,
                "quality_lower_bound": quality_lcb,
                "average_latency_ms": (
                    sum(int(item["latency_ms"]) for item in outcomes) / samples
                    if samples else None
                ),
                "average_input_tokens": (
                    sum(int(item["input_tokens"]) for item in outcomes) / samples
                    if samples else None
                ),
                "average_output_tokens": (
                    sum(int(item["output_tokens"]) for item in outcomes) / samples
                    if samples else None
                ),
                "average_input_cost": (
                    sum(float(item["input_cost"]) for item in outcomes) / samples
                    if samples else None
                ),
                "average_output_cost": (
                    sum(float(item["output_cost"]) for item in outcomes) / samples
                    if samples else None
                ),
                "tool_reliability": (
                    tool_successes / tool_attempts if tool_attempts else None
                ),
                "tool_reliability_lower_bound": tool_lcb,
                "expected_cost": expected_cost,
            })
        return candidates

    def route(
        self,
        request: RouteRequest,
        *,
        allowed_model_ids: frozenset[str] | None = None,
        preferred_model_ids: frozenset[str] = frozenset(),
        commit: bool = True,
    ) -> ModelRoute:
        candidates = self._candidates(
            request, allowed_model_ids=allowed_model_ids
        )
        eligible = [item for item in candidates if item["eligible"]]
        eligible.sort(key=lambda item: (
            float(item["expected_cost"]),
            -float(item["quality_lower_bound"]),
            float(item["average_latency_ms"] or math.inf),
            str(item["model_id"]),
        ))
        preferred = [
            item for item in eligible if item["model_id"] in preferred_model_ids
        ]
        selection_pool = preferred or eligible
        selected = (
            str(selection_pool[0]["model_id"]) if selection_pool else None
        )
        state: RouteState = "selected" if selected else "exhausted"
        route_id = str(uuid.uuid4())
        now = _utc_now()
        self.connection.execute(
            """
            INSERT INTO model_routes (
                id, task_class, request_json, candidates_json, selected_model_id,
                state, escalation_improved, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, NULL, ?, ?)
            """,
            (route_id, request.task_class, json.dumps(request.as_dict()),
             json.dumps(candidates), selected, state, now, now),
        )
        if commit:
            self.connection.commit()
        return self.get(route_id)

    def record_attempt(self, route_id: str, attempt: RouteAttempt) -> ModelRoute:
        route = self.get(route_id)
        if route.state not in ("selected", "escalation_recommended"):
            raise ValueError(f"Route does not accept another attempt in state {route.state}")
        expected = (
            route.selected_model_id if not route.attempts else route.escalation_model_id
        )
        if attempt.model_id != expected:
            raise ValueError(f"Attempt must use recommended model {expected}")
        request = RouteRequest.from_dict(route.request)
        sequence = len(route.attempts) + 1
        passed = (
            attempt.verification_passed
            and attempt.confidence >= request.attempt_confidence_threshold
            and attempt.quality >= request.quality_threshold
        )
        escalation_model: str | None = route.escalation_model_id
        improved: bool | None = None
        state: RouteState
        if sequence == 1 and passed:
            state = "completed"
        elif sequence == 1:
            selected_metrics = next(
                item for item in route.candidates
                if item["model_id"] == route.selected_model_id
            )
            stronger = [
                item for item in route.candidates
                if item["eligible"]
                and item["model_id"] != route.selected_model_id
                and float(item["quality_lower_bound"])
                > float(selected_metrics["quality_lower_bound"])
            ]
            stronger.sort(key=lambda item: (
                float(item["expected_cost"]),
                -float(item["quality_lower_bound"]),
            ))
            if stronger:
                escalation_model = str(stronger[0]["model_id"])
                state = "escalation_recommended"
            else:
                state = "exhausted"
        else:
            first = route.attempts[0]
            improved = (
                (attempt.verification_passed and not first["verification_passed"])
                or attempt.quality > float(first["quality"])
                or attempt.confidence > float(first["confidence"])
            )
            state = "completed" if passed else "exhausted"

        outcome = ModelOutcome(
            model_id=attempt.model_id, task_class=request.task_class,
            success=passed, quality=attempt.quality, latency_ms=attempt.latency_ms,
            input_tokens=attempt.input_tokens, output_tokens=attempt.output_tokens,
            tool_attempts=attempt.tool_attempts,
            tool_successes=attempt.tool_successes, evidence=attempt.evidence,
            input_cost=attempt.input_cost, output_cost=attempt.output_cost,
        )
        try:
            self.connection.execute("BEGIN IMMEDIATE")
            outcome_id = self.record_outcome(outcome, commit=False)
            attempt_id = str(uuid.uuid4())
            self.connection.execute(
                """
                INSERT INTO model_route_attempts (
                    id, route_id, sequence, model_id, verification_passed,
                    confidence, quality, latency_ms, input_tokens, output_tokens,
                    tool_attempts, tool_successes, input_cost, output_cost,
                    evidence_json, outcome_id, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (attempt_id, route_id, sequence, attempt.model_id,
                 attempt.verification_passed, attempt.confidence, attempt.quality,
                 attempt.latency_ms, attempt.input_tokens, attempt.output_tokens,
                 attempt.tool_attempts, attempt.tool_successes,
                 attempt.input_cost, attempt.output_cost,
                 json.dumps(attempt.evidence), outcome_id, _utc_now()),
            )
            ConfidenceCalibration(self.connection).observe(
                "routing",
                attempt_id,
                attempt.confidence,
                attempt.verification_passed,
                group_key=request.task_class,
                evidence=("model_route_attempt_verification",),
                commit=False,
            )
            self.connection.execute(
                """
                UPDATE model_routes
                SET state=?, escalation_model_id=?, escalation_improved=?,
                    updated_at=?
                WHERE id=?
                """,
                (state, escalation_model, improved, _utc_now(), route_id),
            )
            from .utility_governance import UtilityGovernor

            UtilityGovernor(self.connection).observe_model_attempt(attempt_id)
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise
        return self.get(route_id)

    def get(self, route_id: str) -> ModelRoute:
        row = self.connection.execute(
            "SELECT * FROM model_routes WHERE id = ?", (route_id,)
        ).fetchone()
        if row is None:
            raise LookupError(f"Unknown model route: {route_id}")
        attempts = self.connection.execute(
            """
            SELECT sequence, model_id, verification_passed, confidence, quality,
                   latency_ms, input_tokens, output_tokens, tool_attempts,
                   tool_successes, input_cost, output_cost, evidence_json,
                   outcome_id, created_at
            FROM model_route_attempts WHERE route_id=? ORDER BY sequence
            """,
            (route_id,),
        ).fetchall()
        return ModelRoute(
            id=row["id"], request=json.loads(row["request_json"]),
            candidates=tuple(json.loads(row["candidates_json"])),
            selected_model_id=row["selected_model_id"],
            escalation_model_id=row["escalation_model_id"], state=row["state"],
            escalation_improved=(
                None if row["escalation_improved"] is None
                else bool(row["escalation_improved"])
            ),
            attempts=tuple({
                **dict(item),
                "verification_passed": bool(item["verification_passed"]),
                "evidence": json.loads(item["evidence_json"]),
            } for item in attempts),
        )

    def profiles(self) -> list[dict[str, Any]]:
        return [
            dict(row) for row in self.connection.execute(
                "SELECT * FROM model_profiles ORDER BY provider, model"
            ).fetchall()
        ]
