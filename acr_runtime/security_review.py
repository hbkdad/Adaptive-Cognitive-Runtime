from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from .bounded_validation import bounded_text as _bounded_text


SCHEMA_VERSION = 1
SECURITY_CATEGORIES = (
    "trust_boundaries",
    "permission_escalation",
    "injection_risk",
    "unsafe_deserialization",
    "secret_exposure",
    "filesystem_traversal",
    "network_access",
    "shell_execution",
    "sql_injection",
    "memory_poisoning",
    "skill_poisoning",
)
SEVERITIES = ("low", "medium", "high", "critical")
EVIDENCE_STATUSES = ("verified", "supported", "speculative")
FINDING_ID = re.compile(r"^[a-z][a-z0-9._-]{1,127}$")


def _bounded_text_list(
    value: object,
    *,
    field: str,
    minimum: int = 0,
    maximum: int = 16,
) -> tuple[str, ...]:
    if not isinstance(value, list) or not minimum <= len(value) <= maximum:
        raise ValueError(f"{field} must contain {minimum} to {maximum} items")
    result = tuple(
        _bounded_text(item, field=field, maximum=512) for item in value
    )
    if len(set(result)) != len(result):
        raise ValueError(f"{field} contains duplicates")
    return result


@dataclass(frozen=True)
class SecurityFinding:
    id: str
    category: str
    severity: str
    evidence_status: str
    title: str
    affected_component: str
    evidence: tuple[str, ...]
    attack_path: tuple[str, ...]
    recommendation: str

    def __post_init__(self) -> None:
        if not FINDING_ID.fullmatch(self.id):
            raise ValueError("finding id is invalid")
        if self.category not in SECURITY_CATEGORIES:
            raise ValueError("finding category is invalid")
        if self.severity not in SEVERITIES:
            raise ValueError("finding severity is invalid")
        if self.evidence_status not in EVIDENCE_STATUSES:
            raise ValueError("finding evidence_status is invalid")

    @property
    def blocking(self) -> bool:
        return (
            self.evidence_status == "verified"
            and self.severity in {"high", "critical"}
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "category": self.category,
            "severity": self.severity,
            "evidence_status": self.evidence_status,
            "title": self.title,
            "affected_component": self.affected_component,
            "evidence": list(self.evidence),
            "attack_path": list(self.attack_path),
            "recommendation": self.recommendation,
            "blocking": self.blocking,
        }

    @classmethod
    def from_dict(cls, payload: object) -> "SecurityFinding":
        expected = {
            "id",
            "category",
            "severity",
            "evidence_status",
            "title",
            "affected_component",
            "evidence",
            "attack_path",
            "recommendation",
        }
        if not isinstance(payload, dict) or set(payload) != expected:
            raise ValueError("finding has an invalid shape")
        for field in ("id", "category", "severity", "evidence_status"):
            if not isinstance(payload[field], str):
                raise ValueError(f"finding {field} must be text")
        return cls(
            id=payload["id"],
            category=payload["category"],
            severity=payload["severity"],
            evidence_status=payload["evidence_status"],
            title=_bounded_text(payload["title"], field="finding title"),
            affected_component=_bounded_text(
                payload["affected_component"], field="affected component"
            ),
            evidence=_bounded_text_list(
                payload["evidence"], field="finding evidence", minimum=1
            ),
            attack_path=_bounded_text_list(
                payload["attack_path"], field="finding attack_path", minimum=2,
                maximum=8,
            ),
            recommendation=_bounded_text(
                payload["recommendation"], field="finding recommendation"
            ),
        )


@dataclass(frozen=True)
class SecurityReviewReport:
    change_ref: str
    reviewed_categories: tuple[str, ...]
    findings: tuple[SecurityFinding, ...]
    limitations: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.reviewed_categories != SECURITY_CATEGORIES:
            raise ValueError(
                "reviewed_categories must list every required category in order"
            )
        finding_ids = tuple(finding.id for finding in self.findings)
        if len(set(finding_ids)) != len(finding_ids):
            raise ValueError("finding ids must be unique")
        if len(self.findings) > 128:
            raise ValueError("findings exceeds 128 items")

    @property
    def blocking_finding_ids(self) -> tuple[str, ...]:
        return tuple(finding.id for finding in self.findings if finding.blocking)

    @property
    def verdict(self) -> str:
        if self.blocking_finding_ids:
            return "block"
        if self.findings:
            return "pass_with_findings"
        return "pass"

    def as_dict(self) -> dict[str, object]:
        return {
            "schema_version": SCHEMA_VERSION,
            "change_ref": self.change_ref,
            "reviewed_categories": list(self.reviewed_categories),
            "findings": [finding.as_dict() for finding in self.findings],
            "limitations": list(self.limitations),
            "verdict": self.verdict,
            "blocking_finding_ids": list(self.blocking_finding_ids),
        }

    @classmethod
    def from_dict(cls, payload: object) -> "SecurityReviewReport":
        expected = {
            "schema_version",
            "change_ref",
            "reviewed_categories",
            "findings",
            "limitations",
        }
        if not isinstance(payload, dict) or set(payload) != expected:
            raise ValueError("security review report has an invalid shape")
        if payload["schema_version"] != SCHEMA_VERSION:
            raise ValueError("unsupported security review schema_version")
        categories = payload["reviewed_categories"]
        if not isinstance(categories, list) or not all(
            isinstance(category, str) for category in categories
        ):
            raise ValueError("reviewed_categories must be a list of text")
        findings = payload["findings"]
        if not isinstance(findings, list):
            raise ValueError("findings must be a list")
        return cls(
            change_ref=_bounded_text(
                payload["change_ref"], field="change_ref", maximum=512
            ),
            reviewed_categories=tuple(categories),
            findings=tuple(SecurityFinding.from_dict(item) for item in findings),
            limitations=_bounded_text_list(
                payload["limitations"], field="limitations"
            ),
        )


def validate_report(path: str | Path) -> SecurityReviewReport:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    return SecurityReviewReport.from_dict(payload)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate a bounded security-review report."
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
    output = {"valid": True, **report.as_dict()}
    print(json.dumps(output, indent=2, sort_keys=True))
    return 1 if report.blocking_finding_ids else 0


if __name__ == "__main__":
    raise SystemExit(main())
