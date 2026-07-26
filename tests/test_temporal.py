from __future__ import annotations

import tempfile
import unittest
import json
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from io import StringIO
from pathlib import Path

from acr_runtime import AdaptiveRuntime
from acr_runtime.cli import main
from acr_runtime.memory import MemoryCreate, MemoryStatus, MemoryType
from acr_runtime.retrieval import HybridMemoryRetriever, RetrievalRequest
from acr_runtime.temporal import TemporalMemory


def relative(days: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()


class TemporalMemoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.runtime = AdaptiveRuntime(Path(self.temp_dir.name) / "acr.db")
        self.store = self.runtime.db.memories
        self.temporal = TemporalMemory(self.store)

    def tearDown(self) -> None:
        self.runtime.close()
        self.temp_dir.cleanup()

    def add(
        self,
        content: str,
        *,
        subject: str = "database",
        scope: str = "alpha",
        valid_from: str,
        valid_until: str | None = None,
        supersedes: str | None = None,
    ):
        return self.store.create(
            MemoryCreate(
                type=MemoryType.SEMANTIC,
                subject=subject,
                content=content,
                scope=scope,
                confidence=0.95,
                importance=0.9,
                status=MemoryStatus.CONFIRMED,
                valid_from=valid_from,
                valid_until=valid_until,
                supersedes=supersedes,
                evidence=("architecture.md",),
            )
        )

    def test_current_at_and_history_preserve_explicit_change(self):
        firebase = self.add(
            "The database is Firebase",
            valid_from="2026-01-01T00:00:00+00:00",
        )
        supabase = self.add(
            "The database is Supabase",
            valid_from="2026-06-01T00:00:00+00:00",
            supersedes=firebase.id,
        )

        current = self.temporal.current("database", scope="alpha")
        march = self.temporal.at(
            "database", "2026-03-01T00:00:00Z", scope="alpha"
        )
        history = self.temporal.history("DATABASE", scope="alpha")

        self.assertEqual(current.preferred.id, supabase.id)
        self.assertEqual(march.preferred.id, firebase.id)
        self.assertFalse(march.unresolved_conflict)
        self.assertEqual(
            [record.id for record in history.records],
            [firebase.id, supabase.id],
        )
        old = self.store.get(firebase.id)
        self.assertEqual(old.valid_until, "2026-06-01T00:00:00+00:00")
        self.assertEqual(old.superseded_by, supabase.id)
        self.assertEqual(supabase.supersedes, firebase.id)

    def test_valid_until_is_exclusive_at_change_boundary(self):
        old = self.add(
            "The database is Firebase",
            valid_from="2026-01-01T00:00:00Z",
        )
        new = self.add(
            "The database is Supabase",
            valid_from="2026-06-01T00:00:00Z",
            supersedes=old.id,
        )

        boundary = self.temporal.at(
            "database", "2026-06-01T00:00:00Z", scope="alpha"
        )

        self.assertEqual(boundary.preferred.id, new.id)
        self.assertNotIn(old.id, {item.id for item in boundary.alternatives})

    def test_future_change_keeps_old_fact_current_until_effective(self):
        old = self.add("Current engine is v1", valid_from=relative(-10))
        future_at = relative(10)
        new = self.add(
            "Current engine is v2",
            valid_from=future_at,
            supersedes=old.id,
        )

        current = self.temporal.current("database", scope="alpha")
        future = self.temporal.at("database", relative(11), scope="alpha")

        self.assertEqual(current.preferred.id, old.id)
        self.assertEqual(future.preferred.id, new.id)
        self.assertEqual(self.store.get(old.id).status, MemoryStatus.CONFIRMED)

    def test_unlinked_contradictions_remain_visible(self):
        first = self.add("The database is SQLite", valid_from=relative(-10))
        second = self.add("The database is PostgreSQL", valid_from=relative(-5))

        resolution = self.temporal.current("database", scope="alpha")

        self.assertEqual(resolution.preferred.id, second.id)
        self.assertEqual([item.id for item in resolution.alternatives], [first.id])
        self.assertTrue(resolution.unresolved_conflict)
        self.assertIn("unresolved_conflicts=1", resolution.reason)
        self.assertIsNotNone(self.store.get(first.id))

    def test_scope_specific_fact_precedes_newer_global_default(self):
        local = self.add(
            "The database is SQLite",
            valid_from=relative(-10),
            scope="alpha",
        )
        self.add(
            "The default database is PostgreSQL",
            valid_from=relative(-1),
            scope="global",
        )

        resolution = self.temporal.current("database", scope="alpha")

        self.assertEqual(resolution.preferred.id, local.id)
        self.assertFalse(resolution.unresolved_conflict)

    def test_hybrid_retrieval_can_query_historical_truth(self):
        old = self.add(
            "The database is Firebase",
            valid_from="2026-01-01T00:00:00Z",
        )
        self.add(
            "The database is Supabase",
            valid_from="2026-06-01T00:00:00Z",
            supersedes=old.id,
        )
        result = HybridMemoryRetriever(self.store).retrieve(
            RetrievalRequest(
                task="What database was used?",
                query="Firebase",
                scope="alpha",
                token_budget=100,
                valid_at="2026-03-01T00:00:00Z",
            )
        )

        self.assertEqual([item.memory.id for item in result.selected], [old.id])

    def test_invalid_or_reversed_intervals_fail_closed(self):
        with self.assertRaises(ValueError):
            self.add("invalid", valid_from="not-a-time")
        with self.assertRaises(ValueError):
            self.add(
                "reversed",
                valid_from="2026-06-01T00:00:00Z",
                valid_until="2026-01-01T00:00:00Z",
            )
        with self.assertRaises(ValueError):
            self.temporal.at("database", "not-a-time", scope="alpha")

    def test_offset_timestamps_are_compared_as_instants(self):
        record = self.add(
            "The database is SQLite",
            valid_from="2026-01-01T00:00:00-05:00",
        )

        before = self.temporal.at(
            "database", "2026-01-01T04:59:59Z", scope="alpha"
        )
        at_start = self.temporal.at(
            "database", "2026-01-01T05:00:00Z", scope="alpha"
        )

        self.assertIsNone(before.preferred)
        self.assertEqual(at_start.preferred.id, record.id)
        self.assertEqual(record.valid_from, "2026-01-01T05:00:00+00:00")

    def test_explicit_supersede_uses_replacement_effective_time(self):
        old = self.add("Engine v1", valid_from=relative(-10))
        replacement = self.add("Engine v2", valid_from=relative(10))

        self.store.supersede(old.id, replacement.id)

        self.assertEqual(
            self.temporal.current("database", scope="alpha").preferred.id,
            old.id,
        )
        self.assertEqual(
            self.temporal.at("database", relative(11), scope="alpha").preferred.id,
            replacement.id,
        )
        self.assertEqual(
            self.store.get(old.id).valid_until,
            self.store.get(replacement.id).valid_from,
        )

    def test_temporal_cli_returns_inspectable_resolution(self):
        record = self.add("The database is SQLite", valid_from=relative(-1))
        output = StringIO()

        with redirect_stdout(output):
            exit_code = main(
                [
                    "--db",
                    str(self.runtime.settings.database),
                    "memory",
                    "current",
                    "database",
                    "--scope",
                    "alpha",
                ]
            )

        payload = json.loads(output.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["preferred"]["id"], record.id)
        self.assertFalse(payload["unresolved_conflict"])

    def test_supersession_rejects_invalid_or_branching_timeline(self):
        old = self.add("Engine v1", valid_from="2026-06-01T00:00:00Z")
        with self.assertRaises(ValueError):
            self.add(
                "Engine v0",
                valid_from="2026-01-01T00:00:00Z",
                supersedes=old.id,
            )
        first = self.add(
            "Engine v2",
            valid_from="2026-07-01T00:00:00Z",
            supersedes=old.id,
        )
        with self.assertRaises(ValueError):
            self.add(
                "Engine v3",
                valid_from="2026-08-01T00:00:00Z",
                supersedes=old.id,
            )
        self.assertEqual(self.store.get(old.id).superseded_by, first.id)


if __name__ == "__main__":
    unittest.main()
