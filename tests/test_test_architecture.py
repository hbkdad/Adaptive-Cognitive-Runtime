from __future__ import annotations

import io
import json
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from acr_runtime.test_architecture import (
    TEST_TIERS,
    TestManifest,
    build_suite,
    load_manifest,
    main,
)


def count_tests(suite: unittest.TestSuite) -> int:
    return suite.countTestCases()


class TestArchitectureTests(unittest.TestCase):
    def test_every_default_test_is_classified_exactly_once(self) -> None:
        manifest = load_manifest()
        report = manifest.validate()
        self.assertTrue(report["valid"])
        self.assertEqual(set(report["tiers"]), set(TEST_TIERS))
        self.assertEqual(
            report["deterministic_files"],
            len(list(manifest.tests_dir.glob("test_*.py"))),
        )
        self.assertFalse(report["probabilistic_default_discovery"])
        self.assertFalse(report["paid_api_required"])

    def test_each_tier_is_runnable_and_union_matches_default_suite(self) -> None:
        manifest = load_manifest()
        tier_counts = {
            tier: count_tests(build_suite(manifest, tier))
            for tier in TEST_TIERS
        }
        self.assertTrue(all(count > 0 for count in tier_counts.values()))
        deterministic = count_tests(
            build_suite(manifest, "deterministic")
        )
        default = unittest.TestLoader().discover(
            str(manifest.tests_dir), pattern="test_*.py"
        ).countTestCases()
        self.assertEqual(sum(tier_counts.values()), deterministic)
        self.assertEqual(deterministic, default)

    def test_manifest_rejects_duplicates_gaps_and_invalid_paths(self) -> None:
        tests_dir = Path(__file__).parent
        deterministic = {tier: () for tier in TEST_TIERS}
        deterministic["unit"] = (
            "test_test_architecture.py",
            "test_test_architecture.py",
            "../test_escape.py",
        )
        manifest = TestManifest(
            path=tests_dir / "suites.json",
            tests_dir=tests_dir,
            deterministic=deterministic,
            probabilistic={
                "default_discovery": False,
                "paid_api_required": False,
            },
        )
        with self.assertRaisesRegex(ValueError, "duplicates"):
            manifest.validate()

    def test_cli_validate_and_list_are_machine_readable(self) -> None:
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(main(["validate"]), 0)
        self.assertTrue(json.loads(output.getvalue())["valid"])

        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(main(["list"]), 0)
        payload = json.loads(output.getvalue())
        self.assertEqual(set(payload["deterministic"]), set(TEST_TIERS))
        self.assertEqual(
            payload["probabilistic"]["directory"],
            "quality_benchmarks",
        )


if __name__ == "__main__":
    unittest.main()
