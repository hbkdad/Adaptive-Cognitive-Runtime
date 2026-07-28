from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import sqlite3
import subprocess
import time
import uuid
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Protocol

from .memory import utc_now
from .skill_format import SkillPackage, SkillPackageLoader
from .skill_registry import SkillRegistry
from .skill_coevolution import MemorySkillCoevolution
from .write_controller import content_risk_flags


STAGES = (
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
)


@dataclass(frozen=True)
class ValidationPolicy:
    allowed_permissions: tuple[str, ...] = ("filesystem:read",)
    minimum_evaluator_score: float = 0.80
    maximum_cost_regression: float = 0.10

    def __post_init__(self) -> None:
        if not 0 <= self.minimum_evaluator_score <= 1:
            raise ValueError("minimum_evaluator_score must be 0..1")
        if self.maximum_cost_regression < 0:
            raise ValueError("maximum_cost_regression cannot be negative")
        if any(not item.strip() for item in self.allowed_permissions):
            raise ValueError("allowed_permissions cannot contain empty values")


@dataclass(frozen=True)
class ValidationEvidence:
    outcome: str
    score: float | None
    details: dict[str, object]
    token_cost: int = 0
    estimated_cost: float = 0
    latency_ms: int = 0

    def __post_init__(self) -> None:
        if self.outcome not in {"passed", "failed", "blocked", "error"}:
            raise ValueError("invalid validation outcome")
        if self.score is not None and not 0 <= self.score <= 1:
            raise ValueError("validation score must be 0..1")
        if (
            self.token_cost < 0
            or self.estimated_cost < 0
            or self.latency_ms < 0
        ):
            raise ValueError("validation resource metrics cannot be negative")
        encoded = json.dumps(self.details, sort_keys=True)
        if len(encoded.encode("utf-8")) > 10_000:
            raise ValueError("validation details exceed 10 KB")


@dataclass(frozen=True)
class ValidationStageResult:
    order: int
    stage: str
    evidence: ValidationEvidence
    created_at: str


@dataclass(frozen=True)
class SandboxPolicy:
    """Closed, bounded isolation profile for generated executable skills."""

    network: str = "none"
    timeout_seconds: int = 60
    memory_mb: int = 256
    cpu_count: float = 0.5
    pids_limit: int = 64
    open_files_limit: int = 128
    tmpfs_mb: int = 64
    workspace_mb: int = 64
    run_as_user: str = "65532:65532"

    def __post_init__(self) -> None:
        if self.network != "none":
            raise ValueError("Generated-skill sandbox network must be none")
        if not 1 <= self.timeout_seconds <= 600:
            raise ValueError("Sandbox timeout must be 1..600 seconds")
        if not 64 <= self.memory_mb <= 4_096:
            raise ValueError("Sandbox memory must be 64..4096 MB")
        if not 0.1 <= self.cpu_count <= 4:
            raise ValueError("Sandbox CPU count must be 0.1..4")
        if not 8 <= self.pids_limit <= 256:
            raise ValueError("Sandbox PID limit must be 8..256")
        if not 32 <= self.open_files_limit <= 1_024:
            raise ValueError("Sandbox open-file limit must be 32..1024")
        if not 8 <= self.tmpfs_mb <= 512:
            raise ValueError("Sandbox tmpfs must be 8..512 MB")
        if not 8 <= self.workspace_mb <= 512:
            raise ValueError("Sandbox workspace must be 8..512 MB")
        if not re.fullmatch(r"[1-9][0-9]{0,9}:[1-9][0-9]{0,9}", self.run_as_user):
            raise ValueError("Sandbox user must be a numeric uid:gid")


@dataclass(frozen=True)
class SkillValidationRun:
    id: str
    skill_id: str
    package_hash: str
    status: str
    incumbent_skill_id: str | None
    policy: ValidationPolicy
    results: tuple[ValidationStageResult, ...]
    created_at: str
    completed_at: str | None = None
    promoted_at: str | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "skill_id": self.skill_id,
            "package_hash": self.package_hash,
            "status": self.status,
            "incumbent_skill_id": self.incumbent_skill_id,
            "policy": asdict(self.policy),
            "results": [
                {
                    "order": item.order,
                    "stage": item.stage,
                    **asdict(item.evidence),
                    "created_at": item.created_at,
                }
                for item in self.results
            ],
            "created_at": self.created_at,
            "completed_at": self.completed_at,
            "promoted_at": self.promoted_at,
        }


