from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from .secret_management import assert_secret_free


SCHEMA_VERSION = 1
EVIDENCE_SOURCES = (
    "recent_tasks", "failures", "expensive_workflows", "manual_procedures",
    "missing_tools", "user_interventions", "benchmark_weaknesses",
    "token_waste_reports",
)
EVIDENCE_STATUSES = ("verified", "supported", "speculative")
COMPLEXITIES = ("low", "medium", "high")
SECURITY_RISKS = ("low", "medium", "high", "critical")
BENCHMARK_CHECKS = ("baseline", "candidate", "quality", "security")
PROPOSAL_ID = re.compile(r"^[a-z][a-z0-9._-]{1,127}$")
METRIC_ID = re.compile(r"^[a-z][a-z0-9._-]{1,127}$")
EVIDENCE_REF = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}$")
MAX_VALUE = 9_223_372_036_854_775_807


def _text(value: object, *, field: str, maximum: int = 2_000) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ValueError(f"{field} must be bounded non-empty text")
    normalized = value.strip()
    assert_secret_free(normalized, field)
    return normalized


def _text_list(
    value: object, *, field: str, minimum: int = 1, maximum: int = 16
) -> tuple[str, ...]:
    if not isinstance(value, list) or not minimum <= len(value) <= maximum:
        raise ValueError(f"{field} must contain {minimum} to {maximum} items")
    result = tuple(_text(item, field=field, maximum=512) for item in value)
    if len(set(result)) != len(result):
        raise ValueError(f"{field} contains duplicates")
    return result


def _integer(value: object, *, field: str, minimum: int = 0) -> int:
    if type(value) is not int or not minimum <= value <= MAX_VALUE:
        raise ValueError(f"{field} must be a bounded integer >= {minimum}")
    return value


@dataclass(frozen=True)
class CurrentCost:
    tokens: int
    model_calls: int
    retrieval_tokens: int
    database_queries: int
    tool_calls: int
    latency_ms: int
    manual_minutes: int
    failures: int

    def as_dict(self) -> dict[str, int]:
        return {
            "tokens": self.tokens, "model_calls": self.model_calls,
            "retrieval_tokens": self.retrieval_tokens,
            "database_queries": self.database_queries,
            "tool_calls": self.tool_calls, "latency_ms": self.latency_ms,
            "manual_minutes": self.manual_minutes, "failures": self.failures,
        }

    @property
    def measured(self) -> bool:
        return any(self.as_dict().values())

    @classmethod
    def from_dict(cls, payload: object) -> "CurrentCost":
        expected = {
            "tokens", "model_calls", "retrieval_tokens", "database_queries",
            "tool_calls", "latency_ms", "manual_minutes", "failures",
        }
        if not isinstance(payload, dict) or set(payload) != expected:
            raise ValueError("current_cost has an invalid shape")
        return cls(**{
            field: _integer(payload[field], field=f"current_cost.{field}")
            for field in expected
        })


@dataclass(frozen=True)
class BenefitTarget:
    metric: str
    unit: str
    direction: str
    baseline: int
    target: int

    def __post_init__(self) -> None:
        if not METRIC_ID.fullmatch(self.metric) or not METRIC_ID.fullmatch(self.unit):
            raise ValueError("benefit metric or unit is invalid")
        if self.direction not in {"reduce", "increase"}:
            raise ValueError("benefit direction is invalid")
        if self.direction == "reduce" and not self.target < self.baseline:
            raise ValueError("reduction target must be below baseline")
        if self.direction == "increase" and not self.target > self.baseline:
            raise ValueError("increase target must be above baseline")

    @property
    def improvement_value(self) -> int:
        return (
            self.baseline - self.target
            if self.direction == "reduce"
            else self.target - self.baseline
        )

    @property
    def improvement_percent(self) -> float | None:
        if self.baseline == 0:
            return None
        return round(self.improvement_value / self.baseline * 100, 4)

    def as_dict(self) -> dict[str, object]:
        return {
            "metric": self.metric, "unit": self.unit,
            "direction": self.direction, "baseline": self.baseline,
            "target": self.target,
        }

    @classmethod
    def from_dict(cls, payload: object) -> "BenefitTarget":
        expected = {"metric", "unit", "direction", "baseline", "target"}
        if not isinstance(payload, dict) or set(payload) != expected:
            raise ValueError("expected_benefit has an invalid shape")
        for field in ("metric", "unit", "direction"):
            if not isinstance(payload[field], str):
                raise ValueError(f"expected_benefit.{field} must be text")
        return cls(
            metric=payload["metric"], unit=payload["unit"],
            direction=payload["direction"],
            baseline=_integer(payload["baseline"], field="benefit baseline"),
            target=_integer(payload["target"], field="benefit target"),
        )


