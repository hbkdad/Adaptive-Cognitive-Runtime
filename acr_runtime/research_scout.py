from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Sequence
from urllib.parse import urlsplit

from .secret_management import assert_secret_free


SCHEMA_VERSION = 1
TOPICS = (
    "llm_memory",
    "agent_skills",
    "self_evolving_agents",
    "context_engineering",
    "agent_orchestration",
    "model_routing",
    "rag",
    "temporal_memory",
    "experience_distillation",
    "ai_evaluation",
    "tool_routing",
    "prompt_injection_defense",
    "sandboxed_code_execution",
)
SOURCE_KINDS = (
    "original_research",
    "official_repository",
    "maintainer_documentation",
)
CLAIM_MATURITY = (
    "research_claim",
    "documented_implementation",
    "reproduced_engineering_result",
    "insufficient_evidence",
)
NOVELTY_STATUSES = (
    "new_to_acr",
    "new_combination",
    "independent_confirmation",
    "not_new",
    "unclear",
)
CODE_STATUSES = ("available", "unavailable", "unclear")
LICENSE_STATUSES = ("verified", "unverified", "not_applicable")
IMPROVEMENT_STATUSES = (
    "source_reported",
    "acr_measured",
    "hypothesis",
    "unavailable",
)
INTEGRATION_COSTS = ("low", "medium", "high")
BENCHMARK_CHECKS = ("baseline", "candidate", "quality", "security")
IDENTIFIER = re.compile(r"^[a-z][a-z0-9._-]{1,127}$")
REFERENCE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/@#-]{0,255}$")
SHA256 = re.compile(r"^[a-f0-9]{64}$")
CODE_REF = re.compile(r"^[a-f0-9]{40}$")
MAX_NUMBER = 9_223_372_036_854_775_807


def _text(value: object, *, field: str, maximum: int = 4_000) -> str:
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
    result = tuple(_text(item, field=field, maximum=1_000) for item in value)
    if len(set(result)) != len(result):
        raise ValueError(f"{field} contains duplicates")
    return result


def _reference_list(
    value: object,
    *,
    field: str,
    minimum: int = 1,
    maximum: int = 32,
) -> tuple[str, ...]:
    items = _text_list(
        value, field=field, minimum=minimum, maximum=maximum
    )
    if any(not REFERENCE.fullmatch(item) for item in items):
        raise ValueError(f"{field} contains an invalid reference")
    return items


def _number(value: object, *, field: str) -> int:
    if type(value) is not int or not 0 <= value <= MAX_NUMBER:
        raise ValueError(f"{field} must be a bounded non-negative integer")
    return value


def _iso_date(value: object, *, field: str) -> str:
    text = _text(value, field=field, maximum=10)
    try:
        parsed = date.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(f"{field} must be an ISO date") from exc
    if parsed.isoformat() != text:
        raise ValueError(f"{field} must be an ISO date")
    return text


def _https(value: object, *, field: str) -> str:
    locator = _text(value, field=field, maximum=2_048)
    parts = urlsplit(locator)
    if (
        parts.scheme != "https"
        or not parts.netloc
        or parts.username is not None
        or parts.password is not None
        or parts.fragment
    ):
        raise ValueError(f"{field} must be a credential-free HTTPS locator")
    return locator


@dataclass(frozen=True)
class ResearchSource:
    id: str
    title: str
    locator: str
    kind: str
    publisher: str
    published_on: str
    retrieved_on: str
    content_hash: str

    @classmethod
    def from_dict(cls, payload: object) -> "ResearchSource":
        expected = {
            "id",
            "title",
            "locator",
            "kind",
            "publisher",
            "published_on",
            "retrieved_on",
            "content_hash",
        }
        if not isinstance(payload, dict) or set(payload) != expected:
            raise ValueError("research source has an invalid shape")
        source_id = payload["id"]
        if not isinstance(source_id, str) or not IDENTIFIER.fullmatch(source_id):
            raise ValueError("research source id is invalid")
        if payload["kind"] not in SOURCE_KINDS:
            raise ValueError("research source kind is invalid")
        published = _iso_date(payload["published_on"], field="published_on")
        retrieved = _iso_date(payload["retrieved_on"], field="retrieved_on")
        if retrieved < published:
            raise ValueError("retrieved_on cannot precede published_on")
        content_hash = payload["content_hash"]
        if not isinstance(content_hash, str) or not SHA256.fullmatch(content_hash):
            raise ValueError("content_hash must be a lowercase SHA-256 digest")
        return cls(
            id=source_id,
            title=_text(payload["title"], field="source title", maximum=512),
            locator=_https(payload["locator"], field="source locator"),
            kind=payload["kind"],
            publisher=_text(payload["publisher"], field="publisher", maximum=256),
            published_on=published,
            retrieved_on=retrieved,
            content_hash=content_hash,
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "title": self.title,
            "locator": self.locator,
            "kind": self.kind,
            "publisher": self.publisher,
            "published_on": self.published_on,
            "retrieved_on": self.retrieved_on,
            "content_hash": self.content_hash,
        }


