from __future__ import annotations

import io
import json
import sqlite3
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path

from acr_runtime.cache import SafeCache
from acr_runtime.cli import main
from acr_runtime.db import RuntimeDB
from acr_runtime.memory import (
    MemoryCreate,
    MemoryStatus,
    MemoryType,
    SQLiteMemoryStore,
    parse_timestamp,
)
from acr_runtime.memory_scope import MemoryScopeKind
from acr_runtime.retrieval import (
    HybridMemoryRetriever,
    RetrievalRequest,
)


class SemanticScorer:
    def score(self, query, memories):
        return {memory.id: 1.0 for memory in memories}


class SafeCacheTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary.name) / "acr.db"
        self.database = RuntimeDB(self.path)
        self.now = [datetime(2026, 7, 27, 12, 0, tzinfo=timezone.utc)]
        self.cache = SafeCache(
            self.database.connection, clock=lambda: self.now[0]
        )
        self.store = self.database.memories
        self.memory = self.store.create(
            MemoryCreate(
                type=MemoryType.SEMANTIC,
                content="The project database uses SQLite FTS5.",
                scope="project:a",
                subject="database",
                status=MemoryStatus.CONFIRMED,
                confidence=0.95,
                importance=0.8,
                valid_from="2026-01-01T00:00:00Z",
            )
        )
        self.retriever = HybridMemoryRetriever(
            self.store, cache=self.cache
        )

    def tearDown(self) -> None:
        self.database.close()
        self.temporary.cleanup()

    @staticmethod
    def request(
        *,
        scope: str = "project:a",
        token_budget: int = 200,
        max_age: int | None = 30,
    ) -> RetrievalRequest:
        return RetrievalRequest(
            task="Inspect the project database",
            query="SQLite FTS5 database",
            scope=scope,
            token_budget=token_budget,
            target_memories=5,
            cache_max_age_seconds=max_age,
        )

    def test_default_bypasses_and_opt_in_hit_stores_no_source_content(self):
        bypass = self.retriever.retrieve(self.request(max_age=None))
        self.assertEqual(bypass.cache_status, "bypass")
        self.assertEqual(self.cache.status()["entries"], 0)

        miss = self.retriever.retrieve(self.request())
        hit = self.retriever.retrieve(self.request())
        self.assertEqual(miss.cache_status, "miss")
        self.assertEqual(hit.cache_status, "hit")
        self.assertEqual(
            [item.memory.id for item in hit.selected], [self.memory.id]
        )

        row = self.database.connection.execute(
            "SELECT key_hash, payload_json FROM cache_entries"
        ).fetchone()
        self.assertEqual(len(row["key_hash"]), 64)
        retained = row["payload_json"]
        for forbidden in (
            "Inspect the project database",
            "SQLite FTS5 database",
            self.memory.content,
        ):
            self.assertNotIn(forbidden, retained)
        self.assertIn(self.memory.id, retained)
        events = {
            row["outcome"]: row["events"]
            for row in self.cache.status()["events"]
        }
        self.assertEqual(events["hit"], 1)
        self.assertEqual(events["miss"], 1)

    def test_scope_budget_and_freshness_are_exact_key_dimensions(self):
        first = self.retriever.retrieve(self.request())
        budget_change = self.retriever.retrieve(
            self.request(token_budget=201)
        )
        sibling = self.retriever.retrieve(
            self.request(scope="project:b")
        )
        self.assertEqual(first.cache_status, "miss")
        self.assertEqual(budget_change.cache_status, "miss")
        self.assertEqual(sibling.cache_status, "miss")
        self.assertEqual(sibling.selected, ())
        self.assertEqual(self.cache.status()["entries"], 3)

    def test_memory_change_invalidates_generation_and_cached_ids(self):
        self.retriever.retrieve(self.request())
        generation = self.cache.generation()
        self.assertEqual(self.cache.status()["entries"], 1)

        self.store.create(
            MemoryCreate(
                type=MemoryType.SEMANTIC,
                content="SQLite remains local-first.",
                scope="project:a",
                status=MemoryStatus.CONFIRMED,
            )
        )

        self.assertGreater(self.cache.generation(), generation)
        self.assertEqual(self.cache.status()["entries"], 0)
        self.assertEqual(
            self.retriever.retrieve(self.request()).cache_status, "miss"
        )

    def test_scope_and_privacy_policy_changes_invalidate_entries(self):
        self.retriever.retrieve(self.request())
        generation = self.cache.generation()
        self.database.connection.execute(
            """
            UPDATE privacy_policies SET reason = reason
            WHERE classification = 'internal'
            """
        )
        self.database.connection.commit()
        self.assertGreater(self.cache.generation(), generation)
        self.assertEqual(self.cache.status()["entries"], 0)

        self.retriever.retrieve(self.request())
        generation = self.cache.generation()
        self.database.scopes.register(
            "project:new",
            MemoryScopeKind.PROJECT,
            parent_id="global",
        )
        self.assertGreater(self.cache.generation(), generation)
        self.assertEqual(self.cache.status()["entries"], 0)

    def test_ttl_boundary_is_stale_and_corruption_is_a_safe_miss(self):
        self.retriever.retrieve(self.request(max_age=10))
        self.assertEqual(
            self.retriever.retrieve(self.request(max_age=10)).cache_status,
            "hit",
        )
        self.now[0] += timedelta(seconds=10)
        self.assertEqual(
            self.retriever.retrieve(self.request(max_age=10)).cache_status,
            "miss",
        )
        with self.assertRaises(sqlite3.IntegrityError):
            self.database.connection.execute(
                "UPDATE cache_entries SET payload_bytes = 2"
            )
        self.database.connection.rollback()
        self.database.connection.execute(
            """
            UPDATE cache_entries
            SET payload_json = '{"ranked":[]}',
                payload_bytes = 13
            """
        )
        self.database.connection.commit()
        self.assertEqual(
            self.retriever.retrieve(self.request(max_age=10)).cache_status,
            "miss",
        )

    def test_retention_deadline_caps_expiry_and_reauthorizes_hit(self):
        self.now[0] = datetime.now(timezone.utc)
        retention = self.now[0] + timedelta(seconds=5)
        self.database.connection.execute(
            "UPDATE memories SET retention_until = ? WHERE id = ?",
            (retention.isoformat(), self.memory.id),
        )
        self.database.connection.commit()
        self.retriever.retrieve(self.request(max_age=30))
        row = self.database.connection.execute(
            "SELECT expires_at FROM cache_entries"
        ).fetchone()
        self.assertEqual(parse_timestamp(row["expires_at"]), retention)

        self.now[0] = retention
        self.assertEqual(
            self.retriever.retrieve(self.request(max_age=30)).cache_status,
            "bypass",
        )

    def test_historical_query_never_outlives_real_retention_deadline(self):
        self.now[0] = datetime.now(timezone.utc)
        retention = self.now[0] + timedelta(seconds=5)
        self.database.connection.execute(
            "UPDATE memories SET retention_until = ? WHERE id = ?",
            (retention.isoformat(), self.memory.id),
        )
        self.database.connection.commit()
        request = RetrievalRequest(
            task="Inspect historical database",
            query="SQLite FTS5 database",
            scope="project:a",
            token_budget=200,
            valid_at="2026-02-01T00:00:00Z",
            cache_max_age_seconds=30,
        )
        self.assertEqual(
            self.retriever.retrieve(request).cache_status, "miss"
        )
        expires_at = self.database.connection.execute(
            "SELECT expires_at FROM cache_entries"
        ).fetchone()["expires_at"]
        self.assertEqual(parse_timestamp(expires_at), retention)

        self.now[0] = retention
        self.assertEqual(
            self.retriever.retrieve(request).cache_status, "bypass"
        )

    def test_future_validity_and_retention_cap_moving_time_expiry(self):
        self.now[0] = datetime.now(timezone.utc)
        future = self.now[0] + timedelta(seconds=5)
        self.store.create(
            MemoryCreate(
                type=MemoryType.SEMANTIC,
                content="Future SQLite architecture fact.",
                scope="project:a",
                status=MemoryStatus.CONFIRMED,
                valid_from=future.isoformat(),
            )
        )
        self.retriever.retrieve(self.request(max_age=30))
        row = self.database.connection.execute(
            "SELECT expires_at FROM cache_entries"
        ).fetchone()
        self.assertEqual(parse_timestamp(row["expires_at"]), future)

        self.now[0] = future
        self.assertEqual(
            self.retriever.retrieve(self.request(max_age=30)).cache_status,
            "miss",
        )

    def test_concurrent_invalidation_cannot_be_confirmed_as_hit(self):
        self.retriever.retrieve(self.request())
        key = self.database.connection.execute(
            "SELECT key_hash FROM cache_entries"
        ).fetchone()["key_hash"]
        entry = self.cache.probe(key)
        self.assertIsNotNone(entry)

        second = sqlite3.connect(self.path)
        second.row_factory = sqlite3.Row
        try:
            SQLiteMemoryStore(second).create(
                MemoryCreate(
                    type=MemoryType.SEMANTIC,
                    content="Concurrent SQLite memory.",
                    scope="project:a",
                    status=MemoryStatus.CONFIRMED,
                )
            )
        finally:
            second.close()

        self.assertFalse(self.cache.confirm_hit(entry))
        hit_events = self.database.connection.execute(
            "SELECT COUNT(*) FROM cache_events WHERE outcome = 'hit'"
        ).fetchone()[0]
        self.assertEqual(hit_events, 0)

    def test_cache_writes_never_commit_a_caller_transaction(self):
        self.database.connection.execute(
            "CREATE TABLE cache_tx_sentinel(value TEXT)"
        )
        self.database.connection.commit()
        self.retriever.retrieve(self.request())

        self.database.connection.execute("BEGIN")
        self.database.connection.execute(
            "INSERT INTO cache_tx_sentinel VALUES ('rollback-me')"
        )
        self.assertEqual(
            self.retriever.retrieve(self.request()).cache_status, "hit"
        )
        self.database.connection.rollback()
        count = self.database.connection.execute(
            "SELECT COUNT(*) FROM cache_tx_sentinel"
        ).fetchone()[0]
        self.assertEqual(count, 0)

    def test_semantic_retrieval_is_bypassed_without_adapter_identity(self):
        retriever = HybridMemoryRetriever(
            self.store,
            semantic=SemanticScorer(),
            cache=self.cache,
        )
        result = retriever.retrieve(self.request())
        self.assertEqual(result.cache_status, "bypass")
        self.assertTrue(result.semantic_available)
        self.assertEqual(self.cache.status()["entries"], 0)

    def test_cache_age_contract_rejects_boolean_and_unbounded_values(self):
        for value in (True, 0, 86_401):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    self.request(max_age=value)

    def test_cli_opt_in_reports_hit_and_cache_savings_status(self):
        self.database.close()
        outputs = []
        for _ in range(2):
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                code = main(
                    [
                        "--db",
                        str(self.path),
                        "memory",
                        "retrieve",
                        "SQLite FTS5 database",
                        "--task",
                        "Inspect the project database",
                        "--scope",
                        "project:a",
                        "--cache-max-age",
                        "30",
                    ]
                )
            self.assertEqual(code, 0)
            outputs.append(json.loads(stdout.getvalue()))
        self.assertEqual(
            [item["cache_status"] for item in outputs], ["miss", "hit"]
        )

        stdout = io.StringIO()
        with redirect_stdout(stdout):
            self.assertEqual(
                main(["--db", str(self.path), "cache", "status"]), 0
            )
        status = json.loads(stdout.getvalue())
        self.assertEqual(status["entries"], 1)
        self.assertEqual(status["hits"], 1)
        self.assertFalse(status["stored_request_content"])
        self.database = RuntimeDB(self.path)


if __name__ == "__main__":
    unittest.main()
