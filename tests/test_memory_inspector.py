from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from acr_runtime.db import RuntimeDB
from acr_runtime.memory import (
    LifecycleState,
    MemoryCreate,
    MemoryStatus,
    MemoryType,
    Sensitivity,
    SourceClass,
)
from acr_runtime.memory_inspector import MemoryInspector


class MemoryInspectorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database = RuntimeDB(Path(self.temp_dir.name) / "acr.db")
        self.inspector = MemoryInspector(self.database)

    def tearDown(self) -> None:
        self.database.close()
        self.temp_dir.cleanup()

    def create(
        self,
        content: str,
        *,
        scope: str = "alpha",
        subject: str | None = "database",
        sensitivity: Sensitivity = Sensitivity.INTERNAL,
        status: MemoryStatus = MemoryStatus.CONFIRMED,
    ):
        return self.database.memories.create(
            MemoryCreate(
                type=MemoryType.SEMANTIC,
                content=content,
                scope=scope,
                subject=subject,
                source_type="file",
                source_id="docs/design.md",
                source_class=SourceClass.REPOSITORY,
                evidence=("docs/design.md:12",),
                sensitivity=sensitivity,
                status=status,
            )
        )

    def test_search_is_exact_scope_bounded_paginated_and_includes_archived(self):
        alpha = [self.create(f"SQLite alpha {index}") for index in range(3)]
        self.create("SQLite beta", scope="beta")
        self.create("SQLite global", scope="global")
        archived = self.create("SQLite archived")
        self.database.memories.set_status(
            archived.id, MemoryStatus.ARCHIVED
        )
        self.database.memories.set_lifecycle(
            archived.id, LifecycleState.ARCHIVED
        )

        first = self.inspector.search(scope="alpha", text="SQLite", limit=2)
        second = self.inspector.search(
            scope="alpha",
            text="SQLite",
            limit=2,
            cursor=first["next_cursor"],
        )
        ids = [item["id"] for item in first["items"] + second["items"]]

        self.assertEqual(len(ids), 4)
        self.assertEqual(len(set(ids)), 4)
        self.assertTrue({item.id for item in alpha}.issubset(ids))
        self.assertIn(archived.id, ids)
        self.assertTrue(all(item["scope"] == "alpha" for item in first["items"]))
        self.assertIsNone(second["next_cursor"])
        with self.assertRaises(ValueError):
            self.inspector.search(scope="alpha", limit=101)
        with self.assertRaises(ValueError):
            self.inspector.search(scope="alpha", cursor="not-a-cursor")

    def test_restricted_and_deleted_memories_are_indistinguishable_from_absent(self):
        visible = self.create("Visible")
        restricted = [
            self.create("Personal", sensitivity=Sensitivity.PERSONAL),
            self.create("Confidential", sensitivity=Sensitivity.CONFIDENTIAL),
            self.create("Secret", sensitivity=Sensitivity.SECRET),
        ]
        deleted = self.create("Deleted")
        with self.database.connection:
            self.database.connection.execute(
                """
                UPDATE memories
                SET status='deleted', lifecycle_state='deleted', deleted_at=?
                WHERE id=?
                """,
                (deleted.updated_at, deleted.id),
            )

        result = self.inspector.search(scope="alpha")
        self.assertEqual([item["id"] for item in result["items"]], [visible.id])
        for record in (*restricted, deleted):
            self.assertIsNone(
                self.inspector.inspect(record.id, scope="alpha")
            )
        self.assertIsNone(self.inspector.inspect(visible.id, scope="beta"))
        with self.assertRaises(ValueError):
            self.inspector.search(
                scope="alpha", statuses=(MemoryStatus.DELETED,)
            )
        with self.assertRaises(ValueError):
            self.inspector.search(
                scope="alpha", lifecycle_states=(LifecycleState.DELETED,)
            )

    def test_projection_redacts_secrets_paths_and_hidden_supersession_links(self):
        old = self.create("Old belief")
        hidden = self.create(
            "Hidden replacement", sensitivity=Sensitivity.CONFIDENTIAL
        )
        token = "sk-proj-" + "A" * 30
        with self.database.connection:
            self.database.connection.execute(
                """
                UPDATE memories
                SET content=?, subject=?, source_id=?, evidence_json=?,
                    pin_reason=?, superseded_by=?
                WHERE id=?
                """,
                (
                    f"Credential {token}",
                    f"database {token}",
                    rf"C:\private\source.txt:{token}",
                    json.dumps(
                        [rf"C:\private\evidence.txt:14 {token}"]
                    ),
                    f"operator token={token}",
                    hidden.id,
                    old.id,
                ),
            )

        item = self.inspector.inspect(old.id, scope="alpha")
        self.assertIsNotNone(item)
        rendered = json.dumps(item)
        self.assertNotIn(token, rendered)
        self.assertNotIn(r"C:\private", rendered)
        self.assertIn("[REDACTED]", rendered)
        self.assertIn("[REDACTED_PATH]", rendered)
        self.assertIsNone(item["supersession"]["superseded_by"])
        self.assertEqual(
            item["provenance"]["source_class"],
            SourceClass.REPOSITORY.value,
        )

    def test_inspect_timeline_and_related_expose_only_visible_subject_records(self):
        old = self.create("SQLite version one")
        new = self.database.memories.create(
            MemoryCreate(
                type=MemoryType.SEMANTIC,
                content="SQLite version two",
                scope="alpha",
                subject="database",
                status=MemoryStatus.CONFIRMED,
                supersedes=old.id,
            )
        )
        self.create(
            "Private database note", sensitivity=Sensitivity.PERSONAL
        )
        self.create("Other subject", subject="deployment")

        detail = self.inspector.inspect(new.id, scope="alpha")
        timeline = self.inspector.timeline("database", scope="alpha")
        related = self.inspector.related(
            "database", scope="alpha", exclude_id=new.id
        )

        self.assertEqual(detail["supersession"]["supersedes"], old.id)
        self.assertEqual(
            [item["id"] for item in timeline["items"]], [old.id, new.id]
        )
        self.assertEqual([item["id"] for item in related["items"]], [old.id])
        self.assertEqual(related["relation"], "exact_subject")
        self.assertEqual(detail["usage"]["history_status"], "aggregate_only")


if __name__ == "__main__":
    unittest.main()