@dataclass(frozen=True)
class CodeAvailability:
    status: str
    locator: str | None
    version_ref: str | None
    license_id: str | None
    license_status: str

    @classmethod
    def from_dict(cls, payload: object) -> "CodeAvailability":
        expected = {
            "status", "locator", "version_ref", "license_id", "license_status"
        }
        if not isinstance(payload, dict) or set(payload) != expected:
            raise ValueError("source_code has an invalid shape")
        status = payload["status"]
        license_status = payload["license_status"]
        if status not in CODE_STATUSES:
            raise ValueError("source_code status is invalid")
        if license_status not in LICENSE_STATUSES:
            raise ValueError("source_code license_status is invalid")
        locator = payload["locator"]
        version_ref = payload["version_ref"]
        license_id = payload["license_id"]
        if status == "available":
            locator = _https(locator, field="source code locator")
            if (
                not isinstance(version_ref, str)
                or not CODE_REF.fullmatch(version_ref)
            ):
                raise ValueError(
                    "available source code needs an immutable commit version_ref"
                )
            license_id = _text(
                license_id, field="source code license_id", maximum=128
            )
            if license_status == "not_applicable":
                raise ValueError("available source code needs a license assessment")
        elif any(item is not None for item in (locator, version_ref, license_id)):
            raise ValueError(
                "unavailable or unclear source code cannot claim code metadata"
            )
        elif license_status != "not_applicable":
            raise ValueError(
                "unavailable or unclear source code uses not_applicable license"
            )
        return cls(status, locator, version_ref, license_id, license_status)

    def as_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "locator": self.locator,
            "version_ref": self.version_ref,
            "license_id": self.license_id,
            "license_status": self.license_status,
        }


@dataclass(frozen=True)
class EvidenceAssessment:
    maturity: str
    summary: str
    source_refs: tuple[str, ...]
    reproduction_refs: tuple[str, ...]

    @classmethod
    def from_dict(cls, payload: object) -> "EvidenceAssessment":
        expected = {
            "maturity", "summary", "source_refs", "reproduction_refs"
        }
        if not isinstance(payload, dict) or set(payload) != expected:
            raise ValueError("evidence assessment has an invalid shape")
        maturity = payload["maturity"]
        if maturity not in CLAIM_MATURITY:
            raise ValueError("evidence maturity is invalid")
        reproduction = _reference_list(
            payload["reproduction_refs"],
            field="reproduction_refs",
            minimum=0,
        )
        if maturity == "reproduced_engineering_result" and not reproduction:
            raise ValueError("reproduced result needs reproduction_refs")
        if maturity != "reproduced_engineering_result" and reproduction:
            raise ValueError(
                "only reproduced results may contain reproduction_refs"
            )
        return cls(
            maturity=maturity,
            summary=_text(payload["summary"], field="evidence summary"),
            source_refs=_reference_list(
                payload["source_refs"], field="evidence source_refs"
            ),
            reproduction_refs=reproduction,
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "maturity": self.maturity,
            "summary": self.summary,
            "source_refs": list(self.source_refs),
            "reproduction_refs": list(self.reproduction_refs),
        }


