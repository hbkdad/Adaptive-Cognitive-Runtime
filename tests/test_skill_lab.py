from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from acr_runtime.config import Settings
from acr_runtime.service import AdaptiveRuntime
from acr_runtime.skill_lab import SkillLabReader


class SkillLabReaderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.runtime = AdaptiveRuntime(settings=Settings.from_env(
            database=Path(self.temp.name) / "acr.db"
        ))
        self.first = self.runtime.register_skill(
            "diagnostics", "Check schema then query.", version="1.0.0",
            description="Database diagnostics",
        )
        self.second = self.runtime.register_skill(
            "diagnostics", "Check schema, FTS, then query.", version="2.0.0",
            description="Database diagnostics v2",
        )
        self.reader = SkillLabReader(self.runtime)

    def tearDown(self):
        self.runtime.close()
        self.temp.cleanup()

    def test_list_reports_real_rates_without_fabricating_unused_success(self):
        result = self.reader.list()
        self.assertEqual(result["count"], 2)
        self.assertTrue(all(item["success_rate"] is None for item in result["items"]))

    def test_detail_exposes_instructions_tests_permissions_and_history_not_path(self):
        detail = self.reader.detail(self.first)
        self.assertEqual(detail["instructions"], "Check schema then query.")
        self.assertEqual(detail["runtime_authority_status"], "separate_not_inferred")
        self.assertEqual(detail["generated_change_visibility"], "explicit")
        self.assertNotIn("package_path", detail)
        self.assertIn("history", detail)
        self.assertIn("tests", detail)

    def test_compare_shows_every_instruction_and_manifest_change(self):
        compared = self.reader.compare(self.first, self.second)
        rendered = "\n".join(compared["instruction_diff"])
        self.assertIn("-Check schema then query.", rendered)
        self.assertIn("+Check schema, FTS, then query.", rendered)
        self.assertFalse(compared["automatic_changes_hidden"])
        self.assertFalse(compared["diff_truncated"])

    def test_cross_family_comparison_fails_closed(self):
        other = self.runtime.register_skill(
            "unrelated", "Do something else.", version="1.0.0"
        )
        with self.assertRaisesRegex(ValueError, "one exact skill family"):
            self.reader.compare(self.first, other)


if __name__ == "__main__":
    unittest.main()
