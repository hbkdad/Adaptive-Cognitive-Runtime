from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from contextlib import redirect_stdout
from dataclasses import replace
from io import StringIO
from pathlib import Path

from acr_runtime import (
    AdaptiveRuntime,
    PlanSnapshot,
    PlanningRequest,
    Settings,
    SkillValidator,
    ValidationEvidence,
)
from acr_runtime.cli import main


class PassingSandbox:
    def run(self, package, *, stage, cases):
        return ValidationEvidence("passed", 1.0, {"stage": stage})


class PassingEvaluator:
    def review(self, package):
        return ValidationEvidence("passed", 0.95, {"review": "passed"})


class PassingBenchmark:
    def compare(self, package, *, incumbent_skill_id):
        return ValidationEvidence(
            "passed",
            0.95,
            {
                "candidate_quality": 0.95,
                "incumbent_quality": 0.90,
                "candidate_cost": 0.08,
                "incumbent_cost": 0.10,
            },
        )


class HierarchicalPlannerTests(unittest.TestCase):
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
    def work_hint(
        identifier: str,
        *,
        depends_on=(),
        complexity=0.8,
        parallelizable=True,
        required_tools=(),
        required_skills=(),
        task_scope=None,
    ):
        return {
            "id": identifier,
            "objective": f"Complete bounded {identifier} work.",
            "depends_on": list(depends_on),
            "task_scope": list(task_scope or (f"{identifier}-work",)),
            "memory_scope": ["project-alpha"],
            "required_tools": list(required_tools),
            "required_skills": list(required_skills),
            "complexity": complexity,
            "parallelizable": parallelizable,
            "verification_requirements": [
                f"Verify the {identifier} output with bounded evidence."
            ],
        }

    @classmethod
    def payload(cls, *, work_hints=None, prerequisites=None, **overrides):
        payload = {
            "objective": "Produce a verified implementation recommendation.",
            "task_class": "architecture-planning",
            "constraints": ["Remain local-first.", "Do not execute the plan."],
            "prerequisites": prerequisites or [],
            "work_hints": work_hints or [],
            "available_tools": [],
            "permissions": [],
            "model_policy": {
                "allowed_models": ["qwen2.5-coder:7b"],
                "preferred_model": "qwen2.5-coder:7b",
                "local_only": True,
                "allow_fallback": False,
            },
            "token_budget": 12_000,
            "money_budget": 1.0,
            "time_budget": 900,
            "estimated_single_agent_tokens": 5_000,
            "estimated_single_agent_seconds": 500,
            "estimated_context_tokens": 200,
            "estimated_cost_per_1k_tokens": 0.01,
            "uncertainty": 0.7,
            "research_breadth": 0.7,
            "requires_critique": False,
            "requires_synthesis": False,
            "value_score": 0.9,
            "max_agents": 6,
        }
        payload.update(overrides)
        return payload

    def create(self, **kwargs):
        return self.runtime.create_hierarchical_plan(
            PlanningRequest.from_dict(self.payload(**kwargs))
        )

    def test_simple_task_stays_one_step(self):
        plan = self.create(
            work_hints=[
                self.work_hint(
                    "only", complexity=0.1, parallelizable=False
                )
            ]
        )

        self.assertEqual(len(plan.revision.snapshot.nodes), 1)
        node = plan.revision.snapshot.nodes[0]
        self.assertEqual(node.id, "objective")
        self.assertEqual(node.kind, "action")
        self.assertEqual(node.token_budget, 12_000)
        self.assertTrue(node.assigned_agents)
        self.assertEqual(plan.status, "proposed")
        self.assertIn(
            "historical_recommendation",
            plan.revision.snapshot.orchestration_evidence,
        )

    def test_complex_task_gets_small_dependency_graph_and_budgets(self):
        hints = [
            self.work_hint("research"),
            self.work_hint("design", depends_on=("research",)),
            self.work_hint("verify", depends_on=("design",)),
        ]

        plan = self.create(work_hints=hints)
        nodes = plan.revision.snapshot.nodes
        leaves = [node for node in nodes if node.kind == "action"]

        self.assertEqual(len(nodes), 4)
        self.assertEqual(nodes[0].kind, "macro")
        self.assertEqual(leaves[1].depends_on, ("step-1",))
        self.assertEqual(leaves[2].depends_on, ("step-2",))
        self.assertEqual(sum(node.token_budget for node in leaves), 12_000)
        self.assertEqual(sum(node.time_budget for node in leaves), 900)
        self.assertAlmostEqual(
            sum(node.money_budget for node in leaves), 1.0
        )
        self.assertTrue(all(node.verification_requirements for node in leaves))
        self.assertLess(len(nodes), 50)

    def test_missing_prerequisite_blocks_then_revision_resolves_it(self):
        plan = self.create(
            prerequisites=[
                {
                    "id": "credentials",
                    "description": "Local credentials are available.",
                    "satisfied": False,
                    "evidence": [],
                }
            ]
        )
        self.assertEqual(plan.status, "blocked")
        self.assertIsNotNone(plan.agent_factory_plan_id)
        with self.assertRaises(ValueError):
            self.runtime.transition_hierarchical_plan(
                plan.id,
                expected_revision=1,
                phase="executing",
                reason="Cannot start yet.",
            )

        snapshot = plan.revision.snapshot
        resolved = replace(
            snapshot.prerequisites[0],
            satisfied=True,
            evidence=("local-secret-check:passed",),
        )
        nodes = tuple(
            replace(node, status="waiting" if node.depends_on else "ready")
            for node in snapshot.nodes
        )
        revised_snapshot = replace(
            snapshot,
            phase="proposed",
            prerequisites=(resolved,),
            missing_prerequisites=(),
            nodes=nodes,
        )
        revised = self.runtime.revise_hierarchical_plan(
            plan.id,
            expected_revision=1,
            snapshot=revised_snapshot,
            reason="Credential prerequisite was verified.",
        )
        executing = self.runtime.transition_hierarchical_plan(
            plan.id,
            expected_revision=2,
            phase="executing",
            reason="Approved execution start.",
        )

        self.assertEqual(revised.current_revision, 2)
        self.assertEqual(executing.status, "executing")
        self.assertEqual(
            self.runtime.hierarchical_plan(plan.id, revision=1).revision.snapshot.phase,
            "blocked",
        )
        self.assertEqual(len(self.runtime.hierarchical_plan_history(plan.id)), 3)

    def test_plan_is_editable_during_execution_with_optimistic_lock(self):
        plan = self.create()
        executing = self.runtime.transition_hierarchical_plan(
            plan.id,
            expected_revision=1,
            phase="executing",
            reason="Begin bounded work.",
        )
        snapshot = executing.revision.snapshot
        edited_node = replace(
            snapshot.nodes[0],
            objective="Produce the revised verified recommendation.",
        )
        edited = self.runtime.revise_hierarchical_plan(
            plan.id,
            expected_revision=2,
            snapshot=replace(snapshot, nodes=(edited_node,)),
            reason="New evidence narrowed the deliverable.",
        )

        self.assertEqual(edited.current_revision, 3)
        self.assertEqual(edited.status, "executing")
        self.assertEqual(edited.revision.change_kind, "edit")
        with self.assertRaisesRegex(ValueError, "stale"):
            self.runtime.revise_hierarchical_plan(
                plan.id,
                expected_revision=2,
                snapshot=edited.revision.snapshot,
                reason="Stale writer.",
            )

    def test_expandable_node_is_progressively_refined(self):
        plan = self.create(
            work_hints=[
                self.work_hint("research"),
                self.work_hint("synthesis"),
            ]
        )
        target = next(
            node
            for node in plan.revision.snapshot.nodes
            if node.id == "step-1"
        )
        children = (
            PlanningRequest.from_dict(
                self.payload(
                    work_hints=[
                        self.work_hint(
                            "sources",
                            complexity=0.4,
                            task_scope=("research-work",),
                        ),
                        self.work_hint(
                            "analysis",
                            depends_on=("sources",),
                            complexity=0.5,
                            task_scope=("research-work",),
                        ),
                    ]
                )
            ).work_hints
        )

        refined = self.runtime.refine_hierarchical_plan(
            plan.id,
            expected_revision=1,
            target_node_id=target.id,
            children=children,
            reason="Research evidence requires source and analysis stages.",
        )

        snapshot = refined.revision.snapshot
        refined_parent = next(
            node for node in snapshot.nodes if node.id == target.id
        )
        child_nodes = [
            node for node in snapshot.nodes if node.parent_id == target.id
        ]
        self.assertEqual(refined.revision.change_kind, "refinement")
        self.assertEqual(refined_parent.decomposition, "expanded")
        self.assertEqual(len(child_nodes), 2)
        self.assertEqual(
            sum(node.token_budget for node in child_nodes),
            target.token_budget,
        )
        self.assertEqual(child_nodes[1].depends_on, (child_nodes[0].id,))

    def test_missing_tool_is_an_explicit_non_executable_blocker(self):
        hint = self.work_hint(
            "database",
            required_tools=("python:sqlite3",),
            task_scope=("database-diagnostics",),
        )
        plan = self.create(work_hints=[hint])

        self.assertEqual(plan.status, "blocked")
        self.assertIsNone(plan.agent_factory_plan_id)
        self.assertIn(
            "tool:python:sqlite3",
            plan.revision.snapshot.missing_prerequisites,
        )

    def test_execution_can_add_and_resolve_a_discovered_prerequisite(self):
        plan = self.create()
        executing = self.runtime.transition_hierarchical_plan(
            plan.id,
            expected_revision=1,
            phase="executing",
            reason="Start.",
        )
        snapshot = executing.revision.snapshot
        discovered = {
            "id": "schema-decision",
            "description": "The discovered schema choice is approved.",
            "satisfied": False,
            "evidence": [],
        }
        snapshot_payload = json.loads(json.dumps(snapshot.as_dict()))
        blocked_snapshot = PlanSnapshot.from_dict(
            {
                **snapshot_payload,
                "phase": "blocked",
                "prerequisites": [
                    *snapshot_payload["prerequisites"],
                    discovered,
                ],
                "missing_prerequisites": [
                    "prerequisite:schema-decision"
                ],
                "nodes": [
                    {**node, "status": "blocked"}
                    for node in snapshot_payload["nodes"]
                ],
            }
        )
        blocked = self.runtime.revise_hierarchical_plan(
            plan.id,
            expected_revision=2,
            snapshot=blocked_snapshot,
            reason="Execution discovered an approval prerequisite.",
        )
        resolved_prerequisite = replace(
            blocked.revision.snapshot.prerequisites[-1],
            satisfied=True,
            evidence=("approval:local-review",),
        )
        resumed_snapshot = replace(
            blocked.revision.snapshot,
            phase="proposed",
            prerequisites=(
                *blocked.revision.snapshot.prerequisites[:-1],
                resolved_prerequisite,
            ),
            missing_prerequisites=(),
            nodes=tuple(
                replace(
                    node,
                    status="waiting" if node.depends_on else "ready",
                )
                for node in blocked.revision.snapshot.nodes
            ),
        )
        resumed = self.runtime.revise_hierarchical_plan(
            plan.id,
            expected_revision=3,
            snapshot=resumed_snapshot,
            reason="The discovered prerequisite was approved.",
        )

        self.assertEqual(blocked.status, "blocked")
        self.assertEqual(resumed.status, "proposed")
        self.assertEqual(
            len(resumed.revision.snapshot.prerequisites), 1
        )

    def activate_example_skill(self) -> None:
        root = Path(self.directory.name)
        source = (
            Path(__file__).parents[1]
            / "examples"
            / "skill-v1"
            / "sqlite-diagnostics"
        )
        package = root / "sqlite-diagnostics"
        shutil.copytree(source, package)
        skill_id = str(self.runtime.admit_skill_package(package)["id"])
        self.runtime.skill_validator = SkillValidator(
            self.runtime.db.connection,
            self.runtime.skill_registry,
            loader=self.runtime.skill_packages,
            sandbox=PassingSandbox(),
            evaluator=PassingEvaluator(),
            benchmark=PassingBenchmark(),
        )
        validation = self.runtime.validate_skill_candidate(skill_id)
        self.runtime.promote_skill_validation(validation.id)

    def test_planner_selects_real_skill_tools_and_agents(self):
        self.activate_example_skill()
        hint = self.work_hint(
            "database",
            required_skills=("sqlite-diagnostics@1.0.0",),
            task_scope=("database-diagnostics",),
        )
        request = PlanningRequest.from_dict(
            self.payload(
                work_hints=[hint],
                task_class="database-diagnostics",
                available_tools=["python:sqlite3"],
                permissions=["filesystem:read"],
            )
        )

        plan = self.runtime.create_hierarchical_plan(request)
        node = plan.revision.snapshot.nodes[0]

        self.assertEqual(
            node.selected_skills, ("sqlite-diagnostics@1.0.0",)
        )
        self.assertEqual(node.selected_tools, ("python:sqlite3",))
        self.assertTrue(node.assigned_agents)
        self.assertIsNotNone(plan.agent_factory_plan_id)

    def test_cycles_budget_overruns_and_early_completion_fail_closed(self):
        cyclic = [
            self.work_hint("one", depends_on=("two",)),
            self.work_hint("two", depends_on=("one",)),
        ]
        with self.assertRaisesRegex(ValueError, "cycle"):
            self.create(work_hints=cyclic)

        plan = self.create()
        snapshot = plan.revision.snapshot
        oversized = replace(
            snapshot.nodes[0],
            token_budget=plan.request.token_budget + 1,
        )
        with self.assertRaisesRegex(ValueError, "token"):
            self.runtime.revise_hierarchical_plan(
                plan.id,
                expected_revision=1,
                snapshot=replace(snapshot, nodes=(oversized,)),
                reason="Invalid budget edit.",
            )
        executing = self.runtime.transition_hierarchical_plan(
            plan.id,
            expected_revision=1,
            phase="executing",
            reason="Start.",
        )
        with self.assertRaisesRegex(ValueError, "complete"):
            self.runtime.transition_hierarchical_plan(
                plan.id,
                expected_revision=2,
                phase="completed",
                reason="Too early.",
            )

    def test_cli_create_inspect_revise_and_history(self):
        root = Path(self.directory.name)
        request_path = root / "planning-request.json"
        request_path.write_text(
            json.dumps(self.payload()), encoding="utf-8"
        )
        self.runtime.close()
        output = StringIO()
        with redirect_stdout(output):
            self.assertEqual(
                main(
                    [
                        "--db", str(self.database), "plans", "create",
                        str(request_path),
                    ]
                ),
                0,
            )
        created = json.loads(output.getvalue())
        snapshot_path = root / "snapshot.json"
        snapshot_path.write_text(
            json.dumps(created["revision"]["snapshot"]),
            encoding="utf-8",
        )
        output = StringIO()
        with redirect_stdout(output):
            self.assertEqual(
                main(
                    [
                        "--db", str(self.database), "plans", "revise",
                        created["id"], str(snapshot_path),
                        "--expected-revision", "1",
                        "--reason", "Retain a reviewed edit.",
                    ]
                ),
                0,
            )
        self.assertEqual(json.loads(output.getvalue())["current_revision"], 2)
        output = StringIO()
        with redirect_stdout(output):
            self.assertEqual(
                main(
                    [
                        "--db", str(self.database), "plans", "history",
                        created["id"],
                    ]
                ),
                0,
            )
        self.assertEqual(len(json.loads(output.getvalue())["revisions"]), 2)
        self.runtime = AdaptiveRuntime(database=self.database)


if __name__ == "__main__":
    unittest.main()