class SandboxAdapter(Protocol):
    def run(
        self,
        package: SkillPackage,
        *,
        stage: str,
        cases: tuple[str, ...],
    ) -> ValidationEvidence: ...


class EvaluatorAdapter(Protocol):
    def review(self, package: SkillPackage) -> ValidationEvidence: ...


class BenchmarkAdapter(Protocol):
    def compare(
        self,
        package: SkillPackage,
        *,
        incumbent_skill_id: str | None,
    ) -> ValidationEvidence: ...


class UnavailableSandbox:
    def run(
        self,
        package: SkillPackage,
        *,
        stage: str,
        cases: tuple[str, ...],
    ) -> ValidationEvidence:
        return ValidationEvidence(
            "blocked",
            None,
            {
                "reason": "real_sandbox_adapter_required",
                "stage": stage,
                "case_count": len(cases),
            },
        )


class UnavailableEvaluator:
    def review(self, package: SkillPackage) -> ValidationEvidence:
        return ValidationEvidence(
            "blocked", None, {"reason": "evaluator_adapter_required"}
        )


class UnavailableBenchmark:
    def compare(
        self,
        package: SkillPackage,
        *,
        incumbent_skill_id: str | None,
    ) -> ValidationEvidence:
        return ValidationEvidence(
            "blocked",
            None,
            {
                "reason": "benchmark_adapter_required",
                "incumbent_skill_id": incumbent_skill_id,
            },
        )


