from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from acr_runtime.cli import main
from acr_runtime.db import RuntimeDB
from acr_runtime.memory import MemoryCreate, MemoryQuery, MemoryStatus, MemoryType
from acr_runtime.memory_scope import MemoryScopeKind
from acr_runtime.retrieval import HybridMemoryRetriever, RetrievalRequest


class MemoryScopeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.database = RuntimeDB(Path(self.temporary.name) / "acr.db")
        self.store = self.database.memories
        scopes = self.database.scopes
        scopes.register(
            "org:acme", MemoryScopeKind.ORGANIZATION, parent_id="global"
        )
        scopes.register(
            "project:a", MemoryScopeKind.PROJECT, parent_id="org:acme"
        )
        scopes.register(
            "project:b", MemoryScopeKind.PROJECT, parent_id="org:acme"
        )
        scopes.register(
            "repo:a", MemoryScopeKind.REPOSITORY, parent_id="project:a"
        )
        scopes.register(
            "task:a1", MemoryScopeKind.TASK, parent_id="repo:a"
        )
        scopes.register(
            "task:a2", MemoryScopeKind.TASK, parent_id="repo:a"
        )
        scopes.register(
            "agent:a1", MemoryScopeKind.AGENT, parent_id="task:a1"
        )
        scopes.register(
            "agent:a2", MemoryScopeKind.AGENT, parent_id="task:a2"
        )

    def tearDown(self) -> None:
        self.database.close()
        self.temporary.cleanup()

    def add(self, content: str, scope: str):
        return self.store.create(
            MemoryCreate(
                type=MemoryType.SEMANTIC,
                content=content,
                scope=scope,
                status=MemoryStatus.CONFIRMED,
            )
        )

    def test_hierarchy_is_explicit_and_rejects_invalid_parent_kinds(self) -> None:
        path = self.database.scopes.ancestors("agent:a1")
        self.assertEqual(
            [scope.id for scope in path],
            ["agent:a1", "task:a1", "repo:a", "project:a", "org:acme", "global"],
        )
        with self.assertRaisesRegex(ValueError, "cannot descend"):
            self.database.scopes.register(
                "repo:bad",
                MemoryScopeKind.REPOSITORY,
                parent_id="org:acme",
            )
        with self.assertRaises(KeyError):
            self.database.scopes.register(
                "task:orphan",
                MemoryScopeKind.TASK,
                parent_id="repo:missing",
            )

    def test_agents_share_ancestor_memory_but_not_each_others_private_memory(self):
        shared = self.add("Shared project database uses SQLite", "project:a")
        private = self.add("Agent one private SQLite scratch note", "agent:a1")

        agent_one = self.store.search(
            MemoryQuery(scope="agent:a1", text="SQLite")
        )
        agent_two = self.store.search(
            MemoryQuery(scope="agent:a2", text="SQLite")
        )

        self.assertEqual(
            {record.id for record in agent_one.records}, {shared.id, private.id}
        )
        self.assertEqual(
            {record.id for record in agent_two.records}, {shared.id}
        )

    def test_similar_embedding_candidate_cannot_cross_project_boundary(self) -> None:
        allowed = self.add("Project database uses SQLite FTS5", "project:a")
        self.add("Project database uses SQLite FTS5", "project:b")

        result = HybridMemoryRetriever(self.store).retrieve(
            RetrievalRequest(
                task="Inspect the project database",
                query="SQLite FTS5",
                scope="agent:a1",
                token_budget=200,
            )
        )

        self.assertEqual(
            [item.memory.id for item in result.selected], [allowed.id]
        )
        self.assertEqual(result.candidate_count, 1)

    def test_exact_query_can_disable_all_ancestor_sharing(self) -> None:
        self.add("Repository SQLite convention", "repo:a")
        private = self.add("Agent-local SQLite convention", "agent:a1")

        page = self.store.search(
            MemoryQuery(
                scope="agent:a1",
                text="SQLite convention",
                include_global=False,
            )
        )

        self.assertEqual([record.id for record in page.records], [private.id])

    def test_cli_registers_and_inspects_scope_path(self) -> None:
        output = io.StringIO()
        with redirect_stdout(output):
            code = main([
                "--db",
                str(self.database.path),
                "memory",
                "scope-add",
                "agent:cli",
                "agent",
                "--parent",
                "project:a",
            ])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output.getvalue())["id"], "agent:cli")

        output = io.StringIO()
        with redirect_stdout(output):
            code = main([
                "--db",
                str(self.database.path),
                "memory",
                "scope-path",
                "agent:cli",
            ])
        self.assertEqual(code, 0)
        self.assertEqual(
            [item["id"] for item in json.loads(output.getvalue())],
            ["agent:cli", "project:a", "org:acme", "global"],
        )


if __name__ == "__main__":
    unittest.main()