@dataclass(frozen=True)
class NoveltyAssessment:
    status: str
    statement: str
    comparison_refs: tuple[str, ...]

    @classmethod
    def from_dict(cls, payload: object) -> "NoveltyAssessment":
        expected = {"status", "statement", "comparison_refs"}
        if not isinstance(payload, dict) or set(payload) != expected:
            raise ValueError("novelty assessment has an invalid shape")
        status = payload["status"]
        if status not in NOVELTY_STATUSES:
            raise ValueError("novelty status is invalid")
        refs = _reference_list(
            payload["comparison_refs"], field="novelty comparison_refs"
        )
        return cls(
            status=status,
            statement=_text(payload["statement"], field="novelty statement"),
            comparison_refs=refs,
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "statement": self.statement,
            "comparison_refs": list(self.comparison_refs),
        }


@dataclass(frozen=True)
class ExpectedImprovement:
    status: str
    metric: str
    unit: str
    direction: str
    baseline: int | None
    target: int | None
    evidence_refs: tuple[str, ...]

    @classmethod
    def from_dict(cls, payload: object) -> "ExpectedImprovement":
        expected = {
            "status", "metric", "unit", "direction", "baseline", "target",
            "evidence_refs",
        }
        if not isinstance(payload, dict) or set(payload) != expected:
            raise ValueError("expected_improvement has an invalid shape")
        status = payload["status"]
        if status not in IMPROVEMENT_STATUSES:
            raise ValueError("expected improvement status is invalid")
        metric = _text(payload["metric"], field="improvement metric", maximum=128)
        unit = _text(payload["unit"], field="improvement unit", maximum=128)
        direction = payload["direction"]
        if direction not in {"increase", "reduce"}:
            raise ValueError("expected improvement direction is invalid")
        baseline = payload["baseline"]
        target = payload["target"]
        refs = _reference_list(
            payload["evidence_refs"],
            field="improvement evidence_refs",
            minimum=0,
        )
        measured = status in {"source_reported", "acr_measured"}
        if measured:
            baseline = _number(baseline, field="improvement baseline")
            target = _number(target, field="improvement target")
            if not refs:
                raise ValueError("measured improvement needs evidence_refs")
            if (
                direction == "increase" and target <= baseline
            ) or (
                direction == "reduce" and target >= baseline
            ):
                raise ValueError("improvement target does not improve baseline")
        elif baseline is not None or target is not None or refs:
            raise ValueError(
                "hypothesis or unavailable improvement cannot claim measurements"
            )
        return cls(status, metric, unit, direction, baseline, target, refs)

    def as_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "metric": self.metric,
            "unit": self.unit,
            "direction": self.direction,
            "baseline": self.baseline,
            "target": self.target,
            "evidence_refs": list(self.evidence_refs),
        }


@dataclass(frozen=True)
class IntegrationCost:
    level: str
    engineer_days_low: int
    engineer_days_high: int
    affected_components: tuple[str, ...]
    cost_evidence: tuple[str, ...]

    @classmethod
    def from_dict(cls, payload: object) -> "IntegrationCost":
        expected = {
            "level", "engineer_days_low", "engineer_days_high",
            "affected_components", "cost_evidence",
        }
        if not isinstance(payload, dict) or set(payload) != expected:
            raise ValueError("integration_cost has an invalid shape")
        if payload["level"] not in INTEGRATION_COSTS:
            raise ValueError("integration cost level is invalid")
        low = _number(payload["engineer_days_low"], field="engineer_days_low")
        high = _number(payload["engineer_days_high"], field="engineer_days_high")
        if low > high:
            raise ValueError("integration cost range is reversed")
        return cls(
            level=payload["level"],
            engineer_days_low=low,
            engineer_days_high=high,
            affected_components=_reference_list(
                payload["affected_components"],
                field="affected_components",
            ),
            cost_evidence=_text_list(
                payload["cost_evidence"], field="cost_evidence"
            ),
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "level": self.level,
            "engineer_days_low": self.engineer_days_low,
            "engineer_days_high": self.engineer_days_high,
            "affected_components": list(self.affected_components),
            "cost_evidence": list(self.cost_evidence),
        }


