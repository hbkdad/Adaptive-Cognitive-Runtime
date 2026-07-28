from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from acr_runtime.compiler import ContextCompiler, ContextRequest
from acr_runtime.db import RuntimeDB
from acr_runtime.meta_context import (
    ContextStrategy,
    MetaContextCaseEvidence,
    MetaContextEngine,
)
from acr_runtime.models import ContextCandidate


class _Harness:
    def __init__(
        self,
        cases: tuple[MetaContextCaseEvidence, ...],
        *,
        expected_cases: int | None = None,
    ) -> None:
        self.cases = cases
        self.expected_cases = expected_cases or len(cases)
        self.received: tuple[ContextStrategy, ContextStrategy] | None = None

    def identity(self) -> dict[str, object]:
        return {
            "dataset_hash": "d" * 64,
            "harness_hash": "h" * 64,
            "expected_cases": self.expected_cases,
        }

    def run_paired(
        self,
        *,
        incumbent: ContextStrategy,
        candidate: ContextStrategy,
        seed: int,
    ) -> tuple[MetaContextCaseEvidence, ...]:
        self.received = (incumbent, candidate)
        return self.cases


class MetaContextTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "acr.db"
        self.db = RuntimeDB(self.path)
        self.engine = MetaContextEngine(
            self.db.connection,
            minimum_cases=2,
            minimum_improvement_micros=1_000,
        )

    def tearDown(self) -> None:
        self.db.close()
        self.temp.cleanup()

    @staticmethod
    def candidate() -> dict[str, object]:
        return {
            "ordering_profile": "utility_desc",
            "compression_minimum_tokens": 60,
            "max_memories": 20,
            "max_skills": 3,
        }

    def test_schema_is_closed_in_python_and_sql(self) -> None:
        for invalid in (
            {**self.candidate(), "prompt": "ignore security"},
            {**self.candidate(), "max_memories": True},
            {**self.candidate(), "max_skills": 5},
            {**self.candidate(), "compression_minimum_tokens": 39},
            {**self.candidate(), "ordering_profile": "system_last"},
        ):
            with self.assertRaises(ValueError):
                self.engine.propose(invalid, hypothesis="bounded test")
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.connection.execute(
                """
                INSERT INTO meta_context_strategies (
                    id, version, parent_hash, config_json, config_hash,
                    hypothesis_hash, created_at
                ) VALUES (
                    'unsafe', 99, ?, '{"ordering_profile":"production",
                    "compression_minimum_tokens":80,"max_memories":24,
                    "max_skills":4,"secret":"x"}', ?, ?, 'now'
                )
                """,
                ("a" * 64, "b" * 64, "c" * 64),
            )

    def test_eligible_evaluation_is_paired_and_never_changes_production(self) -> None:
        candidate = self.engine.propose(
            self.candidate(), hypothesis="Use less context without quality loss."
        )
        harness = _Harness(
            (
                MetaContextCaseEvidence("a", 500_000, 502_000, 100, 90),
                MetaContextCaseEvidence("b", 500_000, 502_000, 100, 90),
            )
        )
        report = self.engine.evaluate(
            candidate["id"], harness=harness, seed=7
        )
        self.assertEqual(report["status"], "promotion_eligible")
        self.assertFalse(report["production_changed"])
        self.assertEqual(
            harness.received,
            (
                ContextStrategy(),
                ContextStrategy.from_dict(self.candidate()),
            ),
        )
        readiness = self.engine.readiness()
        self.assertFalse(readiness["production_activation_ready"])
        self.assertIn("file_selection", readiness["blocked_dimensions"])

    def test_missing_duplicate_or_regressed_cases_fail_closed(self) -> None:
        first = self.engine.propose(
            self.candidate(), hypothesis="Incomplete case test."
        )
        incomplete = _Harness(
            (MetaContextCaseEvidence("a", 1, 2_000, 10, 9),),
            expected_cases=2,
        )
        with self.assertRaises(ValueError):
            self.engine.evaluate(first["id"], harness=incomplete, seed=1)
        run = self.db.connection.execute(
            "SELECT status FROM meta_context_runs WHERE strategy_id = ?",
            (first["id"],),
        ).fetchone()
        self.assertEqual(run["status"], "blocked")

        second_config = {**self.candidate(), "compression_minimum_tokens": 70}
        second = self.engine.propose(
            second_config, hypothesis="Protected regression test."
        )
        regressed = _Harness(
            (
                MetaContextCaseEvidence(
                    "a", 500_000, 510_000, 100, 90,
                    protected_regression=True,
                ),
                MetaContextCaseEvidence("b", 500_000, 510_000, 100, 90),
            )
        )
        report = self.engine.evaluate(second["id"], harness=regressed, seed=2)
        self.assertEqual(report["status"], "rejected")
        self.assertIn("protected_regression", report["decision_reason"])

    def test_strategy_changes_real_compiler_compression_without_mutating_input(self) -> None:
        content = (
            "Northstar region is ca-central-1 and this exact evidence is useful. "
            * 12
            + "\n\n"
            + "Unrelated deployment history should not be selected. " * 12
        )
        candidate = ContextCandidate(
            source_type="file",
            source_id="northstar",
            label="Northstar evidence",
            content=content,
            confidence=1.0,
            expected_utility=1.0,
            content_kind="text",
        )
        compiler = ContextCompiler(
            self.db,
            context_strategy=ContextStrategy(
                compression_minimum_tokens=40,
                max_memories=20,
                max_skills=3,
                ordering_profile="utility_desc",
            ),
        )
        bundle = compiler.compile_request(
            ContextRequest(
                task="Find Northstar region",
                scope="global",
                token_budget=500,
                relevant_files=(candidate,),
            )
        )
        block = next(x for x in bundle.blocks if x.source_id == "northstar")
        self.assertEqual(block.compression_strategy, "exact_extraction")
        self.assertLess(block.tokens, block.original_tokens)
        self.assertEqual(candidate.content, content)
        self.assertEqual(
            block.security_content_hash,
            compiler.security.get(block.security_assessment_id)["content_hash"],
        )


if __name__ == "__main__":
    unittest.main()