@dataclass(frozen=True)
class CapabilityProposal:
    id: str
    problem: str
    evidence_status: str
    evidence_sources: tuple[str, ...]
    evidence_refs: tuple[str, ...]
    frequency: int
    distinct_tasks: int
    window_days: int
    current_cost: CurrentCost
    proposed_capability: str
    expected_benefit: BenefitTarget
    implementation_complexity: str
    complexity_evidence: tuple[str, ...]
    security_risk: str
    security_evidence: tuple[str, ...]
    attack_path: tuple[str, ...]
    mitigations: tuple[str, ...]
    benchmark_checks: tuple[str, ...]
    benchmark_plan: tuple[str, ...]

    def __post_init__(self) -> None:
        if not PROPOSAL_ID.fullmatch(self.id):
            raise ValueError("proposal id is invalid")
        if self.evidence_status not in EVIDENCE_STATUSES:
            raise ValueError("proposal evidence_status is invalid")
        if (
            not self.evidence_sources
            or len(set(self.evidence_sources)) != len(self.evidence_sources)
            or any(item not in EVIDENCE_SOURCES for item in self.evidence_sources)
        ):
            raise ValueError("proposal evidence_sources are invalid")
        if len(self.evidence_refs) < len(self.evidence_sources) or any(
            not EVIDENCE_REF.fullmatch(item) for item in self.evidence_refs
        ):
            raise ValueError("proposal evidence_refs are incomplete or invalid")
        if self.distinct_tasks > self.frequency:
            raise ValueError("distinct_tasks cannot exceed frequency")
        if self.implementation_complexity not in COMPLEXITIES:
            raise ValueError("implementation complexity is invalid")
        if self.security_risk not in SECURITY_RISKS:
            raise ValueError("security risk is invalid")
        if self.security_risk in {"medium", "high", "critical"} and (
            len(self.attack_path) < 2 or not self.mitigations
        ):
            raise ValueError(
                "elevated security risk requires attack path and mitigations"
            )
        if self.benchmark_checks != BENCHMARK_CHECKS:
            raise ValueError("benchmark_checks must contain all fixed checks")

    @property
    def decision(self) -> str:
        if (
            self.evidence_status == "speculative"
            or self.frequency < 2
            or self.distinct_tasks < 2
            or not self.current_cost.measured
        ):
            return "REJECT"
        if (
            self.evidence_status == "supported"
            or self.frequency < 3
            or self.distinct_tasks < 3
            or len(self.evidence_sources) < 2
            or self.implementation_complexity == "high"
            or self.security_risk in {"high", "critical"}
        ):
            return "DEFER"
        return "BUILD"

    def as_dict(self) -> dict[str, object]:
        return {
            "id": self.id, "problem": self.problem,
            "evidence_status": self.evidence_status,
            "evidence_sources": list(self.evidence_sources),
            "evidence_refs": list(self.evidence_refs),
            "frequency": self.frequency, "distinct_tasks": self.distinct_tasks,
            "window_days": self.window_days,
            "current_cost": self.current_cost.as_dict(),
            "proposed_capability": self.proposed_capability,
            "expected_benefit": self.expected_benefit.as_dict(),
            "expected_improvement_percent": self.expected_benefit.improvement_percent,
            "implementation_complexity": self.implementation_complexity,
            "complexity_evidence": list(self.complexity_evidence),
            "security_risk": self.security_risk,
            "security_evidence": list(self.security_evidence),
            "attack_path": list(self.attack_path),
            "mitigations": list(self.mitigations),
            "benchmark_checks": list(self.benchmark_checks),
            "benchmark_plan": list(self.benchmark_plan),
            "decision": self.decision,
        }

    @classmethod
    def from_dict(cls, payload: object) -> "CapabilityProposal":
        expected = {
            "id", "problem", "evidence_status", "evidence_sources",
            "evidence_refs", "frequency", "distinct_tasks", "window_days",
            "current_cost", "proposed_capability", "expected_benefit",
            "implementation_complexity", "complexity_evidence",
            "security_risk", "security_evidence", "attack_path", "mitigations",
            "benchmark_checks", "benchmark_plan",
        }
        if not isinstance(payload, dict) or set(payload) != expected:
            raise ValueError("capability proposal has an invalid shape")
        for field in (
            "id", "problem", "evidence_status", "proposed_capability",
            "implementation_complexity", "security_risk",
        ):
            if not isinstance(payload[field], str):
                raise ValueError(f"proposal {field} must be text")
        checks = payload["benchmark_checks"]
        return cls(
            id=payload["id"], problem=_text(payload["problem"], field="problem"),
            evidence_status=payload["evidence_status"],
            evidence_sources=_text_list(
                payload["evidence_sources"], field="evidence_sources"
            ),
            evidence_refs=_text_list(
                payload["evidence_refs"], field="evidence_refs", maximum=32
            ),
            frequency=_integer(payload["frequency"], field="frequency"),
            distinct_tasks=_integer(
                payload["distinct_tasks"], field="distinct_tasks"
            ),
            window_days=_integer(
                payload["window_days"], field="window_days", minimum=1
            ),
            current_cost=CurrentCost.from_dict(payload["current_cost"]),
            proposed_capability=_text(
                payload["proposed_capability"], field="proposed capability"
            ),
            expected_benefit=BenefitTarget.from_dict(payload["expected_benefit"]),
            implementation_complexity=payload["implementation_complexity"],
            complexity_evidence=_text_list(
                payload["complexity_evidence"], field="complexity_evidence"
            ),
            security_risk=payload["security_risk"],
            security_evidence=_text_list(
                payload["security_evidence"], field="security_evidence"
            ),
            attack_path=_text_list(
                payload["attack_path"], field="attack_path", minimum=0, maximum=8
            ),
            mitigations=_text_list(
                payload["mitigations"], field="mitigations", minimum=0
            ),
            benchmark_checks=tuple(checks)
            if isinstance(checks, list)
            and all(isinstance(item, str) for item in checks)
            else (),
            benchmark_plan=_text_list(
                payload["benchmark_plan"], field="benchmark_plan", minimum=4
            ),
        )


