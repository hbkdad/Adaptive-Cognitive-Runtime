from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

from acr_runtime import (
    AdaptiveRuntime,
    AgentContextItem,
    AgentSpec,
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


class AgentSpecTests(unittest.TestCase):
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
    def payload(**overrides):
        payload = {
            "id": "database-worker",
            "role": "Focused database diagnostics worker",
            "objective": "Diagnose only the assigned local database issue.",
            "task_scope": ["database-diagnostics"],
            "tools": [],
            "skills": [],
            "memory_scope": ["project-alpha"],
            "model_policy": {
                "allowed_models": ["qwen2.5-coder:7b"],
                "preferred_model": "qwen2.5-coder:7b",
                "local_only": True,
                "allow_fallback": False,
            },
            "token_budget": 4_000,
            "money_budget": 0,
            "time_budget": 300,
            "permissions": [],
            "communication": {
                "mode": "none",
                "allowed_peers": [],
                "max_messages": 0,
            },
            "termination_conditions": [
                "objective_met",
                "verification_failed",
                "budget_exhausted",
                "time_exhausted",
                "cancelled",
            ],
            "verification_requirements": [
                "Return the checks performed and bounded evidence."
            ],
        }
        payload.update(overrides)
        return payload

    def test_strict_round_trip_is_immutable_and_non_executable(self):
        spec = AgentSpec.from_dict(self.payload())
        before_tasks = self.runtime.db.connection.execute(
            "SELECT COUNT(*) FROM tasks"
        ).fetchone()[0]

        stored = self.runtime.define_agent_spec(spec)
        repeated = self.runtime.define_agent_spec(spec)

        self.assertEqual(stored.content_hash, repeated.content_hash)
        self.assertEqual(stored.status, "defined")
        self.assertEqual(
            self.runtime.inspect_agent_spec(spec.id).spec, spec
        )
        self.assertEqual(len(self.runtime.list_agent_specs()), 1)
        self.assertEqual(
            self.runtime.db.connection.execute(
                "SELECT COUNT(*) FROM tasks"
            ).fetchone()[0],
            before_tasks,
        )
        changed = AgentSpec.from_dict(
            self.payload(objective="A different objective.")
        )
        with self.assertRaisesRegex(ValueError, "immutable"):
            self.runtime.define_agent_spec(changed)

    def test_personality_unknown_fields_and_invalid_budgets_fail_closed(self):
        with self.assertRaises(ValueError):
            AgentSpec.from_dict(
                {**self.payload(), "personality": "cheerful"}
            )
        with self.assertRaises(ValueError):
            AgentSpec.from_dict(self.payload(token_budget=1.5))
        with self.assertRaises(ValueError):
            AgentSpec.from_dict(self.payload(money_budget=float("inf")))
        with self.assertRaises(ValueError):
            AgentSpec.from_dict(
                self.payload(
                    termination_conditions=["objective_met", "cancelled"]
                )
            )

    def test_context_is_filtered_by_responsibility_and_memory_scope(self):
        spec = AgentSpec.from_dict(self.payload())
        relevant = AgentContextItem(
            "schema-evidence",
            "database-diagnostics",
            "project-alpha",
            "SQLite schema evidence.",
        )
        wrong_task = AgentContextItem(
            "marketing-note",
            "marketing",
            "project-alpha",
            "Unrelated campaign.",
        )
        wrong_memory = AgentContextItem(
            "other-database",
            "database-diagnostics",
            "project-beta",
            "Another client's database.",
        )
        scoped_nonmemory = AgentContextItem(
            "tool-contract",
            "database-diagnostics",
            None,
            "Allowed tool contract.",
        )

        selected = spec.filter_context(
            (relevant, wrong_task, wrong_memory, scoped_nonmemory)
        )

        self.assertEqual(selected, (relevant, scoped_nonmemory))

    def test_wildcards_and_open_communication_are_rejected(self):
        with self.assertRaises(ValueError):
            AgentSpec.from_dict(self.payload(task_scope=["*"]))
        with self.assertRaises(ValueError):
            AgentSpec.from_dict(
                self.payload(
                    communication={
                        "mode": "none",
                        "allowed_peers": ["manager-agent"],
                        "max_messages": 1,
                    }
                )
            )

    def activate_example_skill(self) -> str:
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
        return skill_id

    def test_exact_active_skills_enforce_tools_permissions_and_scope(self):
        self.activate_example_skill()
        complete = self.payload(
            tools=["python:sqlite3"],
            skills=["sqlite-diagnostics@1.0.0"],
            permissions=["filesystem:read"],
        )
        stored = self.runtime.define_agent_spec(
            AgentSpec.from_dict(complete)
        )
        self.assertEqual(
            stored.resolved_skills[0]["reference"],
            "sqlite-diagnostics@1.0.0",
        )
        with self.assertRaises(ValueError):
            self.runtime.define_agent_spec(
                AgentSpec.from_dict(
                    {
                        **complete,
                        "id": "missing-tool-worker",
                        "tools": [],
                    }
                )
            )
        with self.assertRaises(ValueError):
            AgentSpec.from_dict(
                {
                    **complete,
                    "id": "floating-skill-worker",
                    "skills": ["sqlite-diagnostics"],
                }
            )

    def test_cli_define_list_and_inspect(self):
        spec_path = Path(self.directory.name) / "agent.json"
        spec_path.write_text(
            json.dumps(self.payload()), encoding="utf-8"
        )
        self.runtime.close()
        output = StringIO()
        with redirect_stdout(output):
            code = main(
                [
                    "--db",
                    str(self.database),
                    "agents",
                    "define",
                    str(spec_path),
                ]
            )
        self.assertEqual(code, 0)
        defined = json.loads(output.getvalue())
        output = StringIO()
        with redirect_stdout(output):
            code = main(
                [
                    "--db",
                    str(self.database),
                    "agents",
                    "inspect",
                    defined["id"],
                ]
            )
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output.getvalue())["id"], defined["id"])
        self.runtime = AdaptiveRuntime(database=self.database)


if __name__ == "__main__":
    unittest.main()
