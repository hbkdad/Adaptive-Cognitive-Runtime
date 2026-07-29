from __future__ import annotations

import json
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

from acr_runtime import (
    AdaptiveRuntime,
    AgentFactoryRequest,
    ModelOutcome,
    ModelProfile,
    RouteRequest,
)
from acr_runtime.cli import main


class ExplainabilityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.database = Path(self.directory.name) / "acr.db"
        self.runtime = AdaptiveRuntime(self.database)

    def tearDown(self) -> None:
        self.runtime.close()
        self.directory.cleanup()

    def _route(self):
        for model, cost, quality in (
            ("cheap", 0.1, 0.85),
            ("strong", 2.0, 0.95),
        ):
            profile = ModelProfile(
                provider="test",
                model=model,
                context_capacity=16_000,
                supports_tools=True,
                input_cost_per_million=cost,
                output_cost_per_million=cost * 2,
            )
            self.runtime.register_model(profile)
            for index in range(3):
                self.runtime.record_model_outcome(
                    ModelOutcome(
                        model_id=profile.id,
                        task_class="coding",
                        success=True,
                        quality=quality,
                        latency_ms=100 + index,
                        input_tokens=100,
                        output_tokens=50,
                        tool_attempts=1,
                        tool_successes=1,
                        evidence=(f"verified:{model}:{index}",),
                    )
                )
        return self.runtime.route_model(
            RouteRequest(
                task_class="coding",
                quality_threshold=0.8,
                minimum_success_rate=0.8,
                estimated_input_tokens=1_000,
                estimated_output_tokens=500,
                required_context=4_000,
                requires_tools=True,
                minimum_tool_reliability=0.8,
                minimum_samples=3,
                confidence_z=0,
            )
        )

    @staticmethod
    def _factory_request() -> AgentFactoryRequest:
        return AgentFactoryRequest.from_dict(
            {
                "objective": "SENSITIVE OBJECTIVE MUST NOT BE RETURNED",
                "task_class": "research",
                "workstreams": [
                    {
                        "id": "bounded-stream",
                        "objective": "SENSITIVE WORKSTREAM MUST NOT BE RETURNED",
                        "task_scope": ["bounded-source"],
                        "memory_scope": ["project-alpha"],
                    }
                ],
                "tools": [],
                "skills": [],
                "model_policy": {
                    "allowed_models": ["local:test"],
                    "preferred_model": "local:test",
                    "local_only": True,
                    "allow_fallback": False,
                },
                "token_budget": 5_000,
                "money_budget": 1.0,
                "time_budget": 300,
                "permissions": ["read"],
                "verification_requirements": ["Cite retained evidence."],
                "estimated_single_agent_tokens": 2_000,
                "estimated_single_agent_seconds": 120,
                "estimated_context_tokens": 200,
                "estimated_cost_per_1k_tokens": 0.01,
                "complexity": 0.2,
                "uncertainty": 0.2,
                "research_breadth": 0.2,
                "parallelizable": False,
                "requires_critique": False,
                "requires_synthesis": False,
                "value_score": 0.8,
                "max_agents": 4,
            }
        )

    def test_model_explanation_replays_stored_candidate_scores(self):
        route = self._route()

        result = self.runtime.explainability.model(route.id)

        self.assertEqual(result["status"], "supported")
        self.assertEqual(result["facts"]["selected_model_id"], "test:cheap")
        self.assertEqual(
            result["facts"]["selected_candidate"],
            next(
                item
                for item in route.candidates
                if item["model_id"] == route.selected_model_id
            ),
        )
        self.assertFalse(result["narrative_generated"])
        self.assertEqual(result["sources"][0]["table"], "model_routes")

    def test_memory_skill_and_context_explanations_use_compiler_rows(self):
        memory_id = self.runtime.remember(
            "semantic",
            "SQLite FTS5 provides local full-text retrieval.",
            scope="alpha",
            confidence=0.98,
            importance=0.9,
        )
        skill_id = self.runtime.register_skill(
            "sqlite-diagnostics",
            "Inspect SQLite schema and FTS5 queries.",
            description="SQLite FTS5 diagnostics",
            tags=["sqlite", "fts5"],
            trusted=True,
        )
        bundle = self.runtime.compile_context(
            "Diagnose the SQLite FTS5 index",
            scope="alpha",
            token_budget=300,
        )

        memory = self.runtime.explainability.memory(bundle.task_id, memory_id)
        skill = self.runtime.explainability.skill(bundle.task_id, skill_id)
        context = self.runtime.explainability.context(bundle.task_id)

        self.assertEqual(memory["status"], "supported")
        self.assertEqual(memory["facts"]["utility"], next(
            block.utility for block in bundle.blocks
            if block.source_id == memory_id
        ))
        self.assertEqual(skill["status"], "supported")
        self.assertTrue(skill["facts"]["compiler_selected"])
        self.assertEqual(context["facts"]["summed_context_use_tokens"], bundle.selected_tokens)
        self.assertTrue(context["facts"]["totals_match"])
        self.assertEqual(
            context["facts"]["sources"],
            sorted(
                context["facts"]["sources"],
                key=lambda item: (
                    -item["tokens"], item["source_type"], item["source_id"]
                ),
            ),
        )

    def test_missing_selection_evidence_is_reported_not_reconstructed(self):
        result = self.runtime.explainability.memory(
            "unknown-task", "unknown-memory"
        )

        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(
            result["limitations"], ["no_compiled_memory_use_was_retained"]
        )
        self.assertFalse(result["narrative_generated"])

    def test_agent_plan_is_not_misrepresented_as_a_spawn_and_hides_objectives(self):
        plan = self.runtime.plan_agent_factory(self._factory_request())

        result = self.runtime.explainability.agent(plan.id)
        encoded = json.dumps(result)

        self.assertEqual(result["status"], "not_executed")
        self.assertEqual(result["facts"]["plan_status"], "proposed")
        self.assertIn(
            "no_agent_spawn_or_execution_receipt_exists",
            result["limitations"],
        )
        self.assertNotIn("SENSITIVE OBJECTIVE", encoded)
        self.assertNotIn("SENSITIVE WORKSTREAM", encoded)
        self.assertNotIn("spec_json", encoded)

    def test_supersession_and_cli_report_retained_causes(self):
        old_id = self.runtime.remember(
            "semantic", "The service uses an old index.", scope="alpha"
        )
        new_id = self.runtime.remember(
            "semantic",
            "The service uses the current index.",
            scope="alpha",
            supersedes=old_id,
        )
        forgotten = self.runtime.explainability.forgotten(old_id)
        self.assertEqual(forgotten["status"], "supported")
        self.assertEqual(forgotten["facts"]["superseded_by"], new_id)

        route = self._route()
        self.runtime.close()
        output = StringIO()
        with redirect_stdout(output):
            exit_code = main(
                ["--db", str(self.database), "explain", "model", route.id]
            )
        self.assertEqual(exit_code, 0)
        payload = json.loads(output.getvalue())
        self.assertEqual(payload["facts"]["selected_model_id"], "test:cheap")
        self.assertFalse(payload["narrative_generated"])
        self.runtime = AdaptiveRuntime(self.database)


if __name__ == "__main__":
    unittest.main()
