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
    ExperienceEvent,
    ExperienceEventKind,
    ExperienceTraceCreate,
    ProcedureDetectionError,
    ProcedureDetectionRequest,
    SafeModeViolation,
)
from acr_runtime.cli import main


class ProcedureDetectorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.database = self.root / "acr.db"
        self.runtime = AdaptiveRuntime(self.database)

    def tearDown(self) -> None:
        self.runtime.close()
        self.temporary.cleanup()

    @staticmethod
    def request_payload(**overrides):
        payload = {
            "schema_version": 1,
            "scope": "project:runtime",
            "task_classes": ["repository verification"],
            "observed_before": "2030-01-01T00:00:00Z",
            "minimum_successes": 3,
            "minimum_distinct_tasks": 3,
            "maximum_non_success_rate": 0.0,
            "minimum_significance": 0.6,
            "maximum_traces": 100,
        }
        payload.update(overrides)
        return payload

    def request(self, **overrides):
        return ProcedureDetectionRequest.from_dict(
            self.request_payload(**overrides)
        )

    @staticmethod
    def operations(query, *, include_suite=True, alternate=False):
        steps = [
            {
                "operation": "file.search",
                "parameters": {"scope": "src", "query": query},
            },
            {
                "operation": "config.inspect",
                "parameters": {"format": "toml"},
            },
            {
                "operation": "test.run" if not alternate else "lint.run",
                "parameters": (
                    {"suite": "focused"} if include_suite else {}
                ),
            },
        ]
        return steps

    def capture(
        self,
        task_id,
        *,
        outcome="succeeded",
        query="alpha",
        include_suite=True,
        alternate=False,
        structured=True,
        malformed=False,
        duplicate=False,
        significance=0.9,
    ):
        metadata = {}
        if structured:
            metadata["operation_sequence_v1"] = (
                [{"operation": "bad"}]
                if malformed
                else self.operations(
                    query,
                    include_suite=include_suite,
                    alternate=alternate,
                )
            )
        event = ExperienceEvent(
            ExperienceEventKind.TOOL_SEQUENCE,
            "Search, inspect configuration, and run the focused test.",
            evidence=(f"task:{task_id}",),
            confidence=0.9,
            importance=0.8,
            metadata_json=json.dumps(metadata),
        )
        events = (event, event) if duplicate else (event,)
        return self.runtime.capture_experience(
            ExperienceTraceCreate(
                task_id=task_id,
                scope="project:runtime",
                task_class="repository verification",
                outcome=outcome,
                significance_score=significance,
                events=events,
            )
        )

    def capture_three_successes(self):
        return (
            self.capture("task-1", query="alpha"),
            self.capture("task-2", query="beta"),
            self.capture("task-3", query="gamma", include_suite=False),
        )

    def test_repeated_successes_suggest_structure_and_variability_only(self):
        traces = self.capture_three_successes()
        before_memories = self.runtime.db.connection.execute(
            "SELECT COUNT(*) FROM memories"
        ).fetchone()[0]
        before_skills = self.runtime.db.connection.execute(
            "SELECT COUNT(*) FROM skills"
        ).fetchone()[0]

        run = self.runtime.detect_procedures(self.request())

        self.assertEqual(run.scanned_trace_count, 3)
        self.assertEqual(run.eligible_sequence_count, 3)
        self.assertEqual(run.rejected_sequence_count, 0)
        self.assertEqual(run.cluster_count, 1)
        self.assertEqual(len(run.suggestions), 1)
        candidate = run.suggestions[0]
        self.assertEqual(
            candidate.operations,
            ("file.search", "config.inspect", "test.run"),
        )
        self.assertEqual(candidate.success_count, 3)
        self.assertEqual(candidate.distinct_task_count, 3)
        self.assertEqual(
            candidate.support_trace_ids,
            tuple(sorted(trace.id for trace in traces)),
        )
        first = {
            item["name"]: item
            for item in candidate.variability[0]["parameters"]
        }
        self.assertEqual(first["scope"]["classification"], "invariant")
        self.assertEqual(first["query"]["classification"], "variable")
        third = candidate.variability[2]["parameters"][0]
        self.assertEqual(third["classification"], "optional")
        encoded = json.dumps(candidate.as_dict())
        for raw_value in ("alpha", "beta", "gamma", "src", "focused"):
            self.assertNotIn(raw_value, encoded)
        self.assertEqual(
            self.runtime.db.connection.execute(
                "SELECT COUNT(*) FROM memories"
            ).fetchone()[0],
            before_memories,
        )
        self.assertEqual(
            self.runtime.db.connection.execute(
                "SELECT COUNT(*) FROM skills"
            ).fetchone()[0],
            before_skills,
        )

    def test_non_success_conformance_gate_suppresses_or_allows_candidate(self):
        self.capture_three_successes()
        self.capture("task-failed", outcome="failed", query="failed")

        strict = self.runtime.detect_procedures(self.request())
        self.assertEqual(strict.suggestions, ())

        bounded = self.runtime.detect_procedures(
            self.request(maximum_non_success_rate=0.25)
        )
        self.assertEqual(len(bounded.suggestions), 1)
        self.assertEqual(bounded.suggestions[0].non_success_count, 1)

    def test_exact_skeletons_do_not_merge_different_operations(self):
        self.capture("task-1", query="alpha")
        self.capture("task-2", query="beta")
        self.capture("task-3", query="gamma", alternate=True)

        run = self.runtime.detect_procedures(self.request())

        self.assertEqual(run.cluster_count, 2)
        self.assertEqual(run.suggestions, ())

    def test_prose_malformed_and_ambiguous_sequences_never_become_candidates(self):
        self.capture("task-prose", structured=False)
        self.capture("task-malformed", malformed=True)
        self.capture("task-duplicate", duplicate=True)

        run = self.runtime.detect_procedures(self.request())

        self.assertEqual(run.scanned_trace_count, 3)
        self.assertEqual(run.eligible_sequence_count, 0)
        self.assertEqual(run.rejected_sequence_count, 2)
        self.assertEqual(run.cluster_count, 0)
        self.assertEqual(run.suggestions, ())

    def test_distinct_task_and_significance_gates_prevent_false_repetition(self):
        self.capture("same-task", query="alpha")
        self.capture("same-task", query="beta")
        self.capture("same-task", query="gamma")
        run = self.runtime.detect_procedures(self.request())
        self.assertEqual(run.suggestions, ())

        self.capture("task-low-1", query="one", significance=0.5)
        self.capture("task-low-2", query="two", significance=0.5)
        self.capture("task-low-3", query="three", significance=0.5)
        second = self.runtime.detect_procedures(
            self.request(observed_before="2031-01-01T00:00:00Z")
        )
        self.assertEqual(second.suggestions, ())

    def test_request_is_closed_bounded_and_requires_repetition(self):
        cases = (
            {"minimum_successes": 2},
            {"minimum_distinct_tasks": 4, "minimum_successes": 3},
            {"maximum_non_success_rate": 0.5},
            {"minimum_significance": 0.5},
            {"maximum_traces": 501},
            {"task_classes": []},
            {"observed_before": "not-a-time"},
            {"unexpected": True},
        )
        for override in cases:
            with self.subTest(override=override):
                with self.assertRaises(ProcedureDetectionError):
                    self.request(**override)

    def test_detection_is_idempotent_for_an_exact_source_snapshot(self):
        self.capture_three_successes()
        first = self.runtime.detect_procedures(self.request())
        replay = self.runtime.detect_procedures(self.request())
        self.assertEqual(replay.id, first.id)

        self.capture("task-4", query="delta")
        changed = self.runtime.detect_procedures(self.request())
        self.assertNotEqual(changed.id, first.id)
        self.assertNotEqual(changed.source_digest, first.source_digest)

    def test_reports_are_immutable_and_safe_mode_blocks_new_detection(self):
        self.capture_three_successes()
        run = self.runtime.detect_procedures(self.request())
        candidate_id = run.suggestions[0].id
        with self.assertRaises(sqlite3.IntegrityError):
            self.runtime.db.connection.execute(
                """
                UPDATE procedure_detection_candidates
                SET status='suggested' WHERE id=?
                """,
                (candidate_id,),
            )
        self.runtime.db.connection.rollback()
        self.runtime.safe_mode.enable(
            actor_id="operator:test",
            reason="Contain analysis writes during an incident.",
        )
        self.assertEqual(
            self.runtime.procedure_detection_report(run.id).id,
            run.id,
        )
        with self.assertRaises(SafeModeViolation):
            self.runtime.detect_procedures(
                self.request(observed_before="2031-01-01T00:00:00Z")
            )

    def test_cli_detect_and_report_are_machine_readable(self):
        self.capture_three_successes()
        request_file = self.root / "request.json"
        request_file.write_text(
            json.dumps(self.request_payload()), encoding="utf-8"
        )

        def invoke(*arguments):
            output = io.StringIO()
            with redirect_stdout(output):
                result = main(
                    ["--db", str(self.database), "procedures", *arguments]
                )
            self.assertEqual(result, 0)
            return json.loads(output.getvalue())

        with patch.dict(
            "os.environ",
            {
                "ACR_STATE_DIR": str(self.root / "state"),
                "ACR_SKILLS_DIR": str(self.root / "skills"),
            },
        ):
            detected = invoke("detect", str(request_file))
            report = invoke("report", detected["id"])

        self.assertEqual(detected["id"], report["id"])
        self.assertEqual(detected["suggestion_count"], 1)


if __name__ == "__main__":
    unittest.main()
