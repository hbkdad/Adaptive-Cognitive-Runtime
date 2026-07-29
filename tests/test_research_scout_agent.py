from __future__ import annotations

import json
import unittest
from copy import deepcopy
from pathlib import Path

from acr_runtime.agent_spec import AgentSpec
from acr_runtime.research_scout import ResearchScoutReport, TOPICS


ROOT = Path(__file__).parents[1]


def _source(source_id: str = "official.source") -> dict[str, object]:
    return {
        "id": source_id,
        "title": "Maintainer documentation for a bounded implementation",
        "locator": "https://github.com/example/project",
        "kind": "official_repository",
        "publisher": "Example maintainers",
        "published_on": "2026-01-15",
        "retrieved_on": "2026-07-29",
        "content_hash": "a" * 64,
    }


def _finding(
    *,
    finding_id: str = "bounded.finding",
    maturity: str = "documented_implementation",
    code_status: str = "available",
    license_status: str = "verified",
    improvement_status: str = "source_reported",
) -> dict[str, object]:
    available = code_status == "available"
    measured = improvement_status in {"source_reported", "acr_measured"}
    reproduced = maturity == "reproduced_engineering_result"
    return {
        "id": finding_id,
        "topics": ["llm_memory", "temporal_memory"],
        "source_ids": ["official.source"],
        "what_is_genuinely_new": {
            "status": "new_combination",
            "statement": (
                "The source combines bitemporal provenance with incremental "
                "retrieval in a way not present in the compared ACR surface."
            ),
            "comparison_refs": [
                "acr_runtime/temporal.py",
                "acr_runtime/retrieval.py",
            ],
        },
        "evidence": {
            "maturity": maturity,
            "summary": (
                "The implementation is documented; performance remains a "
                "source claim until reproduced against an ACR baseline."
            ),
            "source_refs": ["official.source#architecture"],
            "reproduction_refs": ["benchmark:memory-v2"] if reproduced else [],
        },
        "source_code": {
            "status": code_status,
            "locator": "https://github.com/example/project" if available else None,
            "version_ref": "b" * 40 if available else None,
            "license_id": "Apache-2.0" if available else None,
            "license_status": license_status if available else "not_applicable",
        },
        "differs_from_acr": (
            "ACR preserves temporal facts but does not use this source's graph "
            "projection or incremental update algorithm."
        ),
        "acr_comparison_refs": [
            "acr_runtime/temporal.py",
            "tests/test_temporal.py",
        ],
        "safe_adaptations": [
            "Adapt the provenance invariant behind a new benchmark fixture."
        ],
        "do_not_copy": [
            "Do not copy performance claims or dependency choices without "
            "reproducing them under ACR constraints."
        ],
        "expected_improvement": {
            "status": improvement_status,
            "metric": "temporal_retrieval_accuracy",
            "unit": "percent",
            "direction": "increase",
            "baseline": 70 if measured else None,
            "target": 80 if measured else None,
            "evidence_refs": (
                ["official.source#evaluation"] if measured else []
            ),
        },
        "integration_cost": {
            "level": "medium",
            "engineer_days_low": 3,
            "engineer_days_high": 8,
            "affected_components": [
                "acr_runtime/temporal.py",
                "tests/test_temporal.py",
            ],
            "cost_evidence": [
                "The adaptation touches one storage path and one benchmark."
            ],
        },
        "benchmark_checks": ["baseline", "candidate", "quality", "security"],
        "benchmark_plan": [
            "Measure the current temporal baseline on fixed cases.",
            "Run the candidate on the same cases and seeds.",
            "Reject any unrelated retrieval-quality regression.",
            "Reject any provenance or isolation regression.",
        ],
    }


def _report(finding: dict[str, object] | None = None) -> dict[str, object]:
    findings = [finding or _finding()]
    finding_topics = {
        topic
        for item in findings
        for topic in item["topics"]  # type: ignore[union-attr]
    }
    return {
        "schema_version": 1,
        "scope_ref": "prompt-99:2026-07-29",
        "sources": [_source()],
        "coverage": [
            {
                "topic": topic,
                "status": (
                    "finding" if topic in finding_topics else "no_relevant_finding"
                ),
                "source_ids": ["official.source"],
                "rationale": (
                    "The bounded primary-source search produced a relevant "
                    "finding." if topic in finding_topics else
                    "The reviewed source did not establish a distinct finding."
                ),
            }
            for topic in TOPICS
        ],
        "findings": findings,
    }


