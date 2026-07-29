from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal

from .economist import TaskComplexity, TokenEconomist
from .execution import Step, Task
from .providers.base import ModelCapabilities, ReasoningControl

Complexity = Literal["low", "medium", "high"]
RiskLevel = Literal["low", "elevated", "protected"]

CLASSIFIER_VERSION = "acr-reasoning-depth-v1.0.0"
BASELINE_POLICY_ID = "reasoning-budget-baseline-v1"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _hash(value: object) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _bounded_text(value: object, field: str, *, maximum: int = 128) -> str:
    text = str(value).strip()
    if not text or len(text) > maximum:
        raise ValueError(f"{field} must be 1..{maximum} characters")
    if not re.fullmatch(r"[A-Za-z0-9._:/-]+", text):
        raise ValueError(f"{field} contains unsupported characters")
    return text


BASELINE_CONFIG: dict[str, object] = {
    "classifier_version": CLASSIFIER_VERSION,
    "thresholds": {"low_max": 0, "high_min": 3},
    "profiles": {
        "low": {
            "planning_mode": "minimal",
            "model_tier": "small",
            "context_fraction_micros": 600_000,
            "verification_mode": "deterministic",
            "reasoning_effort": "low",
            "max_decomposition_depth": 0,
            "max_model_calls": 1,
        },
        "medium": {
            "planning_mode": "standard",
            "model_tier": "medium",
            "context_fraction_micros": 800_000,
            "verification_mode": "standard",
            "reasoning_effort": "medium",
            "max_decomposition_depth": 1,
            "max_model_calls": 1,
        },
        "high": {
            "planning_mode": "decomposed",
            "model_tier": "strong",
            "context_fraction_micros": 1_000_000,
            "verification_mode": "independent",
            "reasoning_effort": "high",
            "max_decomposition_depth": 3,
            "max_model_calls": 2,
        },
    },
    "invariants": [
        "caller_hints_cannot_lower_depth",
        "hard_resource_limits_unchanged",
        "permissions_and_scope_unchanged",
        "protected_risk_forces_high",
        "verification_is_never_disabled",
    ],
}


@dataclass(frozen=True)
class ReasoningBudgetRequest:
    task: str
    task_class: str = "general"
    requested_minimum: Complexity | None = None
    destructive: bool = False
    external_side_effects: bool = False
    high_stakes: bool = False
    handles_secrets: bool = False
    changes_permissions: bool = False
    privacy_sensitive: bool = False
    requires_tools: bool = False
    ambiguity: bool = False
    dependency_count: int = 0
    verification_required: bool = False

    def __post_init__(self) -> None:
        if not self.task.strip():
            raise ValueError("task cannot be empty")
        if len(self.task) > 100_000:
            raise ValueError("task exceeds the 100000 character classifier limit")
        _bounded_text(self.task_class, "task_class")
        if self.requested_minimum not in (None, "low", "medium", "high"):
            raise ValueError("requested_minimum must be low, medium, or high")
        if (
            isinstance(self.dependency_count, bool)
            or not isinstance(self.dependency_count, int)
            or self.dependency_count < 0
            or self.dependency_count > 10_000
        ):
            raise ValueError("dependency_count must be an integer from 0 to 10000")
        for field in (
            "destructive", "external_side_effects", "high_stakes",
            "handles_secrets", "changes_permissions", "privacy_sensitive",
            "requires_tools", "ambiguity", "verification_required",
        ):
            if not isinstance(getattr(self, field), bool):
                raise ValueError(f"{field} must be boolean")

    @classmethod
    def from_dict(cls, payload: object) -> "ReasoningBudgetRequest":
        if not isinstance(payload, dict):
            raise ValueError("reasoning request must be an object")
        allowed = {
            "task", "task_class", "requested_minimum", "destructive",
            "external_side_effects", "high_stakes", "handles_secrets",
            "changes_permissions", "privacy_sensitive", "requires_tools",
            "ambiguity", "dependency_count", "verification_required",
        }
        if "task" not in payload or set(payload) - allowed:
            raise ValueError("reasoning request has missing or unknown fields")
        return cls(**payload)