@dataclass(frozen=True)
class ResearchScoutFinding:
    id: str
    topics: tuple[str, ...]
    source_ids: tuple[str, ...]
    what_is_genuinely_new: NoveltyAssessment
    evidence: EvidenceAssessment
    source_code: CodeAvailability
    differs_from_acr: str
    acr_comparison_refs: tuple[str, ...]
    safe_adaptations: tuple[str, ...]
    do_not_copy: tuple[str, ...]
    expected_improvement: ExpectedImprovement
    integration_cost: IntegrationCost
    benchmark_checks: tuple[str, ...]
    benchmark_plan: tuple[str, ...]

    @classmethod
    def from_dict(cls, payload: object) -> "ResearchScoutFinding":
        expected = {
            "id", "topics", "source_ids", "what_is_genuinely_new", "evidence",
            "source_code", "differs_from_acr", "acr_comparison_refs",
            "safe_adaptations", "do_not_copy", "expected_improvement",
            "integration_cost", "benchmark_checks", "benchmark_plan",
        }
        if not isinstance(payload, dict) or set(payload) != expected:
            raise ValueError("research finding has an invalid shape")
        finding_id = payload["id"]
        if not isinstance(finding_id, str) or not IDENTIFIER.fullmatch(finding_id):
            raise ValueError("research finding id is invalid")
        topics = _reference_list(payload["topics"], field="finding topics")
        if any(topic not in TOPICS for topic in topics):
            raise ValueError("research finding topic is invalid")
        checks = payload["benchmark_checks"]
        if not isinstance(checks, list) or tuple(checks) != BENCHMARK_CHECKS:
            raise ValueError("benchmark_checks must contain all fixed checks")
        source_code = CodeAvailability.from_dict(payload["source_code"])
        evidence = EvidenceAssessment.from_dict(payload["evidence"])
        improvement = ExpectedImprovement.from_dict(
            payload["expected_improvement"]
        )
        if (
            improvement.status == "acr_measured"
            and evidence.maturity != "reproduced_engineering_result"
        ):
            raise ValueError(
                "ACR-measured improvement requires a reproduced engineering result"
            )
        adaptations = _text_list(
            payload["safe_adaptations"], field="safe_adaptations", minimum=0
        )
        if (
            source_code.status == "available"
            and source_code.license_status != "verified"
            and any("reuse code" in item.casefold() for item in adaptations)
        ):
            raise ValueError("code reuse requires a verified source license")
        return cls(
            id=finding_id,
            topics=topics,
            source_ids=_reference_list(
                payload["source_ids"], field="finding source_ids"
            ),
            what_is_genuinely_new=NoveltyAssessment.from_dict(
                payload["what_is_genuinely_new"]
            ),
            evidence=evidence,
            source_code=source_code,
            differs_from_acr=_text(
                payload["differs_from_acr"], field="differs_from_acr"
            ),
            acr_comparison_refs=_reference_list(
                payload["acr_comparison_refs"], field="acr_comparison_refs"
            ),
            safe_adaptations=adaptations,
            do_not_copy=_text_list(
                payload["do_not_copy"], field="do_not_copy"
            ),
            expected_improvement=improvement,
            integration_cost=IntegrationCost.from_dict(
                payload["integration_cost"]
            ),
            benchmark_checks=tuple(checks),
            benchmark_plan=_text_list(
                payload["benchmark_plan"], field="benchmark_plan", minimum=4
            ),
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "topics": list(self.topics),
            "source_ids": list(self.source_ids),
            "what_is_genuinely_new": self.what_is_genuinely_new.as_dict(),
            "evidence": self.evidence.as_dict(),
            "source_code": self.source_code.as_dict(),
            "differs_from_acr": self.differs_from_acr,
            "acr_comparison_refs": list(self.acr_comparison_refs),
            "safe_adaptations": list(self.safe_adaptations),
            "do_not_copy": list(self.do_not_copy),
            "expected_improvement": self.expected_improvement.as_dict(),
            "integration_cost": self.integration_cost.as_dict(),
            "benchmark_checks": list(self.benchmark_checks),
            "benchmark_plan": list(self.benchmark_plan),
        }


