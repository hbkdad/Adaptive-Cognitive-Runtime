from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from acr_runtime import (
    AdaptiveRuntime,
    ConfidenceThresholds,
    EvolutionMetrics,
    GenomeBenchmarkEvidence,
    GenomeCasePair,
    GenomeMutation,
    GenomeParameters,
    Settings,
    SkillGenomeExperiment,
    SkillValidator,
    ValidationEvidence,
)


class PassingSandbox:
    def run(self, package, *, stage, cases):
        return ValidationEvidence("passed", 1.0, {"stage": stage})


class PassingEvaluator:
    def review(self, package):
        return ValidationEvidence("passed", 0.95, {"review": "passed"})


class PassingSkillBenchmark:
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


class TournamentAdapter:
    def __init__(
        self,
        effects: dict[str, float],
        *,
        token_deltas: dict[str, int] | None = None,
        isolated: bool = True,
        case_count: int = 10,
        case_suffixes: dict[str, str] | None = None,
    ) -> None:
        self.effects = effects
        self.token_deltas = token_deltas or {}
        self.isolated = isolated
        self.case_count = case_count
        self.case_suffixes = case_suffixes or {}

    def run(self, source_skill, baseline, candidates):
        results = {}
        if self.isolated:
            for candidate in candidates:
                suffix = self.case_suffixes.get(candidate.id, "")
                results[candidate.id] = tuple(
                    GenomeCasePair(
                        case_id=f"case-{index}{suffix}",
                        baseline=EvolutionMetrics(
                            quality=0.80,
                            tokens=100,
                            cost=0.10,
                            latency_ms=100,
                            reliability=0.90,
                            security=1.0,
                        ),
                        candidate=EvolutionMetrics(
                            quality=0.80 + self.effects[candidate.id],
                            tokens=100 + self.token_deltas.get(candidate.id, 0),
                            cost=0.10,
                            latency_ms=100,
                            reliability=0.90,
                            security=1.0,
                        ),
                    )
                    for index in range(self.case_count)
                )
        return GenomeBenchmarkEvidence(
            isolated=self.isolated,
            isolation_details={
                "network": "none",
                "dataset": "private-fixture-v1",
                "fresh_process_per_case": True,
            },
            results=results,
            blocked_reason=None if self.isolated else "not_sandboxed",
        )


