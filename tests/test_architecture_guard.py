from __future__ import annotations

import io
import json
import tempfile
import textwrap
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from acr_runtime.architecture_guard import check_architecture, load_policy, main


POLICY = """
schema_version = 1
root_package = "sample"

[core_domain]
modules = ["sample.core"]

[forbidden.web_ui]
internal = ["sample.web"]
external = ["fastapi"]

[forbidden.specific_providers]
internal = ["sample.providers.ollama"]
external = []

[forbidden.database_implementation]
internal = ["sample.db"]
external = ["sqlite3"]
"""


class ArchitectureGuardTests(unittest.TestCase):
    def _repository(self, files: dict[str, str]) -> tempfile.TemporaryDirectory[str]:
        temporary = tempfile.TemporaryDirectory()
        root = Path(temporary.name)
        (root / "architecture-boundaries.toml").write_text(
            textwrap.dedent(POLICY), encoding="utf-8"
        )
        for relative, content in {
            "sample/__init__.py": "",
            "sample/core.py": "",
            "sample/web.py": "",
            "sample/db.py": "",
            "sample/providers/__init__.py": "",
            "sample/providers/ollama.py": "",
            **files,
        }.items():
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(textwrap.dedent(content), encoding="utf-8")
        return temporary

    def test_repository_policy_is_valid_and_scans_declared_core(self) -> None:
        report = check_architecture()
        self.assertTrue(report.valid)
        self.assertGreater(report.modules_scanned, 80)
        self.assertGreater(report.imports_scanned, 100)
        self.assertEqual(
            report.core_modules,
            (
                "acr_runtime.capability_vocab",
                "acr_runtime.models",
                "acr_runtime.scoring",
            ),
        )

    def test_direct_forbidden_boundaries_are_reported_by_category(self) -> None:
        for statement, category, target in (
            ("from . import web", "web_ui", "sample.web"),
            (
                "from .providers import ollama",
                "specific_providers",
                "sample.providers.ollama",
            ),
            ("import sqlite3", "database_implementation", "sqlite3"),
        ):
            with self.subTest(statement=statement):
                with self._repository({"sample/core.py": statement}) as repository:
                    report = check_architecture(repository)
                self.assertFalse(report.valid)
                self.assertEqual(report.violations[0].boundary, category)
                self.assertEqual(report.violations[0].forbidden_module, target)

    def test_transitive_dependency_path_prevents_boundary_bypass(self) -> None:
        with self._repository(
            {
                "sample/core.py": "from .neutral import value",
                "sample/neutral.py": "from .db import connection\nvalue = connection",
                "sample/db.py": "connection = object()",
            }
        ) as repository:
            report = check_architecture(repository)
        self.assertFalse(report.valid)
        self.assertEqual(
            report.violations[0].path,
            ("sample.core", "sample.neutral", "sample.db"),
        )

    def test_literal_dynamic_imports_do_not_evade_policy(self) -> None:
        with self._repository(
            {
                "sample/core.py": """
                    import importlib
                    module = importlib.import_module("sample.web")
                """,
            }
        ) as repository:
            report = check_architecture(repository)
        self.assertFalse(report.valid)
        self.assertEqual(report.violations[0].forbidden_module, "sample.web")

    def test_policy_is_strict_and_requires_existing_core_modules(self) -> None:
        with self._repository({}) as repository:
            root = Path(repository)
            policy = root / "architecture-boundaries.toml"
            policy.write_text(
                policy.read_text(encoding="utf-8").replace(
                    'root_package = "sample"',
                    'root_package = "sample"\nunknown = true',
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "unknown or missing"):
                load_policy(policy)

        with self._repository({}) as repository:
            policy = Path(repository) / "architecture-boundaries.toml"
            policy.write_text(
                policy.read_text(encoding="utf-8").replace(
                    'modules = ["sample.core"]',
                    'modules = ["sample.missing"]',
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "do not exist"):
                check_architecture(repository)

        with self._repository({}) as repository:
            policy = Path(repository) / "architecture-boundaries.toml"
            policy.write_text(
                policy.read_text(encoding="utf-8").replace(
                    'internal = ["sample.db"]',
                    'internal = ["sample.missing_database"]',
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "forbidden internal"):
                check_architecture(repository)

    def test_cli_is_machine_readable_and_fail_closed(self) -> None:
        output = io.StringIO()
        with redirect_stdout(output):
            code = main(["check"])
        self.assertEqual(code, 0)
        self.assertTrue(json.loads(output.getvalue())["valid"])

        with self._repository({"sample/core.py": "import sqlite3"}) as repository:
            output = io.StringIO()
            with redirect_stdout(output):
                code = main(["--repository", repository, "check"])
        self.assertEqual(code, 1)
        payload = json.loads(output.getvalue())
        self.assertFalse(payload["valid"])
        self.assertEqual(
            payload["violations"][0]["boundary"],
            "database_implementation",
        )


if __name__ == "__main__":
    unittest.main()
