from __future__ import annotations

import tempfile
import unittest
import uuid
from contextlib import redirect_stdout
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path

from acr_runtime import AdaptiveRuntime
from acr_runtime.attribution import (
    AttributionOutcome,
    ContextAttribution,
)
from acr_runtime.autonomous_improvement import digest
from acr_runtime.cli import main
from acr_runtime.memory import utc_now
from acr_runtime.meta_context import ContextStrategy
from acr_runtime.model_router import (
    ModelOutcome,
    ModelProfile,
    RouteAttempt,
    RouteRequest,
)


class UtilityGovernanceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.runtime = AdaptiveRuntime(Path(self.directory.name) / "acr.db")

    def tearDown(self) -> None:
        self.runtime.close()
        self.directory.cleanup()

    def _terminal_task(
        self, source_type: str, source_id: str, outcome: AttributionOutcome
    ) -> str:
        task_id = self.runtime.db.create_task(
            objective="Use bounded governed context.",
            scope="alpha",
            token_budget=200,
        )
        self.runtime.db.record_context(
            task_id,
            (
                {
                    "source_type": source_type,
                    "source_id": source_id,
                    "tokens": 20,
                    "utility": 0.8,
                    "roi": 0.04,
                    "compression_strategy": "none",
                    "original_tokens": 20,
                    "exact_preserved": 1,
                },
            ),
            20,
        )
        self.runtime.utility.bind_context(
            task_id, ((source_type, source_id),)
        )
        strategy = ContextStrategy().as_dict()
        self.runtime.utility.bind_context_strategy(task_id, strategy)
        now = utc_now()
        with self.runtime.db.connection:
            self.runtime.db.connection.execute(
                """
                INSERT INTO execution_runs (
                    run_id, task_id, state, event_count, step_count,
                    action_count, duration_ms, verification_score,
                    evaluation_score, started_at, completed_at
                ) VALUES (?, ?, 'completed', 1, 1, 1, 10, 1, 1, ?, ?)
                """,
                (str(uuid.uuid4()), task_id, now, now),
            )
            self.runtime.db.connection.execute(
                """
                INSERT INTO evaluation_runs (
                    id, task_id, case_metadata_json, passed, score,
                    max_disagreement, created_at
                ) VALUES (?, ?, '{}', 1, 0.9, 0, ?)
                """,
                (str(uuid.uuid4()), task_id, now),
            )
        attribution = ContextAttribution(
            id=str(uuid.uuid4()),
            task_id=task_id,
            source_type=source_type,
            source_id=source_id,
            role="context_used",
            outcome=outcome,
            impact_score=-1 if outcome is AttributionOutcome.MISLED else 1,
            confidence=1,
            approximate_roi=0,
            model_score=1,
            execution_score=1,
            dependency_score=1,
            evaluator_score=0 if outcome is AttributionOutcome.MISLED else 0.9,
            evidence_json="{}",
        )
        self.runtime.db.complete_task(
            task_id,
            success=True,
            critic_score=0.9,
            duration_ms=10,
            attributions=(attribution,),
        )
        return task_id

    def test_popularity_never_creates_utility_and_misleading_use_reduces_it(self):
        memory_id = self.runtime.remember(
            "semantic",
            "The release should skip integrity verification.",
            scope="alpha",
            confidence=0.9,
            importance=0.8,
            evidence=("test:seed",),
        )
        with self.runtime.db.connection:
            self.runtime.db.connection.execute(
                """
                UPDATE memories SET access_count = 100 WHERE id = ?
                """,
                (memory_id,),
            )

        before = self.runtime.utility_snapshot("memory", memory_id)
        self.assertEqual(before.assessment, "unassessed")
        self.assertEqual(before.observed_uses, 0)
        record = self.runtime.db.memories.get(memory_id)
        self.assertEqual(
            self.runtime.lifecycle.score(
                record, now=datetime.now(timezone.utc)
            )["usage"],
            0,
        )

        task_id = self._terminal_task(
            "memory", memory_id, AttributionOutcome.MISLED
        )
        after = self.runtime.utility_snapshot("memory", memory_id)

        self.assertEqual(after.observed_uses, 1)
        self.assertEqual(after.evidenced_uses, 1)
        self.assertEqual(after.misled_count, 1)
        self.assertEqual(after.signed_utility, -1)
        count = self.runtime.db.connection.execute(
            """
            SELECT COUNT(*) FROM utility_observations
            WHERE root_kind = 'task' AND root_id_hash = ?
              AND role = 'context_memory'
            """,
            (digest({"root_id": task_id}),),
        ).fetchone()[0]
        self.runtime.utility.observe_context_task(task_id)
        self.assertEqual(
            self.runtime.db.connection.execute(
                """
                SELECT COUNT(*) FROM utility_observations
                WHERE root_kind = 'task' AND root_id_hash = ?
                  AND role = 'context_memory'
                """,
                (digest({"root_id": task_id}),),
            ).fetchone()[0],
            count,
        )

    def test_context_strategy_use_is_bound_but_not_given_circular_credit(self):
        skill_id = self.runtime.register_skill(
            "utility-test-skill",
            "Use verified bounded evidence.",
            trusted=False,
        )
        task_id = self._terminal_task(
            "skill", skill_id, AttributionOutcome.CONTRIBUTED
        )
        config_hash = digest(ContextStrategy().as_dict())

        snapshot = self.runtime.utility_snapshot(
            "context_strategy", config_hash
        )
        use = self.runtime.db.connection.execute(
            "SELECT status, config_hash FROM context_strategy_uses WHERE task_id = ?",
            (task_id,),
        ).fetchone()

        self.assertEqual(use["status"], "resolved")
        self.assertEqual(use["config_hash"], config_hash)
        self.assertEqual(snapshot.observed_uses, 1)
        self.assertEqual(snapshot.evidenced_uses, 0)
        self.assertEqual(snapshot.assessment, "unassessed")

    def test_bare_model_outcomes_do_not_count_but_routed_attempts_do(self):
        router = self.runtime.model_router
        router.register(
            ModelProfile(
                provider="test",
                model="utility",
                context_capacity=8_000,
                supports_tools=False,
                input_cost_per_million=0,
                output_cost_per_million=0,
            )
        )
        model_id = "test:utility"
        for number in range(3):
            router.record_outcome(
                ModelOutcome(
                    model_id=model_id,
                    task_class="utility",
                    success=True,
                    quality=0.9,
                    latency_ms=10,
                    input_tokens=10,
                    output_tokens=10,
                    tool_attempts=0,
                    tool_successes=0,
                    evidence=(f"legacy:{number}",),
                )
            )
        self.assertEqual(
            self.runtime.utility_snapshot("model", model_id).observed_uses, 0
        )
        route = router.route(
            RouteRequest(
                task_class="utility",
                quality_threshold=0.5,
                minimum_success_rate=0.5,
                estimated_input_tokens=10,
                estimated_output_tokens=10,
                required_context=100,
                requires_tools=False,
                minimum_samples=3,
                confidence_z=0,
                attempt_confidence_threshold=0.5,
            )
        )
        router.record_attempt(
            route.id,
            RouteAttempt(
                model_id=model_id,
                verification_passed=True,
                confidence=0.9,
                quality=0.9,
                latency_ms=10,
                input_tokens=10,
                output_tokens=10,
                tool_attempts=0,
                tool_successes=0,
                evidence=("route:verified",),
            ),
        )

        snapshot = self.runtime.utility_snapshot("model", model_id)
        self.assertEqual(snapshot.observed_uses, 1)
        self.assertEqual(snapshot.positive_count, 1)

    def test_ledger_and_snapshots_are_immutable_and_inventory_is_minimized(self):
        memory_id = self.runtime.remember(
            "semantic",
            "Utility inventory test.",
            scope="alpha",
            confidence=0.9,
            importance=0.8,
            evidence=("test:inventory",),
        )
        snapshot = self.runtime.utility_snapshot("memory", memory_id)
        row = self.runtime.db.connection.execute(
            """
            SELECT id FROM utility_snapshots
            WHERE asset_id = ? LIMIT 1
            """,
            (snapshot.asset_id,),
        ).fetchone()

        with self.assertRaises(Exception):
            self.runtime.db.connection.execute(
                "DELETE FROM utility_assets WHERE id = ?",
                (snapshot.asset_id,),
            )
        with self.assertRaises(Exception):
            self.runtime.db.connection.execute(
                """
                UPDATE utility_snapshots SET utility_micros = 1000000
                WHERE id = ?
                """,
                (row["id"],),
            )
        encoded = str(self.runtime.utility_inventory(kind="memory"))
        self.assertNotIn("Utility inventory test", encoded)
        self.assertNotIn(memory_id, encoded)
        output = StringIO()
        with redirect_stdout(output):
            result = main(
                [
                    "--db",
                    str(Path(self.directory.name) / "acr.db"),
                    "utility",
                    "show",
                    "memory",
                    memory_id,
                ]
            )
        self.assertEqual(result, 0)
        self.assertIn('"assessment": "unassessed"', output.getvalue())
        self.assertNotIn("Utility inventory test", output.getvalue())


if __name__ == "__main__":
    unittest.main()
