from __future__ import annotations

import tempfile
import unittest
import uuid
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from acr_runtime.autonomous_improvement import (
    AutonomousImprovementLoop,
    BenchmarkEvidence,
    ImprovementPolicyRegistry,
    digest,
)
from acr_runtime.db import RuntimeDB, utc_now
from acr_runtime.migrations import EXPECTED_SCHEMA_VERSION
from acr_runtime.service import AdaptiveRuntime


class _Benchmark:
    def __init__(self, evidence: BenchmarkEvidence) -> None:
        self.evidence = evidence
        self.received: tuple[dict[str, int], dict[str, int]] | None = None

    def identity(self) -> dict[str, object]:
        return {
            "dataset": "sealed-fixture-v1",
            "evaluator": "exact-fixed-point-v1",
            "executor": "test-only-side-effect-free",
        }

    def run_paired(
        self,
        *,
        target: str,
        incumbent: dict[str, int],
        candidate: dict[str, int],
        seed: int,
        maximum_cases: int,
    ) -> BenchmarkEvidence:
        self.received = (dict(incumbent), dict(candidate))
        return self.evidence


class AutonomousImprovementTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "acr.db"
        self.db = RuntimeDB(self.path)
        self.registry = ImprovementPolicyRegistry(self.db.connection)
        self.registry.bootstrap()
        self.loop = AutonomousImprovementLoop(
            self.db.connection,
            self.registry,
            minimum_attributed_tasks=2,
            minimum_cases=2,
            minimum_improvement_micros=1_000,
        )

    def tearDown(self) -> None:
        self.db.close()
        self.temp.cleanup()

    def _candidate(self, target: str) -> dict[str, int]:
        config = dict(self.registry.active(target).config)
        if target == "retrieval_weights":
            config["keyword_bps"] += 100
            config["semantic_bps"] -= 100
        else:
            first = next(iter(config))
            config[first] += 100
        return config

    def _seed_telemetry(self, target: str, count: int = 2) -> None:
        active = self.registry.active(target)
        for _ in range(count):
            task_id = str(uuid.uuid4())
            self.db.connection.execute(
                """
                INSERT INTO tasks (
                    id, objective, scope, token_budget, status, created_at,
                    completed_at
                ) VALUES (?, 'fixture', 'global', 100, 'succeeded', ?, ?)
                """,
                (task_id, utc_now(), utc_now()),
            )
            self.db.connection.execute(
                """
                INSERT INTO task_policy_attributions (
                    task_id, target, version_id, config_hash
                ) VALUES (?, ?, ?, ?)
                """,
                (task_id, target, active.id, active.config_hash),
            )
        self.db.connection.commit()

    def _authorize(
        self, target: str, candidate: dict[str, int], benchmark: _Benchmark
    ) -> str:
        return self.loop.authorize(
            target=target,
            scope="global",
            candidate=candidate,
            benchmark_identity=benchmark.identity(),
            max_cases=10,
            expires_at=(
                datetime.now(timezone.utc) + timedelta(minutes=5)
            ).isoformat(),
        )

    def test_schema_and_bootstrap_are_exact_and_immutable(self) -> None:
        self.assertEqual(self.db.health()["schema_version"], EXPECTED_SCHEMA_VERSION)
        self.assertEqual(self.registry.context_threshold(), 0.05)
        self.assertEqual(
            self.registry.routing_config().minimum_benefit, 0.08
        )
        self.assertAlmostEqual(
            self.registry.retrieval_config().weights.keyword, 0.24
        )
        active = self.registry.active("context_thresholds")
        with self.assertRaisesRegex(Exception, "immutable"):
            self.db.connection.execute(
                """
                UPDATE improvement_policy_versions
                SET config_json = '{}' WHERE id = ?
                """,
                (active.id,),
            )

    def test_closed_schemas_reject_unsafe_or_unbounded_candidates(self) -> None:
        benchmark = _Benchmark(BenchmarkEvidence(2, True, 0, 1, 2_000))
        for forbidden in (
            "security_policies",
            "permission_rules",
            "secret_handling",
            "data_deletion_policies",
            "model_routing_thresholds",
        ):
            with self.assertRaises(ValueError):
                self.loop.authorize(
                    target=forbidden,
                    scope="global",
                    candidate={},
                    benchmark_identity=benchmark.identity(),
                    max_cases=2,
                    expires_at=(
                        datetime.now(timezone.utc) + timedelta(minutes=5)
                    ).isoformat(),
                )
        incumbent = self.registry.active("context_thresholds")
        for candidate in (
            {"minimum_optional_utility_bps": True},
            {"minimum_optional_utility_bps": 501, "secret": 1},
            {"minimum_optional_utility_bps": 10_001},
            {"minimum_optional_utility_bps": -1},
        ):
            with self.assertRaises(ValueError):
                self.registry.create_candidate(
                    "context_thresholds",
                    candidate,
                    parent_id=incumbent.id,
                )
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.connection.execute(
                """
                INSERT INTO improvement_policy_versions (
                    id, target, version, parent_id, config_json, config_hash,
                    provenance_json, created_at
                ) VALUES (
                    'unsafe', 'context_thresholds', 99, NULL,
                    '{"minimum_optional_utility_bps":501,"secret":1}',
                    ?, '{}', ?
                )
                """,
                ("f" * 64, utc_now()),
            )
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.connection.execute(
                """
                INSERT INTO improvement_policy_events (
                    id, target, event_type, evidence_hash, created_at
                ) VALUES ('unsafe-event', 'permission_rules', 'blocked', ?, ?)
                """,
                ("e" * 64, utc_now()),
            )

    def test_readiness_fails_closed_without_attributed_telemetry(self) -> None:
        result = self.loop.readiness("retrieval_weights")
        self.assertFalse(result["ready"])
        self.assertEqual(result["attributed_successful_tasks"], 0)
        skill = self.loop.readiness("skill_instructions")
        self.assertEqual(
            skill["reasons"], ["governed_skill_promotion_adapter_unavailable"]
        )

    def test_winner_promotes_and_cas_rollback_restores_exact_parent(self) -> None:
        target = "context_thresholds"
        self._seed_telemetry(target)
        incumbent = self.registry.active(target)
        candidate = self._candidate(target)
        benchmark = _Benchmark(
            BenchmarkEvidence(
                case_count=2,
                complete=True,
                hard_violations=0,
                incumbent_utility_micros=500_000,
                candidate_utility_micros=502_000,
                summary={"suite": "sealed-fixture-v1"},
            )
        )
        authorization = self._authorize(target, candidate, benchmark)
        result = self.loop.run(
            target=target,
            scope="global",
            hypothesis="A bounded threshold increase improves useful density.",
            candidate=candidate,
            authorization_id=authorization,
            benchmark=benchmark,
            seed=7,
        )
        self.assertEqual(result["decision"], "promoted")
        promoted = self.registry.active(target)
        self.assertEqual(promoted.id, result["candidate_version_id"])
        self.assertEqual(benchmark.received, (incumbent.config, candidate))
        report = self.loop.report(result["run_id"])
        self.assertEqual(report["benchmark"]["case_count"], 2)
        restored = self.registry.rollback(target, expected_head_id=promoted.id)
        self.assertEqual(restored.id, incumbent.id)
        self.assertEqual(restored.config_hash, digest(incumbent.config))
        with self.assertRaises(RuntimeError):
            self.registry.rollback(target, expected_head_id=promoted.id)

    def test_loser_is_recorded_and_never_changes_head(self) -> None:
        target = "skill_routing_thresholds"
        self._seed_telemetry(target)
        incumbent = self.registry.active(target)
        candidate = self._candidate(target)
        benchmark = _Benchmark(
            BenchmarkEvidence(
                case_count=2,
                complete=True,
                hard_violations=0,
                incumbent_utility_micros=500_000,
                candidate_utility_micros=499_000,
                protected_regressions=1,
            )
        )
        result = self.loop.run(
            target=target,
            scope="global",
            hypothesis="Test a bounded routing threshold.",
            candidate=candidate,
            authorization_id=self._authorize(target, candidate, benchmark),
            benchmark=benchmark,
            seed=11,
        )
        self.assertEqual(result["decision"], "rejected")
        self.assertEqual(self.registry.active(target).id, incumbent.id)
        self.assertIn("protected_regression", result["reason"])

    def test_failed_benchmark_is_blocked_and_authorization_cannot_replay(self) -> None:
        target = "context_thresholds"
        self._seed_telemetry(target)
        incumbent = self.registry.active(target)
        candidate = self._candidate(target)
        benchmark = _Benchmark(
            BenchmarkEvidence(11, True, 0, 500_000, 502_000)
        )
        authorization = self._authorize(target, candidate, benchmark)
        arguments = {
            "target": target,
            "scope": "global",
            "hypothesis": "Exercise fail-closed benchmark handling.",
            "candidate": candidate,
            "authorization_id": authorization,
            "benchmark": benchmark,
            "seed": 13,
        }
        with self.assertRaises(ValueError):
            self.loop.run(**arguments)
        self.assertEqual(self.registry.active(target).id, incumbent.id)
        row = self.db.connection.execute(
            """
            SELECT status, decision_reason FROM improvement_runs
            WHERE authorization_id = ?
            """,
            (authorization,),
        ).fetchone()
        self.assertEqual(
            tuple(row), ("blocked", "controlled_benchmark_failed")
        )
        with self.assertRaises(RuntimeError):
            self.loop.run(**arguments)

    def test_runtime_attributes_versions_and_resolves_promoted_config(self) -> None:
        self.db.close()
        with AdaptiveRuntime(database=self.path) as runtime:
            bundle = runtime.compile_context(
                "diagnose sqlite schema", scope="global", token_budget=200
            )
            rows = runtime.db.connection.execute(
                """
                SELECT target, version_id, config_hash
                FROM task_policy_attributions WHERE task_id = ?
                ORDER BY target
                """,
                (bundle.task_id,),
            ).fetchall()
            self.assertEqual(len(rows), 3)
            self.assertTrue(all(len(row["config_hash"]) == 64 for row in rows))
            incumbent = runtime.improvement_policies.active(
                "context_thresholds"
            )
            candidate = runtime.improvement_policies.create_candidate(
                "context_thresholds",
                {"minimum_optional_utility_bps": 600},
                parent_id=incumbent.id,
            )
            runtime.improvement_policies.promote(
                "context_thresholds",
                candidate.id,
                expected_head_id=incumbent.id,
            )
            self.assertEqual(
                runtime.compiler.policy_registry.context_threshold(), 0.06
            )


if __name__ == "__main__":
    unittest.main()
