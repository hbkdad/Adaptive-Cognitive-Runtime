from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from acr_runtime.cli import main
from acr_runtime.db import RuntimeDB
from acr_runtime.model_router import (
    ModelOutcome,
    ModelProfile,
    ModelRouter,
    RouteAttempt,
    RouteRequest,
)
from acr_runtime.multi_model import (
    BaselineWorkflowOutcome,
    MultiModelCoordinator,
    MultiModelWorkflowRequest,
    WorkflowStageRequest,
)


class MultiModelCoordinatorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.database = RuntimeDB(Path(self.temporary.name) / "acr.db")
        self.router = ModelRouter(self.database.connection)
        self.coordinator = MultiModelCoordinator(
            self.database.connection, self.router
        )
        for model, tier, local, cost in (
            ("small-local", "small", True, 0.0),
            ("medium", "medium", False, 0.5),
            ("strong", "strong", False, 3.0),
            ("baseline", "strong", False, 4.0),
        ):
            self.router.register(
                ModelProfile(
                    provider="test",
                    model=model,
                    context_capacity=32_000,
                    supports_tools=True,
                    input_cost_per_million=cost,
                    output_cost_per_million=cost,
                    local=local,
                    tier=tier,
                )
            )
        for model_id, task_class in (
            ("test:small-local", "classification"),
            ("test:medium", "implementation"),
            ("test:strong", "critique"),
        ):
            for index in range(3):
                self.router.record_outcome(
                    ModelOutcome(
                        model_id=model_id,
                        task_class=task_class,
                        success=True,
                        quality=0.9,
                        latency_ms=100,
                        input_tokens=100,
                        output_tokens=50,
                        tool_attempts=0,
                        tool_successes=0,
                        evidence=(f"benchmark:{model_id}:{index}",),
                    )
                )

    def tearDown(self) -> None:
        self.database.close()
        self.temporary.cleanup()

    @staticmethod
    def route(task_class: str) -> RouteRequest:
        return RouteRequest(
            task_class=task_class,
            quality_threshold=0.7,
            minimum_success_rate=0.7,
            estimated_input_tokens=200,
            estimated_output_tokens=100,
            required_context=1_000,
            minimum_samples=3,
            confidence_z=0,
        )

    def request(self, workflow_class: str = "feature-build"):
        return MultiModelWorkflowRequest(
            workflow_class=workflow_class,
            baseline_model_id="test:baseline",
            stages=(
                WorkflowStageRequest(
                    id="classify",
                    role="classification",
                    route=self.route("classification"),
                ),
                WorkflowStageRequest(
                    id="implement",
                    role="implementation",
                    route=self.route("implementation"),
                    dependencies=("classify",),
                ),
                WorkflowStageRequest(
                    id="review",
                    role="critique",
                    route=self.route("critique"),
                    dependencies=("implement",),
                ),
            ),
        )

    def complete(self, workflow_id: str) -> None:
        workflow = self.coordinator.get(workflow_id)
        for stage in workflow.stages:
            self.router.record_attempt(
                str(stage["route_id"]),
                RouteAttempt(
                    model_id=str(stage["selected_model_id"]),
                    verification_passed=True,
                    confidence=0.95,
                    quality=0.9,
                    latency_ms=100,
                    input_tokens=100,
                    output_tokens=50,
                    tool_attempts=0,
                    tool_successes=0,
                    evidence=(f"evaluation:{workflow_id}:{stage['id']}",),
                    input_cost=0.01,
                    output_cost=0.01,
                ),
            )

    @staticmethod
    def baseline():
        return BaselineWorkflowOutcome(
            success=True,
            quality=0.8,
            latency_ms=500,
            input_tokens=500,
            output_tokens=250,
            cost=0.10,
            evidence=("paired-evaluation:baseline",),
        )

    def test_role_tiers_select_three_distinct_models_without_execution(self) -> None:
        workflow = self.coordinator.plan(self.request())

        self.assertEqual(workflow.state, "planned")
        self.assertEqual(
            [stage["required_tier"] for stage in workflow.stages],
            ["small", "medium", "strong"],
        )
        self.assertEqual(
            [stage["selected_model_id"] for stage in workflow.stages],
            ["test:small-local", "test:medium", "test:strong"],
        )
        self.assertTrue(all(
            self.router.get(str(stage["route_id"])).state == "selected"
            for stage in workflow.stages
        ))

    def test_outcome_requires_verified_stages_and_derives_specialized_metrics(self):
        workflow = self.coordinator.plan(self.request())
        with self.assertRaisesRegex(ValueError, "must complete"):
            self.coordinator.record_outcome(workflow.id, self.baseline())

        self.complete(workflow.id)
        outcome = self.coordinator.record_outcome(workflow.id, self.baseline())

        self.assertAlmostEqual(outcome["quality_delta"], 0.1)
        self.assertEqual(outcome["latency_saved_ms"], 200)
        self.assertEqual(outcome["tokens_saved"], 300)
        self.assertAlmostEqual(outcome["cost_saved"], 0.04)
        self.assertEqual(self.coordinator.get(workflow.id).state, "evaluated")

    def test_repeated_paired_evidence_is_required_before_benefit_claim(self) -> None:
        for index in range(3):
            workflow = self.coordinator.plan(self.request("feature-build"))
            self.complete(workflow.id)
            self.coordinator.record_outcome(workflow.id, self.baseline())
            report = self.coordinator.benefit_report("feature-build")
            self.assertEqual(
                report["status"],
                "beneficial" if index == 2 else "insufficient_evidence",
            )
        self.assertEqual(report["pairs"], 3)
        self.assertGreater(report["metrics"]["quality_delta"], 0.02)

    def test_bad_dependencies_and_missing_role_tier_fail_closed(self) -> None:
        with self.assertRaisesRegex(ValueError, "earlier"):
            MultiModelWorkflowRequest(
                workflow_class="bad",
                baseline_model_id="test:baseline",
                stages=(
                    WorkflowStageRequest(
                        id="one",
                        role="classification",
                        route=self.route("classification"),
                        dependencies=("later",),
                    ),
                    WorkflowStageRequest(
                        id="later",
                        role="implementation",
                        route=self.route("implementation"),
                    ),
                ),
            )

        self.database.connection.execute(
            "UPDATE model_profiles SET active=0 WHERE tier='medium'"
        )
        self.database.connection.commit()
        workflow = self.coordinator.plan(self.request("unavailable"))
        self.assertEqual(workflow.state, "unavailable")
        self.assertIn("stage_unavailable:implement", workflow.reasons)

    def test_cli_plans_and_reports_without_executing_models(self) -> None:
        request_file = Path(self.temporary.name) / "workflow.json"
        request_file.write_text(
            json.dumps(self.request("cli-feature").as_dict()),
            encoding="utf-8",
        )
        output = io.StringIO()
        with redirect_stdout(output):
            code = main([
                "--db",
                str(self.database.path),
                "models",
                "workflow-plan",
                str(request_file),
            ])
        self.assertEqual(code, 0)
        workflow = json.loads(output.getvalue())
        self.assertEqual(workflow["state"], "planned")

        output = io.StringIO()
        with redirect_stdout(output):
            code = main([
                "--db",
                str(self.database.path),
                "models",
                "workflow-report",
                workflow["id"],
            ])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output.getvalue())["stages"][0]["role"],
                         "classification")


if __name__ == "__main__":
    unittest.main()
