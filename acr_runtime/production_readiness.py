from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Sequence

from .bounded_validation import bounded_text, bounded_text_list


SCHEMA_VERSION = 1
TARGET_PROFILE = "networked_production"
READINESS_DIMENSIONS = (
    "correctness",
    "security",
    "reliability",
    "observability",
    "performance",
    "backup",
    "migration",
    "rollback",
    "data_privacy",
    "provider_failures",
    "rate_limiting",
    "cost_controls",
    "human_override",
    "documentation",
)
CRITICAL_DIMENSIONS = frozenset(
    {
        "correctness",
        "security",
        "reliability",
        "backup",
        "migration",
        "rollback",
        "data_privacy",
        "provider_failures",
        "rate_limiting",
    }
)
EVIDENCE_LEVELS = (
    "specified",
    "deterministic",
    "rehearsed",
    "production_observed",
)
EVIDENCE_STATUSES = ("passed", "failed", "unavailable")
REFERENCE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}$")
COMMIT_SHA = re.compile(r"^[0-9a-f]{40}$")


def _timestamp(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        raise ValueError("assessed_at must be an ISO-8601 timestamp") from None
    if parsed.tzinfo is None:
        raise ValueError("assessed_at must include a timezone")
    return parsed


@dataclass(frozen=True)
class ReadinessEvidence:
    level: str
    status: str
    reference: str | None
    detail: str

    def __post_init__(self) -> None:
        if self.level not in EVIDENCE_LEVELS:
            raise ValueError("readiness evidence level is invalid")
        if self.status not in EVIDENCE_STATUSES:
            raise ValueError("readiness evidence status is invalid")
        bounded_text(self.detail, field="readiness evidence detail", maximum=1_000)
        if self.status == "unavailable":
            if self.reference is not None:
                raise ValueError("unavailable evidence cannot carry a reference")
        elif (
            self.reference is None
            or not REFERENCE.fullmatch(self.reference)
        ):
            raise ValueError("completed evidence requires a valid reference")

    def as_dict(self) -> dict[str, object]:
        return {
            "level": self.level,
            "status": self.status,
            "reference": self.reference,
            "detail": self.detail,
        }

    @classmethod
    def from_dict(cls, payload: object) -> "ReadinessEvidence":
        expected = {"level", "status", "reference", "detail"}
        if not isinstance(payload, dict) or set(payload) != expected:
            raise ValueError("readiness evidence has an invalid shape")
        for field in ("level", "status", "detail"):
            if not isinstance(payload[field], str):
                raise ValueError(f"{field} must be text")
        reference = payload["reference"]
        if reference is not None and not isinstance(reference, str):
            raise ValueError("reference must be text or null")
        return cls(
            level=payload["level"],
            status=payload["status"],
            reference=reference,
            detail=payload["detail"],
        )


@dataclass(frozen=True)
class ReadinessDimension:
    dimension: str
    evidence: tuple[ReadinessEvidence, ...]
    deficiencies: tuple[str, ...]
    recommendation: str

    def __post_init__(self) -> None:
        if self.dimension not in READINESS_DIMENSIONS:
            raise ValueError("readiness dimension is invalid")
        if tuple(item.level for item in self.evidence) != EVIDENCE_LEVELS:
            raise ValueError("evidence must cover every readiness level in order")
        gap_seen = False
        for item in self.evidence:
            if item.status != "passed":
                gap_seen = True
            elif gap_seen:
                raise ValueError("readiness evidence cannot skip a level")
        if self.score < len(EVIDENCE_LEVELS) and not self.deficiencies:
            raise ValueError("incomplete readiness requires deficiencies")
        if self.score == len(EVIDENCE_LEVELS) and self.deficiencies:
            raise ValueError("complete readiness cannot carry deficiencies")
        bounded_text_list(
            list(self.deficiencies),
            field="readiness deficiencies",
            minimum=0,
            maximum=16,
            item_maximum=1_000,
        )
        bounded_text(
            self.recommendation,
            field="readiness recommendation",
            maximum=2_000,
        )

    @property
    def score(self) -> int:
        score = 0
        for item in self.evidence:
            if item.status != "passed":
                break
            score += 1
        return score

    @property
    def critical(self) -> bool:
        return self.dimension in CRITICAL_DIMENSIONS

    def as_dict(self) -> dict[str, object]:
        return {
            "dimension": self.dimension,
            "critical": self.critical,
            "score": self.score,
            "evidence": [item.as_dict() for item in self.evidence],
            "deficiencies": list(self.deficiencies),
            "recommendation": self.recommendation,
        }

    @classmethod
    def from_dict(cls, payload: object) -> "ReadinessDimension":
        expected = {
            "dimension",
            "evidence",
            "deficiencies",
            "recommendation",
        }
        if not isinstance(payload, dict) or set(payload) != expected:
            raise ValueError("readiness dimension has an invalid shape")
        if not isinstance(payload["dimension"], str):
            raise ValueError("dimension must be text")
        if not isinstance(payload["evidence"], list):
            raise ValueError("evidence must be a list")
        if not isinstance(payload["deficiencies"], list):
            raise ValueError("deficiencies must be a list")
        return cls(
            dimension=payload["dimension"],
            evidence=tuple(
                ReadinessEvidence.from_dict(item)
                for item in payload["evidence"]
            ),
            deficiencies=bounded_text_list(
                payload["deficiencies"],
                field="readiness deficiencies",
                minimum=0,
                maximum=16,
                item_maximum=1_000,
            ),
            recommendation=bounded_text(
                payload["recommendation"],
                field="readiness recommendation",
                maximum=2_000,
            ),
        )


@dataclass(frozen=True)
class ProductionReadinessReport:
    target_profile: str
    commit_sha: str
    assessed_at: str
    dimensions: tuple[ReadinessDimension, ...]

    def __post_init__(self) -> None:
        if self.target_profile != TARGET_PROFILE:
            raise ValueError("target_profile must be networked_production")
        if not COMMIT_SHA.fullmatch(self.commit_sha):
            raise ValueError("commit_sha must be 40 lowercase hex characters")
        bounded_text(self.assessed_at, field="assessed_at", maximum=64)
        _timestamp(self.assessed_at)
        if tuple(item.dimension for item in self.dimensions) != (
            READINESS_DIMENSIONS
        ):
            raise ValueError("dimensions must cover every readiness area in order")

    @property
    def total_score(self) -> int:
        return sum(item.score for item in self.dimensions)

    @property
    def maximum_score(self) -> int:
        return len(READINESS_DIMENSIONS) * len(EVIDENCE_LEVELS)

    @property
    def score_percent(self) -> float:
        return round(self.total_score / self.maximum_score * 100, 2)

    @property
    def blockers(self) -> tuple[str, ...]:
        blockers = [
            f"{item.dimension}:score_{item.score}"
            for item in self.dimensions
            if item.critical and item.score < len(EVIDENCE_LEVELS)
        ]
        blockers.extend(
            f"{item.dimension}:missing"
            for item in self.dimensions
            if not item.critical and item.score == 0
        )
        return tuple(blockers)

    @property
    def production_ready(self) -> bool:
        return all(
            item.score == len(EVIDENCE_LEVELS)
            for item in self.dimensions
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "schema_version": SCHEMA_VERSION,
            "target_profile": self.target_profile,
            "commit_sha": self.commit_sha,
            "assessed_at": self.assessed_at,
            "dimensions": [item.as_dict() for item in self.dimensions],
            "total_score": self.total_score,
            "maximum_score": self.maximum_score,
            "score_percent": self.score_percent,
            "production_ready": self.production_ready,
            "blockers": list(self.blockers),
        }

    @classmethod
    def from_dict(cls, payload: object) -> "ProductionReadinessReport":
        expected = {
            "schema_version",
            "target_profile",
            "commit_sha",
            "assessed_at",
            "dimensions",
        }
        if not isinstance(payload, dict) or set(payload) != expected:
            raise ValueError("production readiness report has an invalid shape")
        if (
            not isinstance(payload["schema_version"], int)
            or isinstance(payload["schema_version"], bool)
            or payload["schema_version"] != SCHEMA_VERSION
        ):
            raise ValueError("unsupported production readiness schema_version")
        for field in ("target_profile", "commit_sha", "assessed_at"):
            if not isinstance(payload[field], str):
                raise ValueError(f"{field} must be text")
        if not isinstance(payload["dimensions"], list):
            raise ValueError("dimensions must be a list")
        return cls(
            target_profile=payload["target_profile"],
            commit_sha=payload["commit_sha"],
            assessed_at=payload["assessed_at"],
            dimensions=tuple(
                ReadinessDimension.from_dict(item)
                for item in payload["dimensions"]
            ),
        )


def validate_report(path: str | Path) -> ProductionReadinessReport:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    return ProductionReadinessReport.from_dict(payload)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Validate a complete production-readiness assessment without "
            "changing runtime or release state."
        )
    )
    parser.add_argument("report")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        report = validate_report(args.report)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"valid": False, "error": str(exc)}, sort_keys=True))
        return 2
    print(json.dumps({"valid": True, **report.as_dict()}, indent=2))
    return 0 if report.production_ready else 1


if __name__ == "__main__":
    raise SystemExit(main())
