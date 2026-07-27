from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from acr_runtime.db import RuntimeDB
from acr_runtime.knowledge_conflict import KnowledgeConflictEngine
from acr_runtime.memory import MemoryCreate, MemoryStatus, MemoryType


class KnowledgeConflictTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.database = RuntimeDB(Path(self.temporary.name) / "acr.db")
        self.engine = KnowledgeConflictEngine(self.database.memories)

    def tearDown(self):
        self.database.close()
        self.temporary.cleanup()

    def add(self, content, *, scope="project:a", start=None, end=None,
            supersedes=None, source="file", evidence=("test:evidence",)):
        return self.database.memories.create(MemoryCreate(
            type=MemoryType.SEMANTIC, subject="database", content=content,
            scope=scope, status=MemoryStatus.CONFIRMED, valid_from=start,
            valid_until=end, supersedes=supersedes, source_type=source,
            evidence=evidence,
        ))

    def test_explicit_supersession_is_the_only_automatic_preference(self):
        old = self.add("Database is SQLite", start="2026-07-20T00:00:00Z")
        new = self.add(
            "Database is PostgreSQL", start="2026-07-21T00:00:00Z",
            supersedes=old.id,
        )
        result = self.engine.compare(old.id, new.id)
        self.assertEqual(result.classification, "one_supersedes_another")
        self.assertEqual(result.preferred_id, new.id)

    def test_different_scope_and_time_are_both_valid_classifications(self):
        project = self.add("Database is SQLite")
        other = self.add("Database is PostgreSQL", scope="project:b")
        scoped = self.engine.compare(project, other)
        self.assertEqual(scoped.classification, "both_valid_different_scopes")
        self.assertIsNone(scoped.preferred_id)

        early = self.add(
            "Database is MySQL", start="2026-01-01T00:00:00Z",
            end="2026-02-01T00:00:00Z",
        )
        later = self.add(
            "Database is SQLite", start="2026-02-01T00:00:00Z"
        )
        temporal = self.engine.compare(early, later)
        self.assertEqual(temporal.classification, "both_valid_different_times")

    def test_unresolved_compares_evidence_time_reliability_and_scope(self):
        left = self.add(
            "Database is SQLite", source="test",
            evidence=("test:a", "test:shared"),
        )
        right = self.add(
            "Database is PostgreSQL", source="model",
            evidence=("test:shared",),
        )
        result = self.engine.compare(left, right)
        self.assertEqual(result.classification, "unresolved_contradiction")
        self.assertIsNone(result.preferred_id)
        self.assertEqual(result.comparison["evidence"]["shared"], ["test:shared"])
        self.assertGreater(
            result.comparison["reliability"]["left"],
            result.comparison["reliability"]["right"],
        )

    def test_subject_analysis_never_silently_picks_unlinked_winner(self):
        self.add("Database is SQLite")
        self.add("Database is PostgreSQL")
        result = self.engine.analyze_subject("database", scope="project:a")
        self.assertTrue(result["requires_review"])
        self.assertEqual(
            result["conflicts"][0]["classification"],
            "unresolved_contradiction",
        )
        self.assertIsNone(result["conflicts"][0]["preferred_id"])


if __name__ == "__main__":
    unittest.main()
