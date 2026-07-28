from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

from acr_runtime import (
    AdaptiveRuntime,
    AgentFactoryRequest,
    Settings,
    TopologyOutcomeCreate,
)
from acr_runtime.cli import main


class TopologyLearningTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        root = Path(self.directory.name)
        self.runtime = AdaptiveRuntime(
            settings=Settings(
                database=root / "acr.db",
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
    def request(**overrides) -> AgentFactoryRequest:
        payload = {
            "objective": "Research and synthesize a verified answer.",
            "task_class": "competitor-research",
            "workstreams": [
                {
                    "id": "market",
                    "objective": "Research the market.",
                    "task_scope": ["market-research"],
                    "memory_scope": ["project-alpha"],
                },
                {
                    "id": "product",
                    "objective": "Research the products.",
                    "task_scope": ["product-research"],
                    "memory_scope": ["project-alpha"],
                },
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
            "verification_requirements": ["Cite bounded evidence."],
            "estimated_single_agent_tokens": 5_000,
            "estimated_single_agent_seconds": 400,
            "estimated_context_tokens": 100,
            "estimated_cost_per_1k_tokens": 0.01,
            "complexity": 0.9,
            "uncertainty": 0.8,
            "research_breadth": 0.9,
            "parallelizable": True,
            "requires_critique": False,
            "requires_synthesis": True,
            "value_score": 1.0,
            "max_agents": 4,
        }
        payload.update(overrides)
        return AgentFactoryRequest.from_dict(payload)

    def record(
        self,
        *,
        success: bool,
        verified: bool,
        quality: float,
        tokens: int = 6_000,
        latency_ms: int = 300_000,
    ):
        plan = self.runtime.plan_agent_factory(self.request())
        return self.runtime.record_topology_outcome(
            TopologyOutcomeCreate(
                plan_id=plan.id,
                models_used=("qwen2.5-coder:7b",),
                skills_used=(),
                tokens=tokens,
                latency_ms=latency_ms,
                quality=quality,
                success=success,
                verification_passed=verified,
                verification_evidence=("benchmark:competitor-research-v1",),
            )
        )

    def test_successful_verified_structure_becomes_recipe(self):
        outcome = self.record(success=True, verified=True, quality=0.9)

        row = self.runtime.db.connection.execute(
            "SELECT * FROM agent_topology_recipes"
        ).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row["task_class"], "competitor-research")
        self.assertEqual(outcome.models, ("qwen2.5-coder:7b",))
        self.assertEqual(outcome.skills, ())
        self.assertGreater(outcome.parallelism, 0)
        utility = self.runtime.utility_snapshot(
            "agent_topology", outcome.structure_hash
        )
        self.assertEqual(utility.positive_count, 1)
        self.assertEqual(utility.observed_uses, 1)

    def test_failure_and_unverified_success_do_not_create_recipes(self):
        self.record(success=False, verified=True, quality=0.4)
        unverified = self.record(success=True, verified=False, quality=0.9)

        self.assertEqual(
            self.runtime.db.connection.execute(
                "SELECT COUNT(*) FROM agent_topology_outcomes"
            ).fetchone()[0],
            2,
        )
        self.assertEqual(
            self.runtime.db.connection.execute(
                "SELECT COUNT(*) FROM agent_topology_recipes"
            ).fetchone()[0],
            0,
        )
        utility = self.runtime.utility_snapshot(
            "agent_topology", unverified.structure_hash
        )
        self.assertEqual(utility.observed_uses, 2)
        self.assertEqual(utility.evidenced_uses, 1)

    def test_recommendation_requires_repeated_comparable_evidence(self):
        first = self.record(success=True, verified=True, quality=0.9)
        early = self.runtime.recommend_topology(self.request())
        self.assertFalse(early.available)
        self.assertIn(
            "insufficient_trials",
            early.candidates[0].rejection_reasons,
        )

        second = self.record(success=True, verified=True, quality=0.9)
        failed = self.record(success=False, verified=True, quality=0.4)
        recommendation = self.runtime.recommend_topology(self.request())

        self.assertTrue(recommendation.available)
        self.assertIsNotNone(recommendation.selected_recipe_id)
        candidate = recommendation.candidates[0]
        self.assertEqual(candidate.trials, 3)
        self.assertEqual(candidate.successes, 2)
        self.assertEqual(candidate.verified_successes, 2)
        self.assertAlmostEqual(candidate.success_rate, 2 / 3, places=5)
        self.assertEqual(
            {first.structure_hash, second.structure_hash, failed.structure_hash},
            {candidate.recipe.structure_hash},
        )

    def test_structure_recipe_ignores_task_specific_workstream_names(self):
        first = self.record(success=True, verified=True, quality=0.9)
        payload = json.loads(json.dumps(self.request().as_dict()))
        payload["workstreams"][0]["id"] = "pricing"
        payload["workstreams"][0]["objective"] = "Research pricing."
        payload["workstreams"][1]["id"] = "positioning"
        payload["workstreams"][1]["objective"] = "Research positioning."
        plan = self.runtime.plan_agent_factory(
            AgentFactoryRequest.from_dict(payload)
        )
        second = self.runtime.record_topology_outcome(
            TopologyOutcomeCreate(
                plan_id=plan.id,
                models_used=("qwen2.5-coder:7b",),
                skills_used=(),
                tokens=6_000,
                latency_ms=300_000,
                quality=0.9,
                success=True,
                verification_passed=True,
                verification_evidence=("benchmark:v1",),
            )
        )

        self.assertEqual(first.structure_hash, second.structure_hash)
        self.assertEqual(
            self.runtime.db.connection.execute(
                "SELECT COUNT(*) FROM agent_topology_recipes"
            ).fetchone()[0],
            1,
        )

    def test_budget_mismatch_keeps_recipe_advisory_and_unavailable(self):
        for _ in range(3):
            self.record(success=True, verified=True, quality=0.9)
        request = self.request(token_budget=5_500)

        recommendation = self.runtime.recommend_topology(request)

        self.assertFalse(recommendation.available)
        self.assertIn(
            "token_budget",
            recommendation.candidates[0].rejection_reasons,
        )

    def test_one_plan_can_have_only_one_append_only_outcome(self):
        plan = self.runtime.plan_agent_factory(self.request())
        create = TopologyOutcomeCreate(
            plan_id=plan.id,
            models_used=("qwen2.5-coder:7b",),
            skills_used=(),
            tokens=5_000,
            latency_ms=200_000,
            quality=0.9,
            success=True,
            verification_passed=True,
            verification_evidence=("benchmark:v1",),
        )
        self.runtime.record_topology_outcome(create)
        with self.assertRaises(sqlite3.IntegrityError):
            self.runtime.record_topology_outcome(create)

    def test_outcome_input_is_strict_and_evidence_required(self):
        payload = {
            "plan_id": "plan",
            "models_used": ["qwen2.5-coder:7b"],
            "skills_used": [],
            "tokens": 1,
            "latency_ms": 1,
            "quality": 1.0,
            "success": True,
            "verification_passed": True,
            "verification_evidence": ["benchmark:v1"],
        }
        self.assertEqual(
            TopologyOutcomeCreate.from_dict(payload).plan_id, "plan"
        )
        with self.assertRaises(ValueError):
            TopologyOutcomeCreate.from_dict({**payload, "models": []})
        with self.assertRaises(ValueError):
            TopologyOutcomeCreate.from_dict(
                {**payload, "verification_evidence": []}
            )
        plan = self.runtime.plan_agent_factory(self.request())
        with self.assertRaisesRegex(ValueError, "outside"):
            self.runtime.record_topology_outcome(
                TopologyOutcomeCreate(
                    plan_id=plan.id,
                    models_used=("unapproved-model",),
                    skills_used=(),
                    tokens=1,
                    latency_ms=1,
                    quality=1.0,
                    success=True,
                    verification_passed=True,
                    verification_evidence=("benchmark:v1",),
                )
            )

    def test_cli_records_and_reports_advisory_recommendation(self):
        root = Path(self.directory.name)
        request = self.request()
        plan = self.runtime.plan_agent_factory(request)
        outcome_path = root / "outcome.json"
        outcome_path.write_text(
            json.dumps(
                {
                    "plan_id": plan.id,
                    "models_used": ["qwen2.5-coder:7b"],
                    "skills_used": [],
                    "tokens": 6_000,
                    "latency_ms": 300_000,
                    "quality": 0.9,
                    "success": True,
                    "verification_passed": True,
                    "verification_evidence": ["benchmark:v1"],
                }
            ),
            encoding="utf-8",
        )
        request_path = root / "request.json"
        request_path.write_text(
            json.dumps(request.as_dict()), encoding="utf-8"
        )
        database = self.runtime.settings.database
        self.runtime.close()
        output = StringIO()
        with redirect_stdout(output):
            self.assertEqual(
                main(
                    [
                        "--db",
                        str(database),
                        "agents",
                        "topology-record",
                        str(outcome_path),
                    ]
                ),
                0,
            )
        self.assertEqual(json.loads(output.getvalue())["plan_id"], plan.id)
        output = StringIO()
        with redirect_stdout(output):
            self.assertEqual(
                main(
                    [
                        "--db",
                        str(database),
                        "agents",
                        "topology-recommend",
                        str(request_path),
                    ]
                ),
                0,
            )
        self.assertFalse(json.loads(output.getvalue())["available"])
        self.runtime = AdaptiveRuntime(database=database)


if __name__ == "__main__":
    unittest.main()
