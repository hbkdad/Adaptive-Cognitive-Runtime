from __future__ import annotations

import io
import json
import sqlite3
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from acr_runtime import AdaptiveRuntime, PROFILE_CATEGORIES
from acr_runtime.cli import main
from acr_runtime.providers import ChatMessage, ChatRequest, MockProvider
from acr_runtime.retrieval import RetrievalRequest


class SemanticStub:
    def score(self, query, memories):
        return {memory.id: 0.8 for memory in memories}


class PerformanceProfilerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.database = Path(self.directory.name) / "acr.db"
        self.runtime = AdaptiveRuntime(self.database)

    def tearDown(self) -> None:
        self.runtime.close()
        self.directory.cleanup()

    def test_capture_measures_all_required_local_boundaries(self) -> None:
        self.runtime.remember(
            "semantic", "Runtime profiling remains local.", scope="profile"
        )
        self.runtime.retriever.semantic = SemanticStub()
        provider = MockProvider(responder=lambda request: "profiled")

        with self.runtime.performance.capture(
            "all required boundaries", scope="profile"
        ) as profile:
            self.runtime.retrieve_memory(
                RetrievalRequest(
                    task="Profile runtime retrieval.",
                    query="runtime profiling",
                    scope="profile",
                    token_budget=100,
                )
            )
            self.runtime.compile_context(
                "Profile runtime context.",
                scope="profile",
                token_budget=300,
            )
            self.runtime.db.connection.execute(
                "-- private_comment_must_not_be_retained\nSELECT 1"
            ).fetchone()
            provider.chat(
                ChatRequest(
                    model="mock-chat",
                    messages=(ChatMessage(role="user", content="profile"),),
                )
            )
            with profile.measure("tool_latency", "test.tool"):
                pass
            profile.serialize({"safe": True})
            for _ in range(5):
                profile.observe(
                    "model_wait", "adapter.reported", 1_000_000_000
                )

        self.assertIsNotNone(profile.run_id)
        report = self.runtime.performance.report(profile.run_id)
        self.assertEqual(set(report["categories"]), set(PROFILE_CATEGORIES))
        self.assertTrue(
            all(
                item["measured"]
                for item in report["categories"].values()
            )
        )
        self.assertEqual(
            report["bottlenecks"][0]["category"], "model_wait"
        )
        self.assertTrue(report["optimization_allowed"])
        self.assertFalse(report["distributed_tracing_enabled"])
        self.assertTrue(report["overlapping_spans"])

        columns = {
            row["name"]
            for row in self.runtime.db.connection.execute(
                "PRAGMA table_info(performance_measurements)"
            )
        }
        self.assertNotIn("sql", columns)
        self.assertNotIn("parameters", columns)
        operations = {
            row["operation"]
            for row in self.runtime.db.connection.execute(
                "SELECT operation FROM performance_measurements"
            )
        }
        self.assertIn("sqlite.select", operations)
        self.assertFalse(
            any("private" in operation for operation in operations)
        )

    def test_failures_are_minimized_and_profiles_are_immutable(self) -> None:
        profile = None
        with self.assertRaisesRegex(RuntimeError, "private failure detail"):
            with self.runtime.performance.capture(
                "failed profile", scope="profile"
            ) as profile:
                with profile.measure(
                    "serialization", "json.failed"
                ):
                    raise RuntimeError("private failure detail")
        self.assertIsNotNone(profile)
        self.assertIsNotNone(profile.run_id)
        report = self.runtime.performance.report(profile.run_id)
        self.assertEqual(report["status"], "failed")
        row = self.runtime.db.connection.execute(
            """
            SELECT error_type FROM performance_measurements
            WHERE run_id=?
            """,
            (profile.run_id,),
        ).fetchone()
        self.assertEqual(row["error_type"], "RuntimeError")
        with self.assertRaisesRegex(sqlite3.IntegrityError, "immutable"):
            self.runtime.db.connection.execute(
                """
                UPDATE performance_measurements
                SET duration_ns=0 WHERE run_id=?
                """,
                (profile.run_id,),
            )
        with self.assertRaisesRegex(sqlite3.IntegrityError, "retained"):
            self.runtime.db.connection.execute(
                "DELETE FROM performance_profile_runs WHERE id=?",
                (profile.run_id,),
            )

    def test_nested_capture_and_invalid_contracts_fail_closed(self) -> None:
        with self.runtime.performance.capture(
            "outer profile", scope="profile"
        ):
            with self.assertRaisesRegex(RuntimeError, "Nested"):
                with self.runtime.performance.capture(
                    "inner profile", scope="profile"
                ):
                    pass
        with self.assertRaises(ValueError):
            with self.runtime.performance.capture("", scope="profile"):
                pass
        with self.assertRaises(ValueError):
            self.runtime.performance.report("")
        with self.assertRaises(ValueError):
            self.runtime.performance.list(limit=201)
        with self.assertRaises(ValueError):
            type(self.runtime.performance)(
                self.runtime.db.connection,
                minimum_bottleneck_mean_ns=999_999,
            )

    def test_cli_profiles_local_work_and_reports_missing_boundaries(self) -> None:
        output = io.StringIO()
        with redirect_stdout(output):
            result = main(
                [
                    "--db",
                    str(self.database),
                    "performance",
                    "profile-local",
                    "--scope",
                    "profile",
                    "--iterations",
                    "2",
                ]
            )
        self.assertEqual(result, 0)
        payload = json.loads(output.getvalue())
        self.assertGreater(payload["measurement_count"], 0)
        self.assertTrue(
            payload["categories"]["database_queries"]["measured"]
        )
        self.assertTrue(
            payload["categories"]["retrieval_latency"]["measured"]
        )
        self.assertTrue(
            payload["categories"]["context_compilation"]["measured"]
        )
        self.assertTrue(payload["categories"]["serialization"]["measured"])
        self.assertFalse(payload["categories"]["model_wait"]["measured"])
        self.assertFalse(payload["categories"]["tool_latency"]["measured"])
        for bottleneck in payload["bottlenecks"]:
            category = payload["categories"][bottleneck["category"]]
            self.assertTrue(category["measured"])
            self.assertGreaterEqual(category["sample_count"], 5)


if __name__ == "__main__":
    unittest.main()
