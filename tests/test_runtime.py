from __future__ import annotations

import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

from acr_runtime import AdaptiveRuntime, Settings
from acr_runtime.cli import main
from acr_runtime.db import RuntimeDB
from acr_runtime.diagnostics import run_doctor
from acr_runtime.memory import SourceClass


class RuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database = Path(self.temp_dir.name) / "acr.db"

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_context_compiler_respects_scope_budget_and_attribution(self):
        with AdaptiveRuntime(self.database) as runtime:
            relevant_id = runtime.remember(
                "semantic",
                "This project uses SQLite FTS5 for local memory retrieval.",
                scope="alpha",
                confidence=0.98,
                importance=0.9,
            )
            runtime.remember(
                "semantic",
                "A different project deploys to a cloud CMS.",
                scope="beta",
                confidence=0.9,
                importance=0.9,
            )
            skill_id = runtime.register_skill(
                "sqlite-check",
                "Inspect SQLite schema and execute a focused FTS5 query.",
                description="SQLite database diagnostics",
                tags=["sqlite", "fts5"],
                trusted=True,
            )

            bundle = runtime.compile_context(
                "Check the SQLite FTS5 database",
                scope="alpha",
                token_budget=240,
            )

            selected_ids = {block.source_id for block in bundle.blocks}
            self.assertIn(relevant_id, selected_ids)
            self.assertIn(skill_id, selected_ids)
            self.assertLessEqual(bundle.total_tokens, 240)
            self.assertNotIn("cloud CMS", bundle.render())

            runtime.complete_task(
                bundle,
                success=True,
                critic_score=0.95,
                duration_ms=25,
                useful_source_ids=selected_ids,
            )
            telemetry = runtime.telemetry()
            self.assertEqual(telemetry["tasks"], 1)
            self.assertEqual(telemetry["successes"], 1)
            self.assertEqual(telemetry["useful_blocks"], len(bundle.blocks))
            self.assertEqual(telemetry["wasted_tokens"], 0)

    def test_superseded_memory_is_not_retrieved(self):
        with AdaptiveRuntime(self.database) as runtime:
            old_id = runtime.remember(
                "semantic",
                "The project database is Firebase.",
                scope="project",
                confidence=0.9,
            )
            new_id = runtime.remember(
                "semantic",
                "The project migrated from Firebase to Supabase.",
                scope="project",
                confidence=0.99,
                supersedes=old_id,
            )

            bundle = runtime.compile_context(
                "Which database replaced Firebase?",
                scope="project",
                token_budget=100,
            )

            selected_ids = {block.source_id for block in bundle.blocks}
            self.assertIn(new_id, selected_ids)
            self.assertNotIn(old_id, selected_ids)

    def test_untrusted_skills_stay_quarantined(self):
        with AdaptiveRuntime(self.database) as runtime:
            quarantined_id = runtime.register_skill(
                "generated-deploy",
                "Deploy the application.",
                description="Generated deployment procedure",
                tags=["deploy"],
                trusted=False,
            )
            bundle = runtime.compile_context(
                "Deploy the application", token_budget=100
            )
            self.assertNotIn(
                quarantined_id, {block.source_id for block in bundle.blocks}
            )

    def test_typed_settings_do_not_expose_secrets(self):
        settings = Settings.from_env(
            database=self.database,
            environ={
                "ACR_STATE_DIR": self.temp_dir.name,
                "ACR_SKILLS_DIR": str(Path(self.temp_dir.name) / "skills"),
                "ACR_PROVIDER": "ollama",
                "ACR_OLLAMA_URL": "http://127.0.0.1:11434/",
                "OPENAI_API_KEY": "must-not-appear",
            },
        )
        summary = settings.public_summary()
        self.assertEqual(summary["provider"], "ollama")
        self.assertEqual(summary["ollama_url"], "http://127.0.0.1:11434")
        self.assertNotIn("must-not-appear", repr(summary))

    def test_doctor_validates_database_migration_and_fts5(self):
        settings = Settings(
            database=self.database,
            state_dir=Path(self.temp_dir.name),
            skills_dir=Path(self.temp_dir.name) / "skills",
            provider=None,
            ollama_url="http://127.0.0.1:1",
            ollama_model=None,
        )
        checks = {check.name: check for check in run_doctor(settings)}
        self.assertEqual(checks["database"].status, "pass")
        self.assertEqual(checks["migrations"].status, "pass")
        self.assertEqual(checks["memory_store"].status, "pass")
        self.assertNotIn("fail", {check.status for check in checks.values()})

    def test_status_cli_reports_current_schema(self):
        output = StringIO()
        with redirect_stdout(output):
            exit_code = main(["--db", str(self.database), "status"])
        self.assertEqual(exit_code, 0)
        self.assertIn('"schema_current": true', output.getvalue())

    def test_memory_add_cli_round_trips_source_class(self):
        output = StringIO()
        with redirect_stdout(output):
            exit_code = main([
                "--db",
                str(self.database),
                "memory",
                "add",
                "semantic",
                "Repository evidence",
                "--source-class",
                "repository",
                "--source-type",
                "git-checkout",
                "--source-id",
                "commit-123",
            ])
        self.assertEqual(exit_code, 0)
        memory_id = output.getvalue().strip()
        with RuntimeDB(self.database) as database:
            self.assertEqual(
                database.memories.get(memory_id).source_class,
                SourceClass.REPOSITORY,
            )


if __name__ == "__main__":
    unittest.main()
