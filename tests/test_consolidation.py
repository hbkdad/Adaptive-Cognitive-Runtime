from __future__ import annotations

import json
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

from acr_runtime import AdaptiveRuntime
from acr_runtime.cli import main
from acr_runtime.consolidation import ConsolidationKind
from acr_runtime.memory import (
    MemoryCreate,
    MemoryPatch,
    MemoryStatus,
    MemoryType,
)


class ConsolidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.runtime = AdaptiveRuntime(Path(self.temp_dir.name) / "acr.db")
        self.store = self.runtime.db.memories

    def tearDown(self) -> None:
        self.runtime.close()
        self.temp_dir.cleanup()

    def add(
        self,
        content: str,
        *,
        memory_type: MemoryType = MemoryType.SEMANTIC,
        status: MemoryStatus = MemoryStatus.CONFIRMED,
        subject: str | None = "database",
        evidence: tuple[str, ...] = (),
        confidence: float = 0.8,
        importance: float = 0.5,
        valid_from: str | None = None,
        valid_until: str | None = None,
    ):
        return self.store.create(
            MemoryCreate(
                type=memory_type,
                content=content,
                scope="alpha",
                subject=subject,
                evidence=evidence,
                confidence=confidence,
                importance=importance,
                status=status,
                valid_from=valid_from,
                valid_until=valid_until,
            )
        )

    def age(
        self,
        memory_id: str,
        *,
        timestamp: str = "2025-01-01T00:00:00+00:00",
        utility: float | None = None,
        access_count: int | None = None,
        successful_uses: int | None = None,
    ) -> None:
        assignments = ["created_at = ?", "last_accessed = ?", "updated_at = ?"]
        values: list[object] = [timestamp, timestamp, timestamp]
        for column, value in (
            ("utility_score", utility),
            ("access_count", access_count),
            ("successful_uses", successful_uses),
        ):
            if value is not None:
                assignments.append(f"{column} = ?")
                values.append(value)
        values.append(memory_id)
        with self.runtime.db.connection:
            self.runtime.db.connection.execute(
                f"UPDATE memories SET {', '.join(assignments)} WHERE id = ?",
                values,
            )

    def test_dry_run_persists_plan_without_mutating_duplicates(self):
        first = self.add("Use SQLite", evidence=("one.md",))
        second = self.add("Use   SQLite", evidence=("two.md",))

        plan = self.runtime.plan_consolidation(scope="alpha")

        self.assertEqual(plan.status, "planned")
        merges = plan.grouped()["MERGES"]
        self.assertEqual(len(merges), 1)
        self.assertEqual(set(merges[0].target_ids), {first.id, second.id})
        self.assertEqual(self.store.get(first.id).status, MemoryStatus.CONFIRMED)
        self.assertEqual(self.store.get(second.id).status, MemoryStatus.CONFIRMED)
        persisted = self.runtime.consolidation_audit.load(plan.id)
        self.assertEqual(persisted.id, plan.id)

    def test_approved_merge_preserves_evidence_and_archived_raw_record(self):
        first = self.add(
            "Use SQLite",
            evidence=("one.md",),
            confidence=0.95,
        )
        second = self.add("Use SQLite", evidence=("two.md",), confidence=0.8)
        plan = self.runtime.plan_consolidation(scope="alpha")

        applied = self.runtime.approve_consolidation(plan.id)

        action = applied.grouped()["MERGES"][0]
        self.assertEqual(action.status, "applied")
        survivor_id = str(action.payload["survivor_id"])
        duplicate_id = next(
            memory_id
            for memory_id in action.target_ids
            if memory_id != survivor_id
        )
        survivor = self.store.get(survivor_id)
        duplicate = self.store.get(duplicate_id)
        self.assertEqual(survivor.evidence, ("one.md", "two.md"))
        self.assertIn(
            "consolidated_exact_duplicates", survivor.retention_reasons
        )
        self.assertEqual(duplicate.status, MemoryStatus.ARCHIVED)
        self.assertEqual(duplicate.content, "Use SQLite")
        self.assertIsNotNone(duplicate)

    def test_conflicts_are_review_only_and_never_auto_resolved(self):
        sqlite = self.add("Database is SQLite")
        postgres = self.add("Database is PostgreSQL")
        plan = self.runtime.plan_consolidation(scope="alpha")

        conflicts = plan.grouped()["CONFLICTS"]
        self.assertEqual(len(conflicts), 1)
        applied = self.runtime.approve_consolidation(plan.id)

        self.assertEqual(
            applied.grouped()["CONFLICTS"][0].status, "review_required"
        )
        self.assertEqual(self.store.get(sqlite.id).status, MemoryStatus.CONFIRMED)
        self.assertEqual(
            self.store.get(postgres.id).status, MemoryStatus.CONFIRMED
        )

    def test_adjacent_truth_is_linked_only_after_approval(self):
        old = self.add(
            "Database is Firebase",
            valid_from="2026-01-01T00:00:00Z",
            valid_until="2026-06-01T00:00:00Z",
        )
        new = self.add(
            "Database is Supabase",
            valid_from="2026-06-01T00:00:00Z",
        )
        plan = self.runtime.plan_consolidation(scope="alpha")

        action = plan.grouped()["SUPERSESSIONS"][0]
        self.assertEqual(action.target_ids, (old.id, new.id))
        self.assertIsNone(self.store.get(old.id).superseded_by)
        self.runtime.approve_consolidation(plan.id)

        self.assertEqual(self.store.get(old.id).superseded_by, new.id)
        self.assertEqual(self.store.get(new.id).supersedes, old.id)

    def test_high_utility_candidate_is_promoted(self):
        candidate = self.add(
            "Rollback migrations transactionally",
            memory_type=MemoryType.PROCEDURAL,
            status=MemoryStatus.CANDIDATE,
            subject="migration procedure",
            evidence=("test_migrations.py",),
            confidence=0.9,
        )
        self.age(
            candidate.id,
            timestamp="2026-07-01T00:00:00+00:00",
            utility=1.0,
            access_count=4,
            successful_uses=4,
        )
        plan = self.runtime.plan_consolidation(scope="alpha")

        self.assertEqual(
            plan.grouped()["PROMOTIONS"][0].target_ids, (candidate.id,)
        )
        self.runtime.approve_consolidation(plan.id)

        self.assertEqual(
            self.store.get(candidate.id).status, MemoryStatus.CONFIRMED
        )
        self.assertIn(
            "consolidated_high_utility_promotion",
            self.store.get(candidate.id).retention_reasons,
        )

    def test_stale_candidate_archives_and_stale_utility_decays(self):
        candidate = self.add(
            "Old unverified claim",
            status=MemoryStatus.CANDIDATE,
            subject="old claim",
            importance=0.2,
        )
        useful = self.add(
            "Occasionally useful fact",
            subject="useful fact",
            importance=0.5,
        )
        self.age(candidate.id)
        self.age(useful.id, utility=0.8, access_count=2, successful_uses=1)
        plan = self.runtime.plan_consolidation(scope="alpha")

        self.assertEqual(
            plan.grouped()["ARCHIVES"][0].target_ids, (candidate.id,)
        )
        decay = plan.grouped()["DECAYS"][0]
        self.assertEqual(decay.target_ids, (useful.id,))
        self.assertLess(float(decay.payload["to"]), 0.8)
        self.runtime.approve_consolidation(plan.id)

        self.assertEqual(
            self.store.get(candidate.id).status, MemoryStatus.ARCHIVED
        )
        self.assertLess(self.store.get(useful.id).utility_score, 0.8)

    def test_changed_memory_causes_stale_action_to_skip(self):
        self.add("Use SQLite", evidence=("one.md",))
        second = self.add("Use SQLite", evidence=("two.md",))
        plan = self.runtime.plan_consolidation(scope="alpha")
        self.store.update(second.id, MemoryPatch(importance=0.9))

        applied = self.runtime.approve_consolidation(plan.id)

        self.assertEqual(applied.status, "partially_applied")
        self.assertEqual(applied.grouped()["MERGES"][0].status, "skipped")
        self.assertEqual(self.store.get(second.id).status, MemoryStatus.CONFIRMED)

    def test_cli_requires_dry_run_then_explicit_run_approval(self):
        self.add("Use SQLite")
        self.add("Use SQLite")
        output = StringIO()
        with redirect_stdout(output):
            exit_code = main(
                [
                    "--db",
                    str(self.runtime.settings.database),
                    "memory",
                    "consolidate",
                    "--dry-run",
                    "--scope",
                    "alpha",
                ]
            )
        proposal = json.loads(output.getvalue())

        self.assertEqual(exit_code, 0)
        self.assertEqual(proposal["status"], "planned")
        self.assertEqual(len(proposal["MERGES"]), 1)
        approve_output = StringIO()
        with redirect_stdout(approve_output):
            main(
                [
                    "--db",
                    str(self.runtime.settings.database),
                    "memory",
                    "consolidate",
                    "--approve",
                    proposal["run_id"],
                ]
            )
        approved = json.loads(approve_output.getvalue())
        self.assertEqual(approved["status"], "applied")
        self.assertEqual(approved["MERGES"][0]["status"], "applied")


if __name__ == "__main__":
    unittest.main()