class ResearchScoutAgentTests(unittest.TestCase):
    def test_report_covers_every_topic_and_preserves_claim_maturity(self) -> None:
        report = ResearchScoutReport.from_dict(_report())
        self.assertEqual(tuple(item.topic for item in report.coverage), TOPICS)
        self.assertEqual(
            report.findings[0].evidence.maturity,
            "documented_implementation",
        )
        self.assertEqual(
            report.findings[0].expected_improvement.status,
            "source_reported",
        )

    def test_reproduced_result_requires_exact_reproduction_evidence(self) -> None:
        finding = _finding(maturity="reproduced_engineering_result")
        finding["evidence"]["reproduction_refs"] = []  # type: ignore[index]
        with self.assertRaisesRegex(ValueError, "reproduction_refs"):
            ResearchScoutReport.from_dict(_report(finding))

        report = ResearchScoutReport.from_dict(
            _report(_finding(maturity="reproduced_engineering_result"))
        )
        self.assertEqual(
            report.findings[0].evidence.reproduction_refs,
            ("benchmark:memory-v2",),
        )

    def test_code_availability_does_not_imply_safe_code_reuse(self) -> None:
        finding = _finding(license_status="unverified")
        finding["safe_adaptations"] = ["Reuse code from the official repository."]
        with self.assertRaisesRegex(ValueError, "verified source license"):
            ResearchScoutReport.from_dict(_report(finding))

        finding = _finding(code_status="unavailable")
        finding["source_code"]["locator"] = "https://github.com/example/project"  # type: ignore[index]
        with self.assertRaisesRegex(ValueError, "cannot claim code metadata"):
            ResearchScoutReport.from_dict(_report(finding))

        payload = _report()
        payload["sources"][0]["locator"] = "https://github.com/example/other"  # type: ignore[index]
        with self.assertRaisesRegex(ValueError, "official repository source"):
            ResearchScoutReport.from_dict(payload)

    def test_hypothesis_cannot_claim_measured_improvement(self) -> None:
        finding = _finding(improvement_status="hypothesis")
        finding["expected_improvement"]["baseline"] = 70  # type: ignore[index]
        with self.assertRaisesRegex(ValueError, "cannot claim measurements"):
            ResearchScoutReport.from_dict(_report(finding))

        finding = _finding(improvement_status="acr_measured")
        with self.assertRaisesRegex(ValueError, "reproduced engineering result"):
            ResearchScoutReport.from_dict(_report(finding))

    def test_cross_references_and_coverage_fail_closed(self) -> None:
        payload = _report()
        payload["findings"][0]["source_ids"] = ["missing.source"]  # type: ignore[index]
        with self.assertRaisesRegex(ValueError, "unknown source ids"):
            ResearchScoutReport.from_dict(payload)

        payload = _report()
        payload["coverage"] = payload["coverage"][:-1]  # type: ignore[index]
        with self.assertRaisesRegex(ValueError, "every fixed topic"):
            ResearchScoutReport.from_dict(payload)

        payload = deepcopy(_report())
        payload["coverage"][0]["status"] = "no_relevant_finding"  # type: ignore[index]
        with self.assertRaisesRegex(ValueError, "does not match findings"):
            ResearchScoutReport.from_dict(payload)

    def test_role_and_workflow_are_complete_and_least_privilege(self) -> None:
        payload = json.loads(
            (
                ROOT / "examples" / "agent-spec" / "research-scout-worker.json"
            ).read_text(encoding="utf-8")
        )
        spec = AgentSpec.from_dict(payload)
        self.assertEqual(spec.task_scope, ("research-scout",))
        self.assertEqual(spec.tools, ())
        self.assertEqual(spec.permissions, ())
        self.assertEqual(spec.communication.mode, "none")
        self.assertTrue(spec.model_policy.local_only)
        self.assertFalse(spec.model_policy.allow_fallback)
        self.assertEqual(spec.money_budget, 0)

        workflow = (
            ROOT / "docs" / "agents" / "research-scout.md"
        ).read_text(encoding="utf-8")
        for label in (
            "WHAT IS GENUINELY NEW",
            "EVIDENCE",
            "SOURCE CODE",
            "DIFFERS FROM ACR",
            "SAFE ADAPTATION",
            "DO NOT COPY",
            "EXPECTED IMPROVEMENT",
            "INTEGRATION COST",
        ):
            self.assertIn(label, workflow)
        self.assertIn("not an executable worker", workflow)


if __name__ == "__main__":
    unittest.main()
