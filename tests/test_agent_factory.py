from __future__ import annotations

import json
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

from acr_runtime import AdaptiveRuntime, AgentFactoryRequest, Settings
from acr_runtime.cli import main


class AgentFactoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        root = Path(self.directory.name)
        self.database = root / "acr.db"
        self.runtime = AdaptiveRuntime(
            settings=Settings(
                database=self.database,
                state_dir=root / "state",
                skills_dir=root / "skills",
                provider=None,
                ollama_url="http://127.0.0.1:11434",
            )
        )

    def tearDown(self) -> None:
        self.runtime.close()
        self.directory.cleanup()

    @staticmethod
    def payload(workstreams: int = 1, **overrides):
        payload = {
            "objective": "Produce a bounded, verified diagnosis.",
            "task_class": "diagnostics",
            "workstreams": [
                {
                    "id": f"stream-{index}",
                    "objective": f"Investigate bounded area {index}.",
                    "task_scope": [f"diagnostics-{index}"],
                    "memory_scope": ["project-alpha"],
                }
                for index in range(1, workstreams + 1)
            ],
            "tools": [],
            "skills": [],
            "model_policy": {
                "allowed_models": ["qwen2.5-coder:7b"],
                "preferred_model": "qwen2.5-coder:7b",
                "local_only": True,
                "allow_fallback": False,
            },
            "token_budget": 10_000,
            "money_budget": 1.0,
            "time_budget": 600,
            "permissions": [],
            "verification_requirements": [
                "Return bounded evidence for every conclusion."
            ],
            "estimated_single_agent_tokens": 5_000,
            "estimated_single_agent_seconds": 300,
            "estimated_context_tokens": 100,
            "estimated_cost_per_1k_tokens": 0.01,
            "complexity": 1.0,
            "uncertainty": 1.0,
            "research_breadth": 0.2,
            "parallelizable": False,
            "requires_critique": False,
            "requires_synthesis": False,
            "value_score": 1.0,
            "max_agents": 8,
        }
        payload.update(overrides)
        return payload

    def plan(self, workstreams: int = 1, **overrides):
        return self.runtime.plan_agent_factory(
            AgentFactoryRequest.from_dict(
                self.payload(workstreams, **overrides)
            )
        )

    def test_simple_work_uses_one_agent_and_never_creates_a_task(self):
        before = self.runtime.db.connection.execute(
            "SELECT COUNT(*) FROM tasks"
        ).fetchone()[0]

        plan = self.plan(complexity=0.2, uncertainty=0.1)

        self.assertEqual(plan.selected_topology, "single_agent")
        self.assertEqual(plan.worker_count, 1)
        self.assertEqual(plan.workers[0].status, "proposed")
        self.assertEqual(
            self.runtime.db.connection.execute(
                "SELECT COUNT(*) FROM tasks"
            ).fetchone()[0],
            before,
        )
        self.assertEqual(len(self.runtime.list_agent_specs()), 0)

    def test_parallel_workers_require_measurable_benefit_and_are_scoped(self):
        plan = self.plan(
            3,
            parallelizable=True,
            requires_synthesis=False,
        )

        self.assertEqual(plan.selected_topology, "parallel_workers")
        self.assertEqual(plan.worker_count, 3)
        self.assertEqual(
            [worker.spec.task_scope for worker in plan.workers],
            [
                ("diagnostics-1",),
                ("diagnostics-2",),
                ("diagnostics-3",),
            ],
        )
        self.assertTrue(
            all(worker.spec.communication.mode == "none" for worker in plan.workers)
        )
        estimate = plan.selected_estimate
        self.assertGreaterEqual(estimate.expected_quality_gain, 0.05)
        self.assertGreater(estimate.parallelism_benefit, 0)
        self.assertGreater(estimate.additional_token_cost, 0)
        self.assertGreaterEqual(estimate.coordination_overhead, 0)

    def test_specialist_and_critic_is_selected_for_uncertain_single_stream(self):
        plan = self.plan(requires_critique=True)

        self.assertEqual(plan.selected_topology, "specialist_critic")
        self.assertEqual(
            [worker.responsibility for worker in plan.workers],
            ["specialist", "critic"],
        )
        self.assertTrue(
            all(
                worker.spec.communication.mode == "manager_only"
                for worker in plan.workers
            )
        )

    def test_budget_and_agent_limits_reject_decorative_complexity(self):
        plan = self.plan(
            3,
            complexity=0.0,
            uncertainty=0.0,
            parallelizable=True,
            max_agents=2,
        )

        self.assertEqual(plan.selected_topology, "single_agent")
        alternatives = [
            candidate
            for candidate in plan.candidates
            if candidate.topology != "single_agent"
        ]
        self.assertTrue(alternatives)
        self.assertTrue(all(not item.feasible for item in alternatives))
        self.assertTrue(
            any("agent_limit" in item.rejection_reasons for item in alternatives)
        )

    def test_research_topology_is_costed_and_plan_round_trips(self):
        plan = self.plan(
            2,
            research_breadth=0.9,
            requires_synthesis=True,
        )

        candidate = next(
            item
            for item in plan.candidates
            if item.topology == "researchers_synthesizer"
        )
        self.assertGreater(candidate.expected_quality_gain, 0)
        self.assertGreater(candidate.additional_token_cost, 0)
        self.assertEqual(
            plan.selected_topology, "researchers_synthesizer"
        )
        loaded = self.runtime.agent_factory_plan(plan.id)
        self.assertEqual(loaded.as_dict(), plan.as_dict())

    def test_request_shape_numbers_and_baseline_budget_fail_closed(self):
        with self.assertRaises(ValueError):
            AgentFactoryRequest.from_dict(
                {**self.payload(), "personality": "decorative"}
            )
        with self.assertRaises(ValueError):
            AgentFactoryRequest.from_dict(
                self.payload(complexity=float("nan"))
            )
        with self.assertRaisesRegex(ValueError, "money budget"):
            AgentFactoryRequest.from_dict(
                self.payload(money_budget=0.001)
            )

    def test_cli_plan_and_report(self):
        request_path = Path(self.directory.name) / "factory-request.json"
        request_path.write_text(json.dumps(self.payload()), encoding="utf-8")
        self.runtime.close()
        output = StringIO()
        with redirect_stdout(output):
            code = main(
                [
                    "--db",
                    str(self.database),
                    "agents",
                    "factory-plan",
                    str(request_path),
                ]
            )
        self.assertEqual(code, 0)
        planned = json.loads(output.getvalue())
        output = StringIO()
        with redirect_stdout(output):
            code = main(
                [
                    "--db",
                    str(self.database),
                    "agents",
                    "factory-report",
                    planned["id"],
                ]
            )
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output.getvalue()), planned)
        self.runtime = AdaptiveRuntime(database=self.database)


if __name__ == "__main__":
    unittest.main()
