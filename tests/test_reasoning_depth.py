from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from acr_runtime.db import RuntimeDB
from acr_runtime.reasoning_depth import (
    ReasoningBudgetPlanner,
    ReasoningBudgetRequest,
    ReasoningDepthEngine,
    ReasoningOutcome,
)
from acr_runtime.execution import Task
from acr_runtime.providers import ModelCapabilities


class ReasoningDepthTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.db = RuntimeDB(Path(self.directory.name) / "acr.db")
        self.engine = ReasoningDepthEngine(self.db.connection)

    def tearDown(self) -> None:
        self.db.close()
        self.directory.cleanup()

    def test_trivial_task_gets_minimal_bounded_bundle(self):
        decision = self.engine.decide(
            ReasoningBudgetRequest(task="Classify this as yes or no")
        )
        self.assertEqual(decision.complexity, "low")
        self.assertEqual(decision.planning_mode, "minimal")
        self.assertEqual(decision.model_tier, "small")
        self.assertEqual(decision.context_fraction_micros, 600_000)
        self.assertEqual(decision.verification_mode, "deterministic")
        self.assertEqual(decision.max_decomposition_depth, 0)

    def test_short_high_risk_tasks_force_high_floor(self):
        for task in (
            "delete prod",
            "send $100000",
            "dose infant?",
            "publish customer list",
            "grant admin",
            "rotate key",
        ):
            with self.subTest(task=task):
                decision = self.engine.decide(
                    ReasoningBudgetRequest(task=task, requested_minimum="low")
                )
                self.assertEqual(decision.complexity, "high")
                self.assertEqual(decision.risk_level, "protected")
                self.assertEqual(decision.verification_mode, "independent")
                self.assertEqual(decision.max_model_calls, 2)

    def test_structured_risk_and_ambiguous_action_cannot_be_simple(self):
        decision = self.engine.decide(
            ReasoningBudgetRequest(
                task="yes, proceed",
                external_side_effects=True,
                requires_tools=True,
            )
        )
        self.assertNotEqual(decision.complexity, "low")
        self.assertIn("context_dependent_action", decision.reasons)
        self.assertIn("structured_risk_floor", decision.reasons)

    def test_long_marker_padding_is_bounded_by_policy(self):
        decision = self.engine.decide(
            ReasoningBudgetRequest(task=("architect security debug " * 100))
        )
        self.assertEqual(decision.complexity, "high")
        self.assertLessEqual(decision.score, 7)
        self.assertEqual(decision.max_decomposition_depth, 3)
        self.assertEqual(decision.max_model_calls, 2)

    def test_task_content_is_hashed_not_retained(self):
        secret_text = "private novel task text classify this"
        decision = self.engine.decide(ReasoningBudgetRequest(task=secret_text))
        serialized = repr(self.engine.inspect(decision.id))
        self.assertNotIn(secret_text, serialized)
        self.assertEqual(len(decision.task_hash), 64)

    def test_caller_outcomes_do_not_refine_thresholds(self):
        decision = self.engine.decide(ReasoningBudgetRequest(task="classify item"))
        receipt = self.engine.record_outcome(
            ReasoningOutcome(
                decision_id=decision.id,
                success=True,
                quality=1.0,
                verification_passed=True,
                hard_violation=False,
                policy_conformant=True,
                input_tokens=10,
                output_tokens=4,
                reasoning_tokens=None,
                latency_ms=10,
                cost_microunits=0,
                evidence=("caller-claim",),
            )
        )
        report = self.engine.refine("general", minimum_samples=5)
        self.assertEqual(receipt["provenance"], "caller_supplied_unverified")
        self.assertFalse(receipt["eligible_for_refinement"])
        self.assertEqual(report["summary"]["trusted_samples"], 0)
        self.assertEqual(report["status"], "insufficient_evidence")
        self.assertFalse(report["automatic_activation"])

    def test_trusted_hard_violation_rejects_advisory_candidate(self):
        decisions = [
            self.engine.decide(ReasoningBudgetRequest(task=f"classify item {i}"))
            for i in range(5)
        ]
        for index, decision in enumerate(decisions):
            self.engine.record_outcome(
                ReasoningOutcome(
                    decision_id=decision.id,
                    success=index != 0,
                    quality=0.9,
                    verification_passed=index != 0,
                    hard_violation=index == 0,
                    policy_conformant=True,
                    input_tokens=10,
                    output_tokens=5,
                    reasoning_tokens=1,
                    latency_ms=10,
                    cost_microunits=1,
                    evidence=(f"trusted-receipt-{index}",),
                ),
                trusted_runtime=True,
            )
        report = self.engine.refine("general", minimum_samples=5)
        self.assertEqual(report["status"], "rejected")
        self.assertEqual(report["recommendation"], "reject_candidate")

    def test_nonconformant_hard_violation_still_rejects(self):
        decision = self.engine.decide(
            ReasoningBudgetRequest(task="architect service")
        )
        receipt = self.engine.record_outcome(
            ReasoningOutcome(
                decision_id=decision.id,
                success=False,
                quality=0.0,
                verification_passed=False,
                hard_violation=True,
                policy_conformant=False,
                input_tokens=1,
                output_tokens=1,
                reasoning_tokens=None,
                latency_ms=1,
                cost_microunits=0,
                evidence=("trusted-hard-violation",),
            ),
            trusted_runtime=True,
        )
        report = self.engine.refine("general", minimum_samples=5)
        self.assertFalse(receipt["eligible_for_refinement"])
        self.assertEqual(report["status"], "rejected")
        self.assertEqual(report["summary"]["trusted_samples"], 0)
        self.assertEqual(report["summary"]["hard_violations"], 1)

    def test_policy_decisions_and_outcomes_are_immutable(self):
        decision = self.engine.decide(ReasoningBudgetRequest(task="classify item"))
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.connection.execute(
                "UPDATE reasoning_budget_decisions SET score=2 WHERE id=?",
                (decision.id,),
            )

    def test_reasoning_tokens_are_inclusive_output_subset(self):
        decision = self.engine.decide(ReasoningBudgetRequest(task="classify item"))
        with self.assertRaises(ValueError):
            ReasoningOutcome(
                decision_id=decision.id,
                success=True,
                quality=1.0,
                verification_passed=True,
                hard_violation=False,
                policy_conformant=True,
                input_tokens=1,
                output_tokens=2,
                reasoning_tokens=3,
                latency_ms=1,
                cost_microunits=0,
                evidence=("receipt",),
            )

    def test_validated_provider_control_and_planner_match_decision(self):
        capabilities = ModelCapabilities(
            reasoning_modes=("effort",),
            reasoning_efforts=("low", "medium", "high"),
        )
        decision = self.engine.decide(
            ReasoningBudgetRequest(task="architect and migrate the service"),
            provider_capabilities=capabilities,
        )
        control = self.engine.control_for(decision, capabilities)
        step = ReasoningBudgetPlanner(decision).plan(Task("build service"))[0]
        self.assertEqual(decision.provider_control_state, "validated")
        self.assertEqual(control.mode, "effort")
        self.assertEqual(control.effort, "high")
        self.assertIn("Decompose", step.name)
        self.assertNotIn("build service", step.input_json)


if __name__ == "__main__":
    unittest.main()
