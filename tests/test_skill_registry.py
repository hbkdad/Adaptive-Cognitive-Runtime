from __future__ import annotations

import json
import shutil
import tempfile
import unittest
import io
from contextlib import redirect_stdout
from pathlib import Path

from acr_runtime import (
    AdaptiveRuntime,
    AttributionSignals,
    SkillValidator,
    ValidationEvidence,
)
from acr_runtime.cli import main


class SemanticStub:
    def __init__(self, skill_id: str) -> None:
        self.skill_id = skill_id
        self.queries: list[str] = []

    def search(self, query: str, *, limit: int) -> dict[str, float]:
        self.queries.append(query)
        return {self.skill_id: 0.91}


class PassingSandbox:
    def run(self, package, *, stage, cases):
        return ValidationEvidence("passed", 1.0, {"stage": stage, "cases": len(cases)})


class PassingEvaluator:
    def review(self, package):
        return ValidationEvidence("passed", 0.95, {"review": "test"})


class PassingBenchmark:
    def compare(self, package, *, incumbent_skill_id):
        return ValidationEvidence(
            "passed",
            0.95,
            {
                "candidate_quality": 0.95,
                "incumbent_quality": 0.90,
                "candidate_cost": 0.0,
                "incumbent_cost": 0.0,
            },
        )


class SkillRegistryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.database = Path(self.directory.name) / "acr.db"
        source = (
            Path(__file__).parents[1]
            / "examples"
            / "skill-v1"
            / "sqlite-diagnostics"
        )
        self.package = Path(self.directory.name) / "sqlite-diagnostics"
        shutil.copytree(source, self.package)
        self.runtime = AdaptiveRuntime(self.database)

    def tearDown(self) -> None:
        self.runtime.close()
        self.directory.cleanup()

    def cli(self, *arguments: str) -> object:
        output = io.StringIO()
        with redirect_stdout(output):
            result = main(
                ["--db", str(self.database), "skills", *arguments]
            )
        self.assertEqual(result, 0)
        return json.loads(output.getvalue())

    def fully_activate(self, skill_id: str) -> None:
        self.runtime.skill_validator = SkillValidator(
            self.runtime.db.connection,
            self.runtime.skill_registry,
            loader=self.runtime.skill_packages,
            sandbox=PassingSandbox(),
            evaluator=PassingEvaluator(),
            benchmark=PassingBenchmark(),
        )
        run = self.runtime.validate_skill_candidate(skill_id)
        self.assertEqual(run.status, "passed")
        promoted = self.runtime.promote_skill_validation(run.id)
        self.assertEqual(promoted.status, "promoted")

    def test_admission_is_quarantined_versioned_and_immutable(self):
        admitted = self.runtime.admit_skill_package(self.package)
        self.assertEqual(admitted["lifecycle_status"], "quarantined")
        self.assertEqual(admitted["status"], "quarantine")
        repeated = self.runtime.admit_skill_package(self.package)
        self.assertEqual(repeated["id"], admitted["id"])
        instructions = self.package / "instructions.md"
        instructions.write_text(
            instructions.read_text(encoding="utf-8") + "\nChanged.",
            encoding="utf-8",
        )
        with self.assertRaises(ValueError):
            self.runtime.admit_skill_package(self.package)

    def test_keyword_and_semantic_search_do_not_return_instructions(self):
        admitted = self.runtime.admit_skill_package(self.package)
        keyword = self.runtime.search_skills("SQLite FTS diagnostics")
        self.assertFalse(keyword["semantic_available"])
        self.assertEqual(keyword["results"][0]["id"], admitted["id"])
        self.assertNotIn("instructions", keyword["results"][0])

        semantic = SemanticStub(admitted["id"])
        self.runtime.skill_registry.semantic_index = semantic
        result = self.runtime.search_skills("nonlexical database concern")
        self.assertTrue(result["semantic_available"])
        self.assertEqual(result["results"][0]["semantic_score"], 0.91)
        self.assertEqual(semantic.queries, ["nonlexical database concern"])

    def test_stable_id_resolves_latest_semver_and_exact_reference(self):
        first = self.runtime.admit_skill_package(self.package)
        manifest_path = self.package / "SKILL.yaml"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["version"] = "1.1.0"
        manifest["updated_at"] = "2026-07-27T01:00:00+00:00"
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        second = self.runtime.admit_skill_package(self.package)
        self.assertNotEqual(first["id"], second["id"])
        self.assertEqual(
            self.runtime.inspect_skill("sqlite-diagnostics")["version"], "1.1.0"
        )
        self.assertEqual(
            self.runtime.inspect_skill("sqlite-diagnostics@1.0.0")["id"],
            first["id"],
        )

    def test_test_activation_quarantine_retire_and_history(self):
        admitted = self.runtime.admit_skill_package(self.package)
        with self.assertRaises(ValueError):
            self.runtime.activate_skill(admitted["id"])
        test_result = self.runtime.test_skill(admitted["id"])
        self.assertEqual(test_result["verification_status"], "static_passed")
        self.assertFalse(test_result["executed"])
        instructions = self.package / "instructions.md"
        original = instructions.read_bytes()
        instructions.write_bytes(original + b"\nmutated")
        with self.assertRaises(ValueError):
            self.runtime.activate_skill(admitted["id"])
        instructions.write_bytes(original)
        self.fully_activate(admitted["id"])
        active = self.runtime.inspect_skill(admitted["id"])
        self.assertEqual(active["lifecycle_status"], "active")
        quarantined = self.runtime.quarantine_skill(admitted["id"])
        self.assertEqual(quarantined["lifecycle_status"], "quarantined")
        retired = self.runtime.retire_skill(admitted["id"])
        self.assertEqual(retired["lifecycle_status"], "retired")
        with self.assertRaises(ValueError):
            self.runtime.activate_skill(admitted["id"])
        history = self.runtime.skill_history(admitted["id"])
        self.assertEqual(history[0]["event"], "admitted")
        self.assertEqual(history[-1]["to_status"], "retired")

    def test_attribution_updates_task_class_and_model_performance(self):
        admitted = self.runtime.admit_skill_package(self.package)
        self.fully_activate(admitted["id"])
        bundle = self.runtime.compile_context(
            "diagnose SQLite FTS database",
            token_budget=180,
        )
        skill_block = next(
            block for block in bundle.blocks if block.source_type == "skill"
        )
        self.runtime.complete_task(
            bundle,
            success=True,
            critic_score=0.95,
            duration_ms=40,
            attribution_signals=AttributionSignals(
                execution_sources=(("skill", skill_block.source_id),)
            ),
            task_class="database-diagnostics",
            model="qwen-local",
            estimated_cost=0.02,
        )
        inspected = self.runtime.inspect_skill(admitted["id"])
        self.assertEqual(inspected["use_count"], 1)
        self.assertEqual(inspected["success_count"], 1)
        self.assertEqual(inspected["failure_count"], 0)
        self.assertEqual(inspected["last_used"] is not None, True)
        performance = inspected["performance"][0]
        self.assertEqual(performance["task_class"], "database-diagnostics")
        self.assertEqual(performance["model"], "qwen-local")
        self.assertEqual(performance["uses"], 1)
        self.assertGreater(performance["average_tokens"], 0)
        self.assertAlmostEqual(performance["average_cost"], 0.02)
        self.assertEqual(performance["average_latency_ms"], 40)

    def test_all_prompt_17_cli_commands_are_inspectable(self):
        installed = self.cli("install", str(self.package))
        skill_id = installed["id"]
        self.assertTrue(self.cli("list"))
        self.assertEqual(self.cli("inspect", skill_id)["id"], skill_id)
        self.assertEqual(
            self.cli("search", "SQLite diagnostics")["results"][0]["id"],
            skill_id,
        )
        self.assertEqual(
            self.cli("test", skill_id)["verification_status"],
            "static_passed",
        )
        with self.assertRaises(ValueError):
            self.cli("activate", skill_id)
        validation = self.cli("certify", skill_id)
        self.assertEqual(validation["status"], "blocked")
        self.assertEqual(len(validation["results"]), 10)
        self.assertEqual(
            self.cli("quarantine", skill_id)["lifecycle_status"],
            "quarantined",
        )
        self.assertTrue(self.cli("history", skill_id))
        self.assertEqual(
            self.cli("retire", skill_id)["lifecycle_status"], "retired"
        )


if __name__ == "__main__":
    unittest.main()
