from __future__ import annotations

import io
import json
import sqlite3
import tempfile
import unittest
import uuid
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path

from acr_runtime.autonomous_improvement import (
    AutonomousImprovementLoop,
    BenchmarkEvidence,
    ImprovementPolicyRegistry,
    digest,
)
from acr_runtime.cli import main
from acr_runtime.continuous_quality import (
    ContinuousQualityError,
    QualityGateApprovalCreate,
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

    @staticmethod
    def _passing_evidence(**changes) -> BenchmarkEvidence:
        values = {
            "case_count": 2,
            "complete": True,
            "hard_violations": 0,
            "incumbent_utility_micros": 500_000,
            "candidate_utility_micros": 502_000,
            "protected_regressions": 0,
            "summary": {"suite": "sealed-fixture-v1"},
            "unit_tests_passed": True,
            "security_checks_passed": True,
            "benchmark_quality_micros": 900_000,
            "minimum_quality_micros": 850_000,
            "token_regression_bps": 0,
            "maximum_token_regression_bps": 100,
            "cost_regression_bps": 0,
            "maximum_cost_regression_bps": 100,
            "latency_regression_bps": 0,
            "maximum_latency_regression_bps": 100,
            "gate_evidence": (
                "test:unit-v1",
                "security:scan-v1",
                "benchmark:paired-v1",
            ),
        }
        values.update(changes)
        return BenchmarkEvidence(**values)

    @staticmethod
    def _approval(
        result: dict[str, object], **changes
    ) -> QualityGateApprovalCreate:
        values = {
            "schema_version": 1,
            "run_id": result["run_id"],
            "assessment_hash": result["quality_assessment"][
                "assessment_hash"
            ],
            "actor_ref": "human:operator-miche",
            "decision": "approve",
            "justified_tradeoffs": [],
            "justification": "All retained continuous quality gates passed.",
            "evidence": ["approval:quality-gate-v1"],
        }
        values.update(changes)
        return QualityGateApprovalCreate.from_dict(values)

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
        benchmark = _Benchmark(self._passing_evidence())
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
            self._passing_evidence()
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
        self.assertEqual(result["decision"], "awaiting_approval")
        self.assertEqual(self.registry.active(target).id, incumbent.id)
        approved = self.loop.approve(self._approval(result))
        self.assertEqual(approved["decision"], "promoted")
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
            self._passing_evidence(
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
            self._passing_evidence(case_count=11)
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

    def test_quantitative_tradeoffs_require_exact_human_justification(self):
        target = "context_thresholds"
        self._seed_telemetry(target)
        incumbent = self.registry.active(target)
        candidate = self._candidate(target)
        benchmark = _Benchmark(
            self._passing_evidence(
                benchmark_quality_micros=800_000,
                token_regression_bps=250,
                maximum_token_regression_bps=100,
                cost_regression_bps=250,
                maximum_cost_regression_bps=100,
                latency_regression_bps=250,
                maximum_latency_regression_bps=100,
            )
        )
        result = self.loop.run(
            target=target,
            scope="global",
            hypothesis="Trade tokens for measured quality.",
            candidate=candidate,
            authorization_id=self._authorize(
                target, candidate, benchmark
            ),
            benchmark=benchmark,
            seed=19,
        )
        self.assertEqual(result["decision"], "awaiting_approval")
        self.assertEqual(
            result["quality_assessment"]["tradeoff_failures"],
            [
                "benchmark_quality",
                "token_regression",
                "cost_regression",
                "latency_regression",
            ],
        )
        with self.assertRaisesRegex(
            ContinuousQualityError, "every and only"
        ):
            self.loop.approve(self._approval(result))
        approved = self.loop.approve(
            self._approval(
                result,
                justified_tradeoffs=[
                    "benchmark_quality",
                    "token_regression",
                    "cost_regression",
                    "latency_regression",
                ],
                justification=(
                    "Approve the bounded token increase because the retained "
                    "quality evidence exceeds its threshold."
                ),
            )
        )
        self.assertEqual(approved["decision"], "promoted")
        self.assertEqual(
            self.registry.active(target).id, result["candidate_version_id"]
        )

    def test_unit_or_security_failure_is_nonwaivable(self):
        target = "context_thresholds"
        self._seed_telemetry(target)
        incumbent = self.registry.active(target)
        candidate = self._candidate(target)
        benchmark = _Benchmark(
            self._passing_evidence(unit_tests_passed=False)
        )
        result = self.loop.run(
            target=target,
            scope="global",
            hypothesis="A failed test must prevent promotion.",
            candidate=candidate,
            authorization_id=self._authorize(
                target, candidate, benchmark
            ),
            benchmark=benchmark,
            seed=23,
        )
        self.assertEqual(result["decision"], "rejected")
        self.assertIn(
            "unit_tests", result["quality_assessment"]["hard_failures"]
        )
        self.assertEqual(self.registry.active(target).id, incumbent.id)
        with self.assertRaisesRegex(ValueError, "benchmarked improvement"):
            self.loop.approve(
                self._approval(
                    result,
                    justified_tradeoffs=["benchmark_quality"],
                )
            )

    def test_human_rejection_is_retained_without_promotion(self):
        target = "context_thresholds"
        self._seed_telemetry(target)
        incumbent = self.registry.active(target)
        candidate = self._candidate(target)
        benchmark = _Benchmark(self._passing_evidence())
        result = self.loop.run(
            target=target,
            scope="global",
            hypothesis="Retain the operator rejection.",
            candidate=candidate,
            authorization_id=self._authorize(
                target, candidate, benchmark
            ),
            benchmark=benchmark,
            seed=29,
        )
        rejected = self.loop.approve(
            self._approval(
                result,
                decision="reject",
                justification="Reject pending a broader retained benchmark.",
            )
        )
        self.assertEqual(rejected["decision"], "rejected")
        self.assertEqual(rejected["approval"]["decision"], "reject")
        self.assertEqual(self.registry.active(target).id, incumbent.id)
        self.assertEqual(
            self.loop.report(result["run_id"])["decision_reason"],
            "human_quality_gate_rejection",
        )

    def test_approval_schema_rejects_models_secrets_and_stale_assessments(self):
        target = "context_thresholds"
        self._seed_telemetry(target)
        candidate = self._candidate(target)
        benchmark = _Benchmark(self._passing_evidence())
        result = self.loop.run(
            target=target,
            scope="global",
            hypothesis="Exercise the closed approval boundary.",
            candidate=candidate,
            authorization_id=self._authorize(
                target, candidate, benchmark
            ),
            benchmark=benchmark,
            seed=31,
        )
        with self.assertRaisesRegex(
            ContinuousQualityError, "explicit human"
        ):
            self._approval(result, actor_ref="model:automatic-judge")
        with self.assertRaisesRegex(
            ContinuousQualityError, "secret material"
        ):
            self._approval(
                result,
                justification="Bearer " + "a" * 40,
            )
        stale = self._approval(result)
        object.__setattr__(stale, "assessment_hash", "b" * 64)
        with self.assertRaisesRegex(
            ContinuousQualityError, "retained assessment"
        ):
            self.loop.approve(stale)

    def test_quality_ledgers_are_immutable_and_content_minimized(self):
        target = "context_thresholds"
        self._seed_telemetry(target)
        candidate = self._candidate(target)
        benchmark = _Benchmark(self._passing_evidence())
        result = self.loop.run(
            target=target,
            scope="global",
            hypothesis="Verify immutable approval evidence.",
            candidate=candidate,
            authorization_id=self._authorize(
                target, candidate, benchmark
            ),
            benchmark=benchmark,
            seed=37,
        )
        approved = self.loop.approve(self._approval(result))
        approval = approved["approval"]
        self.assertNotIn("operator-miche", json.dumps(approval))
        self.assertNotIn(
            "All retained continuous quality gates passed.",
            json.dumps(approval),
        )
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.connection.execute(
                """
                UPDATE continuous_quality_assessments
                SET target='changed' WHERE run_id=?
                """,
                (result["run_id"],),
            )
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.connection.execute(
                "DELETE FROM continuous_quality_approvals WHERE id=?",
                (approval["id"],),
            )

    def test_safe_mode_blocks_final_approval_and_cli_can_record_it(self):
        target = "context_thresholds"
        self._seed_telemetry(target)
        candidate = self._candidate(target)
        benchmark = _Benchmark(self._passing_evidence())
        result = self.loop.run(
            target=target,
            scope="global",
            hypothesis="Exercise final approval interfaces.",
            candidate=candidate,
            authorization_id=self._authorize(
                target, candidate, benchmark
            ),
            benchmark=benchmark,
            seed=41,
        )
        guarded = AutonomousImprovementLoop(
            self.db.connection,
            self.registry,
            minimum_attributed_tasks=2,
            minimum_cases=2,
            minimum_improvement_micros=1_000,
            mutation_guard=lambda _capability: (_ for _ in ()).throw(
                PermissionError("safe mode blocks autonomous_optimization")
            ),
        )
        with self.assertRaisesRegex(PermissionError, "safe mode"):
            guarded.approve(self._approval(result))

        approval_file = Path(self.temp.name) / "approval.json"
        approval_file.write_text(
            json.dumps(self._approval(result).as_dict()),
            encoding="utf-8",
        )
        self.db.close()
        output = io.StringIO()
        with redirect_stdout(output):
            exit_code = main(
                [
                    "--db",
                    str(self.path),
                    "improvements",
                    "approve",
                    str(approval_file),
                ]
            )
        self.assertEqual(exit_code, 0)
        self.assertEqual(json.loads(output.getvalue())["decision"], "promoted")
        self.db = RuntimeDB(self.path)

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
