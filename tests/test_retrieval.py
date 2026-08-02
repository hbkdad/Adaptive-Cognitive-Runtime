from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from acr_runtime.db import RuntimeDB
from acr_runtime.memory import MemoryCreate, MemoryStatus, MemoryType
from acr_runtime.retrieval import (
    HybridMemoryRetriever,
    RetrievalConfig,
    RetrievalRequest,
    RetrievalWeights,
)


def iso(days: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()


class FixedSemanticScorer:
    def __init__(self, scores: dict[str, float]) -> None:
        self.scores = scores

    def score(self, query, memories):
        return {memory.id: self.scores.get(memory.id, 0.0) for memory in memories}


class FailingSemanticScorer:
    def score(self, query, memories):
        raise TimeoutError("unavailable")


class RetrievalTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database = RuntimeDB(Path(self.temp_dir.name) / "acr.db")
        self.store = self.database.memories

    def tearDown(self) -> None:
        self.database.close()
        self.temp_dir.cleanup()

    def add(
        self,
        content: str,
        *,
        memory_type: MemoryType = MemoryType.SEMANTIC,
        scope: str = "alpha",
        subject: str | None = None,
        confidence: float = 0.9,
        importance: float = 0.7,
        source_type: str = "file",
        valid_from: str | None = None,
        valid_until: str | None = None,
    ):
        return self.store.create(
            MemoryCreate(
                type=memory_type,
                content=content,
                scope=scope,
                subject=subject,
                confidence=confidence,
                importance=importance,
                source_type=source_type,
                status=MemoryStatus.CONFIRMED,
                valid_from=valid_from,
                valid_until=valid_until,
            )
        )

    def request(self, query: str = "database") -> RetrievalRequest:
        return RetrievalRequest(
            task="Diagnose the project database",
            query=query,
            scope="alpha",
            token_budget=500,
            target_memories=10,
        )

    def test_outdated_facts_and_other_scopes_do_not_leak(self):
        current = self.add(
            "The database is SQLite",
            subject="database",
            valid_from=iso(-1),
        )
        self.add(
            "The database is Firebase",
            subject="database",
            valid_from=iso(-20),
            valid_until=iso(-10),
        )
        self.add("The database is MongoDB", scope="beta", subject="database")

        result = HybridMemoryRetriever(self.store).retrieve(self.request())

        self.assertEqual([item.memory.id for item in result.selected], [current.id])

    def test_duplicates_are_rejected_and_candidate_pool_is_larger(self):
        first = self.add("Use SQLite WAL mode", subject="database mode")
        second = self.add("Use SQLite WAL mode", subject="database mode")
        self.add("SQLite backups use the backup API", subject="backups")

        result = HybridMemoryRetriever(self.store).retrieve(
            RetrievalRequest(
                task="Inspect SQLite",
                query="SQLite",
                scope="alpha",
                token_budget=500,
                target_memories=2,
            )
        )

        duplicate_ids = {
            item.memory.id
            for item in result.rejected
            if item.rejection_reason == "duplicate"
        }
        self.assertEqual(len({first.id, second.id} & duplicate_ids), 1)
        self.assertGreater(result.candidate_count, len(result.selected))

    def test_contradictory_subjects_are_flagged_not_silently_hidden(self):
        sqlite = self.add("The database is SQLite", subject="database")
        postgres = self.add("The database is PostgreSQL", subject="database")

        result = HybridMemoryRetriever(self.store).retrieve(self.request())
        by_id = {item.memory.id: item for item in result.selected}

        self.assertIn(postgres.id, by_id[sqlite.id].conflict_ids)
        self.assertIn(sqlite.id, by_id[postgres.id].conflict_ids)
        self.assertIn("unresolved_conflicts=1", by_id[sqlite.id].explanation)

    def test_low_confidence_is_filtered_before_ranking(self):
        trusted = self.add("SQLite is the database", confidence=0.95)
        self.add("SQLite might be replaced", confidence=0.2)
        request = RetrievalRequest(
            task="Inspect SQLite",
            query="SQLite",
            scope="alpha",
            token_budget=500,
            minimum_confidence=0.8,
        )

        result = HybridMemoryRetriever(self.store).retrieve(request)

        self.assertEqual([item.memory.id for item in result.selected], [trusted.id])
        self.assertEqual(result.candidate_count, 1)

    def test_broad_pool_does_not_inject_unrelated_memory(self):
        relevant = self.add("SQLite is the database")
        unrelated = self.add(
            "The office paint colour is orange",
            confidence=0.99,
            importance=0.99,
        )

        result = HybridMemoryRetriever(self.store).retrieve(
            self.request("SQLite")
        )

        self.assertEqual([item.memory.id for item in result.selected], [relevant.id])
        rejected = {item.memory.id: item.rejection_reason for item in result.rejected}
        self.assertEqual(rejected[unrelated.id], "no_relevance")

    def test_high_value_historical_failure_is_preferred(self):
        failure = self.add(
            "SQLite migration failed when foreign keys stayed enabled",
            memory_type=MemoryType.FAILURE,
            confidence=0.99,
            importance=0.99,
            source_type="test",
        )
        self.add(
            "SQLite migration notes",
            confidence=0.5,
            importance=0.2,
            source_type="model",
        )
        for _ in range(4):
            self.store.record_usage(failure.id, successful=True)

        result = HybridMemoryRetriever(self.store).retrieve(
            self.request("SQLite migration")
        )

        self.assertEqual(result.selected[0].memory.id, failure.id)
        self.assertGreater(
            result.selected[0].breakdown.historical_utility, 0.9
        )

    def test_recency_uses_type_profile_and_effective_validity_time(self):
        effective = iso(-90)
        decision = self.add(
            "Database decision",
            memory_type=MemoryType.DECISION,
            valid_from=effective,
        )
        semantic = self.add(
            "Database semantic fact",
            memory_type=MemoryType.SEMANTIC,
            valid_from=effective,
        )
        temporary = self.add(
            "Database temporary state",
            memory_type=MemoryType.TEMPORARY,
            valid_from=effective,
        )

        result = HybridMemoryRetriever(self.store).retrieve(
            self.request("Database")
        )
        by_id = {item.memory.id: item for item in result.ranked}

        self.assertEqual(by_id[decision.id].breakdown.recency, 1.0)
        self.assertAlmostEqual(
            by_id[semantic.id].breakdown.recency,
            0.5,
            delta=0.01,
        )
        self.assertLess(by_id[temporary.id].breakdown.recency, 0.001)

    def test_semantic_provider_can_recover_non_keyword_candidate(self):
        keyword = self.add("Database operations use SQLite")
        semantic = self.add("Persistent state lives in a local relational store")
        weights = RetrievalWeights(
            keyword=0,
            semantic=1,
            scope=0,
            recency=0,
            temporal=0,
            confidence=0,
            historical_utility=0,
            importance=0,
            task_similarity=0,
            source_reliability=0,
        )
        retriever = HybridMemoryRetriever(
            self.store,
            semantic=FixedSemanticScorer({keyword.id: 0.1, semantic.id: 0.95}),
            config=RetrievalConfig(weights=weights),
        )

        result = retriever.retrieve(self.request("database"))

        self.assertTrue(result.semantic_available)
        self.assertEqual(result.selected[0].memory.id, semantic.id)
        self.assertAlmostEqual(result.selected[0].score, 0.95)

    def test_semantic_failure_falls_back_with_visible_status(self):
        memory = self.add("Database operations use SQLite")
        retriever = HybridMemoryRetriever(
            self.store, semantic=FailingSemanticScorer()
        )

        result = retriever.retrieve(self.request("SQLite"))

        self.assertFalse(result.semantic_available)
        self.assertEqual(result.semantic_status, "failed:TimeoutError")
        self.assertEqual(result.selected[0].memory.id, memory.id)
        self.assertIn("semantic=unavailable", result.selected[0].explanation)

    def test_token_budget_rejections_are_explained(self):
        self.add("SQLite " + "x" * 80)
        self.add("SQLite " + "y" * 80)

        result = HybridMemoryRetriever(self.store).retrieve(
            RetrievalRequest(
                task="SQLite",
                query="SQLite",
                scope="alpha",
                token_budget=25,
                target_memories=10,
            )
        )

        self.assertEqual(len(result.selected), 1)
        self.assertTrue(
            any(item.rejection_reason == "token_budget" for item in result.rejected)
        )


if __name__ == "__main__":
    unittest.main()
