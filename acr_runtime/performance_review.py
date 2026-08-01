from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from .bounded_validation import (
    bounded_text as _text,
    bounded_text_list as _text_list,
)


SCHEMA_VERSION = 1
PERFORMANCE_CATEGORIES = (
    "token_usage",
    "model_calls",
    "retrieval_volume",
    "database_queries",
    "tool_calls",
    "latency",
)
CATEGORY_UNITS = {
    "token_usage": "tokens",
    "model_calls": "calls",
    "retrieval_volume": "tokens",
    "database_queries": "queries",
    "tool_calls": "calls",
    "latency": "nanoseconds",
}
MEASUREMENT_STATUSES = (
    "measured_waste",
    "observed_overhead",
    "unmeasured",
)
MEASUREMENT_REF = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}$")
MAX_VALUE = 9_223_372_036_854_775_807


def _count(value: object, *, field: str) -> int:
    if (
        type(value) is not int
        or not 0 <= value <= MAX_VALUE
    ):
        raise ValueError(f"{field} must be a non-negative bounded integer")
    return value


@dataclass(frozen=True)
class PerformanceObservation:
    category: str
    status: str
    unit: str
    baseline_value: int | None
    candidate_value: int | None
    sample_count: int
    measurement_ref: str | None
    quality_gate_passed: bool
    security_gate_passed: bool
    evidence: tuple[str, ...]
    recommendation: str

    def __post_init__(self) -> None:
        if self.category not in PERFORMANCE_CATEGORIES:
            raise ValueError("performance category is invalid")
        if self.status not in MEASUREMENT_STATUSES:
            raise ValueError("measurement status is invalid")
        if self.unit != CATEGORY_UNITS[self.category]:
            raise ValueError("measurement unit does not match category")
        if (
            type(self.quality_gate_passed) is not bool
            or type(self.security_gate_passed) is not bool
        ):
            raise ValueError("comparison gates must be booleans")
        if self.status == "unmeasured":
            if (
                self.baseline_value is not None
                or self.candidate_value is not None
                or self.sample_count != 0
                or self.measurement_ref is not None
                or self.quality_gate_passed
                or self.security_gate_passed
            ):
                raise ValueError("unmeasured observation carries measurements")
            return
        if self.baseline_value is None or self.sample_count < 1:
            raise ValueError("measured observation requires a baseline and samples")
        if self.measurement_ref is None or not MEASUREMENT_REF.fullmatch(
            self.measurement_ref
        ):
            raise ValueError("measured observation requires a valid reference")
        if self.status == "observed_overhead":
            if self.candidate_value is not None:
                raise ValueError("observed overhead cannot claim a candidate result")
            if self.quality_gate_passed or self.security_gate_passed:
                raise ValueError("observed overhead cannot claim comparison gates")
            return
        if (
            self.candidate_value is None
            or self.sample_count < 3
            or self.candidate_value >= self.baseline_value
            or not self.quality_gate_passed
            or not self.security_gate_passed
        ):
            raise ValueError(
                "measured waste requires a lower paired candidate, at least "
                "three samples, and passing quality and security gates"
            )

    @property
    def waste_value(self) -> int:
        if self.status != "measured_waste":
            return 0
        assert self.baseline_value is not None
        assert self.candidate_value is not None
        return self.baseline_value - self.candidate_value

    @property
    def waste_ratio(self) -> float:
        if self.status != "measured_waste" or not self.baseline_value:
            return 0.0
        return self.waste_value / self.baseline_value

    @property
    def impact(self) -> str | None:
        if self.status != "measured_waste":
            return None
        if self.waste_ratio >= 0.25:
            return "high"
        if self.waste_ratio >= 0.10:
            return "medium"
        return "low"

    def opportunity(self) -> dict[str, object]:
        if self.status != "measured_waste":
            raise ValueError("only measured waste is an opportunity")
        return {
            "category": self.category,
            "impact": self.impact,
            "unit": self.unit,
            "baseline_value": self.baseline_value,
            "candidate_value": self.candidate_value,
            "waste_value": self.waste_value,
            "waste_percent": round(self.waste_ratio * 100, 4),
            "sample_count": self.sample_count,
            "measurement_ref": self.measurement_ref,
            "evidence": list(self.evidence),
            "recommendation": self.recommendation,
        }

    def as_dict(self) -> dict[str, object]:
        return {
            "category": self.category,
            "status": self.status,
            "unit": self.unit,
            "baseline_value": self.baseline_value,
            "candidate_value": self.candidate_value,
            "sample_count": self.sample_count,
            "measurement_ref": self.measurement_ref,
            "quality_gate_passed": self.quality_gate_passed,
            "security_gate_passed": self.security_gate_passed,
            "evidence": list(self.evidence),
            "recommendation": self.recommendation,
        }

    @classmethod
    def from_dict(cls, payload: object) -> "PerformanceObservation":
        expected = {
            "category",
            "status",
            "unit",
            "baseline_value",
            "candidate_value",
            "sample_count",
            "measurement_ref",
            "quality_gate_passed",
            "security_gate_passed",
            "evidence",
            "recommendation",
        }
        if not isinstance(payload, dict) or set(payload) != expected:
            raise ValueError("performance observation has an invalid shape")
        for field in ("category", "status", "unit"):
            if not isinstance(payload[field], str):
                raise ValueError(f"{field} must be text")
        for field in ("baseline_value", "candidate_value"):
            if payload[field] is not None:
                _count(payload[field], field=field)
        reference = payload["measurement_ref"]
        if reference is not None and not isinstance(reference, str):
            raise ValueError("measurement_ref must be text or null")
        return cls(
            category=payload["category"],
            status=payload["status"],
            unit=payload["unit"],
            baseline_value=payload["baseline_value"],
            candidate_value=payload["candidate_value"],
            sample_count=_count(payload["sample_count"], field="sample_count"),
            measurement_ref=reference,
            quality_gate_passed=payload["quality_gate_passed"],
            security_gate_passed=payload["security_gate_passed"],
            evidence=_text_list(payload["evidence"], field="evidence"),
            recommendation=_text(
                payload["recommendation"], field="recommendation"
            ),
        )