@dataclass(frozen=True)
class ExpansionDiscoveryReport:
    change_ref: str
    proposals: tuple[CapabilityProposal, ...]

    def __post_init__(self) -> None:
        if len(self.proposals) > 128:
            raise ValueError("proposals exceeds 128 items")
        ids = tuple(item.id for item in self.proposals)
        if len(set(ids)) != len(ids):
            raise ValueError("proposal ids must be unique")

    @property
    def ranked(self) -> tuple[CapabilityProposal, ...]:
        order = {"BUILD": 0, "DEFER": 1, "REJECT": 2}
        return tuple(sorted(
            self.proposals,
            key=lambda item: (
                order[item.decision], -item.distinct_tasks, -item.frequency,
                item.id,
            ),
        ))

    def as_dict(self) -> dict[str, object]:
        return {
            "schema_version": SCHEMA_VERSION, "change_ref": self.change_ref,
            "proposals": [
                {"rank": rank, **item.as_dict()}
                for rank, item in enumerate(self.ranked, start=1)
            ],
        }

    @classmethod
    def from_dict(cls, payload: object) -> "ExpansionDiscoveryReport":
        expected = {"schema_version", "change_ref", "proposals"}
        if not isinstance(payload, dict) or set(payload) != expected:
            raise ValueError("expansion discovery report has an invalid shape")
        if payload["schema_version"] != SCHEMA_VERSION:
            raise ValueError("unsupported expansion discovery schema_version")
        if not isinstance(payload["proposals"], list):
            raise ValueError("proposals must be a list")
        return cls(
            change_ref=_text(payload["change_ref"], field="change_ref", maximum=512),
            proposals=tuple(
                CapabilityProposal.from_dict(item) for item in payload["proposals"]
            ),
        )


def validate_report(path: str | Path) -> ExpansionDiscoveryReport:
    return ExpansionDiscoveryReport.from_dict(
        json.loads(Path(path).read_text(encoding="utf-8"))
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate and rank evidence-backed capability gaps."
    )
    commands = parser.add_subparsers(dest="command", required=True)
    validate = commands.add_parser("validate")
    validate.add_argument("report")
    args = parser.parse_args(argv)
    try:
        report = validate_report(args.report)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"valid": False, "error": str(exc)}, sort_keys=True))
        return 2
    print(json.dumps({"valid": True, **report.as_dict()}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
