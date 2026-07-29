from __future__ import annotations

import argparse
import json
import sys
import unittest
from dataclasses import dataclass
from pathlib import Path


TEST_TIERS = (
    "unit",
    "integration",
    "scenario",
    "benchmark",
    "security",
    "regression",
)


@dataclass(frozen=True)
class TestManifest:
    path: Path
    tests_dir: Path
    deterministic: dict[str, tuple[str, ...]]
    probabilistic: dict[str, object]

    @property
    def deterministic_files(self) -> tuple[str, ...]:
        return tuple(
            filename
            for tier in TEST_TIERS
            for filename in self.deterministic[tier]
        )

    def validate(self) -> dict[str, object]:
        files = self.deterministic_files
        duplicates = sorted(
            {filename for filename in files if files.count(filename) > 1}
        )
        actual = {
            path.name
            for path in self.tests_dir.glob("test_*.py")
            if path.is_file()
        }
        declared = set(files)
        missing = sorted(actual - declared)
        nonexistent = sorted(declared - actual)
        invalid = sorted(
            filename
            for filename in files
            if Path(filename).name != filename
            or not filename.startswith("test_")
            or not filename.endswith(".py")
        )
        errors = {
            "duplicates": duplicates,
            "unclassified": missing,
            "nonexistent": nonexistent,
            "invalid": invalid,
        }
        if any(errors.values()):
            raise ValueError(
                "Invalid deterministic test manifest: "
                + json.dumps(errors, sort_keys=True)
            )
        return {
            "valid": True,
            "tiers": {
                tier: len(self.deterministic[tier])
                for tier in TEST_TIERS
            },
            "deterministic_files": len(files),
            "probabilistic_default_discovery": bool(
                self.probabilistic["default_discovery"]
            ),
            "paid_api_required": bool(
                self.probabilistic["paid_api_required"]
            ),
        }


def load_manifest(path: str | Path | None = None) -> TestManifest:
    repository = Path(__file__).resolve().parents[1]
    source = (
        Path(path)
        if path is not None
        else repository / "tests" / "suites.json"
    )
    payload = json.loads(source.read_text(encoding="utf-8"))
    if set(payload) != {
        "schema_version",
        "deterministic",
        "probabilistic",
    } or payload["schema_version"] != 1:
        raise ValueError("Unsupported test manifest schema")
    deterministic = payload["deterministic"]
    if (
        not isinstance(deterministic, dict)
        or tuple(deterministic) != TEST_TIERS
        or any(
            not isinstance(deterministic[tier], list)
            or not all(
                isinstance(item, str) for item in deterministic[tier]
            )
            for tier in TEST_TIERS
        )
    ):
        raise ValueError("Deterministic test tiers are invalid")
    probabilistic = payload["probabilistic"]
    expected_probabilistic = {
        "default_discovery",
        "paid_api_required",
        "minimum_repetitions",
        "directory",
    }
    if (
        not isinstance(probabilistic, dict)
        or set(probabilistic) != expected_probabilistic
        or probabilistic["default_discovery"] is not False
        or probabilistic["paid_api_required"] is not False
        or type(probabilistic["minimum_repetitions"]) is not int
        or probabilistic["minimum_repetitions"] < 3
        or probabilistic["directory"] != "quality_benchmarks"
    ):
        raise ValueError("Probabilistic test boundary is invalid")
    manifest = TestManifest(
        path=source,
        tests_dir=source.parent,
        deterministic={
            tier: tuple(deterministic[tier]) for tier in TEST_TIERS
        },
        probabilistic=dict(probabilistic),
    )
    manifest.validate()
    return manifest


def build_suite(
    manifest: TestManifest, tier: str
) -> unittest.TestSuite:
    if tier not in (*TEST_TIERS, "deterministic"):
        raise ValueError(f"Unknown deterministic test tier: {tier}")
    filenames = (
        manifest.deterministic_files
        if tier == "deterministic"
        else manifest.deterministic[tier]
    )
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    for filename in filenames:
        suite.addTests(
            loader.discover(
                str(manifest.tests_dir),
                pattern=filename,
            )
        )
    if loader.errors:
        raise RuntimeError(
            "Test discovery failed: " + " | ".join(loader.errors)
        )
    return suite


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="acr-test",
        description="Run deterministic ACR test tiers without paid APIs.",
    )
    parser.add_argument("--manifest")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("validate")
    sub.add_parser("list")
    run = sub.add_parser("run")
    run.add_argument("tier", choices=(*TEST_TIERS, "deterministic"))
    run.add_argument("--quiet", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    manifest = load_manifest(args.manifest)
    if args.command == "validate":
        print(json.dumps(manifest.validate(), indent=2))
        return 0
    if args.command == "list":
        print(
            json.dumps(
                {
                    "deterministic": {
                        tier: list(manifest.deterministic[tier])
                        for tier in TEST_TIERS
                    },
                    "probabilistic": manifest.probabilistic,
                },
                indent=2,
            )
        )
        return 0
    suite = build_suite(manifest, args.tier)
    result = unittest.TextTestRunner(
        verbosity=1 if args.quiet else 2,
        stream=sys.stderr,
    ).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
