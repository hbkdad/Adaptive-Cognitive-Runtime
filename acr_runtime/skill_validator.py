from __future__ import annotations

import json
import shlex
import sqlite3
import subprocess
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Protocol

from .memory import utc_now
from .skill_format import SkillPackage, SkillPackageLoader
from .skill_registry import SkillRegistry
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
    """Runs allowlisted Python checks in a locked-down, preinstalled image."""

    def __init__(
        self,
        *,
        image: str = "python:3.11-slim",
        docker_executable: str = "docker",
        timeout_seconds: int = 60,
    ) -> None:
        if not image.strip() or not docker_executable.strip():
            raise ValueError("Docker sandbox image and executable are required")
        if timeout_seconds < 1:
            raise ValueError("Docker sandbox timeout must be positive")
        self.image = image
        self.docker_executable = docker_executable
        self.timeout_seconds = timeout_seconds

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
        self, package: SkillPackage, arguments: tuple[str, ...]
    ) -> list[str]:
        return [
            self.docker_executable,
            "run",
            "--rm",
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
            "no-new-privileges",
            "--pids-limit",
            "64",
            "--memory",
            "256m",
            "--cpus",
            "0.5",
            "--tmpfs",
            "/tmp:rw,noexec,nosuid,size=64m",
            "--mount",
            f"type=bind,src={package.root},dst=/skill,readonly",
            "--workdir",
            "/skill",
            self.image,
            *arguments,
        ]

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
                        "from pathlib import Path;"
                        "assert Path('/skill/SKILL.yaml').is_file()"
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
        exit_codes: list[int] = []
        try:
            for arguments in commands:
                result = subprocess.run(
                    self._command(package, arguments),
                    shell=False,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=self.timeout_seconds,
                    check=False,
                )
                exit_codes.append(result.returncode)
                if result.returncode != 0:
                    break
        except subprocess.TimeoutExpired:
            return ValidationEvidence(
                "failed",
                0.0,
                {"reason": "sandbox_timeout", "stage": stage},
                latency_ms=round((time.monotonic() - started) * 1000),
            )
        except OSError as error:
            return ValidationEvidence(
                "blocked",
                None,
                {"reason": "sandbox_unavailable", "error_type": type(error).__name__},
            )
        if 125 in exit_codes:
            return ValidationEvidence(
                "blocked",
                None,
                {
                    "reason": "docker_runtime_or_preinstalled_image_unavailable",
                    "image": self.image,
                },
                latency_ms=round((time.monotonic() - started) * 1000),
            )
        passed = bool(exit_codes) and all(code == 0 for code in exit_codes)
        return ValidationEvidence(
            "passed" if passed else "failed",
            1.0 if passed else 0.0,
            {
                "runtime": "docker",
                "image": self.image,
                "network": "none",
                "root_filesystem": "read_only",
                "capabilities": "dropped_all",
                "command_count": len(exit_codes),
                "exit_codes": exit_codes,
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
        self.registry.activate(run.skill_id)
        with self.connection:
            self.connection.execute(
                """
                UPDATE skill_validation_runs
                SET status = 'promoted', promoted_at = ? WHERE id = ?
                """,
                (utc_now(), run_id),
            )
        return self.load(run_id)
