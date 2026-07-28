from __future__ import annotations

import io
import json
import sqlite3
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from acr_runtime.cli import main
from acr_runtime.db import RuntimeDB
from acr_runtime.deduplication import (
    DeduplicationArtifact,
    DeduplicationEngine,
    canonical_json,
    deduplicate_context_candidates,
    deduplicate_context_candidates_with_aliases,
)
from acr_runtime.memory import MemoryCreate, MemoryStatus, MemoryType
from acr_runtime.models import ContextCandidate
from acr_runtime.service import AdaptiveRuntime
from acr_runtime.compiler import ContextRequest


class FakeSemanticAdapter:
    trusted_local = True
    model_id = "test-embedding"
    version = "sha256:test-v1"

    def __init__(self, score: float = 0.97) -> None:
        self.score = score
        self.calls = 0

    def similarity(self, left: str, right: str) -> float:
        self.calls += 1
        return self.score


def artifact(
    artifact_id: str,
    text: str,
    *,
    kind: str = "memory",
    scope: str = "project:a",
    privacy: str = "public",
    behavior: object = None,
) -> DeduplicationArtifact:
    return DeduplicationArtifact(
        kind=kind,
        artifact_id=artifact_id,
        identity={"content": text},
        similarity_text=text,
        scope=scope,
        privacy=privacy,
        behavior=behavior if behavior is not None else {"contract": "v1"},
        provenance=(f"source:{artifact_id}",),
    )


class DeduplicationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary.name) / "acr.db"
        self.database = RuntimeDB(self.path)

    def tearDown(self) -> None:
        self.database.close()
        self.temporary.cleanup()

    def test_canonical_json_normalizes_unicode_and_line_endings_not_case(self):
        composed = artifact("a", "Caf\u00e9\r\nLine")
        decomposed = artifact("b", "Cafe\u0301\nLine")
        changed_case = artifact("c", "caf\u00e9\nLine")
        self.assertEqual(composed.canonical_hash, decomposed.canonical_hash)
        self.assertNotEqual(composed.canonical_hash, changed_case.canonical_hash)
        self.assertEqual(
            canonical_json({"b": 1, "a": "x\r\ny"}),
            '{"a":"x\\ny","b":1}',
        )

    def test_exact_hash_short_circuits_semantic_work(self):
        adapter = FakeSemanticAdapter()
        engine = DeduplicationEngine(
            self.database.connection, semantic_similarity=adapter
        )
        result = engine.analyze(
            [artifact("a", "same"), artifact("b", "same")],
            persist=False,
        )
        self.assertEqual(adapter.calls, 0)
        self.assertEqual(result.similarity_comparisons, 0)
        self.assertEqual(len(result.matches), 1)
        self.assertEqual(result.matches[0].relation, "exact_duplicate")
        self.assertEqual(result.matches[0].recommendation, "REFERENCE")
        self.assertFalse(result.matches[0].automatic_action_allowed)

    def test_scope_and_privacy_partition_before_pair_or_semantic_call(self):
        adapter = FakeSemanticAdapter()
        engine = DeduplicationEngine(
            self.database.connection, semantic_similarity=adapter
        )
        result = engine.analyze(
            [
                artifact("a", "same content", scope="project:a"),
                artifact("b", "same content", scope="project:b"),
                artifact(
                    "c", "same content", scope="project:a", privacy="secret"
                ),
            ],
            persist=False,
        )
        self.assertEqual(result.matches, ())
        self.assertEqual(result.similarity_comparisons, 0)
        self.assertEqual(adapter.calls, 0)

    def test_secret_material_is_rejected_from_retained_identifiers(self):
        key_shaped_identifier = (
            "api_" + "key=" + "sk-" + "proj-" + "1234567890abcdefghijklmnop"
        )
        with self.assertRaisesRegex(ValueError, "secret material"):
            artifact(
                key_shaped_identifier,
                "safe body",
            )
        password_reference = (
            "password=" + "correct-" + "horse-" + "battery-" + "staple"
        )
        with self.assertRaisesRegex(ValueError, "secret material"):
            DeduplicationArtifact(
                kind="memory",
                artifact_id="safe-id",
                identity={"content": "safe"},
                similarity_text="safe",
                provenance=(password_reference,),
            )
        with self.assertRaisesRegex(ValueError, "byte limit"):
            artifact("large", "x" * 1_048_577)

    def test_near_duplicate_with_changed_number_or_negation_stays_separate(self):
        engine = DeduplicationEngine(self.database.connection)
        number = engine.analyze(
            [
                artifact("a", "Retain backups for 10 days"),
                artifact("b", "Retain backups for 100 days"),
            ],
            persist=False,
        )
        negation = engine.analyze(
            [
                artifact("c", "Always delete the local backup"),
                artifact("d", "Never delete the local backup"),
            ],
            persist=False,
        )
        for result in (number, negation):
            self.assertEqual(len(result.matches), 1)
            self.assertEqual(result.matches[0].relation, "near_duplicate")
            self.assertEqual(
                result.matches[0].recommendation, "KEEP_SEPARATE"
            )
            self.assertTrue(result.matches[0].evidence["blockers"])

        prefix = " ".join(f"term{i}" for i in range(30))
        long = engine.analyze(
            [
                artifact("e", f"{prefix} allow deletion"),
                artifact("f", f"{prefix} never allow deletion"),
            ],
            persist=False,
        )
        self.assertEqual(long.matches[0].recommendation, "KEEP_SEPARATE")
        self.assertIn("negation_differs", long.matches[0].evidence["blockers"])

    def test_semantic_is_versioned_trusted_public_and_invalid_scores_fail(self):
        adapter = FakeSemanticAdapter()
        engine = DeduplicationEngine(
            self.database.connection, semantic_similarity=adapter
        )
        result = engine.analyze(
            [
                artifact("a", "Inspect SQLite database schema"),
                artifact("b", "Inspect the SQLite database structure"),
            ],
            persist=False,
        )
        self.assertEqual(result.matches[0].relation, "semantic_duplicate")
        self.assertIn(adapter.model_id, result.matches[0].method)
        self.assertEqual(adapter.calls, 1)

        invalid = FakeSemanticAdapter(float("nan"))
        with self.assertRaises(ValueError):
            DeduplicationEngine(
                self.database.connection, semantic_similarity=invalid
            ).analyze(
                [
                    artifact("c", "Inspect SQLite database schema"),
                    artifact("d", "Inspect the SQLite database structure"),
                ],
                persist=False,
            )

    def test_skill_behavior_difference_is_overlap_not_merge(self):
        result = DeduplicationEngine(self.database.connection).analyze(
            [
                artifact(
                    "a",
                    "Deploy and verify the web service",
                    kind="skill",
                    behavior={"permissions": ["read"]},
                ),
                artifact(
                    "b",
                    "Deploy then verify the web service",
                    kind="skill",
                    behavior={"permissions": ["write"]},
                ),
            ],
            persist=False,
        )
        self.assertEqual(result.matches[0].relation, "overlapping_capability")
        self.assertEqual(result.matches[0].recommendation, "COMPOSE")
        self.assertIn(
            "behavior_contract_differs", result.matches[0].evidence["blockers"]
        )

    def test_context_coalescing_preserves_provenance_and_dependencies(self):
        first = ContextCandidate(
            source_type="file",
            source_id="a",
            label="A",
            content="Exact content",
            dependencies=("dependency-a",),
            content_origin="document",
            security_authority="data_only",
            provenance=("file:a",),
        )
        second = ContextCandidate(
            source_type="file",
            source_id="b",
            label="B",
            content="Exact content",
            required=True,
            dependencies=("dependency-b",),
            content_origin="document",
            security_authority="data_only",
            provenance=("file:b",),
        )
        selected, rejected = deduplicate_context_candidates([first, second])
        self.assertEqual(len(selected), 1)
        self.assertTrue(selected[0].required)
        self.assertEqual(
            set(selected[0].dependencies), {"dependency-a", "dependency-b"}
        )
        self.assertIn("file:a", selected[0].provenance)
        self.assertIn("file:b", selected[0].provenance)
        self.assertEqual(len(rejected), 1)
        self.assertIn("exact_duplicate_of", rejected[0].reason)

        different_authority = ContextCandidate(
            **{
                **first.__dict__,
                "source_id": "system",
                "security_authority": "system",
            }
        )
        selected, rejected = deduplicate_context_candidates(
            [first, different_authority]
        )
        self.assertEqual(len(selected), 2)
        self.assertEqual(rejected, [])

        third = ContextCandidate(
            **{
                **first.__dict__,
                "source_id": "c:with:colon",
                "required": True,
                "expected_utility": 0.95,
            }
        )
        selected, rejected, aliases = (
            deduplicate_context_candidates_with_aliases(
                [first, second, third]
            )
        )
        self.assertEqual([item.source_id for item in selected], ["c:with:colon"])
        self.assertEqual(
            aliases, {"a": "c:with:colon", "b": "c:with:colon"}
        )
        self.assertEqual(len(rejected), 2)

    def test_compiler_dependency_on_duplicate_resolves_to_retained_source(self):
        path = Path(self.temporary.name) / "context.db"
        with AdaptiveRuntime(path) as runtime:
            first = ContextCandidate(
                source_type="file",
                source_id="a",
                label="Shared A",
                content="SQLite shared schema details.",
            )
            second = ContextCandidate(
                source_type="file",
                source_id="b",
                label="Shared B",
                content="SQLite shared schema details.",
            )
            dependent = ContextCandidate(
                source_type="file",
                source_id="consumer",
                label="Consumer",
                content="Use SQLite shared schema details.",
                dependencies=("a",),
            )
            bundle = runtime.compile_context_request(
                ContextRequest(
                    task="Use SQLite shared schema details",
                    relevant_files=(first, second, dependent),
                    token_budget=200,
                )
            )
        blocks = {item.source_id: item for item in bundle.blocks}
        self.assertNotIn("a", blocks)
        self.assertIn("b", blocks)
        self.assertTrue(blocks["b"].required)
        self.assertIn("context:file:a", blocks["b"].provenance)

    def test_persistence_is_content_minimized_append_only_and_loadable(self):
        result = DeduplicationEngine(self.database.connection).analyze(
            [
                artifact(
                    "customer@example.com", "TOP-SECRET-VALUE"
                ),
                artifact("b", "TOP-SECRET-VALUE"),
            ]
        )
        loaded = DeduplicationEngine(self.database.connection).load(
            result.id, scope="project:a"
        )
        self.assertEqual(loaded.match_count, result.match_count)
        self.assertEqual(
            [item.relation for item in loaded.matches],
            [item.relation for item in result.matches],
        )
        self.assertTrue(
            all(
                item.left_artifact_id.startswith("ref:")
                and item.right_artifact_id.startswith("ref:")
                for item in loaded.matches
            )
        )
        dumped = "\n".join(
            str(value)
            for table in (
                "deduplication_runs",
                "deduplication_items",
                "deduplication_matches",
            )
            for row in self.database.connection.execute(
                f"SELECT * FROM {table}"
            )
            for value in row
        )
        self.assertNotIn("TOP-SECRET-VALUE", dumped)
        self.assertNotIn("customer@example.com", dumped)
        with self.assertRaises(PermissionError):
            DeduplicationEngine(self.database.connection).load(
                result.id, scope="project:b"
            )
        with self.assertRaises(sqlite3.IntegrityError):
            self.database.connection.execute(
                """
                UPDATE deduplication_matches
                SET automatic_action_allowed=1 WHERE run_id=?
                """,
                (result.id,),
            )
        with self.assertRaises(sqlite3.IntegrityError):
            self.database.connection.execute(
                "DELETE FROM deduplication_runs WHERE id=?", (result.id,)
            )
        self.database.connection.rollback()

    def test_database_scan_and_cli_report(self):
        for source_id in ("source-a", "source-b"):
            self.database.memories.create(
                MemoryCreate(
                    type=MemoryType.SEMANTIC,
                    content="The database uses SQLite FTS5.",
                    subject="database",
                    scope="project:a",
                    status=MemoryStatus.CONFIRMED,
                    source_id=source_id,
                )
            )
        expired = self.database.memories.create(
            MemoryCreate(
                type=MemoryType.SEMANTIC,
                content="The database uses SQLite FTS5.",
                subject="database",
                scope="project:a",
                status=MemoryStatus.CONFIRMED,
                source_id="expired-source",
            )
        )
        self.database.connection.execute(
            """
            UPDATE memories
            SET retention_until='2020-01-01T00:00:00Z'
            WHERE id=?
            """,
            (expired.id,),
        )
        self.database.connection.commit()
        result = DeduplicationEngine(
            self.database.connection
        ).scan_database(kinds=("memory",), scope="project:a", limit=10)
        self.assertEqual(result.artifact_count, 2)
        self.assertEqual(result.match_count, 1)

        output = io.StringIO()
        with redirect_stdout(output):
            code = main(
                [
                    "--db",
                    str(self.path),
                    "dedup",
                    "report",
                    result.id,
                    "--scope",
                    "project:a",
                ]
            )
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output.getvalue())["id"], result.id)


if __name__ == "__main__":
    unittest.main()
