from __future__ import annotations

import io
import json
import sqlite3
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from acr_runtime import (
    AdaptiveRuntime,
    ProjectCreate,
    ProjectItemCreate,
    ProjectItemUpdate,
    ProjectStateConflict,
    ProjectStateError,
    SafeModeViolation,
)
from acr_runtime.cli import main


class ProjectStateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.database = self.root / "acr.db"
        self.runtime = AdaptiveRuntime(self.database)
        self.actor = "operator:test"

    def tearDown(self) -> None:
        self.runtime.close()
        self.temporary.cleanup()

    @staticmethod
    def project_payload(**overrides):
        payload = {
            "schema_version": 1,
            "project_key": "adaptive-runtime",
            "name": "Adaptive Cognitive Runtime",
            "objective": "Build a bounded local-first cognitive runtime.",
            "scope": "project:runtime",
        }
        payload.update(overrides)
        return payload

    @staticmethod
    def item_payload(kind="next_work", **overrides):
        payload = {
            "schema_version": 1,
            "kind": kind,
            "title": "Implement the next bounded capability",
            "detail": "Use deterministic gates and retain repository evidence.",
            "status": "planned",
            "priority": 80,
            "evidence": [],
            "dependencies": [],
        }
        if kind in {"completed_work", "decision"}:
            payload["status"] = "completed"
        elif kind == "blocker":
            payload["status"] = "blocked"
        elif kind == "benchmark":
            payload["status"] = "completed"
            payload["evidence"] = ["test:deterministic-v1"]
        payload.update(overrides)
        return payload

    def create_project(self):
        return self.runtime.create_project_state(
            ProjectCreate.from_dict(self.project_payload()),
            actor=self.actor,
        )

    def add_item(self, revision, kind="next_work", **overrides):
        return self.runtime.add_project_item(
            "adaptive-runtime",
            ProjectItemCreate.from_dict(
                self.item_payload(kind, **overrides)
            ),
            expected_project_revision=revision,
            actor=self.actor,
        )

    def test_structured_state_is_persistent_and_separate_from_memory(self):
        created = self.create_project()
        self.assertEqual(created["project"]["revision"], 1)
        self.assertEqual(created["content_trust"], "operator_authored_untrusted_data")
        self.assertEqual(
            self.runtime.db.connection.execute(
                "SELECT COUNT(*) FROM memories"
            ).fetchone()[0],
            0,
        )

        item = self.add_item(1, "milestone")
        self.assertEqual(item["revision"], 1)
        self.runtime.close()
        self.runtime = AdaptiveRuntime(self.database)

        snapshot = self.runtime.projects.snapshot("adaptive-runtime")
        self.assertEqual(snapshot["project"]["revision"], 2)
        self.assertEqual(snapshot["counts"]["milestone"], 1)
        self.assertEqual(snapshot["items"][0]["id"], item["id"])
        self.assertNotIn("summary", snapshot)
        self.assertEqual(
            self.runtime.db.connection.execute(
                "SELECT COUNT(*) FROM memories"
            ).fetchone()[0],
            0,
        )

    def test_all_required_item_kinds_are_typed_and_bounded(self):
        snapshot = self.create_project()
        revision = snapshot["project"]["revision"]
        for kind in (
            "milestone",
            "completed_work",
            "decision",
            "blocker",
            "dependency",
            "technical_debt",
            "benchmark",
            "next_work",
        ):
            self.add_item(revision, kind, title=f"Track {kind} state")
            revision += 1

        state = self.runtime.projects.snapshot("adaptive-runtime")
        self.assertEqual(set(state["counts"]), {
            "milestone",
            "completed_work",
            "decision",
            "blocker",
            "dependency",
            "technical_debt",
            "benchmark",
            "next_work",
        })
        self.assertTrue(all(value == 1 for value in state["counts"].values()))
        self.assertEqual(state["project"]["revision"], 9)

    def test_dependency_readiness_and_cycle_rejection_are_deterministic(self):
        self.create_project()
        dependency = self.add_item(
            1,
            "dependency",
            title="Obtain verified sandbox receipts",
            status="planned",
        )
        next_work = self.add_item(
            2,
            dependencies=[dependency["id"]],
            title="Bind the coding adapter",
        )

        recommendation = self.runtime.projects.recommend("adaptive-runtime")
        self.assertFalse(recommendation[0]["ready"])
        self.assertEqual(
            recommendation[0]["blocked_by"][0]["id"], dependency["id"]
        )

        dependency_update = ProjectItemUpdate.from_dict({
            "schema_version": 1,
            "expected_revision": 1,
            "status": "completed",
            "detail": "The sandbox receipt gate passed.",
            "priority": 80,
            "evidence": ["test:sandbox-receipt-v1"],
            "dependencies": [],
        })
        self.runtime.update_project_item(
            "adaptive-runtime",
            dependency["id"],
            dependency_update,
            expected_project_revision=3,
            actor=self.actor,
        )
        self.assertTrue(
            self.runtime.projects.recommend("adaptive-runtime")[0]["ready"]
        )

        cyclic = ProjectItemUpdate.from_dict({
            "schema_version": 1,
            "expected_revision": 2,
            "status": "completed",
            "detail": "Attempt to add a backwards dependency.",
            "priority": 80,
            "evidence": ["test:sandbox-receipt-v1"],
            "dependencies": [next_work["id"]],
        })
        with self.assertRaisesRegex(ProjectStateError, "cycle"):
            self.runtime.update_project_item(
                "adaptive-runtime",
                dependency["id"],
                cyclic,
                expected_project_revision=4,
                actor=self.actor,
            )

    def test_optimistic_revisions_and_project_lifecycle_prevent_stale_writes(self):
        self.create_project()
        item = self.add_item(1)
        with self.assertRaises(ProjectStateConflict):
            self.add_item(1, title="Stale concurrent write")

        paused = self.runtime.projects.update_status(
            "adaptive-runtime",
            expected_revision=2,
            status="paused",
            actor=self.actor,
        )
        self.assertEqual(paused["project"]["status"], "paused")
        with self.assertRaisesRegex(ProjectStateConflict, "active"):
            self.runtime.update_project_item(
                "adaptive-runtime",
                item["id"],
                ProjectItemUpdate.from_dict({
                    "schema_version": 1,
                    "expected_revision": 1,
                    "status": "in_progress",
                    "detail": "Work started under a paused project.",
                    "priority": 80,
                    "evidence": [],
                    "dependencies": [],
                }),
                expected_project_revision=3,
                actor=self.actor,
            )

        self.runtime.projects.update_status(
            "adaptive-runtime",
            expected_revision=3,
            status="archived",
            actor=self.actor,
        )
        with self.assertRaisesRegex(ProjectStateError, "invalid"):
            self.runtime.projects.update_status(
                "adaptive-runtime",
                expected_revision=4,
                status="active",
                actor=self.actor,
            )

    def test_validation_rejects_secrets_unknown_fields_and_invalid_semantics(self):
        with self.assertRaises(ProjectStateError):
            ProjectCreate.from_dict({
                **self.project_payload(),
                "unexpected": True,
            })
        with self.assertRaises(ProjectStateError):
            ProjectCreate.from_dict(
                self.project_payload(
                    objective="Store API_KEY=abcdefghijklmnop in state."
                )
            )
        with self.assertRaisesRegex(ProjectStateError, "must be completed"):
            ProjectItemCreate.from_dict(
                self.item_payload("decision", status="planned")
            )
        with self.assertRaisesRegex(ProjectStateError, "require evidence"):
            ProjectItemCreate.from_dict(
                self.item_payload("benchmark", evidence=[])
            )

    def test_events_are_content_minimized_hashed_and_immutable(self):
        self.create_project()
        self.add_item(
            1,
            detail="Sensitive project prose stays in the typed item table.",
        )
        snapshot = self.runtime.projects.snapshot("adaptive-runtime")
        events = snapshot["recent_events"]
        self.assertEqual(len(events), 2)
        self.assertTrue(all(len(event["actor_hash"]) == 64 for event in events))
        self.assertNotIn(
            "Sensitive project prose",
            json.dumps(events),
        )
        event_id = self.runtime.db.connection.execute(
            "SELECT id FROM project_state_events LIMIT 1"
        ).fetchone()[0]
        with self.assertRaises(sqlite3.IntegrityError):
            self.runtime.db.connection.execute(
                "UPDATE project_state_events SET event_type='item_updated' WHERE id=?",
                (event_id,),
            )

    def test_safe_mode_blocks_project_state_mutation_but_allows_read(self):
        self.create_project()
        self.runtime.safe_mode.enable(
            actor_id=self.actor,
            reason="Contain state changes during an incident.",
        )
        self.assertEqual(
            self.runtime.projects.snapshot("adaptive-runtime")["project"]["revision"],
            1,
        )
        with self.assertRaises(SafeModeViolation):
            self.add_item(1)

    def test_cli_persists_state_across_independent_invocations(self):
        project_file = self.root / "project.json"
        item_file = self.root / "item.json"
        project_file.write_text(
            json.dumps(self.project_payload()), encoding="utf-8"
        )
        item_file.write_text(
            json.dumps(self.item_payload()), encoding="utf-8"
        )

        def invoke(*arguments):
            output = io.StringIO()
            with redirect_stdout(output):
                result = main(["--db", str(self.database), "project", *arguments])
            self.assertEqual(result, 0)
            return json.loads(output.getvalue())

        with patch.dict(
            "os.environ",
            {
                "ACR_STATE_DIR": str(self.root / "state"),
                "ACR_SKILLS_DIR": str(self.root / "skills"),
            },
        ):
            created = invoke(
                "create", str(project_file), "--actor", self.actor
            )
            added = invoke(
                "item-add",
                "adaptive-runtime",
                str(item_file),
                "--expected-project-revision",
                str(created["project"]["revision"]),
                "--actor",
                self.actor,
            )
            shown = invoke("show", "adaptive-runtime")
            listed = invoke("list")

        self.assertEqual(shown["items"][0]["id"], added["id"])
        self.assertEqual(listed["projects"][0]["project_key"], "adaptive-runtime")


if __name__ == "__main__":
    unittest.main()
