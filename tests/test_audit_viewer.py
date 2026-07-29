from __future__ import annotations

import io
import json
import sqlite3
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from acr_runtime import AdaptiveRuntime, AuditQuery, EVENT_TYPES
from acr_runtime.cli import main


NOW = "2026-07-29T12:00:00.000Z"


class AuditViewerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.database = Path(self.directory.name) / "acr.db"
        self.runtime = AdaptiveRuntime(self.database)

    def tearDown(self) -> None:
        self.runtime.close()
        self.directory.cleanup()

    def _emit_required_events(self) -> None:
        old_memory = self.runtime.remember(
            "semantic", "The old bounded fact.", scope="audit-test"
        )
        new_memory = self.runtime.remember(
            "semantic", "The replacement bounded fact.", scope="audit-test"
        )
        self.runtime.db.memories.supersede(old_memory, new_memory)

        skill_id = self.runtime.register_skill(
            "audit-test-skill",
            "Perform a bounded audit test.",
            trusted=False,
        )
        connection = self.runtime.db.connection
        empty_list = "[]"
        with connection:
            connection.execute(
                """
                INSERT INTO skill_generation_runs(
                    id, status, scope, config_json, candidate_count,
                    created_at, applied_at
                ) VALUES (?, 'planned', 'audit-test', '{}', 1, ?, NULL)
                """,
                ("generation-run", NOW),
            )
            connection.execute(
                """
                INSERT INTO skill_generation_candidates(
                    id, run_id, pattern_hash, trigger_kind, scope, task_class,
                    occurrence_count, average_significance, procedure,
                    applicability_json, inputs_json, outputs_json,
                    verification_json, failure_modes_json, permissions_json,
                    tools_json, evidence_json, trace_ids_json, status,
                    package_path, skill_id, error_type, created_at, applied_at
                ) VALUES (
                    ?, ?, ?, 'repeated_successful_procedure', ?, ?, 3, 0.8, ?,
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, 'proposed',
                    NULL, ?, NULL, ?, NULL
                )
                """,
                (
                    "generation-candidate",
                    "generation-run",
                    "a" * 64,
                    "audit-test",
                    "audit",
                    "Perform a bounded audit test.",
                    empty_list,
                    empty_list,
                    empty_list,
                    empty_list,
                    empty_list,
                    empty_list,
                    empty_list,
                    empty_list,
                    empty_list,
                    skill_id,
                    NOW,
                ),
            )
            connection.execute(
                """
                UPDATE skill_generation_candidates
                SET status='generated', applied_at=?
                WHERE id='generation-candidate'
                """,
                (NOW,),
            )
            connection.execute(
                """
                INSERT INTO skill_registry_history(
                    id, skill_id, event, from_status, to_status,
                    details_json, created_at
                ) VALUES (?, ?, 'promoted', 'quarantined', 'active', '{}', ?)
                """,
                ("skill-promoted", skill_id, NOW),
            )
            connection.execute(
                """
                INSERT INTO skill_registry_history(
                    id, skill_id, event, from_status, to_status,
                    details_json, created_at
                ) VALUES (?, ?, 'retired', 'active', 'retired', '{}', ?)
                """,
                ("skill-retired", skill_id, NOW),
            )
            connection.execute(
                """
                INSERT INTO improvement_policy_events(
                    id, target, event_type, run_id, from_version_id,
                    to_version_id, evidence_hash, created_at
                ) VALUES (
                    'routing-change', 'skill_routing_thresholds', 'promote',
                    NULL, 'routing-v1', 'routing-v2', ?, ?
                )
                """,
                ("b" * 64, NOW),
            )
            connection.execute(
                """
                INSERT INTO agent_specs(
                    id, role, objective, task_scope_json, memory_scope_json,
                    tools_json, skills_json, permissions_json, spec_json,
                    resolved_skills_json, content_hash, status, created_at
                ) VALUES (
                    'audit-agent', 'Auditor', 'Inspect bounded events.',
                    '["audit"]', '["audit-test"]', '[]', '[]', '[]', '{}',
                    '[]', ?, 'defined', ?
                )
                """,
                ("c" * 64, NOW),
            )
            connection.execute(
                """
                INSERT INTO capability_decisions(
                    id, subject_type, subject_id, capability, resource_scope,
                    allowed, grant_id, reason, created_at
                ) VALUES (
                    'denied-decision', 'agent', 'audit-agent', 'filesystem.write',
                    'audit-test', 0, NULL, 'default deny', ?
                )
                """,
                (NOW,),
            )

    def test_required_mutations_emit_minimal_immutable_events(self) -> None:
        self.assertEqual(self.runtime.audit.summary()["latest_sequence"], 0)
        self._emit_required_events()

        events = self.runtime.audit.list(AuditQuery(limit=100))
        counts: dict[str, int] = {}
        for event in events:
            counts[event["event_type"]] = counts.get(event["event_type"], 0) + 1
            self.assertNotIn("content", event["details"])
            self.assertNotIn("instructions", event["details"])
        self.assertEqual(set(counts), set(EVENT_TYPES))
        self.assertEqual(counts["MEMORY_CREATED"], 2)
        self.assertTrue(all(counts[kind] == 1 for kind in EVENT_TYPES - {"MEMORY_CREATED"}))
        self.assertEqual(
            sorted(event["sequence"] for event in events),
            list(range(1, 10)),
        )

        selected = self.runtime.audit.list(
            AuditQuery(event_type="PERMISSION_DENIED", entity_type="permission")
        )
        self.assertEqual(len(selected), 1)
        self.assertEqual(selected[0]["details"]["reason"], "default deny")
        self.assertEqual(
            self.runtime.audit.get(selected[0]["id"])["sequence"],
            selected[0]["sequence"],
        )

        with self.assertRaisesRegex(sqlite3.IntegrityError, "immutable"):
            self.runtime.db.connection.execute(
                "UPDATE audit_events SET details_json='{}' WHERE sequence=1"
            )
        with self.assertRaisesRegex(sqlite3.IntegrityError, "retained"):
            self.runtime.db.connection.execute(
                "DELETE FROM audit_events WHERE sequence=1"
            )

    def test_ordinary_state_is_not_event_sourced(self) -> None:
        with self.runtime.db.connection:
            self.runtime.db.connection.execute(
                """
                INSERT INTO recovery_runs(
                    id, task_id, plan_hash, status, current_sequence,
                    created_at, updated_at
                ) VALUES (
                    'ordinary-recovery', 'ordinary-task', ?, 'planned', 1,
                    ?, ?
                )
                """,
                ("d" * 64, NOW, NOW),
            )
        summary = self.runtime.audit.summary()
        self.assertEqual(summary["latest_sequence"], 0)
        self.assertFalse(summary["ordinary_state_event_sourced"])

    def test_query_validation_and_cli_viewer(self) -> None:
        memory_id = self.runtime.remember(
            "semantic", "Visible through the audit CLI.", scope="audit-cli"
        )
        event = self.runtime.audit.list(
            AuditQuery(entity_id=memory_id, limit=1)
        )[0]
        with self.assertRaises(ValueError):
            AuditQuery(event_type="TASK_CHANGED")
        with self.assertRaises(ValueError):
            AuditQuery(after="2026-07-29")
        with self.assertRaises(ValueError):
            AuditQuery(limit=1001)

        output = io.StringIO()
        with redirect_stdout(output):
            result = main(
                [
                    "--db",
                    str(self.database),
                    "audit",
                    "show",
                    event["id"],
                ]
            )
        self.assertEqual(result, 0)
        self.assertEqual(json.loads(output.getvalue())["entity_id"], memory_id)


if __name__ == "__main__":
    unittest.main()
