from __future__ import annotations

import json
import unittest
from pathlib import Path

from acr_runtime.agent_spec import AgentSpec
from acr_runtime.expansion_discovery import ExpansionDiscoveryReport


ROOT = Path(__file__).parents[1]


def _proposal(
    proposal_id: str,
    *,
    status: str = "verified",
    frequency: int = 4,
    tasks: int = 4,
    sources: list[str] | None = None,
    complexity: str = "medium",
    risk: str = "low",
    cost: int = 60,
) -> dict[str, object]:
    elevated = risk != "low"
    evidence_sources = sources or ["manual_procedures", "user_interventions"]
    return {
        "id": proposal_id,
        "problem": "A repeated bounded workflow requires manual intervention.",
        "evidence_status": status,
        "evidence_sources": evidence_sources,
        "evidence_refs": [f"evidence:{item}" for item in evidence_sources],
        "frequency": frequency,
        "distinct_tasks": tasks,
        "window_days": 30,
        "current_cost": {
            "tokens": 0, "model_calls": 0, "retrieval_tokens": 0,
            "database_queries": 0, "tool_calls": 0, "latency_ms": 0,
            "manual_minutes": cost, "failures": 0,
        },
        "proposed_capability": "Add one bounded reusable workflow.",
        "expected_benefit": {
            "metric": "manual_minutes", "unit": "minutes",
            "direction": "reduce", "baseline": 60, "target": 20,
        },
        "implementation_complexity": complexity,
        "complexity_evidence": ["Touches one interface and test boundary."],
        "security_risk": risk,
        "security_evidence": ["No new authority is implied by the proposal."],
        "attack_path": (
            ["Untrusted input reaches the workflow.", "The workflow changes state."]
            if elevated else []
        ),
        "mitigations": ["Keep default-deny capability checks."] if elevated else [],
        "benchmark_checks": ["baseline", "candidate", "quality", "security"],
        "benchmark_plan": [
            "Measure the representative baseline.",
            "Run the candidate on the same cases.",
            "Reject any quality regression.",
            "Reject any security regression.",
        ],
    }


def _report(proposals: list[dict[str, object]]) -> dict[str, object]:
    return {
        "schema_version": 1,
        "change_ref": "evidence-window:2026-07",
        "proposals": proposals,
    }


class ExpansionDiscoveryAgentTests(unittest.TestCase):
    def test_build_requires_verified_repeated_multisource_demand(self) -> None:
        report = ExpansionDiscoveryReport.from_dict(
            _report([_proposal("bounded.workflow")])
        )
        self.assertEqual(report.ranked[0].decision, "BUILD")

        for field, value in (
            ("evidence_status", "supported"),
            ("frequency", 2),
            ("distinct_tasks", 2),
            ("evidence_sources", ["manual_procedures"]),
            ("implementation_complexity", "high"),
            ("security_risk", "high"),
        ):
            proposal = (
                _proposal("deferred.workflow", risk="high")
                if field == "security_risk"
                else _proposal("deferred.workflow")
            )
            proposal[field] = value
            if field == "frequency":
                proposal["distinct_tasks"] = 2
            if field == "evidence_sources":
                proposal["evidence_refs"] = ["evidence:manual_procedures"]
            with self.subTest(field=field):
                decision = ExpansionDiscoveryReport.from_dict(
                    _report([proposal])
                ).ranked[0].decision
                self.assertEqual(decision, "DEFER")

    def test_speculative_one_off_or_zero_cost_ideas_are_rejected(self) -> None:
        report = ExpansionDiscoveryReport.from_dict(_report([
            _proposal("speculative.idea", status="speculative"),
            _proposal("one.off", frequency=1, tasks=1),
            _proposal("zero.cost", cost=0),
        ]))
        self.assertTrue(all(item.decision == "REJECT" for item in report.ranked))

    def test_ranking_prefers_decision_then_repeated_demand(self) -> None:
        report = ExpansionDiscoveryReport.from_dict(_report([
            _proposal("defer.item", status="supported", frequency=10, tasks=10),
            _proposal("build.smaller", frequency=3, tasks=3),
            _proposal("build.larger", frequency=8, tasks=6),
        ]))
        self.assertEqual(
            tuple(item.id for item in report.ranked),
            ("build.larger", "build.smaller", "defer.item"),
        )

    def test_benchmark_and_security_contracts_fail_closed(self) -> None:
        proposal = _proposal("bad.benchmark")
        proposal["benchmark_checks"] = ["baseline", "candidate"]
        with self.assertRaisesRegex(ValueError, "benchmark_checks"):
            ExpansionDiscoveryReport.from_dict(_report([proposal]))

        proposal = _proposal("bad.risk", risk="medium")
        proposal["attack_path"] = []
        with self.assertRaisesRegex(ValueError, "attack path"):
            ExpansionDiscoveryReport.from_dict(_report([proposal]))

    def test_decision_is_derived_and_schema_is_closed(self) -> None:
        proposal = _proposal("caller.decision")
        proposal["decision"] = "BUILD"
        with self.assertRaisesRegex(ValueError, "invalid shape"):
            ExpansionDiscoveryReport.from_dict(_report([proposal]))

    def test_role_and_workflow_are_complete_and_least_privilege(self) -> None:
        payload = json.loads(
            (
                ROOT / "examples" / "agent-spec"
                / "expansion-discovery-worker.json"
            ).read_text(encoding="utf-8")
        )
        spec = AgentSpec.from_dict(payload)
        self.assertEqual(spec.task_scope, ("expansion-discovery",))
        self.assertEqual(spec.tools, ())
        self.assertEqual(spec.permissions, ())
        self.assertEqual(spec.communication.mode, "none")
        self.assertTrue(spec.model_policy.local_only)
        self.assertFalse(spec.model_policy.allow_fallback)
        self.assertEqual(spec.money_budget, 0)

        source = (
            ROOT / "docs" / "agents" / "expansion-discovery.md"
        ).read_text(encoding="utf-8")
        for label in (
            "PROBLEM", "FREQUENCY", "CURRENT COST", "PROPOSED CAPABILITY",
            "EXPECTED BENEFIT", "IMPLEMENTATION COMPLEXITY", "SECURITY RISK",
            "HOW TO BENCHMARK", "BUILD", "DEFER", "REJECT",
        ):
            self.assertIn(label, source)
        self.assertIn("not an executable worker", source)


if __name__ == "__main__":
    unittest.main()