class DockerSandboxAdapter:
    """Runs Python checks in an immutable, credential-free Docker boundary."""

    def __init__(
        self,
        *,
        image: str = "python:3.11-slim",
        docker_executable: str = "docker",
        timeout_seconds: int | None = None,
        policy: SandboxPolicy | None = None,
    ) -> None:
        if (
            not image.strip()
            or image != image.strip()
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/@:-]{0,254}", image)
        ):
            raise ValueError("Docker sandbox image reference is invalid")
        if (
            not docker_executable.strip()
            or docker_executable != docker_executable.strip()
            or len(docker_executable) > 1_024
        ):
            raise ValueError("Docker sandbox image and executable are required")
        self.image = image
        self.docker_executable = docker_executable
        self.policy = policy or SandboxPolicy()
        if timeout_seconds is not None:
            self.policy = replace(
                self.policy, timeout_seconds=timeout_seconds
            )

    @staticmethod
    def _host_environment() -> dict[str, str]:
        """Pass only values needed by the trusted Docker client, never user secrets."""
        allowed = {
            "PATH", "PATHEXT", "SYSTEMROOT", "WINDIR", "COMSPEC",
            "TMP", "TEMP", "TMPDIR",
            "DOCKER_HOST", "DOCKER_CONTEXT", "DOCKER_TLS_VERIFY",
            "DOCKER_CERT_PATH",
        }
        return {
            key: value for key, value in os.environ.items()
            if key.upper() in allowed
        }

    def _resolve_image(self) -> tuple[str | None, str | None]:
        try:
            result = subprocess.run(
                [
                    self.docker_executable,
                    "image",
                    "inspect",
                    self.image,
                    "--format",
                    "{{.Id}}",
                ],
                shell=False,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                timeout=min(self.policy.timeout_seconds, 30),
                check=False,
                text=True,
                env=self._host_environment(),
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            return None, type(error).__name__
        image_id = result.stdout.strip()
        if result.returncode != 0:
            return None, "preinstalled_image_unavailable"
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", image_id):
            return None, "invalid_image_identity"
        return image_id, None

    @staticmethod
    def _unit_commands(cases: tuple[str, ...]) -> tuple[tuple[str, ...], ...]:
        commands: list[tuple[str, ...]] = []
        forbidden = ("&&", "||", ";", "|", ">", "<", "`", "$(")
        for case in cases:
            if any(value in case for value in forbidden):
                raise ValueError("verification command contains shell syntax")
            arguments = tuple(shlex.split(case, posix=True))
            if not arguments or arguments[0] not in {"python", "python3"}:
                raise ValueError("only direct Python verification is allowlisted")
            commands.append(arguments)
        if not commands:
            raise ValueError("no runnable unit-test commands declared")
        return tuple(commands)

    def _command(
        self,
        package: SkillPackage,
        image_id: str,
        container_name: str,
        audit_id: str,
        arguments: tuple[str, ...],
    ) -> list[str]:
        package_root = package.root.resolve()
        if any(value in str(package_root) for value in (",", "\r", "\n")):
            raise ValueError("Sandbox mount path contains unsupported syntax")
        policy = self.policy
        user_id, group_id = policy.run_as_user.split(":", 1)
        return [
            self.docker_executable,
            "run",
            "--rm",
            "--name",
            container_name,
            "--label",
            f"acr.sandbox.audit={audit_id}",
            "--pull",
            "never",
            "--log-driver",
            "none",
            "--network",
            "none",
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges=true",
            "--security-opt",
            "seccomp=builtin",
            "--ipc",
            "none",
            "--cgroupns",
            "private",
            "--user",
            policy.run_as_user,
            "--init",
            "--pids-limit",
            str(policy.pids_limit),
            "--memory",
            f"{policy.memory_mb}m",
            "--memory-swap",
            f"{policy.memory_mb}m",
            "--cpus",
            str(policy.cpu_count),
            "--ulimit",
            (
                f"nofile={policy.open_files_limit}:"
                f"{policy.open_files_limit}"
            ),
            "--tmpfs",
            f"/tmp:rw,noexec,nosuid,nodev,size={policy.tmpfs_mb}m",
            "--tmpfs",
            (
                "/workspace:rw,noexec,nosuid,nodev,"
                f"size={policy.workspace_mb}m,uid={user_id},"
                f"gid={group_id},mode=0700"
            ),
            "--mount",
            f"type=bind,src={package_root},dst=/skill,readonly",
            "--workdir",
            "/skill",
            "--entrypoint",
            "/usr/bin/env",
            image_id,
            "-i",
            "PATH=/usr/local/bin:/usr/bin:/bin",
            "HOME=/workspace",
            "TMPDIR=/tmp",
            "PYTHONHASHSEED=0",
            "PYTHONDONTWRITEBYTECODE=1",
            "PYTHONNOUSERSITE=1",
            "ACR_SANDBOX=1",
            *arguments,
        ]

    def _force_cleanup(self, container_name: str) -> int | None:
        try:
            result = subprocess.run(
                [
                    self.docker_executable,
                    "rm",
                    "--force",
                    container_name,
                ],
                shell=False,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=15,
                check=False,
                env=self._host_environment(),
            )
            return result.returncode
        except (OSError, subprocess.TimeoutExpired):
            return None

    def run(
        self,
        package: SkillPackage,
        *,
        stage: str,
        cases: tuple[str, ...],
    ) -> ValidationEvidence:
        if stage == "sandbox_execution":
            commands = (
                (
                    "python",
                    "-c",
                    (
                        "import os, socket\n"
                        "from pathlib import Path\n"
                        "assert Path('/skill/SKILL.yaml').is_file()\n"
                        "allowed = {'PATH', 'HOME', 'TMPDIR', "
                        "'PYTHONHASHSEED', 'PYTHONDONTWRITEBYTECODE', "
                        "'PYTHONNOUSERSITE', 'ACR_SANDBOX', 'LC_CTYPE'}\n"
                        "assert set(os.environ) <= allowed\n"
                        "assert os.environ.get('ACR_SANDBOX') == '1'\n"
                        "for target in "
                        "('/skill/.acr-write-probe', '/etc/.acr-write-probe'):\n"
                        "    try:\n"
                        "        Path(target).write_text('denied')\n"
                        "    except OSError:\n"
                        "        pass\n"
                        "    else:\n"
                        "        raise AssertionError('restricted path writable')\n"
                        "probe = Path('/workspace/probe')\n"
                        "probe.write_text('temporary')\n"
                        "assert probe.read_text() == 'temporary'\n"
                        "probe.unlink()\n"
                        "try:\n"
                        "    socket.create_connection(('1.1.1.1', 53), 0.25)\n"
                        "except OSError:\n"
                        "    pass\n"
                        "else:\n"
                        "    raise AssertionError('network unexpectedly available')\n"
                    ),
                ),
            )
        elif stage == "unit_tests":
            try:
                commands = self._unit_commands(cases)
            except ValueError as error:
                return ValidationEvidence(
                    "blocked", None, {"reason": str(error), "stage": stage}
                )
        else:
            return ValidationEvidence(
                "blocked",
                None,
                {
                    "reason": "runnable_stage_harness_required",
                    "stage": stage,
                    "case_count": len(cases),
                },
            )
        started = time.monotonic()
        image_id, image_error = self._resolve_image()
        if image_id is None:
            return ValidationEvidence(
                "blocked",
                None,
                {
                    "reason": image_error,
                    "runtime": "docker",
                    "image_requested": self.image,
                    "pull_policy": "never",
                },
                latency_ms=round((time.monotonic() - started) * 1000),
            )
        audit_id = str(uuid.uuid4())
        exit_codes: list[int] = []
        command_hashes: list[str] = []
        for arguments in commands:
            remaining = self.policy.timeout_seconds - (
                time.monotonic() - started
            )
            if remaining <= 0:
                return ValidationEvidence(
                    "failed",
                    0.0,
                    {
                        "reason": "sandbox_timeout",
                        "stage": stage,
                        "audit_id": audit_id,
                        "cleanup": "no_active_container",
                    },
                    latency_ms=round(
                        (time.monotonic() - started) * 1000
                    ),
                )
            container_name = "acr-sandbox-" + uuid.uuid4().hex[:20]
            command_hashes.append(hashlib.sha256(
                json.dumps(
                    arguments, separators=(",", ":")
                ).encode("utf-8")
            ).hexdigest())
            try:
                result = subprocess.run(
                    self._command(
                        package,
                        image_id,
                        container_name,
                        audit_id,
                        arguments,
                    ),
                    shell=False,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=remaining,
                    check=False,
                    env=self._host_environment(),
                )
            except subprocess.TimeoutExpired:
                cleanup_code = self._force_cleanup(container_name)
                return ValidationEvidence(
                    "failed",
                    0.0,
                    {
                        "reason": "sandbox_timeout",
                        "stage": stage,
                        "audit_id": audit_id,
                        "forced_cleanup_exit_code": cleanup_code,
                    },
                    latency_ms=round(
                        (time.monotonic() - started) * 1000
                    ),
                )
            except (OSError, ValueError) as error:
                self._force_cleanup(container_name)
                return ValidationEvidence(
                    "blocked",
                    None,
                    {
                        "reason": "sandbox_unavailable",
                        "error_type": type(error).__name__,
                        "audit_id": audit_id,
                    },
                )
            exit_codes.append(result.returncode)
            if result.returncode != 0:
                break
        if 125 in exit_codes:
            return ValidationEvidence(
                "blocked",
                None,
                {
                    "reason": "docker_runtime_or_preinstalled_image_unavailable",
                    "image_requested": self.image,
                    "image_id": image_id,
                    "audit_id": audit_id,
                },
                latency_ms=round((time.monotonic() - started) * 1000),
            )
        passed = bool(exit_codes) and all(code == 0 for code in exit_codes)
        return ValidationEvidence(
            "passed" if passed else "failed",
            1.0 if passed else 0.0,
            {
                "audit_id": audit_id,
                "runtime": "docker",
                "image_requested": self.image,
                "image_id": image_id,
                "pull_policy": "never",
                "network": "none",
                "root_filesystem": "read_only",
                "package_mount": "read_only",
                "temporary_workspace": "bounded_tmpfs_deleted",
                "writable_host_mounts": 0,
                "capabilities": "dropped_all",
                "no_new_privileges": True,
                "seccomp": "builtin",
                "process_isolation": {
                    "pid": "private_default",
                    "ipc": "none",
                    "cgroup": "private",
                    "user": self.policy.run_as_user,
                    "pids_limit": self.policy.pids_limit,
                },
                "resources": {
                    "timeout_seconds": self.policy.timeout_seconds,
                    "memory_mb": self.policy.memory_mb,
                    "cpu_count": self.policy.cpu_count,
                    "open_files_limit": self.policy.open_files_limit,
                    "tmpfs_mb": self.policy.tmpfs_mb,
                    "workspace_mb": self.policy.workspace_mb,
                },
                "environment": {
                    "inherit_host": False,
                    "container_policy": "env_i_allowlist",
                },
                "command_count": len(exit_codes),
                "command_hashes": command_hashes,
                "exit_codes": exit_codes,
                "boundary_self_test": (
                    "passed" if stage == "sandbox_execution" else "not_run"
                ),
            },
            latency_ms=round((time.monotonic() - started) * 1000),
        )


class SkillValidator:
    """Retains a fail-closed ten-stage candidate validation pipeline."""

    def __init__(
        self,
        connection: sqlite3.Connection,
        registry: SkillRegistry,
        *,
        loader: SkillPackageLoader | None = None,
        sandbox: SandboxAdapter | None = None,
        evaluator: EvaluatorAdapter | None = None,
        benchmark: BenchmarkAdapter | None = None,
        policy: ValidationPolicy | None = None,
    ) -> None:
        self.connection = connection
        self.registry = registry
        self.loader = loader or SkillPackageLoader()
        self.sandbox = sandbox or UnavailableSandbox()
        self.evaluator = evaluator or UnavailableEvaluator()
        self.benchmark = benchmark or UnavailableBenchmark()
        self.policy = policy or ValidationPolicy()

    def _incumbent(self, package: SkillPackage, skill_id: str) -> str | None:
        task_classes = set(package.manifest.task_classes)
        rows = self.connection.execute(
            """
            SELECT id, task_classes_json, reliability, use_count
            FROM skills
            WHERE status = 'active' AND lifecycle_status = 'active' AND id != ?
            """,
            (skill_id,),
        ).fetchall()
        matches = [
            row for row in rows
            if task_classes & set(json.loads(row["task_classes_json"]))
        ]
        if not matches:
            return None
        return max(
            matches,
            key=lambda row: (row["reliability"], row["use_count"], row["id"]),
        )["id"]

    def _create_run(
        self,
        skill_id: str,
        package_hash: str,
        incumbent_skill_id: str | None,
    ) -> str:
        run_id = str(uuid.uuid4())
        self.connection.execute(
            """
            INSERT INTO skill_validation_runs(
                id, skill_id, package_hash, status, incumbent_skill_id,
                policy_json, created_at
            ) VALUES (?, ?, ?, 'running', ?, ?, ?)
            """,
            (
                run_id, skill_id, package_hash, incumbent_skill_id,
                json.dumps(asdict(self.policy)), utc_now(),
            ),
        )
        self.connection.commit()
        return run_id

    def _record(
        self,
        run_id: str,
        order: int,
        stage: str,
        evidence: ValidationEvidence,
    ) -> None:
        self.connection.execute(
            """
            INSERT INTO skill_validation_results(
                run_id, stage_order, stage, outcome, score, token_cost,
                estimated_cost, latency_ms, details_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id, order, stage, evidence.outcome, evidence.score,
                evidence.token_cost, evidence.estimated_cost,
                evidence.latency_ms,
                json.dumps(evidence.details, sort_keys=True), utc_now(),
            ),
        )
        self.connection.commit()

    @staticmethod
    def _blocked(previous: str) -> ValidationEvidence:
        return ValidationEvidence(
            "blocked", None, {"reason": "prerequisite_not_passed", "stage": previous}
        )

    def _dependency_validation(
        self, package: SkillPackage
    ) -> ValidationEvidence:
        missing: list[str] = []
        for dependency in package.manifest.dependencies:
            manifest_id, version = dependency.rsplit("@", 1)
            row = self.connection.execute(
                """
                SELECT lifecycle_status FROM skills
                WHERE manifest_id = ? AND version = ?
                """,
                (manifest_id, version),
            ).fetchone()
            if row is None or row["lifecycle_status"] != "active":
                missing.append(dependency)
        return ValidationEvidence(
            "passed" if not missing else "failed",
            1.0 if not missing else 0.0,
            {"active_dependencies": len(package.manifest.dependencies) - len(missing),
             "missing_or_inactive": missing},
        )

    def _security_scan(self, package: SkillPackage) -> ValidationEvidence:
        findings: list[str] = []
        suspicious_code = (
            "shell=true", "os.system(", "eval(", "exec(",
            "subprocess.popen(", "subprocess.run(",
        )
        for path in package.root.rglob("*"):
            if not path.is_file():
                continue
            try:
                content = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            findings.extend(
                f"{path.relative_to(package.root).as_posix()}:{flag}"
                for flag in content_risk_flags(content)
            )
            lowered = content.casefold().replace(" ", "")
            if path.suffix in {".py", ".ps1", ".sh", ".bat", ".cmd"}:
                findings.extend(
                    f"{path.relative_to(package.root).as_posix()}:dangerous_execution"
                    for pattern in suspicious_code
                    if pattern.replace(" ", "") in lowered
                )
        findings = list(dict.fromkeys(findings))
        return ValidationEvidence(
            "passed" if not findings else "failed",
            1.0 if not findings else 0.0,
            {"finding_count": len(findings), "findings": findings[:50]},
        )

    def _permission_analysis(
        self, package: SkillPackage
    ) -> ValidationEvidence:
        allowed = set(self.policy.allowed_permissions)
        requested = set(package.manifest.permissions)
        denied = sorted(requested - allowed)
        return ValidationEvidence(
            "passed" if not denied else "failed",
            1.0 if not denied else 0.0,
            {"requested": sorted(requested), "denied": denied},
        )

    @staticmethod
    def _scenario_cases(package: SkillPackage) -> tuple[str, ...]:
        path = package.root / "tests" / "scenarios.json"
        if not path.is_file():
            return ()
        payload = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(payload, dict):
            values = payload.get("verification", [])
        else:
            values = payload
        if not isinstance(values, list):
            raise ValueError("scenario tests must be a list")
        return tuple(str(item) for item in values)

    def _benchmark_result(
        self, package: SkillPackage, incumbent: str | None
    ) -> ValidationEvidence:
        evidence = self.benchmark.compare(
            package, incumbent_skill_id=incumbent
        )
        if evidence.outcome != "passed":
            return evidence
        details = evidence.details
        required = {
            "candidate_quality", "incumbent_quality",
            "candidate_cost", "incumbent_cost",
        }
        if not required.issubset(details):
            return ValidationEvidence(
                "error", None, {"reason": "incomplete_benchmark_evidence"}
            )
        candidate_quality = float(details["candidate_quality"])
        incumbent_quality = float(details["incumbent_quality"])
        candidate_cost = float(details["candidate_cost"])
        incumbent_cost = float(details["incumbent_cost"])
        quality_ok = candidate_quality >= incumbent_quality
        allowed_cost = incumbent_cost * (1 + self.policy.maximum_cost_regression)
        cost_ok = candidate_cost <= allowed_cost if incumbent_cost else candidate_cost == 0
        return ValidationEvidence(
            "passed" if quality_ok and cost_ok else "failed",
            candidate_quality,
            {**details, "quality_regression": not quality_ok,
             "cost_regression": not cost_ok},
            token_cost=evidence.token_cost,
            estimated_cost=evidence.estimated_cost,
            latency_ms=evidence.latency_ms,
        )

    def validate(self, reference: str) -> SkillValidationRun:
        skill = self.registry.inspect(reference)
        skill_id = str(skill["id"])
        package_hash = str(skill.get("content_hash") or "")
        package: SkillPackage | None = None
        try:
            package_path = skill.get("package_path")
            if not package_path:
                raise ValueError("skill has no v1 package")
            package = self.loader.load(str(package_path))
            incumbent = self._incumbent(package, skill_id)
        except Exception:
            incumbent = None
        run_id = self._create_run(skill_id, package_hash, incumbent)
        prerequisite = True
        previous = ""
        for order, stage in enumerate(STAGES, start=1):
            if not prerequisite:
                evidence = self._blocked(previous)
            else:
                try:
                    if stage == "syntax_validation":
                        if package is None or package.content_hash != package_hash:
                            raise ValueError("package is missing or changed")
                        tested = self.registry.test(skill_id)
                        passed = tested["verification_status"] == "static_passed"
                        evidence = ValidationEvidence(
                            "passed" if passed else "failed",
                            float(passed),
                            {"format": "skill-v1", "digest_unchanged": passed},
                        )
                    elif stage == "dependency_validation":
                        evidence = self._dependency_validation(package)
                    elif stage == "static_security_scan":
                        evidence = self._security_scan(package)
                    elif stage == "permission_analysis":
                        evidence = self._permission_analysis(package)
                    elif stage == "sandbox_execution":
                        evidence = self.sandbox.run(
                            package, stage=stage, cases=("package_smoke_test",)
                        )
                    elif stage == "unit_tests":
                        evidence = self.sandbox.run(
                            package,
                            stage=stage,
                            cases=package.manifest.verification,
                        )
                    elif stage == "scenario_tests":
                        evidence = self.sandbox.run(
                            package,
                            stage=stage,
                            cases=self._scenario_cases(package),
                        )
                    elif stage == "adversarial_tests":
                        evidence = self.sandbox.run(
                            package,
                            stage=stage,
                            cases=(
                                "prompt injection resistance",
                                "permission boundary escape",
                                "malformed input handling",
                            ),
                        )
                    elif stage == "evaluator_review":
                        evidence = self.evaluator.review(package)
                        if (
                            evidence.outcome == "passed"
                            and (
                                evidence.score is None
                                or evidence.score
                                < self.policy.minimum_evaluator_score
                            )
                        ):
                            evidence = ValidationEvidence(
                                "failed",
                                evidence.score,
                                {
                                    **evidence.details,
                                    "reason": "evaluator_score_below_threshold",
                                },
                                token_cost=evidence.token_cost,
                                estimated_cost=evidence.estimated_cost,
                                latency_ms=evidence.latency_ms,
                            )
                    else:
                        evidence = self._benchmark_result(package, incumbent)
                except Exception as error:
                    evidence = ValidationEvidence(
                        "error", None, {"error_type": type(error).__name__}
                    )
            self._record(run_id, order, stage, evidence)
            if evidence.outcome != "passed":
                prerequisite = False
                previous = stage
        outcomes = [
            row[0] for row in self.connection.execute(
                """
                SELECT outcome FROM skill_validation_results
                WHERE run_id = ? ORDER BY stage_order
                """,
                (run_id,),
            )
        ]
        status = (
            "passed"
            if outcomes and all(item == "passed" for item in outcomes)
            else "failed"
            if any(item in {"failed", "error"} for item in outcomes)
            else "blocked"
        )
        with self.connection:
            self.connection.execute(
                """
                UPDATE skill_validation_runs
                SET status = ?, completed_at = ? WHERE id = ?
                """,
                (status, utc_now(), run_id),
            )
        return self.load(run_id)

    def load(self, run_id: str) -> SkillValidationRun:
        run = self.connection.execute(
            "SELECT * FROM skill_validation_runs WHERE id = ?", (run_id,)
        ).fetchone()
        if run is None:
            raise KeyError(run_id)
        rows = self.connection.execute(
            """
            SELECT * FROM skill_validation_results
            WHERE run_id = ? ORDER BY stage_order
            """,
            (run_id,),
        ).fetchall()
        return SkillValidationRun(
            id=run["id"],
            skill_id=run["skill_id"],
            package_hash=run["package_hash"],
            status=run["status"],
            incumbent_skill_id=run["incumbent_skill_id"],
            policy=ValidationPolicy(**json.loads(run["policy_json"])),
            results=tuple(
                ValidationStageResult(
                    order=row["stage_order"],
                    stage=row["stage"],
                    evidence=ValidationEvidence(
                        outcome=row["outcome"],
                        score=row["score"],
                        details=json.loads(row["details_json"]),
                        token_cost=row["token_cost"],
                        estimated_cost=row["estimated_cost"],
                        latency_ms=row["latency_ms"],
                    ),
                    created_at=row["created_at"],
                )
                for row in rows
            ),
            created_at=run["created_at"],
            completed_at=run["completed_at"],
            promoted_at=run["promoted_at"],
        )

    def promote(self, run_id: str) -> SkillValidationRun:
        run = self.load(run_id)
        if run.status != "passed":
            raise ValueError("Only a fully passed validation run can be promoted")
        if len(run.results) != len(STAGES) or any(
            item.evidence.outcome != "passed" for item in run.results
        ):
            raise ValueError("All mandatory validation stages must pass")
        skill = self.registry.inspect(run.skill_id)
        package = self.loader.load(Path(str(skill["package_path"])))
        if package.content_hash != run.package_hash:
            raise ValueError("Skill package changed after validation")
        generated = self.connection.execute(
            """
            SELECT 1 FROM skill_generation_candidates
            WHERE skill_id = ? LIMIT 1
            """,
            (run.skill_id,),
        ).fetchone()
        if generated is not None:
            trust = MemorySkillCoevolution(self.connection).refresh(run.skill_id)
            if not trust.activation_eligible:
                raise ValueError(
                    "Generated skill lacks current, independently verified "
                    "memory support"
                )
        timestamp = utc_now()
        try:
            self.connection.execute("BEGIN IMMEDIATE")
            current = self.connection.execute(
                """
                SELECT lifecycle_status, verification_status, content_hash
                FROM skills WHERE id = ?
                """,
                (run.skill_id,),
            ).fetchone()
            validation = self.connection.execute(
                """
                SELECT status, package_hash FROM skill_validation_runs
                WHERE id = ?
                """,
                (run_id,),
            ).fetchone()
            passed_stages = self.connection.execute(
                """
                SELECT COUNT(*) FROM skill_validation_results
                WHERE run_id = ? AND outcome = 'passed'
                """,
                (run_id,),
            ).fetchone()[0]
            if (
                generated is not None
                and not MemorySkillCoevolution(
                    self.connection
                ).trust(run.skill_id).activation_eligible
            ):
                raise ValueError(
                    "Generated skill support changed during promotion"
                )
            if (
                current is None
                or validation is None
                or current["lifecycle_status"] == "retired"
                or current["verification_status"] != "static_passed"
                or current["content_hash"] != package.content_hash
                or validation["status"] != "passed"
                or validation["package_hash"] != package.content_hash
                or passed_stages != len(STAGES)
            ):
                raise ValueError("Skill promotion prerequisites changed")
            changed = self.connection.execute(
                """
                UPDATE skills
                SET lifecycle_status = 'active', status = 'active'
                WHERE id = ? AND lifecycle_status != 'retired'
                """,
                (run.skill_id,),
            ).rowcount
            if changed != 1:
                raise RuntimeError("Skill activation compare-and-swap failed")
            self.connection.execute(
                """
                INSERT INTO skill_registry_history (
                    id, skill_id, event, from_status, to_status,
                    details_json, created_at
                ) VALUES (?, ?, 'status_changed', ?, 'active', '{}', ?)
                """,
                (
                    str(uuid.uuid4()),
                    run.skill_id,
                    current["lifecycle_status"],
                    timestamp,
                ),
            )
            self.connection.execute(
                """
                UPDATE skill_validation_runs
                SET status = 'promoted', promoted_at = ? WHERE id = ?
                """,
                (timestamp, run_id),
            )
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise
        return self.load(run_id)
