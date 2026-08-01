from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from acr_runtime.cli import _parser, main
from acr_runtime.db import RuntimeDB


class TerminalBuffer(io.StringIO):
    def isatty(self) -> bool:
        return True


class CliExperienceTests(unittest.TestCase):
    def test_required_first_class_groups_are_documented(self):
        help_text = _parser().format_help()
        for command in (
            "run", "task", "memory", "skills", "agents", "models",
            "tools", "benchmark", "telemetry", "config", "doctor",
        ):
            self.assertIn(command, help_text)
        for flag in ("--json", "--verbose", "--dry-run"):
            self.assertIn(flag, help_text)

    def test_config_is_human_by_default_and_json_on_request(self):
        with tempfile.TemporaryDirectory() as temporary:
            database = Path(temporary) / "state" / "acr.db"
            human = TerminalBuffer()
            with redirect_stdout(human):
                self.assertEqual(
                    main(["--db", str(database), "config", "show"]),
                    0,
                )
            self.assertIn("Database:", human.getvalue())
            with self.assertRaises(json.JSONDecodeError):
                json.loads(human.getvalue())

            machine = io.StringIO()
            with redirect_stdout(machine):
                self.assertEqual(
                    main([
                        "config", "show", "--json",
                        "--db", str(database),
                    ]),
                    0,
                )
            self.assertEqual(
                json.loads(machine.getvalue())["database"],
                str(database),
            )
            self.assertFalse(database.exists())

    def test_verbose_uses_stderr_without_contaminating_json(self):
        output = io.StringIO()
        diagnostics = io.StringIO()
        with redirect_stdout(output), redirect_stderr(diagnostics):
            self.assertEqual(
                main(["config", "show", "--json", "--verbose"]),
                0,
            )
        json.loads(output.getvalue())
        self.assertIn("acr: command=config", diagnostics.getvalue())

    def test_global_dry_run_accepts_trailing_flag_and_has_no_side_effects(self):
        with tempfile.TemporaryDirectory() as temporary:
            database = Path(temporary) / "new" / "acr.db"
            output = io.StringIO()
            with redirect_stdout(output):
                self.assertEqual(
                    main([
                        "--db", str(database),
                        "run", "Inspect SQLite", "--dry-run",
                    ]),
                    0,
                )
            payload = json.loads(output.getvalue())
            self.assertTrue(payload["dry_run"])
            self.assertFalse(payload["executed"])
            self.assertEqual(payload["command"], "run")
            self.assertFalse(database.exists())

    def test_run_environment_is_a_bounded_json_object(self):
        parser = _parser()
        parsed = parser.parse_args([
            "run",
            "Inspect SQLite",
            "--environment",
            '{"platform": "windows"}',
        ])
        self.assertEqual(parsed.environment, '{"platform":"windows"}')

        for invalid in ("windows-local", "[]", "{" + ("x" * 16_001)):
            with self.subTest(invalid=invalid[:20]):
                diagnostics = io.StringIO()
                with (
                    redirect_stderr(diagnostics),
                    self.assertRaises(SystemExit) as raised,
                ):
                    parser.parse_args([
                        "run",
                        "Inspect SQLite",
                        "--environment",
                        invalid,
                    ])
                self.assertEqual(raised.exception.code, 2)
                self.assertIn("--environment", diagnostics.getvalue())

    def test_task_list_and_show_are_bounded_and_machine_readable(self):
        with tempfile.TemporaryDirectory() as temporary:
            database = Path(temporary) / "acr.db"
            with RuntimeDB(database) as runtime_db:
                task_id = runtime_db.create_task(
                    objective="Inspect task state",
                    scope="test",
                    token_budget=256,
                )
            listed = io.StringIO()
            with redirect_stdout(listed):
                self.assertEqual(
                    main([
                        "--json", "--db", str(database),
                        "task", "list", "--limit", "1",
                    ]),
                    0,
                )
            self.assertEqual(
                json.loads(listed.getvalue())["tasks"][0]["id"],
                task_id,
            )
            shown = io.StringIO()
            with redirect_stdout(shown):
                self.assertEqual(
                    main([
                        "--json", "--db", str(database),
                        "task", "show", task_id,
                    ]),
                    0,
                )
            self.assertEqual(json.loads(shown.getvalue())["id"], task_id)
            with self.assertRaisesRegex(ValueError, "1..200"):
                main([
                    "--db", str(database),
                    "task", "list", "--limit", "0",
                ])


if __name__ == "__main__":
    unittest.main()
