from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from acr_runtime.cli import main
from acr_runtime.db import RuntimeDB
from acr_runtime.decision_memory import (
    DecisionAssumption,
    DecisionCheck,
    DecisionCreate,
    DecisionMemory,
)
from acr_runtime.memory import MemoryCreate, MemoryStatus, MemoryType
from acr_runtime.retrieval import HybridMemoryRetriever


class DecisionMemoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.database = RuntimeDB(Path(self.temporary.name) / "acr.db")
        self.decisions = DecisionMemory(
            self.database.memories,
            HybridMemoryRetriever(self.database.memories),
        )

    def tearDown(self) -> None:
        self.database.close()
        self.temporary.cleanup()

    @staticmethod
    def request(**changes) -> DecisionCreate:
        values = {
            "topic": "database architecture",
            "decision": "Use SQLite with FTS5 for local persistent memory.",
            "context": "The runtime must remain local-first and easy to install.",
            "alternatives": ("PostgreSQL", "a remote vector database"),
            "reason": "SQLite is embedded and FTS5 provides focused local search.",
            "consequences": (
                "Single-file backups remain possible.",
                "Write concurrency is intentionally bounded.",
            ),
            "decided_at": "2026-07-27T12:00:00Z",
            "scope": "project:a",
            "evidence": ("adr:0001", "test:fts5"),
            "assumptions": (
                DecisionAssumption("deployment", "single-node"),
                DecisionAssumption("offline_required", "true"),
            ),
        }
        values.update(changes)
        return DecisionCreate(**values)

    def check(self, assumptions):
        return self.decisions.check(
            DecisionCheck(
                task="Change the database architecture",
                query="database architecture SQLite",
                scope="project:a",
                assumptions=assumptions,
            )
        )

    def test_structured_decision_round_trips_every_required_field(self) -> None:
        record = self.decisions.record(self.request())
        inspected = self.decisions.inspect(record.id)

        self.assertEqual(inspected["decision"], self.request().decision)
        self.assertEqual(inspected["context"], self.request().context)
        self.assertEqual(inspected["alternatives"], list(self.request().alternatives))
        self.assertEqual(inspected["consequences"], list(self.request().consequences))
        self.assertEqual(inspected["decided_at"], "2026-07-27T12:00:00+00:00")
        self.assertEqual(inspected["scope"], "project:a")
        self.assertEqual(inspected["evidence"], ["adr:0001", "test:fts5"])
        self.assertEqual(record.type, MemoryType.DECISION)
        self.assertEqual(record.status, MemoryStatus.CONFIRMED)

    def test_assumptions_are_applicable_unverified_or_stale_not_blindly_followed(self):
        self.decisions.record(self.request())

        unchecked = self.check({})
        applicable = self.check({
            "deployment": "single-node",
            "offline_required": "true",
        })
        stale = self.check({
            "deployment": "multi-node",
            "offline_required": "true",
        })

        self.assertEqual(
            unchecked["decisions"][0]["status"], "needs_validation"
        )
        self.assertTrue(unchecked["requires_reconsideration"])
        self.assertEqual(applicable["decisions"][0]["status"], "applicable")
        self.assertFalse(applicable["requires_reconsideration"])
        self.assertEqual(stale["decisions"][0]["status"], "stale_assumptions")
        self.assertEqual(
            stale["decisions"][0]["changed_assumptions"], ["deployment"]
        )

    def test_supersession_and_scope_filter_before_architecture_check(self) -> None:
        old = self.decisions.record(self.request(
            decided_at="2026-07-25T12:00:00Z"
        ))
        new = self.decisions.record(self.request(
            decision="Use PostgreSQL for the multi-node deployment.",
            reason="The deployment now requires concurrent multi-node writes.",
            assumptions=(
                DecisionAssumption("deployment", "multi-node"),
                DecisionAssumption("offline_required", "false"),
            ),
            decided_at="2026-07-26T12:00:00Z",
            supersedes=old.id,
        ))
        self.decisions.record(self.request(
            decision="Use a remote database for the unrelated project.",
            scope="project:b",
            decided_at="2026-07-27T12:00:00Z",
        ))

        result = self.check({
            "deployment": "multi-node",
            "offline_required": "false",
        })

        self.assertEqual(
            [item["memory_id"] for item in result["decisions"]], [new.id]
        )

    def test_legacy_decision_is_visible_but_requires_manual_validation(self) -> None:
        legacy = self.database.memories.create(
            MemoryCreate(
                type=MemoryType.DECISION,
                content="Use SQLite for database architecture",
                scope="project:a",
                subject="database architecture",
                status=MemoryStatus.CONFIRMED,
            )
        )
        result = self.check({})
        self.assertEqual(result["decisions"][0]["memory_id"], legacy.id)
        self.assertEqual(
            result["decisions"][0]["status"], "unstructured_legacy"
        )
        self.assertTrue(result["requires_reconsideration"])

    def test_malformed_v1_payload_fails_closed_as_manual_review(self) -> None:
        malformed = self.database.memories.create(
            MemoryCreate(
                type=MemoryType.DECISION,
                content="Use SQLite for database architecture",
                scope="project:a",
                subject="database architecture",
                structured_payload_json='{"schema":"acr.decision.v1"}',
                status=MemoryStatus.CONFIRMED,
            )
        )
        result = self.check({})
        self.assertEqual(result["decisions"][0]["memory_id"], malformed.id)
        self.assertEqual(
            result["decisions"][0]["status"], "invalid_structured_decision"
        )
        self.assertTrue(result["requires_reconsideration"])

    def test_cli_records_then_checks_decision_assumptions(self) -> None:
        decision_file = Path(self.temporary.name) / "decision.json"
        payload = self.request().payload()
        payload.pop("schema")
        payload["supersedes"] = None
        decision_file.write_text(json.dumps(payload), encoding="utf-8")

        output = io.StringIO()
        with redirect_stdout(output):
            code = main([
                "--db",
                str(self.database.path),
                "memory",
                "decision-add",
                str(decision_file),
            ])
        self.assertEqual(code, 0)
        self.assertTrue(json.loads(output.getvalue())["id"])

        output = io.StringIO()
        with redirect_stdout(output):
            code = main([
                "--db",
                str(self.database.path),
                "memory",
                "decision-check",
                "database architecture SQLite",
                "--scope",
                "project:a",
                "--assumption",
                "deployment=single-node",
                "--assumption",
                "offline_required=true",
            ])
        self.assertEqual(code, 0)
        self.assertEqual(
            json.loads(output.getvalue())["decisions"][0]["status"],
            "applicable",
        )


if __name__ == "__main__":
    unittest.main()
