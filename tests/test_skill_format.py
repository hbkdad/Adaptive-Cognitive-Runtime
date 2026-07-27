from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from acr_runtime import SkillFormatError, SkillPackageLoader, SkillStatus
from acr_runtime.cli import main


class SkillFormatTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name) / "sqlite-diagnostics"
        self.root.mkdir()
        for name in ("examples", "tests", "scripts", "assets"):
            (self.root / name).mkdir()
        self.manifest = {
            "id": "sqlite-diagnostics",
            "name": "SQLite diagnostics",
            "version": "1.0.0",
            "description": "Focused, local SQLite checks.",
            "task_classes": ["database-diagnostics"],
            "inputs": {"database": "local SQLite path"},
            "outputs": {"report": "evidence-backed diagnostic summary"},
            "dependencies": [],
            "permissions": ["filesystem:read"],
            "tools": ["sqlite3"],
            "models": ["any"],
            "token_estimate": 40,
            "applicability": ["SQLite schema or FTS diagnosis"],
            "contraindications": ["remote database mutation"],
            "verification": ["python -m unittest tests.test_runtime"],
            "author": "ACR contributors",
            "origin": "local",
            "created_at": "2026-07-27T00:00:00Z",
            "updated_at": "2026-07-27T00:00:00Z",
            "status": "experimental",
            "reliability": 0.5,
        }
        self._write()

    def tearDown(self) -> None:
        self.directory.cleanup()

    def _write(self) -> None:
        (self.root / "SKILL.yaml").write_text(
            json.dumps(self.manifest, indent=2), encoding="utf-8"
        )
        (self.root / "instructions.md").write_text(
            "Inspect only the requested SQLite database and report evidence.",
            encoding="utf-8",
        )
        (self.root / "history.jsonl").write_text(
            json.dumps({"event": "created", "version": "1.0.0"}) + "\n",
            encoding="utf-8",
        )

    def test_valid_package_has_composable_interfaces_and_stable_hash(self):
        loader = SkillPackageLoader()
        first = loader.load(self.root)
        second = loader.load(self.root)
        self.assertEqual(first.manifest.status, SkillStatus.EXPERIMENTAL)
        self.assertEqual(first.manifest.inputs["database"], "local SQLite path")
        self.assertEqual(first.manifest.outputs["report"], "evidence-backed diagnostic summary")
        self.assertEqual(first.content_hash, second.content_hash)
        self.assertEqual(len(first.content_hash), 64)

    def test_all_v1_statuses_are_supported(self):
        loader = SkillPackageLoader()
        for status in SkillStatus:
            with self.subTest(status=status.value):
                self.manifest["status"] = status.value
                self._write()
                self.assertEqual(loader.load(self.root).manifest.status, status)

    def test_manifest_is_strict_and_semver_dependencies_are_validated(self):
        loader = SkillPackageLoader()
        cases = (
            ("version", "01.0.0"),
            ("dependencies", ["sqlite-diagnostics@1.0.0"]),
            ("status", "trusted"),
        )
        for field, value in cases:
            with self.subTest(field=field):
                original = self.manifest[field]
                self.manifest[field] = value
                self._write()
                with self.assertRaises(SkillFormatError):
                    loader.load(self.root)
                self.manifest[field] = original
        self.manifest["unexpected"] = True
        self._write()
        with self.assertRaises(SkillFormatError):
            loader.load(self.root)

    def test_materially_inaccurate_token_estimate_fails_closed(self):
        self.manifest["token_estimate"] = 3_000
        self._write()
        with self.assertRaises(SkillFormatError):
            SkillPackageLoader().load(self.root)

    def test_required_layout_and_history_fail_closed(self):
        loader = SkillPackageLoader()
        (self.root / "assets").rmdir()
        with self.assertRaises(SkillFormatError):
            loader.load(self.root)
        (self.root / "assets").mkdir()
        (self.root / "history.jsonl").write_text("not-json\n", encoding="utf-8")
        with self.assertRaises(SkillFormatError):
            loader.load(self.root)

    def test_secret_material_in_any_text_file_fails_closed(self):
        token = "sk-" + "A1b2C3d4E5f6G7h8I9j0K1"
        (self.root / "scripts" / "credential.txt").write_text(
            f"api_key={token}\n", encoding="utf-8"
        )
        with self.assertRaisesRegex(
            SkillFormatError, "contains secret material"
        ):
            SkillPackageLoader().load(self.root)

    def test_cli_validates_without_activating_or_executing_skill(self):
        database = Path(self.directory.name) / "acr.db"
        output = io.StringIO()
        with redirect_stdout(output):
            result = main(
                [
                    "--db",
                    str(database),
                    "skills",
                    "validate",
                    str(self.root),
                ]
            )
        self.assertEqual(result, 0)
        payload = json.loads(output.getvalue())
        self.assertEqual(payload["id"], "sqlite-diagnostics")
        self.assertEqual(payload["status"], "experimental")


if __name__ == "__main__":
    unittest.main()
