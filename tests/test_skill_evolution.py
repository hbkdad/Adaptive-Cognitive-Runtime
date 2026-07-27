from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from acr_runtime import (
    AdaptiveRuntime,
    Settings,
    SkillMutation,
    SkillValidator,
    ValidationEvidence,
)


class PassingSandbox:
    def run(self, package, *, stage, cases):
        return ValidationEvidence("passed", 1.0, {"stage": stage})


class PassingEvaluator:
    def review(self, package):
        return ValidationEvidence("passed", 0.95, {"review": "passed"})


class EvolutionBenchmark:
    def __init__(self, *, candidate_cost: float = 0.08) -> None:
        self.candidate_cost = candidate_cost

    def compare(self, package, *, incumbent_skill_id):
        return ValidationEvidence(
            "passed",
            0.95,
            {
                "candidate_quality": 0.95,
                "incumbent_quality": 0.90,
                "candidate_tokens": 80,
                "incumbent_tokens": 90,
                "candidate_cost": self.candidate_cost,
                "incumbent_cost": 0.10,
                "candidate_latency_ms": 80,
                "incumbent_latency_ms": 100,
                "candidate_reliability": 0.90,
                "incumbent_reliability": 0.90,
                "candidate_security": 1.0,
                "incumbent_security": 1.0,
            },
        )


class SkillEvolutionTests(unittest.TestCase):
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
        source = (
            Path(__file__).parents[1]
            / "examples"
            / "skill-v1"
            / "sqlite-diagnostics"
        )
        self.package = root / "sqlite-diagnostics"
        shutil.copytree(source, self.package)
        self.source_id = str(
            self.runtime.admit_skill_package(self.package)["id"]
        )
        self.configure_validator()
        baseline = self.runtime.validate_skill_candidate(self.source_id)
        self.runtime.promote_skill_validation(baseline.id)
        self.baseline_validation_id = baseline.id

    def tearDown(self) -> None:
        self.runtime.close()
        self.directory.cleanup()

    def configure_validator(self, *, candidate_cost: float = 0.08) -> None:
        self.runtime.skill_validator = SkillValidator(
            self.runtime.db.connection,
            self.runtime.skill_registry,
            loader=self.runtime.skill_packages,
            sandbox=PassingSandbox(),
            evaluator=PassingEvaluator(),
            benchmark=EvolutionBenchmark(candidate_cost=candidate_cost),
        )

    def mutation(self) -> SkillMutation:
        return SkillMutation(
            workflow=(
                "Inspect schema and integrity.",
                "Run focused FTS5 queries.",
                "Report bounded evidence.",
            ),
            retrieval_strategy="Retrieve only schema and requested FTS evidence.",
            verification=("python -m unittest discover -s tests",),
            error_handling="Stop without repair when any integrity check fails.",
            token_budget=120,
        )

    def test_candidate_is_new_immutable_quarantined_version(self):
        source_before = self.runtime.inspect_skill(self.source_id)

        run = self.runtime.create_skill_evolution(
            self.source_id, self.mutation()
        )

        source_after = self.runtime.inspect_skill(self.source_id)
        candidate = self.runtime.inspect_skill(run.candidate_skill_id)
        self.assertEqual(run.source_version, "1.0.0")
        self.assertEqual(run.candidate_version, "1.1.0")
        self.assertEqual(source_before["content_hash"], source_after["content_hash"])
        self.assertNotEqual(run.source_hash, run.candidate_hash)
        self.assertEqual(candidate["lifecycle_status"], "quarantined")
        self.assertEqual(candidate["manifest"]["status"], "experimental")
        self.assertIn("## Evolved workflow", candidate["instructions"])
        self.assertIn("## Retrieval strategy", candidate["instructions"])
        self.assertIn("## Error handling", candidate["instructions"])
        self.assertIn("## Token budget", candidate["instructions"])

    def test_multi_objective_winner_promotes_and_can_rollback(self):
        run = self.runtime.create_skill_evolution(
            self.source_id, self.mutation()
        )
        candidate_validation = self.runtime.validate_skill_candidate(
            run.candidate_skill_id
        )
        compared = self.runtime.compare_skill_evolution(
            run.id,
            baseline_validation_id=self.baseline_validation_id,
            candidate_validation_id=candidate_validation.id,
        )

        promoted = self.runtime.promote_skill_evolution(run.id)
        self.assertEqual(promoted.status, "promoted")
        self.assertEqual(
            self.runtime.inspect_skill(run.candidate_skill_id)[
                "lifecycle_status"
            ],
            "active",
        )
        self.assertEqual(
            self.runtime.inspect_skill(self.source_id)["lifecycle_status"],
            "quarantined",
        )

        rolled_back = self.runtime.rollback_skill_evolution(
            run.id, reason="Production regression observed"
        )

        self.assertEqual(compared.winner, "candidate")
        self.assertFalse(
            compared.comparison["benchmark_score_alone_sufficient"]
        )
        self.assertEqual(
            self.runtime.inspect_skill(run.candidate_skill_id)[
                "lifecycle_status"
            ],
            "quarantined",
        )
        self.assertEqual(rolled_back.status, "rolled_back")
        self.assertEqual(
            self.runtime.inspect_skill(self.source_id)["lifecycle_status"],
            "active",
        )
        rollback = self.runtime.db.connection.execute(
            "SELECT reason FROM skill_evolution_rollbacks WHERE run_id = ?",
            (run.id,),
        ).fetchone()
        self.assertEqual(rollback[0], "Production regression observed")

    def test_benchmark_gain_cannot_hide_cost_regression(self):
        run = self.runtime.create_skill_evolution(
            self.source_id, self.mutation()
        )
        self.configure_validator(candidate_cost=0.11)
        candidate_validation = self.runtime.validate_skill_candidate(
            run.candidate_skill_id
        )
        self.assertEqual(candidate_validation.status, "passed")

        compared = self.runtime.compare_skill_evolution(
            run.id,
            baseline_validation_id=self.baseline_validation_id,
            candidate_validation_id=candidate_validation.id,
        )

        self.assertEqual(compared.status, "rejected")
        self.assertEqual(compared.winner, "source")
        self.assertFalse(compared.comparison["weakly_better"]["cost"])
        with self.assertRaises(ValueError):
            self.runtime.promote_skill_evolution(run.id)

    def test_versions_never_overwrite_or_move_backwards(self):
        self.runtime.create_skill_evolution(
            self.source_id, self.mutation(), version="1.1.0"
        )
        with self.assertRaises((ValueError, FileExistsError)):
            self.runtime.create_skill_evolution(
                self.source_id, self.mutation(), version="1.1.0"
            )
        with self.assertRaises(ValueError):
            self.runtime.create_skill_evolution(
                self.source_id, self.mutation(), version="0.9.0"
            )

    def test_changed_source_package_cannot_evolve(self):
        instructions = self.package / "instructions.md"
        instructions.write_text(
            instructions.read_text(encoding="utf-8") + "\nUnvalidated change.\n",
            encoding="utf-8",
        )

        with self.assertRaisesRegex(
            ValueError, "Source package changed after validation"
        ):
            self.runtime.create_skill_evolution(
                self.source_id, self.mutation()
            )


if __name__ == "__main__":
    unittest.main()
