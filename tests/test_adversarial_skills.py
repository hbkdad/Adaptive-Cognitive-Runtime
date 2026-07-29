from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from acr_runtime import AdaptiveRuntime
from acr_runtime.skill_validator import (
    ADVERSARIAL_SKILL_CASES,
    DockerSandboxAdapter,
    SkillValidator,
    ValidationEvidence,
)


class RecordingSandbox:
    def __init__(self) -> None:
        self.stages: list[str] = []

    def run(self, package, *, stage, cases):
        self.stages.append(stage)
        return ValidationEvidence("passed", 1.0, {"stage": stage})


class AdversarialSkillTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.runtime = AdaptiveRuntime(root / "acr.db")
        source = (
            Path(__file__).parents[1]
            / "examples"
            / "skill-v1"
            / "sqlite-diagnostics"
        )
        self.package_path = root / "generated-skill"
        shutil.copytree(source, self.package_path)
        self.package = self.runtime.validate_skill_package(self.package_path)

    def tearDown(self) -> None:
        self.runtime.close()
        self.temporary.cleanup()

    def test_fixed_probe_attempts_all_six_attacks_inside_locked_sandbox(self):
        adapter = DockerSandboxAdapter(image="local/python-test:1")
        image_id = "sha256:" + ("d" * 64)
        inspected = Mock(returncode=0, stdout=image_id)
        completed = Mock(returncode=0)

        with patch.dict(
            os.environ,
            {
                "ACR_TEST_SECRET": "must-not-cross",
                "ACR_PERMISSION_GRANT": "must-not-cross",
            },
            clear=False,
        ):
            with patch(
                "acr_runtime.skill_validator.subprocess.run",
                side_effect=(inspected, completed),
            ) as run:
                evidence = adapter.run(
                    self.package,
                    stage="adversarial_tests",
                    cases=ADVERSARIAL_SKILL_CASES,
                )

        command = run.call_args_list[1].args[0]
        container_environment = command[command.index("-i") + 1 :]
        host_environment = run.call_args_list[1].kwargs["env"]
        probe = command[-1]
        self.assertEqual(evidence.outcome, "passed")
        self.assertEqual(
            evidence.details["adversarial_cases"],
            {case: "prevented" for case in ADVERSARIAL_SKILL_CASES},
        )
        self.assertEqual(
            evidence.details["adversarial_boundary_test"], "passed"
        )
        self.assertIn("/run/secrets", probe)
        self.assertIn("except OSError", probe)
        self.assertIn("/etc/.acr-unrelated-write", probe)
        self.assertIn("CapEff", probe)
        self.assertIn("/skill/tests/.acr-disable-tests", probe)
        self.assertIn("/skill/.acr-hide-telemetry", probe)
        self.assertIn("192.0.2.1", probe)
        self.assertIn("--network", command)
        self.assertIn("none", command)
        self.assertIn("--read-only", command)
        self.assertIn("--cap-drop", command)
        self.assertIn("ALL", command)
        self.assertFalse(any(
            item.startswith("type=bind") and "readonly" not in item
            for item in command
        ))
        self.assertNotIn("ACR_TEST_SECRET", host_environment)
        self.assertNotIn("ACR_PERMISSION_GRANT", host_environment)
        self.assertFalse(any(
            "ACR_TEST_SECRET" in item or "ACR_PERMISSION_GRANT" in item
            for item in container_environment
        ))
        self.assertFalse(run.call_args_list[1].kwargs["shell"])

    def test_adversarial_case_set_is_closed_and_cannot_inject_commands(self):
        adapter = DockerSandboxAdapter(image="local/python-test:1")

        with patch(
            "acr_runtime.skill_validator.subprocess.run"
        ) as run:
            evidence = adapter.run(
                self.package,
                stage="adversarial_tests",
                cases=(*ADVERSARIAL_SKILL_CASES, "python -c 'escape'"),
            )

        self.assertEqual(evidence.outcome, "blocked")
        self.assertEqual(
            evidence.details["reason"], "invalid_adversarial_case_set"
        )
        run.assert_not_called()

    def test_failed_probe_blocks_validation_evidence(self):
        adapter = DockerSandboxAdapter(image="local/python-test:1")
        inspected = Mock(
            returncode=0, stdout="sha256:" + ("e" * 64)
        )
        prevented_boundary_failed = Mock(returncode=1)

        with patch(
            "acr_runtime.skill_validator.subprocess.run",
            side_effect=(inspected, prevented_boundary_failed),
        ):
            evidence = adapter.run(
                self.package,
                stage="adversarial_tests",
                cases=ADVERSARIAL_SKILL_CASES,
            )

        self.assertEqual(evidence.outcome, "failed")
        self.assertEqual(evidence.score, 0.0)
        self.assertEqual(
            evidence.details["adversarial_boundary_test"], "failed"
        )
        self.assertEqual(
            set(evidence.details["adversarial_cases"].values()),
            {"not_proven"},
        )

    def test_unauthorized_manifest_permission_stops_before_execution(self):
        manifest_path = self.package_path / "SKILL.yaml"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["permissions"] = [
            "filesystem:read",
            "network:write",
            "credential:read",
        ]
        manifest_path.write_text(
            json.dumps(manifest, indent=2), encoding="utf-8"
        )
        skill_id = str(
            self.runtime.admit_skill_package(self.package_path)["id"]
        )
        sandbox = RecordingSandbox()
        self.runtime.skill_validator = SkillValidator(
            self.runtime.db.connection,
            self.runtime.skill_registry,
            loader=self.runtime.skill_packages,
            sandbox=sandbox,
        )

        run = self.runtime.validate_skill_candidate(skill_id)

        permission = next(
            result for result in run.results
            if result.stage == "permission_analysis"
        )
        self.assertEqual(permission.evidence.outcome, "failed")
        self.assertEqual(
            permission.evidence.details["denied"],
            ["credential:read", "network:write"],
        )
        self.assertEqual(sandbox.stages, [])
        self.assertEqual(run.status, "failed")
        with self.assertRaises(ValueError):
            self.runtime.promote_skill_validation(run.id)

    def test_generated_verification_cannot_smuggle_shell_syntax(self):
        adapter = DockerSandboxAdapter(image="local/python-test:1")

        with patch(
            "acr_runtime.skill_validator.subprocess.run"
        ) as run:
            evidence = adapter.run(
                self.package,
                stage="unit_tests",
                cases=("python -m unittest; powershell disable-tests",),
            )

        self.assertEqual(evidence.outcome, "blocked")
        self.assertIn("shell syntax", evidence.details["reason"])
        run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
