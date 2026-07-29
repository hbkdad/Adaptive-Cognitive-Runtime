from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from .secret_management import assert_secret_free


SCHEMA_VERSION = 1
ARCHITECTURE_DIMENSIONS = (
    "cohesion",
    "coupling",
    "interfaces",
    "data_ownership",
    "testability",
    "failure_modes",
    "provider_independence",
    "future_replacement",
)
RATINGS = ("sound", "concern", "unverified")
SEVERITIES = ("none", "low", "medium", "high", "critical")
EVIDENCE_STATUSES = ("verified", "supported", "speculative")
ABSTRACTION_VERDICTS = ("justified", "needless", "uncertain")
ABSTRACTION_ID = re.compile(r"^[a-z][a-z0-9._-]{1,127}$")


def _text(value: object, *, field: str, maximum: int = 2_000) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ValueError(f"{field} must be bounded non-empty text")
    normalized = value.strip()
    assert_secret_free(normalized, field)
    return normalized


def _text_list(
    value: object,
    *,
    field: str,
    minimum: int = 1,
    maximum: int = 16,
) -> tuple[str, ...]:
    if not isinstance(value, list) or not minimum <= len(value) <= maximum:
        raise ValueError(f"{field} must contain {minimum} to {maximum} items")
    result = tuple(_text(item, field=field, maximum=512) for item in value)
    if len(set(result)) != len(result):
        raise ValueError(f"{field} contains duplicates")
    return result


@dataclass(frozen=True)
class DimensionAssessment:
    dimension: str
    rating: str
    severity: str
    evidence_status: str
    evidence: tuple[str, ...]
    impact_path: tuple[str, ...]
    recommendation: str

    def __post_init__(self) -> None:
        if self.dimension not in ARCHITECTURE_DIMENSIONS:
            raise ValueError("architecture dimension is invalid")
        if self.rating not in RATINGS:
            raise ValueError("architecture rating is invalid")
        if self.severity not in SEVERITIES:
            raise ValueError("architecture severity is invalid")
        if self.evidence_status not in EVIDENCE_STATUSES:
            raise ValueError("architecture evidence_status is invalid")
        if self.rating == "sound" and (
            self.severity != "none" or self.impact_path
        ):
            raise ValueError("sound assessment cannot carry severity or impact")
        if self.rating == "unverified" and (
            self.severity != "none"
            or self.evidence_status != "speculative"
            or self.impact_path
        ):
            raise ValueError("unverified assessment has invalid certainty")
        if self.rating == "concern" and (
            self.severity == "none" or len(self.impact_path) < 2
        ):
            raise ValueError(
                "architecture concern requires severity and an impact path"
            )

    @property
    def rejecting(self) -> bool:
        return (
            self.rating == "concern"
            and self.evidence_status == "verified"
            and self.severity in {"high", "critical"}
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "dimension": self.dimension,
            "rating": self.rating,
            "severity": self.severity,
            "evidence_status": self.evidence_status,
            "evidence": list(self.evidence),
            "impact_path": list(self.impact_path),
            "recommendation": self.recommendation,
            "rejecting": self.rejecting,
        }

    @classmethod
    def from_dict(cls, payload: object) -> "DimensionAssessment":
        expected = {
            "dimension",
            "rating",
            "severity",
            "evidence_status",
            "evidence",
            "impact_path",
            "recommendation",
        }
        if not isinstance(payload, dict) or set(payload) != expected:
            raise ValueError("dimension assessment has an invalid shape")
        for field in ("dimension", "rating", "severity", "evidence_status"):
            if not isinstance(payload[field], str):
                raise ValueError(f"{field} must be text")
        return cls(
            dimension=payload["dimension"],
            rating=payload["rating"],
            severity=payload["severity"],
            evidence_status=payload["evidence_status"],
            evidence=_text_list(payload["evidence"], field="dimension evidence"),
            impact_path=_text_list(
                payload["impact_path"],
                field="dimension impact_path",
                minimum=0,
                maximum=8,
            ),
            recommendation=_text(
                payload["recommendation"], field="dimension recommendation"
            ),
        )


