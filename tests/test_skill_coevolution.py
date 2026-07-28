from __future__ import annotations

import json
import tempfile
import unittest
import uuid
from pathlib import Path

from acr_runtime import AdaptiveRuntime
from acr_runtime.attribution import (
    AttributionOutcome,
    ContextAttribution,
)
from acr_runtime.experience import (
    DistilledKind,
    ExperienceEvent,
    ExperienceEventKind,
    ExperienceTraceCreate,
)
from acr_runtime.memory import utc_now


PROCEDURE = "Back up, migrate, verify integrity, then report evidence."


class MemorySkillCoevolutionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.runtime = AdaptiveRuntime(Path(self.directory.name) / "acr.db")

    def tearDown(self) -> None:
        self.runtime.close()
        self.directory.cleanup()

    def _verified_task(self, number: int) -> str:
        task_id = self.runtime.db.create_task(
            objective=f"Verified procedure {number}",
            scope="alpha",
            token_budget=200,
        )
        now = utc_now()
        with self.runtime.db.connection:
            self.runtime.db.connection.execute(
                """
                UPDATE tasks SET status = 'succeeded', critic_score = 0.95,
                    completed_at = ? WHERE id = ?
                """,
                (now, task_id),
            )
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
                ) VALUES (?, ?, '{}', 1, 1, 0, ?)
                """,
                (str(uuid.uuid4()), task_id, now),
            )
        return task_id

    def _grounded_trace(self, number: int) -> str:
        task_id = self._verified_task(number)
        trace = self.runtime.capture_experience(
            ExperienceTraceCreate(
                task_id=task_id,
                scope="alpha",
                task_class="database-release",
                outcome="succeeded",
                significance_score=0.95,
                events=(
                    ExperienceEvent(
                        ExperienceEventKind.PROCEDURE,
                        PROCEDURE,
                        evidence=(f"verified-run-{number}",),
                        metadata_json=json.dumps(
                            {
                                "inputs": {"database": "local SQLite path"},
                                "outputs": {"report": "integrity evidence"},
                                "applicability": ["Local SQLite releases"],
                                "permissions": ["filesystem:read"],
                                "tools": ["python:sqlite3"],
                                "verification": ["Run PRAGMA integrity_check"],
                            }
                        ),
                    ),
                ),
            )
        )
        planned = self.runtime.plan_distillation(trace.id)
        applied = self.runtime.approve_distillation(planned.id)
        item = next(
            entry
            for entry in applied.items
            if entry.kind is DistilledKind.SUCCESSFUL_PROCEDURE
        )
        self.runtime.db.connection.execute(
            """
            UPDATE memories SET status = 'confirmed'
            WHERE id = ? AND status = 'candidate'
            """,
            (item.memory_id,),
        )
        self.runtime.db.connection.commit()
        return trace.id

    def _generated_skill(self) -> str:
        for number in range(3):
            self._grounded_trace(number)
        plan = self.runtime.plan_skill_generation(scope="alpha")
        procedure = next(
            item
            for item in plan.candidates
            if item.trigger_kind == "repeated_successful_procedure"
        )
        applied = self.runtime.approve_skill_generation(plan.id)
        return next(
            item.skill_id
            for item in applied.candidates
            if item.id == procedure.id
        )

    def test_grounded_repetition_creates_reloadable_minimized_lineage(self):
        skill_id = self._generated_skill()

        report = self.runtime.skill_evidence(skill_id)

        self.assertEqual(report["assessment"], "grounded")
        self.assertEqual(report["support_total"], 3)
        self.assertEqual(report["support_valid"], 3)
        self.assertEqual(report["independent_roots"], 3)
        self.assertTrue(report["activation_eligible"])
        encoded = json.dumps(report, sort_keys=True)
        self.assertNotIn(PROCEDURE, encoded)
        self.assertNotIn("verified-run", encoded)

    def test_unmanaged_generated_candidate_cannot_activate(self):
        for number in range(3):
            task_id = self._verified_task(number)
            self.runtime.capture_experience(
                ExperienceTraceCreate(
                    task_id=task_id,
                    scope="alpha",
                    task_class="database-release",
                    outcome="succeeded",
                    significance_score=0.95,
                    events=(
                        ExperienceEvent(
                            ExperienceEventKind.PROCEDURE,
                            PROCEDURE,
                            evidence=(f"raw-{number}",),
                        ),
                    ),
                )
            )
        plan = self.runtime.plan_skill_generation(scope="alpha")
        applied = self.runtime.approve_skill_generation(plan.id)
        skill_id = applied.candidates[0].skill_id
        skill = self.runtime.inspect_skill(skill_id)
        with self.runtime.db.connection:
            self.runtime.db.connection.execute(
                """
                UPDATE skills SET verification_status = 'static_passed'
                WHERE id = ?
                """,
                (skill_id,),
            )
            self.runtime.db.connection.execute(
                """
                INSERT INTO skill_validation_runs (
                    id, skill_id, package_hash, status, policy_json,
                    created_at, completed_at
                ) VALUES (?, ?, ?, 'passed', '{}', ?, ?)
                """,
                (
                    str(uuid.uuid4()),
                    skill_id,
                    skill["content_hash"],
                    utc_now(),
                    utc_now(),
                ),
            )

        with self.assertRaisesRegex(ValueError, "memory support"):
            self.runtime.activate_skill(skill_id)
        self.assertEqual(
            self.runtime.skill_evidence(skill_id)["assessment"], "unassessed"
        )

    def test_invalidation_lowers_trust_and_quarantines_active_skill(self):
        skill_id = self._generated_skill()
        with self.runtime.db.connection:
            self.runtime.db.connection.execute(
                """
                UPDATE skills SET lifecycle_status = 'active', status = 'active'
                WHERE id = ?
                """,
                (skill_id,),
            )
        support_id = self.runtime.skill_evidence(skill_id)["supports"][0]["id"]

        trust = self.runtime.invalidate_skill_support(
            support_id,
            reason="operator_rejected",
            actor="test-operator",
        )
        repeated = self.runtime.invalidate_skill_support(
            support_id,
            reason="operator_rejected",
            actor="test-operator",
        )

        self.assertEqual(trust.assessment, "invalidated")
        self.assertFalse(trust.activation_eligible)
        self.assertEqual(repeated.evidence_revision, trust.evidence_revision)
        self.assertEqual(
            self.runtime.inspect_skill(skill_id)["lifecycle_status"],
            "quarantined",
        )
        count = self.runtime.db.connection.execute(
            """
            SELECT COUNT(*) FROM skill_support_invalidations
            WHERE support_link_id = ?
            """,
            (support_id,),
        ).fetchone()[0]
        self.assertEqual(count, 1)

    def test_lineage_and_snapshots_are_immutable(self):
        skill_id = self._generated_skill()
        support_id = self.runtime.skill_evidence(skill_id)["supports"][0]["id"]
        snapshot_id = self.runtime.db.connection.execute(
            """
            SELECT id FROM skill_reliability_snapshots
            WHERE skill_id = ? LIMIT 1
            """,
            (skill_id,),
        ).fetchone()[0]

        with self.assertRaises(Exception):
            self.runtime.db.connection.execute(
                "DELETE FROM skill_support_links WHERE id = ?", (support_id,)
            )
        with self.assertRaises(Exception):
            self.runtime.db.connection.execute(
                """
                UPDATE skill_reliability_snapshots SET assessment = 'probation'
                WHERE id = ?
                """,
                (snapshot_id,),
            )

    def test_router_reconciles_changed_memory_before_selection(self):
        skill_id = self._generated_skill()
        memory_id = self.runtime.skill_evidence(skill_id)["supports"][0][
            "memory_id"
        ]
        with self.runtime.db.connection:
            self.runtime.db.connection.execute(
                """
                UPDATE skills SET lifecycle_status = 'active', status = 'active'
                WHERE id = ?
                """,
                (skill_id,),
            )
            self.runtime.db.connection.execute(
                """
                UPDATE memories SET status = 'archived',
                    lifecycle_state = 'archived'
                WHERE id = ?
                """,
                (memory_id,),
            )

        route = self.runtime.route_skills(
            "Back up and migrate a local SQLite database",
            task_class="database-release",
            token_budget=1_000,
        )

        self.assertNotIn(skill_id, {item.id for item in route.selected})
        self.assertEqual(
            self.runtime.inspect_skill(skill_id)["lifecycle_status"],
            "quarantined",
        )

    def test_execution_evidence_recomputes_conservative_reliability(self):
        skill_id = self._generated_skill()
        task_id = self._verified_task(50)
        self.runtime.db.record_context(
            task_id,
            (
                {
                    "source_type": "skill",
                    "source_id": skill_id,
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
        attribution = ContextAttribution(
            id=str(uuid.uuid4()),
            task_id=task_id,
            source_type="skill",
            source_id=skill_id,
            role="skill_used",
            outcome=AttributionOutcome.CONTRIBUTED,
            impact_score=1,
            confidence=1,
            approximate_roi=0.1,
            model_score=1,
            execution_score=1,
            dependency_score=1,
            evaluator_score=1,
            evidence_json="{}",
        )

        self.runtime.db.complete_task(
            task_id,
            success=True,
            critic_score=1,
            duration_ms=10,
            attributions=(attribution,),
        )

        trust = self.runtime.reconcile_skill_evidence(skill_id)
        self.assertEqual(trust.execution_successes, 1)
        self.assertEqual(trust.execution_failures, 0)
        self.assertGreater(trust.reliability, 0)


if __name__ == "__main__":
    unittest.main()
