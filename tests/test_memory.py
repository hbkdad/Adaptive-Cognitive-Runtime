from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from acr_runtime.db import RuntimeDB
from acr_runtime.memory import (
    MemoryCreate,
    MemoryPatch,
    MemoryQuery,
    MemoryStatus,
    MemoryType,
)


class MemoryStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database = RuntimeDB(Path(self.temp_dir.name) / "acr.db")
        self.store = self.database.memories

    def tearDown(self) -> None:
        self.database.close()
        self.temp_dir.cleanup()

    def test_all_types_and_structured_provenance_round_trip(self):
        for memory_type in MemoryType:
            record = self.store.create(
                MemoryCreate(
                    type=memory_type,
                    subject=f"{memory_type.value} subject",
                    content=f"{memory_type.value} content",
                    scope="alpha",
                    structured_payload_json='{"version": 1}',
                    evidence=("file.py:10",),
                    source_type="file",
                    source_id="file.py",
                    status=MemoryStatus.CONFIRMED,
                )
            )
            loaded = self.store.get(record.id)
            self.assertEqual(loaded.type, memory_type)
            self.assertEqual(loaded.evidence, ("file.py:10",))
            self.assertEqual(loaded.source_type, "file")
            self.assertEqual(loaded.structured_payload_json, '{"version": 1}')

    def test_default_search_only_returns_confirmed_and_respects_scope(self):
        confirmed = self.store.create(
            MemoryCreate(
                type=MemoryType.DECISION,
                content="Use SQLite for the alpha project",
                scope="alpha",
                status=MemoryStatus.CONFIRMED,
            )
        )
        self.store.create(
            MemoryCreate(
                type=MemoryType.DECISION,
                content="Use SQLite for the beta project",
                scope="beta",
                status=MemoryStatus.CONFIRMED,
            )
        )
        self.store.create(
            MemoryCreate(
                type=MemoryType.DECISION,
                content="Unverified SQLite proposal",
                scope="alpha",
                status=MemoryStatus.CANDIDATE,
            )
        )
        page = self.store.search(MemoryQuery(scope="alpha", text="SQLite"))
        self.assertEqual([record.id for record in page.records], [confirmed.id])

    def test_update_refreshes_fts_and_rejects_stale_writer(self):
        record = self.store.create(
            MemoryCreate(
                type=MemoryType.SEMANTIC,
                content="The service uses Firebase",
                status=MemoryStatus.CONFIRMED,
            )
        )
        updated = self.store.update(
            record.id,
            MemoryPatch(
                content="The service uses Supabase",
                expected_updated_at=record.updated_at,
            ),
        )
        self.assertEqual(
            [item.id for item in self.store.search(
                MemoryQuery(scope="global", text="Supabase")
            ).records],
            [record.id],
        )
        self.assertEqual(
            self.store.search(
                MemoryQuery(scope="global", text="Firebase")
            ).records,
            (),
        )
        with self.assertRaises(RuntimeError):
            self.store.update(
                record.id,
                MemoryPatch(content="stale", expected_updated_at=record.updated_at),
            )
        self.assertNotEqual(updated.updated_at, record.updated_at)

    def test_supersession_is_bidirectional_and_hides_old_memory(self):
        old = self.store.create(
            MemoryCreate(
                type=MemoryType.PREFERENCE,
                content="Prefer blue",
                status=MemoryStatus.CONFIRMED,
            )
        )
        new = self.store.create(
            MemoryCreate(
                type=MemoryType.PREFERENCE,
                content="Prefer orange",
                status=MemoryStatus.CONFIRMED,
                supersedes=old.id,
            )
        )
        self.assertEqual(self.store.get(old.id).superseded_by, new.id)
        self.assertEqual(self.store.get(old.id).status, MemoryStatus.SUPERSEDED)
        self.assertEqual(self.store.get(new.id).supersedes, old.id)

    def test_cursor_pagination_is_stable(self):
        for index in range(3):
            self.store.create(
                MemoryCreate(
                    type=MemoryType.TEMPORARY,
                    content=f"item {index}",
                    status=MemoryStatus.CONFIRMED,
                )
            )
        first = self.store.search(MemoryQuery(scope="global", limit=2))
        second = self.store.search(
            MemoryQuery(scope="global", limit=2, cursor=first.next_cursor)
        )
        self.assertEqual(len(first.records), 2)
        self.assertEqual(len(second.records), 1)
        self.assertTrue(
            {item.id for item in first.records}.isdisjoint(
                {item.id for item in second.records}
            )
        )

    def test_invalid_domain_values_fail_closed(self):
        with self.assertRaises(ValueError):
            MemoryCreate(type=MemoryType.FAILURE, content="", confidence=2)
        with self.assertRaises(ValueError):
            MemoryPatch(structured_payload_json="not-json")


if __name__ == "__main__":
    unittest.main()