@dataclass(frozen=True)
class ReasoningOutcome:
    decision_id: str
    success: bool
    quality: float
    verification_passed: bool
    hard_violation: bool
    policy_conformant: bool
    input_tokens: int
    output_tokens: int
    reasoning_tokens: int | None
    latency_ms: int
    cost_microunits: int
    evidence: tuple[str, ...]

    def __post_init__(self) -> None:
        _bounded_text(self.decision_id, "decision_id", maximum=200)
        for field in (
            "success", "verification_passed", "hard_violation",
            "policy_conformant",
        ):
            if not isinstance(getattr(self, field), bool):
                raise ValueError(f"{field} must be boolean")
        if not 0 <= self.quality <= 1:
            raise ValueError("quality must be 0..1")
        for field in (
            "input_tokens", "output_tokens", "latency_ms", "cost_microunits"
        ):
            value = getattr(self, field)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{field} must be a non-negative integer")
        if self.reasoning_tokens is not None and (
            isinstance(self.reasoning_tokens, bool)
            or not isinstance(self.reasoning_tokens, int)
            or self.reasoning_tokens < 0
            or self.reasoning_tokens > self.output_tokens
        ):
            raise ValueError(
                "reasoning_tokens must be null or within inclusive output_tokens"
            )
        if not self.evidence or any(not str(item).strip() for item in self.evidence):
            raise ValueError("outcome evidence cannot be empty")

    @classmethod
    def from_dict(cls, payload: object) -> "ReasoningOutcome":
        if not isinstance(payload, dict):
            raise ValueError("reasoning outcome must be an object")
        required = {
            "decision_id", "success", "quality", "verification_passed",
            "hard_violation", "input_tokens", "output_tokens",
            "policy_conformant", "reasoning_tokens", "latency_ms",
            "cost_microunits", "evidence",
        }
        if set(payload) != required or not isinstance(payload["evidence"], list):
            raise ValueError(f"reasoning outcome requires exactly {sorted(required)}")
        return cls(
            **{**payload, "evidence": tuple(str(x) for x in payload["evidence"])}
        )


@dataclass(frozen=True)
class ReasoningDecision:
    id: str
    policy_id: str
    task_hash: str
    task_class: str
    score: int
    complexity: Complexity
    risk_level: RiskLevel
    features: dict[str, object]
    reasons: tuple[str, ...]
    planning_mode: str
    model_tier: str
    context_fraction_micros: int
    verification_mode: str
    reasoning_effort: str
    max_decomposition_depth: int
    max_model_calls: int
    provider_control_state: str = "advisory_only"
    provider_reasoning_mode: str = "provider_default"

    def as_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "policy_id": self.policy_id,
            "task_hash": self.task_hash,
            "task_class": self.task_class,
            "score": self.score,
            "complexity": self.complexity,
            "risk_level": self.risk_level,
            "features": self.features,
            "reasons": list(self.reasons),
            "planning_mode": self.planning_mode,
            "model_tier": self.model_tier,
            "context_fraction_micros": self.context_fraction_micros,
            "verification_mode": self.verification_mode,
            "reasoning_effort": self.reasoning_effort,
            "max_decomposition_depth": self.max_decomposition_depth,
            "max_model_calls": self.max_model_calls,
            "provider_control_state": self.provider_control_state,
            "provider_reasoning_mode": self.provider_reasoning_mode,
            "automatic_activation": False,
        }