@dataclass(frozen=True)
class AbstractionAssessment:
    id: str
    verdict: str
    evidence_status: str
    purpose: str
    use_evidence: tuple[str, ...]
    complexity_cost: tuple[str, ...]
    simplification_path: tuple[str, ...]
    recommendation: str

    def __post_init__(self) -> None:
        if not ABSTRACTION_ID.fullmatch(self.id):
            raise ValueError("abstraction id is invalid")
        if self.verdict not in ABSTRACTION_VERDICTS:
            raise ValueError("abstraction verdict is invalid")
        if self.evidence_status not in EVIDENCE_STATUSES:
            raise ValueError("abstraction evidence_status is invalid")
        if self.verdict == "needless" and (
            self.evidence_status != "verified"
            or not self.complexity_cost
            or len(self.simplification_path) < 2
        ):
            raise ValueError(
                "needless abstraction requires verified cost and a "
                "multi-step simplification path"
            )
        if self.verdict == "justified" and not self.use_evidence:
            raise ValueError("justified abstraction requires current-use evidence")
        if self.verdict == "uncertain" and self.evidence_status == "verified":
            raise ValueError("uncertain abstraction cannot claim verified evidence")

    @property
    def rejecting(self) -> bool:
        return self.verdict == "needless"

    def as_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "verdict": self.verdict,
            "evidence_status": self.evidence_status,
            "purpose": self.purpose,
            "use_evidence": list(self.use_evidence),
            "complexity_cost": list(self.complexity_cost),
            "simplification_path": list(self.simplification_path),
            "recommendation": self.recommendation,
            "rejecting": self.rejecting,
        }

    @classmethod
    def from_dict(cls, payload: object) -> "AbstractionAssessment":
        expected = {
            "id",
            "verdict",
            "evidence_status",
            "purpose",
            "use_evidence",
            "complexity_cost",
            "simplification_path",
            "recommendation",
        }
        if not isinstance(payload, dict) or set(payload) != expected:
            raise ValueError("abstraction assessment has an invalid shape")
        for field in ("id", "verdict", "evidence_status"):
            if not isinstance(payload[field], str):
                raise ValueError(f"abstraction {field} must be text")
        return cls(
            id=payload["id"],
            verdict=payload["verdict"],
            evidence_status=payload["evidence_status"],
            purpose=_text(payload["purpose"], field="abstraction purpose"),
            use_evidence=_text_list(
                payload["use_evidence"],
                field="abstraction use_evidence",
                minimum=0,
            ),
            complexity_cost=_text_list(
                payload["complexity_cost"],
                field="abstraction complexity_cost",
                minimum=0,
            ),
            simplification_path=_text_list(
                payload["simplification_path"],
                field="abstraction simplification_path",
                minimum=0,
                maximum=8,
            ),
            recommendation=_text(
                payload["recommendation"], field="abstraction recommendation"
            ),
        )


@dataclass(frozen=True)
class ArchitectureReviewReport:
    change_ref: str
    dimensions: tuple[DimensionAssessment, ...]
    abstractions: tuple[AbstractionAssessment, ...]

    def __post_init__(self) -> None:
        if tuple(item.dimension for item in self.dimensions) != (
            ARCHITECTURE_DIMENSIONS
        ):
            raise ValueError(
                "dimensions must cover every architecture dimension in order"
            )
        abstraction_ids = tuple(item.id for item in self.abstractions)
        if len(self.abstractions) > 64:
            raise ValueError("abstractions exceeds 64 items")
        if len(set(abstraction_ids)) != len(abstraction_ids):
            raise ValueError("abstraction ids must be unique")

    @property
    def rejection_reasons(self) -> tuple[str, ...]:
        dimensions = (
            f"dimension:{item.dimension}" for item in self.dimensions
            if item.rejecting
        )
        abstractions = (
            f"abstraction:{item.id}" for item in self.abstractions
            if item.rejecting
        )
        return tuple((*dimensions, *abstractions))

    @property
    def verdict(self) -> str:
        return "reject" if self.rejection_reasons else "pass"

    def as_dict(self) -> dict[str, object]:
        return {
            "schema_version": SCHEMA_VERSION,
            "change_ref": self.change_ref,
            "dimensions": [item.as_dict() for item in self.dimensions],
            "abstractions": [item.as_dict() for item in self.abstractions],
            "verdict": self.verdict,
            "rejection_reasons": list(self.rejection_reasons),
        }

    @classmethod
    def from_dict(cls, payload: object) -> "ArchitectureReviewReport":
        expected = {
            "schema_version",
            "change_ref",
            "dimensions",
            "abstractions",
        }
        if not isinstance(payload, dict) or set(payload) != expected:
            raise ValueError("architecture review report has an invalid shape")
        if payload["schema_version"] != SCHEMA_VERSION:
            raise ValueError("unsupported architecture review schema_version")
        if not isinstance(payload["dimensions"], list):
            raise ValueError("dimensions must be a list")
        if not isinstance(payload["abstractions"], list):
            raise ValueError("abstractions must be a list")
        return cls(
            change_ref=_text(
                payload["change_ref"], field="change_ref", maximum=512
            ),
            dimensions=tuple(
                DimensionAssessment.from_dict(item)
                for item in payload["dimensions"]
            ),
            abstractions=tuple(
                AbstractionAssessment.from_dict(item)
                for item in payload["abstractions"]
            ),
        )


def validate_report(path: str | Path) -> ArchitectureReviewReport:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    return ArchitectureReviewReport.from_dict(payload)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate a bounded architecture-review report."
    )
    commands = parser.add_subparsers(dest="command", required=True)
    validate = commands.add_parser("validate")
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
    return 1 if report.rejection_reasons else 0


if __name__ == "__main__":
    raise SystemExit(main())
