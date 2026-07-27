from __future__ import annotations

import json
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path

from acr_runtime import AdaptiveRuntime
from acr_runtime.cli import main
from acr_runtime.lifecycle import LifecycleConfig
from acr_runtime.memory import (
    LifecycleState,
    MemoryCreate,
    MemoryQuery,
    MemoryStatus,
    MemoryType,
)


OLD = "2025-01-01T00:00:00+00:00"
NOW = datetime(2026, 7, 26, tzinfo=timezone.utc)


class LifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.path = Path(self.temp_dir.name) / "acr.db"
        self.runtime = AdaptiveRuntime(self.path)
        self.store = self.runtime.db.memories

    def tearDown(self) -> None:
        self.runtime.close()
        self.temp_dir.cleanup()

    def add(
        self,
        *,
        memory_type: MemoryType = MemoryType.SEMANTIC,
        importance: float = 0.1,
        confidence: float = 0.2,
        utility: float = 0.0,
        payload: str = "{}",
    ):
        record = self.store.create(
            MemoryCreate(
                type=memory_type,
                content=f"{memory_type.value} lifecycle example",
                scope="alpha",
                subject=f"subject-{memory_type.value}",
                importance=importance,
                confidence=confidence,
                utility_score=utility,
                structured_payload_json=payload,
                status=MemoryStatus.CONFIRMED,
            )
        )
        with self.runtime.db.connection:
            self.runtime.db.connection.execute(
                """
                UPDATE memories
                SET created_at = ?, updated_at = ?, last_accessed = ?,
                    lifecycle_updated_at = ?
                WHERE id = ?
                """,
                (OLD, OLD, OLD, OLD, record.id),
            )
            self.runtime.db.connection.execute(
                """
                INSERT INTO memory_scope_activity(
                    scope, last_active_at, access_count, updated_at
                ) VALUES ('alpha', ?, 0, ?)
                ON CONFLICT(scope) DO UPDATE SET
                    last_active_at = excluded.last_active_at,
                    updated_at = excluded.updated_at
                """,
                (OLD, OLD),
            )
        return self.store.get(record.id)

    def test_dry_run_scores_every_decay_factor_without_mutating(self):
        record = self.add()

        plan = self.runtime.lifecycle.dry_run(scope="alpha", now=NOW)

        self.assertEqual(plan.status, "planned")
        self.assertEqual(len(plan.actions), 1)
        action = plan.actions[0]
        self.assertEqual(action.from_state, LifecycleState.ACTIVE)
        self.assertEqual(action.to_state, LifecycleState.COLD)
        self.assertEqual(
            set(action.score),
            {
                "retention",
                "age_days",
                "usage",
                "importance",
                "confidence",
                "utility",
                "superseded",
                "scope_activity",
            },
        )
        self.assertEqual(
            self.store.get(record.id).lifecycle_state, LifecycleState.ACTIVE
        )

    def test_approval_moves_active_to_cold_then_old_cold_to_archive(self):
        record = self.add()
        first = self.runtime.lifecycle.dry_run(scope="alpha", now=NOW)
        self.runtime.lifecycle.approve(first.id)
        cold = self.store.get(record.id)
        self.assertEqual(cold.lifecycle_state, LifecycleState.COLD)
        with self.runtime.db.connection:
            self.runtime.db.connection.execute(
                "UPDATE memories SET lifecycle_updated_at = ?, updated_at = ? WHERE id = ?",
                (OLD, OLD, record.id),
            )

        second = self.runtime.lifecycle.dry_run(scope="alpha", now=NOW)
        self.assertEqual(second.actions[0].to_state, LifecycleState.ARCHIVED)
        self.runtime.lifecycle.approve(second.id)

        archived = self.store.get(record.id)
        self.assertEqual(archived.lifecycle_state, LifecycleState.ARCHIVED)
        self.assertIsNotNone(archived.archived_at)

    def test_pinned_and_strongly_preserved_memory_never_gets_gc_actions(self):
        pinned = self.add()
        self.runtime.lifecycle.pin(pinned.id, reason="operator hold")
        self.add(memory_type=MemoryType.DECISION)
        self.add(memory_type=MemoryType.FAILURE, importance=0.9)
        self.add(memory_type=MemoryType.PROCEDURAL, utility=0.9)
        self.add(
            memory_type=MemoryType.ENVIRONMENT,
            payload=json.dumps({"security_event": True}),
        )

        plan = self.runtime.lifecycle.dry_run(scope="alpha", now=NOW)

        self.assertEqual(plan.actions, ())
        pinned_now = self.store.get(pinned.id)
        self.assertTrue(pinned_now.pinned)
        self.assertEqual(pinned_now.pin_reason, "operator hold")

    def test_manual_archive_is_reversible_and_pins_require_force(self):
        record = self.add()
        self.runtime.lifecycle.pin(record.id)
        with self.assertRaises(ValueError):
            self.runtime.lifecycle.archive(record.id)
        archived = self.runtime.lifecycle.archive(record.id, force=True)
        self.assertEqual(archived.lifecycle_state, LifecycleState.ARCHIVED)
        self.assertEqual(
            self.store.search(MemoryQuery(scope="alpha")).records, ()
        )
        self.runtime.lifecycle.unpin(record.id)
        restored = self.runtime.lifecycle.restore(record.id)
        self.assertEqual(restored.lifecycle_state, LifecycleState.ACTIVE)
        self.assertEqual(
            self.store.search(MemoryQuery(scope="alpha")).records[0].id,
            record.id,
        )

    def test_changed_or_newly_pinned_memory_skips_stale_plan(self):
        record = self.add()
        plan = self.runtime.lifecycle.dry_run(scope="alpha", now=NOW)
        self.runtime.lifecycle.pin(record.id)

        applied = self.runtime.lifecycle.approve(plan.id)

        self.assertEqual(applied.status, "partially_applied")
        self.assertEqual(applied.actions[0].status, "skipped")
        self.assertEqual(
            self.store.get(record.id).lifecycle_state, LifecycleState.ACTIVE
        )

    def test_scope_activity_is_recorded_and_configuration_fails_closed(self):
        record = self.add()
        self.store.record_usage(record.id, successful=True)
        row = self.runtime.db.connection.execute(
            "SELECT access_count FROM memory_scope_activity WHERE scope = 'alpha'"
        ).fetchone()
        self.assertEqual(row[0], 1)
        with self.assertRaises(ValueError):
            LifecycleConfig(cold_after_days=0)
        with self.assertRaises(ValueError):
            LifecycleConfig(cold_after_days=200, archive_after_days=100)
        with self.assertRaises(ValueError):
            LifecycleConfig(
                recency_weight=0,
                usage_weight=0,
                importance_weight=0,
                confidence_weight=0,
                utility_weight=0,
                scope_activity_weight=0,
            )

    def test_gc_never_proposes_deleted_state(self):
        self.add()
        plan = self.runtime.lifecycle.dry_run(scope="alpha", now=NOW)
        self.assertNotIn(
            LifecycleState.DELETED,
            {action.to_state for action in plan.actions},
        )

    def test_cli_pin_archive_restore_and_gc_dry_run(self):
        record = self.add()
        outputs = []
        for args in (
            ["--db", str(self.path), "memory", "pin", record.id],
            ["--db", str(self.path), "memory", "archive", record.id, "--force"],
            ["--db", str(self.path), "memory", "unpin", record.id],
            ["--db", str(self.path), "memory", "restore", record.id],
            ["--db", str(self.path), "memory", "gc", "--dry-run", "--scope", "alpha"],
        ):
            buffer = StringIO()
            with redirect_stdout(buffer):
                self.assertEqual(main(args), 0)
            outputs.append(json.loads(buffer.getvalue()))
        self.assertTrue(outputs[0]["pinned"])
        self.assertEqual(outputs[1]["lifecycle_state"], "archived")
        self.assertEqual(outputs[3]["lifecycle_state"], "active")
        self.assertEqual(outputs[4]["status"], "planned")


if __name__ == "__main__":
    unittest.main()
