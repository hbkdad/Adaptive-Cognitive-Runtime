from __future__ import annotations

import json
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from types import SimpleNamespace

from acr_runtime import (
    AdaptiveRuntime,
    AgentFactoryRequest,
    HumanOverrideRequest,
    ModelOutcome,
    ModelProfile,
    RouteRequest,
)
from acr_runtime.cli import main


class HumanOverrideTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.database = Path(self.temporary.name) / "acr.db"
        self.runtime = AdaptiveRuntime(self.database)

    def tearDown(self) -> None:
        self.runtime.close()
        self.temporary.cleanup()

    @staticmethod
    def request(
        action: str,
        *,
        target_id: str | None = None,
        value: dict[str, object] | None = None,
        scope: str = "global",
    ) -> HumanOverrideRequest:
        return HumanOverrideRequest(
            action=action,
            scope=scope,
            target_id=target_id,
            value=value or {},
            actor_id="operator:miche",
            reason=f"Operator requires {action}.",
        )

    def _models(self) -> None:
        for name, cost in (("cheap", 0.1), ("strong", 2.0)):
            profile = ModelProfile(
                provider="test",
                model=name,
                context_capacity=16_000,
                supports_tools=False,
                input_cost_per_million=cost,
                output_cost_per_million=cost,
            )
            self.runtime.register_model(profile)
            for index in range(3):
                self.runtime.record_model_outcome(
                    ModelOutcome(
                        model_id=profile.id,
                        task_class="coding",
                        success=True,
                        quality=0.95,
                        latency_ms=10,
                        input_tokens=10,
                        output_tokens=10,
                        tool_attempts=0,
                        tool_successes=0,
                        evidence=(f"verified:{name}:{index}",),
                    )
                )

    @staticmethod
    def factory_request() -> AgentFactoryRequest:
        return AgentFactoryRequest.from_dict(
            {
                "objective": "Plan bounded independent work.",
                "task_class": "research",
                "workstreams": [
                    {
                        "id": f"stream-{index}",
                        "objective": f"Review source {index}.",
                        "task_scope": [f"source-{index}"],
                        "memory_scope": ["alpha"],
                    }
                    for index in range(3)
                ],
                "tools": [],
                "skills": [],
                "model_policy": {
                    "allowed_models": ["local:test"],
                    "preferred_model": "local:test",
                    "local_only": True,
                    "allow_fallback": False,
                },
                "token_budget": 10_000,
                "money_budget": 5.0,
                "time_budget": 600,
                "permissions": [],
                "verification_requirements": ["Retain evidence."],
                "estimated_single_agent_tokens": 2_000,
                "estimated_single_agent_seconds": 120,
                "estimated_context_tokens": 100,
                "estimated_cost_per_1k_tokens": 0.01,
                "complexity": 0.8,
                "uncertainty": 0.7,
                "research_breadth": 0.8,
                "parallelizable": True,
                "requires_critique": False,
                "requires_synthesis": True,
                "value_score": 1.0,
                "max_agents": 8,
            }
        )

    def test_memory_pin_and_block_are_applied_and_recorded(self):
        pinned_id = self.runtime.remember(
            "semantic", "Pinned operator fact.", scope="alpha"
        )
        blocked_id = self.runtime.remember(
            "semantic", "Blocked operator fact.", scope="alpha"
        )

        pinned = self.runtime.apply_human_override(
            self.request("pin_memory", target_id=pinned_id)
        )
        blocked = self.runtime.apply_human_override(
            self.request("block_memory", target_id=blocked_id)
        )

        self.assertEqual(pinned.status, "active")
        self.assertTrue(self.runtime.db.memories.get(pinned_id).pinned)
        self.assertEqual(blocked.status, "active")
        self.assertEqual(
            self.runtime.db.memories.get(blocked_id).lifecycle_state.value,
            "archived",
        )
        with self.assertRaisesRegex(Exception, "immutable"):
            self.runtime.db.connection.execute(
                "UPDATE human_overrides SET reason='changed' WHERE id=?",
                (pinned.id,),
            )

    def test_forced_model_must_still_be_eligible(self):
        self._models()
        override = self.runtime.apply_human_override(
            self.request(
                "force_model", target_id="test:strong", scope="coding"
            )
        )
        route = self.runtime.route_model(
            RouteRequest(
                task_class="coding",
                quality_threshold=0.8,
                minimum_success_rate=0.8,
                estimated_input_tokens=100,
                estimated_output_tokens=50,
                required_context=100,
                minimum_samples=3,
                confidence_z=0,
            )
        )
        self.assertEqual(route.selected_model_id, "test:strong")

        self.runtime.revoke_human_override(
            override.id,
            actor_id="operator:miche",
            reason="Return routing to evidence policy.",
        )
        route = self.runtime.route_model(
            RouteRequest(
                task_class="coding",
                quality_threshold=0.8,
                minimum_success_rate=0.8,
                estimated_input_tokens=100,
                estimated_output_tokens=50,
                required_context=100,
                minimum_samples=3,
                confidence_z=0,
            )
        )
        self.assertEqual(route.selected_model_id, "test:cheap")

    def test_force_disable_skill_and_activation_guard(self):
        forced_id = self.runtime.register_skill(
            "forced-review",
            "Review the exact operator-selected evidence.",
            description="Narrow evidence review",
            trusted=True,
        )
        disabled_id = self.runtime.register_skill(
            "disabled-review",
            "Review unrelated evidence.",
            description="Evidence review",
            trusted=True,
        )
        self.runtime.apply_human_override(
            self.request(
                "force_skill", target_id=forced_id, scope="diagnostics"
            )
        )
        self.runtime.apply_human_override(
            self.request("disable_skill", target_id=disabled_id)
        )

        route = self.runtime.route_skills(
            "A task with no lexical overlap",
            task_class="diagnostics",
            token_budget=1_000,
        )
        selected = {item.id for item in route.selected}
        self.assertIn(forced_id, selected)
        self.assertNotIn(disabled_id, selected)
        with self.assertRaises(PermissionError):
            self.runtime.activate_skill(disabled_id)

    def test_agent_learning_and_architecture_controls_are_enforced(self):
        self.runtime.apply_human_override(
            self.request(
                "limit_agents", value={"max_agents": 1}, scope="research"
            )
        )
        self.runtime.apply_human_override(
            self.request("disable_learning", scope="coding")
        )
        self.runtime.apply_human_override(
            self.request("freeze_architecture", scope="project-alpha")
        )

        plan = self.runtime.plan_agent_factory(self.factory_request())
        self.assertEqual(plan.request.max_agents, 1)
        self.assertEqual(plan.worker_count, 1)
        with self.assertRaises(PermissionError):
            self.runtime.learn(SimpleNamespace(task_class="coding"))
        with self.assertRaises(PermissionError):
            self.runtime.assert_architecture_mutable("project-alpha")
        self.runtime.assert_architecture_mutable("project-beta")

    def test_improvement_policy_rollback_and_cli_are_audited(self):
        target = "context_thresholds"
        incumbent = self.runtime.improvement_policies.active(target)
        candidate_config = dict(incumbent.config)
        candidate_config["minimum_optional_utility_bps"] += 1
        candidate = self.runtime.improvement_policies.create_candidate(
            target, candidate_config, parent_id=incumbent.id
        )
        self.runtime.improvement_policies.promote(
            target, candidate.id, expected_head_id=incumbent.id
        )
        request = self.request(
            "rollback_version",
            target_id=target,
            value={
                "version_kind": "improvement_policy",
                "expected_head_id": candidate.id,
            },
        )
        path = Path(self.temporary.name) / "override.json"
        path.write_text(
            json.dumps(
                {
                    "action": request.action,
                    "scope": request.scope,
                    "target_id": request.target_id,
                    "value": request.value,
                    "actor_id": request.actor_id,
                    "reason": request.reason,
                }
            ),
            encoding="utf-8",
        )
        self.runtime.close()
        output = StringIO()
        with redirect_stdout(output):
            self.assertEqual(
                main(
                    [
                        "--db",
                        str(self.database),
                        "overrides",
                        "apply",
                        str(path),
                    ]
                ),
                0,
            )
        payload = json.loads(output.getvalue())
        self.assertEqual(payload["status"], "applied")
        self.runtime = AdaptiveRuntime(self.database)
        self.assertEqual(
            self.runtime.improvement_policies.active(target).id,
            incumbent.id,
        )
        self.assertEqual(
            payload["events"][-1]["details"]["restored_version_id"],
            incumbent.id,
        )

    def test_contract_rejects_unsafe_shapes_and_duplicate_controls(self):
        with self.assertRaises(ValueError):
            HumanOverrideRequest.from_dict(
                {
                    "action": "limit_agents",
                    "scope": "global",
                    "target_id": None,
                    "value": {"max_agents": True},
                    "actor_id": "operator",
                    "reason": "Limit workers.",
                }
            )
        self.runtime.apply_human_override(
            self.request("disable_learning")
        )
        with self.assertRaises(ValueError):
            self.runtime.apply_human_override(
                self.request("disable_learning")
            )


if __name__ == "__main__":
    unittest.main()