@dataclass(frozen=True)
class TopicCoverage:
    topic: str
    status: str
    source_ids: tuple[str, ...]
    rationale: str

    @classmethod
    def from_dict(cls, payload: object) -> "TopicCoverage":
        expected = {"topic", "status", "source_ids", "rationale"}
        if not isinstance(payload, dict) or set(payload) != expected:
            raise ValueError("topic coverage has an invalid shape")
        if payload["topic"] not in TOPICS:
            raise ValueError("coverage topic is invalid")
        if payload["status"] not in {"finding", "no_relevant_finding"}:
            raise ValueError("coverage status is invalid")
        return cls(
            topic=payload["topic"],
            status=payload["status"],
            source_ids=_reference_list(
                payload["source_ids"], field="coverage source_ids"
            ),
            rationale=_text(payload["rationale"], field="coverage rationale"),
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "topic": self.topic,
            "status": self.status,
            "source_ids": list(self.source_ids),
            "rationale": self.rationale,
        }


@dataclass(frozen=True)
class ResearchScoutReport:
    scope_ref: str
    sources: tuple[ResearchSource, ...]
    coverage: tuple[TopicCoverage, ...]
    findings: tuple[ResearchScoutFinding, ...]

    @classmethod
    def from_dict(cls, payload: object) -> "ResearchScoutReport":
        expected = {
            "schema_version", "scope_ref", "sources", "coverage", "findings"
        }
        if not isinstance(payload, dict) or set(payload) != expected:
            raise ValueError("research scout report has an invalid shape")
        if payload["schema_version"] != SCHEMA_VERSION:
            raise ValueError("unsupported research scout schema_version")
        for field, maximum in (("sources", 256), ("findings", 128)):
            if not isinstance(payload[field], list) or len(payload[field]) > maximum:
                raise ValueError(f"{field} must be a bounded list")
        if not isinstance(payload["coverage"], list):
            raise ValueError("coverage must be a list")
        sources = tuple(ResearchSource.from_dict(item) for item in payload["sources"])
        findings = tuple(
            ResearchScoutFinding.from_dict(item) for item in payload["findings"]
        )
        coverage = tuple(TopicCoverage.from_dict(item) for item in payload["coverage"])
        source_ids = tuple(item.id for item in sources)
        finding_ids = tuple(item.id for item in findings)
        if len(set(source_ids)) != len(source_ids):
            raise ValueError("research source ids must be unique")
        if len(set(finding_ids)) != len(finding_ids):
            raise ValueError("research finding ids must be unique")
        if tuple(item.topic for item in coverage) != TOPICS:
            raise ValueError("coverage must contain every fixed topic in order")
        known_sources = set(source_ids)
        for item in (*coverage, *findings):
            missing = set(item.source_ids) - known_sources
            if missing:
                raise ValueError(f"unknown source ids: {sorted(missing)}")
        source_by_id = {item.id: item for item in sources}
        for finding in findings:
            evidence_sources = {
                item.split("#", 1)[0] for item in finding.evidence.source_refs
            }
            if not evidence_sources <= set(finding.source_ids):
                raise ValueError(
                    "evidence source_refs must be bound to finding source_ids"
                )
            if finding.expected_improvement.status == "source_reported":
                improvement_sources = {
                    item.split("#", 1)[0]
                    for item in finding.expected_improvement.evidence_refs
                }
                if not improvement_sources <= set(finding.source_ids):
                    raise ValueError(
                        "source-reported improvement must cite finding sources"
                    )
            if finding.source_code.status == "available" and not any(
                source_by_id[source_id].kind == "official_repository"
                and source_by_id[source_id].locator
                == finding.source_code.locator
                for source_id in finding.source_ids
            ):
                raise ValueError(
                    "available source code must cite its official repository source"
                )
        topics_with_findings = {
            topic for finding in findings for topic in finding.topics
        }
        for item in coverage:
            if (item.status == "finding") != (item.topic in topics_with_findings):
                raise ValueError("coverage status does not match findings")
        return cls(
            scope_ref=_text(payload["scope_ref"], field="scope_ref", maximum=512),
            sources=sources,
            coverage=coverage,
            findings=findings,
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "schema_version": SCHEMA_VERSION,
            "scope_ref": self.scope_ref,
            "sources": [item.as_dict() for item in self.sources],
            "coverage": [item.as_dict() for item in self.coverage],
            "findings": [item.as_dict() for item in self.findings],
        }


def validate_report(path: str | Path) -> ResearchScoutReport:
    return ResearchScoutReport.from_dict(
        json.loads(Path(path).read_text(encoding="utf-8"))
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate source-bound ACR research-scout findings."
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
