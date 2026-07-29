from __future__ import annotations

import json
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from io import StringIO
from pathlib import Path
from types import SimpleNamespace

from acr_runtime import (
    AdaptiveRuntime,
    AgentFactoryRequest,
    CapabilityCheck,
    CapabilityGrantRequest,
    ModelOutcome,
    ModelProfile,
    RetrievalRequest,
    RouteRequest,
    SafeModeController,
    SafeModeViolation,
)
from acr_runtime.cli import main


class SafeModeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.database = Path(self.temporary.name) / "acr.db"
        self.runtime = AdaptiveRuntime(self.database)

    def tearDown(self) -> None:
        self.runtime.close()
        self.temporary.cleanup()

    def enable(self) -> dict[str, object]:
        return self.runtime.safe_mode.enable(
            actor_id="operator:miche",
            reason="Contain an incident while preserving evidence.",
        )

    @staticmethod
    def factory_request() -> AgentFactoryRequest:
        return AgentFactoryRequest.from_dict(
            {
                "objective": "Plan bounded independent work.",
                "task_class": "research",
                "workstreams": [
                    {
                        "id": "stream-1",
                        "objective": "Inspect retained evidence.",
                        "task_scope": ["evidence"],
                        "memory_scope": ["alpha"],
                    }
                ],
                "tools": [],
                "skills": [],
                "model_policy": {
                    "allowed_models": ["test:basic"],
                    "preferred_model": "test:basic",
                    "local_only": True,
                    "allow_fallback": False,
                },
                "token_budget": 2_000,
                "money_budget": 1.0,
                "time_budget": 60,
                "permissions": [],
                "verification_requirements": ["Retain evidence."],
                "estimated_single_agent_tokens": 500,
                "estimated_single_agent_seconds": 10,
                "estimated_context_tokens": 50,
                "estimated_cost_per_1k_tokens": 0.01,
                "complexity": 0.2,
                "uncertainty": 0.2,
                "research_breadth": 0.2,
                "parallelizable": False,
                "requires_critique": False,
                "requires_synthesis": False,
                "value_score": 0.5,
                "max_agents": 2,
            }
        )

    def test_enable_status_disable_and_cli_are_audited(self):
        status = self.enable()
        self.assertTrue(status["enabled"])
        self.assertEqual(status["sources"], ["database"])
        self.assertIn("memory_deletion", status["blocked_actions"])
        self.assertIn("rollback", status["permitted_actions"])

        self.runtime.close()
        output = StringIO()
        with redirect_stdout(output):
            self.assertEqual(
                main(["--db", str(self.database), "safe-mode"]),
                0,
            )
        self.assertTrue(json.loads(output.getvalue())["enabled"])
        self.runtime = AdaptiveRuntime(self.database)

        status = self.runtime.safe_mode.disable(
            actor_id="operator:miche",
            reason="Containment checks completed.",
        )
        self.assertFalse(status["enabled"])
        events = self.runtime.safe_mode.events()
        self.assertEqual(
            [item["event"] for item in reversed(events)],
            ["enabled", "disabled"],
        )
        with self.assertRaisesRegex(Exception, "immutable"):
            self.runtime.db.connection.execute(
                "UPDATE safe_mode_events SET reason='changed'"
            )

    def test_environment_latch_fails_closed_and_cannot_be_disabled(self):
        controller = SafeModeController(
            self.runtime.db.connection,
            environment={"ACR_SAFE_MODE": "unexpected-value"},
        )
        self.assertTrue(controller.status()["environment_enabled"])
        with self.assertRaises(SafeModeViolation):
            controller.assert_allowed("shell_write")
        with self.assertRaises(SafeModeViolation):
            controller.disable(actor_id="operator", reason="Attempt disable.")
        event = controller.events(limit=1)[0]
        self.assertEqual(event["event"], "blocked")
        self.assertEqual(event["details"]["action"], "shell_write")

    def test_retrieval_model_routing_and_inspection_remain_available(self):
        memory_id = self.runtime.remember(
            "semantic",
            "Safe mode keeps focused retrieval available.",
            scope="alpha",
        )
        profile = ModelProfile(
            provider="test",
            model="basic",
            context_capacity=8_000,
            supports_tools=False,
            input_cost_per_million=0,
            output_cost_per_million=0,
        )
        self.runtime.register_model(profile)
        self.runtime.record_model_outcome(
            ModelOutcome(
                model_id=profile.id,
                task_class="inspection",
                success=True,
                quality=0.9,
                latency_ms=5,
                input_tokens=10,
                output_tokens=10,
                tool_attempts=0,
                tool_successes=0,
                evidence=("verified:basic",),
            )
        )
        self.enable()

        retrieval = self.runtime.retrieve_memory(
            RetrievalRequest(
                task="focused retrieval",
                query="focused retrieval",
                scope="alpha",
                token_budget=500,
                target_memories=5,
            )
        )
        self.assertIn(memory_id, {item.memory.id for item in retrieval.selected})
        route = self.runtime.route_model(
            RouteRequest(
                task_class="inspection",
                quality_threshold=0.5,
                minimum_success_rate=0.5,
                estimated_input_tokens=10,
                estimated_output_tokens=10,
                required_context=100,
                minimum_samples=1,
                confidence_z=0,
            )
        )
        self.assertEqual(route.selected_model_id, "test:basic")
        self.assertTrue(self.runtime.safe_mode.status()["enabled"])

    def test_generation_mutation_deletion_and_optimization_are_blocked(self):
        memory_id = self.runtime.remember(
            "semantic", "Erasure target.", scope="alpha"
        )
        deletion = self.runtime.privacy.plan_deletion(
            memory_id,
            requested_by="operator",
            reason="Test safe-mode erasure guard.",
        )
        incumbent = self.runtime.improvement_policies.active(
            "context_thresholds"
        )
        candidate = dict(incumbent.config)
        candidate["minimum_optional_utility_bps"] += 1
        self.enable()

        blocked = (
            lambda: self.runtime.plan_skill_generation(scope="alpha"),
            lambda: self.runtime.mutate_skill_genome("missing", object()),
            lambda: self.runtime.plan_agent_factory(self.factory_request()),
            lambda: self.runtime.privacy.approve_deletion(deletion["id"]),
            lambda: self.runtime.improvement_policies.create_candidate(
                "context_thresholds", candidate, parent_id=incumbent.id
            ),
            lambda: self.runtime.learn(
                SimpleNamespace(task_class="incident-recovery")
            ),
        )
        for operation in blocked:
            with self.subTest(operation=operation):
                with self.assertRaises(SafeModeViolation):
                    operation()
        self.assertIsNotNone(self.runtime.db.memories.get(memory_id))
        actions = {
            event["details"].get("action")
            for event in self.runtime.safe_mode.events()
            if event["event"] == "blocked"
        }
        self.assertEqual(
            actions,
            {
                "skill_generation",
                "skill_mutation",
                "agent_generation",
                "memory_deletion",
                "autonomous_optimization",
            },
        )

    def test_write_capabilities_are_ineffective_but_read_stays_available(self):
        expires = (
            datetime.now(timezone.utc) + timedelta(hours=1)
        ).isoformat()
        for capability in ("filesystem.write", "database.read", "shell.execute"):
            self.runtime.permissions.grant(
                CapabilityGrantRequest(
                    subject_type="task",
                    subject_id="safe-mode-task",
                    capability=capability,
                    resource_scope="repository:alpha",
                    expires_at=expires,
                    delegable=False,
                    grantor_type="trusted_workflow",
                    grantor_id="incident-workflow",
                    reason="Bounded test grant.",
                    evidence=("test:safe-mode",),
                )
            )
        self.enable()

        decisions = {
            capability: self.runtime.permissions.check(
                CapabilityCheck(
                    "task",
                    "safe-mode-task",
                    capability,
                    "repository:alpha",
                )
            )
            for capability in (
                "filesystem.write",
                "database.read",
                "shell.execute",
            )
        }
        self.assertFalse(decisions["filesystem.write"]["allowed"])
        self.assertEqual(decisions["filesystem.write"]["reason"], "safe_mode")
        self.assertFalse(decisions["shell.execute"]["allowed"])
        self.assertTrue(decisions["database.read"]["allowed"])
        with self.assertRaises(PermissionError):
            self.runtime.permissions.grant(
                CapabilityGrantRequest(
                    subject_type="task",
                    subject_id="new-task",
                    capability="memory.write",
                    resource_scope="memory:alpha",
                    expires_at=expires,
                    delegable=False,
                    grantor_type="trusted_workflow",
                    grantor_id="incident-workflow",
                    reason="Should be blocked.",
                    evidence=("test:safe-mode",),
                )
            )


if __name__ == "__main__":
    unittest.main()