class ReasoningDepthEngine:
    PROTECTED_PATTERNS = re.compile(
        r"\b(delete|destroy|drop\s+(?:table|database)|prod(?:uction)?|"
        r"transfer|payment|send\s+\$|dose|diagnos(?:e|is)|medical|legal|"
        r"credential|password|secret|api[\s_-]?key|rotate\s+(?:key|credential)|"
        r"grant\s+admin|permission|publish\s+customer|customer\s+list)\b",
        re.IGNORECASE,
    )
    AMBIGUOUS_ACTION = re.compile(
        r"^\s*(?:yes|ok(?:ay)?|sure|do it|proceed|go ahead)"
        r"(?:\s*,?\s*(?:do it|proceed|go ahead))?[.!]?\s*$",
        re.IGNORECASE,
    )
    MULTISTEP_PATTERNS = re.compile(
        r"\b(multi[- ]?step|research|investigate|compare|migrate|architect|"
        r"refactor|debug|diagnose|verify|audit|benchmark|deploy)\b",
        re.IGNORECASE,
    )
    ORDER = {"low": 0, "medium": 1, "high": 2}

    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection
        self.economist = TokenEconomist()
        self._bootstrap()

    def _bootstrap(self) -> None:
        policy_hash = _hash(BASELINE_CONFIG)
        with self.connection:
            self.connection.execute(
                """
                INSERT OR IGNORE INTO reasoning_budget_policies(
                    id, policy_hash, classifier_version, config_json,
                    status, created_at
                ) VALUES (?, ?, ?, ?, 'active', ?)
                """,
                (
                    BASELINE_POLICY_ID, policy_hash, CLASSIFIER_VERSION,
                    _canonical(BASELINE_CONFIG), _now(),
                ),
            )

    @staticmethod
    def _profile(complexity: Complexity) -> dict[str, object]:
        return dict(BASELINE_CONFIG["profiles"][complexity])

    @staticmethod
    def _provider_control(
        complexity: Complexity,
        effort: str,
        capabilities: ModelCapabilities | None,
    ) -> ReasoningControl:
        if capabilities is None:
            return ReasoningControl()
        if (
            "effort" in capabilities.reasoning_modes
            and effort in capabilities.reasoning_efforts
        ):
            return ReasoningControl(mode="effort", effort=effort)
        if complexity == "low" and "disabled" in capabilities.reasoning_modes:
            return ReasoningControl(mode="disabled")
        if complexity in ("medium", "high") and (
            "enabled" in capabilities.reasoning_modes
        ):
            return ReasoningControl(mode="enabled")
        return ReasoningControl()

    def decide(
        self,
        request: ReasoningBudgetRequest,
        *,
        provider_capabilities: ModelCapabilities | None = None,
    ) -> ReasoningDecision:
        baseline = self.economist.complexity(request.task).value
        explicit_protected = any(
            (
                request.destructive,
                request.high_stakes,
                request.handles_secrets,
                request.changes_permissions,
                request.privacy_sensitive,
            )
        )
        lexical_protected = bool(self.PROTECTED_PATTERNS.search(request.task))
        ambiguous_action = bool(self.AMBIGUOUS_ACTION.match(request.task))
        protected = explicit_protected or lexical_protected
        elevated = any(
            (
                request.external_side_effects,
                request.requires_tools,
                request.ambiguity,
                ambiguous_action,
                request.verification_required,
                request.dependency_count > 0,
            )
        )
        multistep = len(self.MULTISTEP_PATTERNS.findall(request.task))
        score = (
            {"low": 0, "medium": 1, "high": 3}[baseline]
            + min(2, multistep)
            + int(request.dependency_count > 0)
            + int(request.dependency_count > 2)
            + int(elevated)
        )
        if protected:
            complexity: Complexity = "high"
            risk: RiskLevel = "protected"
        elif score >= int(BASELINE_CONFIG["thresholds"]["high_min"]):
            complexity = "high"
            risk = "elevated" if elevated else "low"
        elif score <= int(BASELINE_CONFIG["thresholds"]["low_max"]) and not elevated:
            complexity = "low"
            risk = "low"
        else:
            complexity = "medium"
            risk = "elevated" if elevated else "low"
        if (
            request.requested_minimum is not None
            and self.ORDER[request.requested_minimum] > self.ORDER[complexity]
        ):
            complexity = request.requested_minimum
        reasons: list[str] = [f"economist_baseline:{baseline}"]
        if protected:
            reasons.append("protected_risk_floor")
        if elevated:
            reasons.append("structured_risk_floor")
        if ambiguous_action:
            reasons.append("context_dependent_action")
        if multistep:
            reasons.append("multistep_signal")
        if request.requested_minimum is not None:
            reasons.append("caller_minimum_applied_monotonically")
        features = {
            "economist_baseline": baseline,
            "estimated_task_tokens": self.economist.budget(
                request.task,
                requested_input_budget=64,
                task_importance=0.5,
            ).task_tokens,
            "protected_signal": protected,
            "elevated_signal": elevated,
            "ambiguous_action": ambiguous_action,
            "multistep_markers_capped": min(2, multistep),
            "dependency_bucket": min(3, request.dependency_count),
        }
        profile = self._profile(complexity)
        control = self._provider_control(
            complexity,
            str(profile["reasoning_effort"]),
            provider_capabilities,
        )
        decision = ReasoningDecision(
            id=str(uuid.uuid4()),
            policy_id=BASELINE_POLICY_ID,
            task_hash=_hash({"task": request.task}),
            task_class=request.task_class,
            score=score,
            complexity=complexity,
            risk_level=risk,
            features=features,
            reasons=tuple(reasons),
            planning_mode=str(profile["planning_mode"]),
            model_tier=str(profile["model_tier"]),
            context_fraction_micros=int(profile["context_fraction_micros"]),
            verification_mode=str(profile["verification_mode"]),
            reasoning_effort=str(profile["reasoning_effort"]),
            max_decomposition_depth=int(profile["max_decomposition_depth"]),
            max_model_calls=int(profile["max_model_calls"]),
            provider_control_state=(
                "validated"
                if control.mode != "provider_default"
                else "advisory_only"
            ),
            provider_reasoning_mode=control.mode,
        )
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO reasoning_budget_decisions(
                    id, policy_id, task_hash, task_class, classifier_version,
                    score, complexity, risk_level, features_json, reasons_json,
                    planning_mode, model_tier, context_fraction_micros,
                    verification_mode, reasoning_effort,
                    max_decomposition_depth, max_model_calls,
                    provider_control_state, provider_reasoning_mode, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    decision.id, decision.policy_id, decision.task_hash,
                    decision.task_class, CLASSIFIER_VERSION, decision.score,
                    decision.complexity, decision.risk_level,
                    _canonical(decision.features), _canonical(decision.reasons),
                    decision.planning_mode, decision.model_tier,
                    decision.context_fraction_micros,
                    decision.verification_mode, decision.reasoning_effort,
                    decision.max_decomposition_depth, decision.max_model_calls,
                    decision.provider_control_state,
                    decision.provider_reasoning_mode, _now(),
                ),
            )
        return decision

    def control_for(
        self,
        decision: ReasoningDecision,
        capabilities: ModelCapabilities,
    ) -> ReasoningControl:
        control = self._provider_control(
            decision.complexity, decision.reasoning_effort, capabilities
        )
        if control.mode != decision.provider_reasoning_mode:
            raise ValueError("Provider reasoning capabilities changed after decision")
        return control

    def inspect(self, decision_id: str) -> dict[str, object]:
        row = self.connection.execute(
            "SELECT * FROM reasoning_budget_decisions WHERE id=?",
            (decision_id,),
        ).fetchone()
        if row is None:
            raise LookupError(f"Unknown reasoning decision: {decision_id}")
        columns = [
            item[1] for item in self.connection.execute(
                "PRAGMA table_info(reasoning_budget_decisions)"
            )
        ]
        result = dict(zip(columns, row))
        result["features"] = json.loads(str(result.pop("features_json")))
        result["reasons"] = json.loads(str(result.pop("reasons_json")))
        result["automatic_activation"] = False
        return result

    def policy(self) -> dict[str, object]:
        row = self.connection.execute(
            """
            SELECT id, policy_hash, classifier_version, config_json, status,
                   created_at
            FROM reasoning_budget_policies WHERE status='active'
            """
        ).fetchone()
        if row is None:
            raise RuntimeError("No active reasoning budget policy")
        return {
            "id": row[0], "policy_hash": row[1],
            "classifier_version": row[2], "config": json.loads(row[3]),
            "status": row[4], "created_at": row[5],
            "automatic_activation": False,
        }

    def record_outcome(
        self, outcome: ReasoningOutcome, *, trusted_runtime: bool = False
    ) -> dict[str, object]:
        if self.connection.execute(
            "SELECT 1 FROM reasoning_budget_decisions WHERE id=?",
            (outcome.decision_id,),
        ).fetchone() is None:
            raise LookupError(f"Unknown reasoning decision: {outcome.decision_id}")
        outcome_id = str(uuid.uuid4())
        provenance = (
            "trusted_runtime" if trusted_runtime else "caller_supplied_unverified"
        )
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO reasoning_budget_outcomes(
                    id, decision_id, success, quality_micros,
                    verification_passed, hard_violation, policy_conformant,
                    input_tokens,
                    output_tokens, reasoning_tokens, latency_ms,
                    cost_microunits, evidence_hash, provenance, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    outcome_id, outcome.decision_id, int(outcome.success),
                    round(outcome.quality * 1_000_000),
                    int(outcome.verification_passed),
                    int(outcome.hard_violation),
                    int(outcome.policy_conformant), outcome.input_tokens,
                    outcome.output_tokens, outcome.reasoning_tokens,
                    outcome.latency_ms, outcome.cost_microunits,
                    _hash({"evidence": outcome.evidence}), provenance, _now(),
                ),
            )
        return {
            "id": outcome_id,
            "decision_id": outcome.decision_id,
            "provenance": provenance,
            "eligible_for_refinement": (
                trusted_runtime and outcome.policy_conformant
            ),
            "raw_reasoning_retained": False,
        }

    def refine(
        self, task_class: str, *, minimum_samples: int = 8
    ) -> dict[str, object]:
        task_class = _bounded_text(task_class, "task_class")
        if minimum_samples < 5 or minimum_samples > 10_000:
            raise ValueError("minimum_samples must be between 5 and 10000")
        rows = self.connection.execute(
            """
            SELECT d.score, d.complexity, o.success, o.quality_micros,
                   o.verification_passed, o.hard_violation,
                   o.input_tokens + o.output_tokens AS tokens,
                   o.policy_conformant
            FROM reasoning_budget_outcomes AS o
            JOIN reasoning_budget_decisions AS d ON d.id=o.decision_id
            WHERE d.task_class=? AND o.provenance='trusted_runtime'
              AND d.policy_id=?
            ORDER BY d.created_at, o.id
            """,
            (task_class, BASELINE_POLICY_ID),
        ).fetchall()
        eligible_rows = [row for row in rows if row[7]]
        proposed = dict(BASELINE_CONFIG["thresholds"])
        hard_violations = sum(int(row[5]) for row in rows)
        failures = [
            row for row in eligible_rows if not row[2] or not row[4]
        ]
        if failures:
            proposed["high_min"] = max(
                1, min(int(proposed["high_min"]), min(int(row[0]) for row in failures))
            )
        protected_counts = {
            level: sum(1 for row in eligible_rows if row[1] == level)
            for level in ("low", "medium", "high")
        }
        complete = (
            len(eligible_rows) >= minimum_samples
            and all(count >= 2 for count in protected_counts.values())
        )
        if hard_violations:
            status = "rejected"
            recommendation = "reject_candidate"
        else:
            status = "insufficient_evidence"
            recommendation = "collect_verified_paired_receipts"
        summary = {
            "trusted_receipts": len(rows),
            "trusted_samples": len(eligible_rows),
            "minimum_samples": minimum_samples,
            "cohort_counts": protected_counts,
            "hard_violations": hard_violations,
            "failed_or_unverified": len(failures),
            "coverage_complete": complete,
            "causal_paired_evidence": False,
            "reason": (
                "hard_violation"
                if hard_violations
                else "paired incumbent_candidate receipts are required"
            ),
        }
        evaluation_id = str(uuid.uuid4())
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO reasoning_budget_policy_evaluations(
                    id, policy_id, task_class, minimum_samples,
                    trusted_sample_count, status, recommendation,
                    proposed_thresholds_json, summary_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    evaluation_id, BASELINE_POLICY_ID, task_class,
                    minimum_samples, len(eligible_rows), status, recommendation,
                    _canonical(proposed), _canonical(summary), _now(),
                ),
            )
        return {
            "id": evaluation_id,
            "task_class": task_class,
            "status": status,
            "recommendation": recommendation,
            "proposed_thresholds": proposed,
            "summary": summary,
            "automatic_activation": False,
        }


class ReasoningBudgetPlanner:
    """Executes one bounded step whose instructions match the policy depth."""

    def __init__(self, decision: ReasoningDecision) -> None:
        self.decision = decision

    def plan(self, task: Task) -> tuple[Step, ...]:
        if self.decision.planning_mode == "minimal":
            instruction = task.objective
        elif self.decision.planning_mode == "standard":
            instruction = (
                "Make a short bounded plan, complete it, and verify the result: "
                f"{task.objective}"
            )
        else:
            instruction = (
                "Decompose this into bounded dependent parts, solve them, then "
                f"independently check the final result: {task.objective}"
            )
        return (
            Step(
                sequence=1,
                name=instruction,
                operation="execute",
                input_json=_canonical(
                    {
                        "objective_hash": _hash({"task": task.objective}),
                        "reasoning_decision_id": self.decision.id,
                        "planning_mode": self.decision.planning_mode,
                    }
                ),
            ),
        )