@dataclass(frozen=True)
class PerformanceReviewReport:
    change_ref: str
    observations: tuple[PerformanceObservation, ...]

    def __post_init__(self) -> None:
        categories = tuple(item.category for item in self.observations)
        if categories != PERFORMANCE_CATEGORIES:
            raise ValueError(
                "observations must cover every performance category in order"
            )

    @property
    def opportunities(self) -> tuple[dict[str, object], ...]:
        measured = (
            item for item in self.observations if item.status == "measured_waste"
        )
        return tuple(
            item.opportunity()
            for item in sorted(
                measured,
                key=lambda item: (
                    -item.waste_ratio,
                    PERFORMANCE_CATEGORIES.index(item.category),
                ),
            )
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "schema_version": SCHEMA_VERSION,
            "change_ref": self.change_ref,
            "observations": [item.as_dict() for item in self.observations],
            "opportunities": list(self.opportunities),
            "measured_waste_categories": [
                item["category"] for item in self.opportunities
            ],
        }

    @classmethod
    def from_dict(cls, payload: object) -> "PerformanceReviewReport":
        expected = {"schema_version", "change_ref", "observations"}
        if not isinstance(payload, dict) or set(payload) != expected:
            raise ValueError("performance review report has an invalid shape")
        if payload["schema_version"] != SCHEMA_VERSION:
            raise ValueError("unsupported performance review schema_version")
        observations = payload["observations"]
        if not isinstance(observations, list):
            raise ValueError("observations must be a list")
        return cls(
            change_ref=_text(
                payload["change_ref"], field="change_ref", maximum=512
            ),
            observations=tuple(
                PerformanceObservation.from_dict(item) for item in observations
            ),
        )


def validate_report(path: str | Path) -> PerformanceReviewReport:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    return PerformanceReviewReport.from_dict(payload)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate and rank a bounded performance-review report."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    validate = subparsers.add_parser("validate")
    validate.add_argument("report")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        report = validate_report(args.report)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"valid": False, "error": str(exc)}, sort_keys=True))
        return 2
    print(json.dumps({"valid": True, **report.as_dict()}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
