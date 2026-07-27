from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from acr_runtime import (
    AdaptiveRuntime,
    DockerSandboxAdapter,
    SandboxPolicy,
    SkillValidator,
    ValidationEvidence,
    ValidationPolicy,
)


class PassingSandbox:
    def __init__(self) -> None:
        self.stages: list[str] = []

    def run(self, package, *, stage, cases):
        self.stages.append(stage)
        return ValidationEvidence(
            "passed", 1.0, {"stage": stage, "case_count": len(cases)}
        )


class PassingEvaluator:
    def review(self, package):
        return ValidationEvidence(
            "passed", 0.95, {"reviewer": "deterministic-test"}
        )


class BenchmarkEvidence:
    def __init__(
        self,
        *,
        candidate_quality: float = 0.95,
        incumbent_quality: float = 0.90,
        candidate_cost: float = 0.09,
        incumbent_cost: float = 0.10,
    ) -> None:
        self.values = {
            "candidate_quality": candidate_quality,
            "incumbent_quality": incumbent_quality,
            "candidate_cost": candidate_cost,
            "incumbent_cost": incumbent_cost,
        }

    def compare(self, package, *, incumbent_skill_id):
        return ValidationEvidence("passed", 0.95, dict(self.values))


class SkillValidatorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        root = Path(self.directory.name)
        self.runtime = AdaptiveRuntime(root / "acr.db")
        source = (
            Path(__file__).parents[1]
            / "examples"
            / "skill-v1"
            / "sqlite-diagnostics"
        )
        self.package = root / "sqlite-diagnostics"
        shutil.copytree(source, self.package)

    def tearDown(self) -> None:
        self.runtime.close()
        self.directory.cleanup()

    def admit(self) -> str:
        return str(self.runtime.admit_skill_package(self.package)["id"])

    def passing_validator(
        self, *, benchmark=None, policy: ValidationPolicy | None = None
    ) -> SkillValidator:
        return SkillValidator(
            self.runtime.db.connection,
            self.runtime.skill_registry,
            loader=self.runtime.skill_packages,
            sandbox=PassingSandbox(),
            evaluator=PassingEvaluator(),
            benchmark=benchmark or BenchmarkEvidence(),
            policy=policy,
        )

    def test_default_pipeline_fails_closed_and_retains_all_stages(self):
        skill_id = self.admit()

        run = self.runtime.validate_skill_candidate(skill_id)

        self.assertEqual(run.status, "blocked")
        self.assertEqual(len(run.results), 10)
        self.assertEqual(
            [item.stage for item in run.results],
            [
                "syntax_validation",
                "dependency_validation",
                "static_security_scan",
                "permission_analysis",
                "sandbox_execution",
                "unit_tests",
                "scenario_tests",
                "adversarial_tests",
                "evaluator_review",
                "benchmark_comparison",
            ],
        )
        self.assertTrue(
            all(
                item.evidence.outcome == "passed"
                for item in run.results[:4]
            )
        )
        self.assertTrue(
            all(
                item.evidence.outcome == "blocked"
                for item in run.results[4:]
            )
        )
        with self.assertRaises(ValueError):
            self.runtime.activate_skill(skill_id)
        with self.assertRaises(ValueError):
            self.runtime.promote_skill_validation(run.id)

    def test_fully_passing_pipeline_can_be_explicitly_promoted(self):
        skill_id = self.admit()
        sandbox = PassingSandbox()
        self.runtime.skill_validator = SkillValidator(
            self.runtime.db.connection,
            self.runtime.skill_registry,
            loader=self.runtime.skill_packages,
            sandbox=sandbox,
            evaluator=PassingEvaluator(),
            benchmark=BenchmarkEvidence(),
        )

        run = self.runtime.validate_skill_candidate(skill_id)
        promoted = self.runtime.promote_skill_validation(run.id)

        self.assertEqual(run.status, "passed")
        self.assertEqual(promoted.status, "promoted")
        self.assertEqual(
            self.runtime.inspect_skill(skill_id)["lifecycle_status"], "active"
        )
        self.assertEqual(
            sandbox.stages,
            [
                "sandbox_execution",
                "unit_tests",
                "scenario_tests",
                "adversarial_tests",
            ],
        )

    def test_quality_or_cost_regression_blocks_promotion(self):
        skill_id = self.admit()
        self.runtime.skill_validator = self.passing_validator(
            benchmark=BenchmarkEvidence(
                candidate_quality=0.80,
                incumbent_quality=0.90,
                candidate_cost=0.20,
                incumbent_cost=0.10,
            )
        )

        run = self.runtime.validate_skill_candidate(skill_id)

        self.assertEqual(run.status, "failed")
        benchmark = run.results[-1]
        self.assertEqual(benchmark.stage, "benchmark_comparison")
        self.assertEqual(benchmark.evidence.outcome, "failed")
        self.assertTrue(benchmark.evidence.details["quality_regression"])
        self.assertTrue(benchmark.evidence.details["cost_regression"])
        with self.assertRaises(ValueError):
            self.runtime.promote_skill_validation(run.id)

    def test_unapproved_permission_stops_before_execution(self):
        manifest_path = self.package / "SKILL.yaml"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["permissions"] = ["filesystem:write"]
        manifest_path.write_text(
            json.dumps(manifest, indent=2), encoding="utf-8"
        )
        skill_id = self.admit()
        sandbox = PassingSandbox()
        self.runtime.skill_validator = SkillValidator(
            self.runtime.db.connection,
            self.runtime.skill_registry,
            loader=self.runtime.skill_packages,
            sandbox=sandbox,
            evaluator=PassingEvaluator(),
            benchmark=BenchmarkEvidence(),
        )

        run = self.runtime.validate_skill_candidate(skill_id)

        self.assertEqual(run.status, "failed")
        self.assertEqual(run.results[3].evidence.outcome, "failed")
        self.assertEqual(sandbox.stages, [])

    def test_package_change_after_validation_prevents_promotion(self):
        skill_id = self.admit()
        self.runtime.skill_validator = self.passing_validator()
        run = self.runtime.validate_skill_candidate(skill_id)
        self.assertEqual(run.status, "passed")
        instructions = self.package / "instructions.md"
        instructions.write_text(
            instructions.read_text(encoding="utf-8") + "\nChanged.",
            encoding="utf-8",
        )

        with self.assertRaises(ValueError):
            self.runtime.promote_skill_validation(run.id)

    def test_docker_adapter_uses_locked_down_non_shell_command(self):
        package = self.runtime.validate_skill_package(self.package)
        adapter = DockerSandboxAdapter(image="local/python-test:1")
        image_id = "sha256:" + ("a" * 64)
        inspected = Mock(returncode=0, stdout=image_id)
        completed = Mock(returncode=0)

        with patch.dict(
            os.environ, {"ACR_TEST_SECRET": "must-not-cross"}, clear=False
        ):
            with patch(
                "acr_runtime.skill_validator.subprocess.run",
                side_effect=(inspected, completed),
            ) as run:
                evidence = adapter.run(
                    package,
                    stage="sandbox_execution",
                    cases=("package_smoke_test",),
                )

        command = run.call_args_list[1].args[0]
        host_environment = run.call_args_list[1].kwargs["env"]
        self.assertEqual(evidence.outcome, "passed")
        self.assertIn("--network", command)
        self.assertIn("none", command)
        self.assertIn("--read-only", command)
        self.assertIn("--cap-drop", command)
        self.assertIn("ALL", command)
        self.assertIn("--pull", command)
        self.assertIn("never", command)
        self.assertIn("--security-opt", command)
        self.assertIn("no-new-privileges=true", command)
        self.assertIn("seccomp=builtin", command)
        self.assertNotIn("--pid", command)
        self.assertIn("--ipc", command)
        self.assertIn("--cgroupns", command)
        self.assertIn("--memory-swap", command)
        self.assertIn("--ulimit", command)
        self.assertIn("--user", command)
        self.assertIn("--entrypoint", command)
        self.assertIn("/usr/bin/env", command)
        self.assertIn("-i", command)
        self.assertIn(image_id, command)
        self.assertNotIn("local/python-test:1", command)
        self.assertNotIn("ACR_TEST_SECRET", host_environment)
        self.assertFalse(run.call_args_list[1].kwargs["shell"])
        self.assertTrue(any(
            item.startswith("/workspace:rw,noexec,nosuid,nodev,size=64m")
            for item in command
        ))
        self.assertFalse(any(
            item.startswith("type=bind")
            and item.endswith("dst=/workspace")
            for item in command
        ))
        self.assertEqual(
            evidence.details["environment"]["inherit_host"], False
        )
        self.assertEqual(
            evidence.details["temporary_workspace"], "bounded_tmpfs_deleted"
        )
        self.assertEqual(evidence.details["writable_host_mounts"], 0)
        self.assertEqual(
            evidence.details["image_id"], image_id
        )
        self.assertEqual(len(evidence.details["command_hashes"]), 1)

    def test_docker_timeout_force_removes_named_container(self):
        package = self.runtime.validate_skill_package(self.package)
        adapter = DockerSandboxAdapter(
            image="local/python-test:1",
            policy=SandboxPolicy(timeout_seconds=5),
        )
        inspected = Mock(
            returncode=0, stdout="sha256:" + ("b" * 64)
        )
        timeout = subprocess.TimeoutExpired("docker", 5)
        cleanup = Mock(returncode=0)

        with patch(
            "acr_runtime.skill_validator.subprocess.run",
            side_effect=(inspected, timeout, cleanup),
        ) as run:
            evidence = adapter.run(
                package,
                stage="sandbox_execution",
                cases=("package_smoke_test",),
            )

        self.assertEqual(evidence.outcome, "failed")
        self.assertEqual(evidence.details["reason"], "sandbox_timeout")
        self.assertEqual(evidence.details["forced_cleanup_exit_code"], 0)
        run_command = run.call_args_list[1].args[0]
        cleanup_command = run.call_args_list[2].args[0]
        container_name = run_command[run_command.index("--name") + 1]
        self.assertEqual(
            cleanup_command,
            ["docker", "rm", "--force", container_name],
        )

    def test_sandbox_policy_rejects_network_and_unbounded_resources(self):
        with self.assertRaises(ValueError):
            SandboxPolicy(network="bridge")
        with self.assertRaises(ValueError):
            SandboxPolicy(timeout_seconds=0)
        with self.assertRaises(ValueError):
            SandboxPolicy(memory_mb=32)
        with self.assertRaises(ValueError):
            SandboxPolicy(pids_limit=1)

    def test_missing_preinstalled_image_fails_before_generated_code(self):
        package = self.runtime.validate_skill_package(self.package)
        adapter = DockerSandboxAdapter(image="local/missing:1")
        missing = Mock(returncode=1, stdout="")

        with patch(
            "acr_runtime.skill_validator.subprocess.run",
            return_value=missing,
        ) as run:
            evidence = adapter.run(
                package,
                stage="sandbox_execution",
                cases=("package_smoke_test",),
            )

        self.assertEqual(evidence.outcome, "blocked")
        self.assertEqual(
            evidence.details["reason"], "preinstalled_image_unavailable"
        )
        self.assertEqual(run.call_count, 1)


if __name__ == "__main__":
    unittest.main()