class SkillGenomeTests(unittest.TestCase):
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
        package = root / "sqlite-diagnostics"
        shutil.copytree(source, package)
        self.source_id = str(
            self.runtime.admit_skill_package(package)["id"]
        )
        self.runtime.skill_validator = SkillValidator(
            self.runtime.db.connection,
            self.runtime.skill_registry,
            loader=self.runtime.skill_packages,
            sandbox=PassingSandbox(),
            evaluator=PassingEvaluator(),
            benchmark=PassingSkillBenchmark(),
        )
        validation = self.runtime.validate_skill_candidate(self.source_id)
        self.runtime.promote_skill_validation(validation.id)

    def tearDown(self) -> None:
        self.runtime.close()
        self.directory.cleanup()

    @staticmethod
    def parameters() -> GenomeParameters:
        return GenomeParameters(
            retrieval_depth=12,
            context_budget=4_000,
            maximum_iterations=6,
            verification_intensity="standard",
            model_tier="standard",
            parallelism=2,
            confidence_thresholds=ConfidenceThresholds(
                retrieval=0.60,
                verification=0.80,
                acceptance=0.90,
            ),
        )

    def baseline_and_candidates(self):
        baseline = self.runtime.create_skill_genome(
            self.source_id, self.parameters()
        )
        first = self.runtime.mutate_skill_genome(
            baseline.id, GenomeMutation(context_budget=3_500)
        )
        second = self.runtime.mutate_skill_genome(
            baseline.id, GenomeMutation(retrieval_depth=14)
        )
        return baseline, first, second

    def use_adapter(self, adapter) -> None:
        self.runtime.skill_genome = SkillGenomeExperiment(
            self.runtime.db.connection,
            self.runtime.skill_registry,
            benchmark=adapter,
            loader=self.runtime.skill_packages,
        )

    def test_controlled_mutation_is_separate_from_production_skill(self):
        source_before = self.runtime.inspect_skill(self.source_id)
        baseline = self.runtime.create_skill_genome(
            self.source_id, self.parameters()
        )
        candidate = self.runtime.mutate_skill_genome(
            baseline.id,
            GenomeMutation(
                context_budget=3_000,
                verification_intensity="strict",
            ),
        )

        self.assertEqual(baseline.status, "baseline")
        self.assertEqual(candidate.status, "experimental")
        self.assertEqual(candidate.generation, 1)
        self.assertEqual(candidate.parameters.context_budget, 3_000)
        source_after = self.runtime.inspect_skill(self.source_id)
        self.assertEqual(
            source_before["content_hash"], source_after["content_hash"]
        )
        self.assertEqual(source_after["lifecycle_status"], "active")
        with self.assertRaises(ValueError):
            self.runtime.mutate_skill_genome(
                baseline.id, GenomeMutation(context_budget=20_000)
            )
        with self.assertRaises(ValueError):
            GenomeMutation(
                retrieval_depth=10,
                context_budget=3_000,
                maximum_iterations=5,
                parallelism=1,
            )

    def test_default_tournament_blocks_without_isolated_adapter(self):
        baseline, candidate, _ = self.baseline_and_candidates()

        tournament = self.runtime.run_skill_genome_tournament(
            baseline.id, (candidate.id,)
        )

        self.assertEqual(tournament.status, "blocked")
        self.assertEqual(
            tournament.blocked_reason,
            "isolated_genome_benchmark_unavailable",
        )
        self.assertIsNone(tournament.winner_genome_id)
        self.assertEqual(
            self.runtime.inspect_skill_genome(candidate.id).status,
            "experimental",
        )

    def test_holm_significant_multiobjective_winner_stays_experimental(self):
        baseline, first, second = self.baseline_and_candidates()
        source_before = self.runtime.inspect_skill(self.source_id)
        self.use_adapter(
            TournamentAdapter({first.id: 0.05, second.id: 0.04})
        )

        tournament = self.runtime.run_skill_genome_tournament(
            baseline.id, (first.id, second.id)
        )

        self.assertEqual(tournament.status, "completed")
        self.assertEqual(tournament.winner_genome_id, first.id)
        self.assertTrue(tournament.candidates[0].qualified)
        self.assertLessEqual(
            tournament.candidates[0].statistics["raw_p_value"],
            tournament.candidates[0].statistics["holm_threshold"],
        )
        self.assertEqual(
            self.runtime.inspect_skill_genome(first.id).status, "selected"
        )
        self.assertEqual(
            self.runtime.inspect_skill_genome(second.id).status, "rejected"
        )
        source_after = self.runtime.inspect_skill(self.source_id)
        self.assertEqual(source_after["lifecycle_status"], "active")
        self.assertEqual(
            source_before["content_hash"], source_after["content_hash"]
        )
        self.assertEqual(
            source_before["instructions"], source_after["instructions"]
        )

    def test_significance_cannot_hide_small_effect_or_resource_regression(self):
        baseline, first, second = self.baseline_and_candidates()
        self.use_adapter(
            TournamentAdapter(
                {first.id: 0.01, second.id: 0.05},
                token_deltas={second.id: 1},
            )
        )

        tournament = self.runtime.run_skill_genome_tournament(
            baseline.id, (first.id, second.id)
        )

        self.assertIsNone(tournament.winner_genome_id)
        reports = {
            item.candidate_genome_id: item for item in tournament.candidates
        }
        self.assertIn(
            "quality_effect_below_minimum",
            reports[first.id].rejection_reasons,
        )
        self.assertIn(
            "multi_objective_regression",
            reports[second.id].rejection_reasons,
        )
        self.assertFalse(reports[first.id].qualified)
        self.assertFalse(reports[second.id].qualified)

    def test_nonisolated_and_mismatched_cases_never_select(self):
        baseline, first, second = self.baseline_and_candidates()
        self.use_adapter(
            TournamentAdapter(
                {first.id: 0.05, second.id: 0.05}, isolated=False
            )
        )
        blocked = self.runtime.run_skill_genome_tournament(
            baseline.id, (first.id, second.id)
        )
        self.assertEqual(blocked.status, "blocked")

        self.use_adapter(
            TournamentAdapter(
                {first.id: 0.05, second.id: 0.05},
                case_suffixes={second.id: "-different"},
            )
        )
        completed = self.runtime.run_skill_genome_tournament(
            baseline.id, (first.id, second.id)
        )
        self.assertIsNone(completed.winner_genome_id)
        self.assertTrue(
            all(
                "tournament_case_sets_do_not_match"
                in candidate.rejection_reasons
                for candidate in completed.candidates
            )
        )

    def test_tournament_bounds_and_parameter_schema_fail_closed(self):
        baseline, candidate, _ = self.baseline_and_candidates()
        with self.assertRaises(ValueError):
            self.runtime.run_skill_genome_tournament(
                baseline.id, (candidate.id, candidate.id)
            )
        self.use_adapter(
            TournamentAdapter({candidate.id: 0.05}, case_count=51)
        )
        oversized = self.runtime.run_skill_genome_tournament(
            baseline.id, (candidate.id,)
        )
        self.assertEqual(oversized.status, "blocked")
        self.assertEqual(
            oversized.blocked_reason,
            "benchmark_case_count_exceeds_limit",
        )
        with self.assertRaises(ValueError):
            GenomeParameters.from_dict(
                {
                    **self.parameters().as_dict(),
                    "production_override": True,
                }
            )
        invalid_number = self.parameters().as_dict()
        invalid_number["parallelism"] = 1.5
        with self.assertRaises(ValueError):
            GenomeParameters.from_dict(invalid_number)


if __name__ == "__main__":
    unittest.main()
