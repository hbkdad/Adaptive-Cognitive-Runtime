from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Callable, Mapping, Protocol

from .bounded_validation import bounded_text, bounded_text_list
from .content_security import detect_suspicious_instructions
from .secret_management import assert_secret_free, detect_secret_material


SCHEMA_VERSION = 1
MAX_ISSUE_BODY_CHARS = 16_000
MAX_PATCH_BYTES = 1_000_000
MAX_CHANGED_PATHS = 64
MAX_ITERATIONS = 8
GATE_OUTCOMES = frozenset({"passed", "failed", "blocked", "not_relevant"})
BLOCKING_ISSUE_SIGNALS = frozenset(
    {
        "authority_override",
        "policy_redefinition",
        "identity_override",
        "secret_exfiltration",
        "covert_action",
        "active_content",
        "invisible_characters",
    }
)
STAGES = (
    "understand",
    "plan",
    "implement",
    "test",
    "review",
    "security_review",
    "benchmark",
    "produce_patch",
)
REPOSITORY = re.compile(r"^[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}$")
COMMIT_SHA = re.compile(r"^[0-9a-f]{40}$")
PATCH_HEADER = re.compile(r"^diff --git a/([^ \t]+) b/([^ \t]+)$", re.MULTILINE)
PATCH_FILE_MARKER = re.compile(
    r"^(?:---|\+\+\+) (?:[ab]/)?([^ \t]+)(?:\t.*)?$", re.MULTILINE
)
EVIDENCE_REF = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/#-]{0,255}$")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _hash_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class CodingExperimentError(ValueError):
    pass


@dataclass(frozen=True)
class BoundedGitIssue:
    repository: str
    number: int
    title: str
    body: str
    state: str

    @classmethod
    def from_dict(cls, payload: object) -> "BoundedGitIssue":
        expected = {"repository", "number", "title", "body", "state"}
        if not isinstance(payload, dict) or set(payload) != expected:
            raise CodingExperimentError(
                "issue requires repository, number, title, body, and state"
            )
        repository = bounded_text(
            payload["repository"], field="issue.repository", maximum=201
        )
        if not REPOSITORY.fullmatch(repository):
            raise CodingExperimentError("issue.repository must be owner/name")
        number = payload["number"]
        if type(number) is not int or not 1 <= number <= 2_147_483_647:
            raise CodingExperimentError("issue.number is invalid")
        state = payload["state"]
        if state != "open":
            raise CodingExperimentError("only one open Git issue is accepted")
        title = bounded_text(payload["title"], field="issue.title", maximum=512)
        body = bounded_text(
            payload["body"], field="issue.body", maximum=MAX_ISSUE_BODY_CHARS
        )
        assert_secret_free(title, "coding experiment issue title")
        assert_secret_free(body, "coding experiment issue body")
        signals = set(detect_suspicious_instructions(f"{title}\n{body}"))
        blocking = sorted(
            signal for signal in signals
            if signal in BLOCKING_ISSUE_SIGNALS
            or signal.startswith("secret_material:")
        )
        if blocking:
            raise CodingExperimentError(
                "issue contains untrusted instruction signals: "
                + ",".join(blocking)
            )
        return cls(repository, number, title, body, state)

    @property
    def reference(self) -> str:
        return f"{self.repository}#{self.number}"


@dataclass(frozen=True)
class CodingExperimentRequest:
    issue: BoundedGitIssue
    repository_root: Path
    baseline_commit: str
    allowed_paths: tuple[str, ...]
    maximum_iterations: int
    benchmark_mode: str
    benchmark_reason: str
    production_deployment: bool
    scope_expansion: bool

    @classmethod
    def from_dict(cls, payload: object) -> "CodingExperimentRequest":
        expected = {
            "schema_version",
            "issue",
            "repository_root",
            "baseline_commit",
            "allowed_paths",
            "maximum_iterations",
            "benchmark_mode",
            "benchmark_reason",
            "production_deployment",
            "scope_expansion",
        }
        if not isinstance(payload, dict) or set(payload) != expected:
            raise CodingExperimentError(
                "coding experiment request has an invalid shape"
            )
        if payload["schema_version"] != SCHEMA_VERSION:
            raise CodingExperimentError(
                "unsupported coding experiment schema_version"
            )
        root_text = bounded_text(
            payload["repository_root"],
            field="repository_root",
            maximum=1_024,
        )
        baseline = payload["baseline_commit"]
        if not isinstance(baseline, str) or not COMMIT_SHA.fullmatch(baseline):
            raise CodingExperimentError(
                "baseline_commit must be a full lowercase Git SHA"
            )
        paths = bounded_text_list(
            payload["allowed_paths"],
            field="allowed_paths",
            minimum=1,
            maximum=32,
        )
        normalized_paths = tuple(_normalize_scope_path(item) for item in paths)
        if len(set(normalized_paths)) != len(normalized_paths):
            raise CodingExperimentError("allowed_paths contains duplicates")
        iterations = payload["maximum_iterations"]
        if type(iterations) is not int or not 1 <= iterations <= MAX_ITERATIONS:
            raise CodingExperimentError(
                f"maximum_iterations must be 1..{MAX_ITERATIONS}"
            )
        benchmark_mode = payload["benchmark_mode"]
        if benchmark_mode not in {"required", "not_relevant"}:
            raise CodingExperimentError(
                "benchmark_mode must be required or not_relevant"
            )
        benchmark_reason = bounded_text(
            payload["benchmark_reason"],
            field="benchmark_reason",
            maximum=1_000,
        )
        if payload["production_deployment"] is not False:
            raise CodingExperimentError("production deployment is forbidden")
        if payload["scope_expansion"] is not False:
            raise CodingExperimentError("scope expansion is forbidden")
        return cls(
            issue=BoundedGitIssue.from_dict(payload["issue"]),
            repository_root=Path(root_text).resolve(),
            baseline_commit=baseline,
            allowed_paths=normalized_paths,
            maximum_iterations=iterations,
            benchmark_mode=benchmark_mode,
            benchmark_reason=benchmark_reason,
            production_deployment=False,
            scope_expansion=False,
        )


@dataclass(frozen=True)
class GateEvidence:
    outcome: str
    summary: str
    evidence_refs: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.outcome not in GATE_OUTCOMES:
            raise CodingExperimentError("invalid coding gate outcome")
        bounded_text(self.summary, field="gate summary", maximum=2_000)
        if not 1 <= len(self.evidence_refs) <= 16:
            raise CodingExperimentError("gate evidence requires 1..16 references")
        if any(not EVIDENCE_REF.fullmatch(item) for item in self.evidence_refs):
            raise CodingExperimentError("gate evidence reference is invalid")
        assert_secret_free(self.summary, "coding gate summary")


@dataclass(frozen=True)
class CodingIterationCandidate:
    understanding: GateEvidence
    plan: GateEvidence
    planned_paths: tuple[str, ...]
    patch: str
    tests: GateEvidence
    review: GateEvidence
    security_review: GateEvidence
    benchmark: GateEvidence


@dataclass(frozen=True)
class CodingTaskContext:
    issue_reference: str
    issue_title: str
    issue_body: str
    repository: str
    baseline_commit: str
    allowed_paths: tuple[str, ...]
    benchmark_mode: str
    benchmark_reason: str
    production_deployment_allowed: bool = False
    scope_expansion_allowed: bool = False


class CodingIterationAdapter(Protocol):
    def identity(self) -> Mapping[str, object]: ...

    def run_iteration(
        self,
        context: CodingTaskContext,
        *,
        iteration: int,
        prior_attempts: tuple[dict[str, object], ...],
    ) -> CodingIterationCandidate: ...


class UnavailableCodingAdapter:
    def identity(self) -> Mapping[str, object]:
        return {"available": False}

    def run_iteration(
        self,
        context: CodingTaskContext,
        *,
        iteration: int,
        prior_attempts: tuple[dict[str, object], ...],
    ) -> CodingIterationCandidate:
        raise RuntimeError("coding execution adapter is unavailable")


@dataclass(frozen=True)
class CodingExperimentRun:
    id: str
    issue_reference: str
    issue_hash: str
    baseline_commit: str
    allowed_paths: tuple[str, ...]
    maximum_iterations: int
    status: str
    iterations: tuple[dict[str, object], ...]
    patch_hash: str | None
    patch_path: str | None
    decision_reason: str
    adapter_identity: dict[str, object]
    created_at: str
    completed_at: str

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


class AutonomousCodingExperiment:
    """Run a patch-only loop without editing, committing, pushing, or deploying."""

    def __init__(
        self,
        output_directory: str | Path,
        *,
        adapter: CodingIterationAdapter | None = None,
        mutation_guard: Callable[[str], None] | None = None,
        git_executable: str = "git",
    ) -> None:
        self.output_directory = Path(output_directory).resolve()
        self.adapter = adapter or UnavailableCodingAdapter()
        self.mutation_guard = mutation_guard
        self.git_executable = git_executable

    def run(self, request: CodingExperimentRequest) -> CodingExperimentRun:
        if self.mutation_guard is not None:
            self.mutation_guard("shell_write")
        created_at = _now()
        run_id = str(uuid.uuid4())
        issue_hash = _hash_text(
            json.dumps(
                {
                    "repository": request.issue.repository,
                    "number": request.issue.number,
                    "title": request.issue.title,
                    "body": request.issue.body,
                },
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        root = self._validate_repository(request)
        identity = self._adapter_identity()
        if not identity.get("available"):
            return self._retain(
                CodingExperimentRun(
                    id=run_id,
                    issue_reference=request.issue.reference,
                    issue_hash=issue_hash,
                    baseline_commit=request.baseline_commit,
                    allowed_paths=request.allowed_paths,
                    maximum_iterations=request.maximum_iterations,
                    status="blocked",
                    iterations=(),
                    patch_hash=None,
                    patch_path=None,
                    decision_reason="trusted_execution_adapter_required",
                    adapter_identity=identity,
                    created_at=created_at,
                    completed_at=_now(),
                )
            )
        context = CodingTaskContext(
            issue_reference=request.issue.reference,
            issue_title=request.issue.title,
            issue_body=request.issue.body,
            repository=request.issue.repository,
            baseline_commit=request.baseline_commit,
            allowed_paths=request.allowed_paths,
            benchmark_mode=request.benchmark_mode,
            benchmark_reason=request.benchmark_reason,
        )
        attempts: list[dict[str, object]] = []
        final_patch: str | None = None
        final_hash: str | None = None
        reason = "maximum_iterations_exhausted"
        authoritative_snapshot = (request.baseline_commit, "")
        for iteration in range(1, request.maximum_iterations + 1):
            before = self._repository_snapshot(root)
            if before != authoritative_snapshot:
                reason = "authoritative_repository_changed"
                break
            try:
                candidate = self.adapter.run_iteration(
                    context,
                    iteration=iteration,
                    prior_attempts=tuple(attempts),
                )
                after = self._repository_snapshot(root)
                if after != before:
                    raise CodingExperimentError(
                        "adapter mutated the authoritative repository"
                    )
                attempt, accepted = self._validate_candidate(
                    request, root, iteration, candidate
                )
            except Exception as error:
                attempt = {
                    "iteration": iteration,
                    "status": "failed",
                    "reason": type(error).__name__,
                    "stages": _blocked_stages("adapter_or_validation_error"),
                }
                accepted = False
            attempts.append(attempt)
            if self._repository_snapshot(root) != authoritative_snapshot:
                reason = "authoritative_repository_changed"
                break
            if accepted:
                final_patch = candidate.patch
                final_hash = _hash_text(candidate.patch)
                reason = "all_patch_gates_passed"
                break
        patch_path = None
        status = "failed"
        if final_patch is not None and final_hash is not None:
            self._safe_output_directory()
            destination = self.output_directory / f"{run_id}.patch"
            destination.write_text(final_patch, encoding="utf-8", newline="\n")
            patch_path = str(destination)
            status = "patch_ready"
        return self._retain(
            CodingExperimentRun(
                id=run_id,
                issue_reference=request.issue.reference,
                issue_hash=issue_hash,
                baseline_commit=request.baseline_commit,
                allowed_paths=request.allowed_paths,
                maximum_iterations=request.maximum_iterations,
                status=status,
                iterations=tuple(attempts),
                patch_hash=final_hash,
                patch_path=patch_path,
                decision_reason=reason,
                adapter_identity=identity,
                created_at=created_at,
                completed_at=_now(),
            )
        )

    def load(self, run_id: str) -> CodingExperimentRun:
        if not re.fullmatch(
            r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-"
            r"[0-9a-f]{4}-[0-9a-f]{12}",
            run_id,
        ):
            raise CodingExperimentError("run_id is invalid")
        path = self.output_directory / f"{run_id}.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        expected = {
            "id",
            "issue_reference",
            "issue_hash",
            "baseline_commit",
            "allowed_paths",
            "maximum_iterations",
            "status",
            "iterations",
            "patch_hash",
            "patch_path",
            "decision_reason",
            "adapter_identity",
            "created_at",
            "completed_at",
        }
        if not isinstance(payload, dict) or set(payload) != expected:
            raise CodingExperimentError("retained coding report has invalid shape")
        payload["allowed_paths"] = tuple(payload["allowed_paths"])
        payload["iterations"] = tuple(payload["iterations"])
        return CodingExperimentRun(**payload)

    def _validate_candidate(
        self,
        request: CodingExperimentRequest,
        root: Path,
        iteration: int,
        candidate: CodingIterationCandidate,
    ) -> tuple[dict[str, object], bool]:
        if not isinstance(candidate, CodingIterationCandidate):
            raise CodingExperimentError("adapter returned an invalid candidate")
        planned_paths = tuple(
            _normalize_changed_path(path) for path in candidate.planned_paths
        )
        if not planned_paths or len(planned_paths) > MAX_CHANGED_PATHS:
            raise CodingExperimentError("planned_paths must contain 1..64 paths")
        self._enforce_scope(planned_paths, request.allowed_paths)
        patch_paths = _patch_paths(candidate.patch)
        self._enforce_scope(patch_paths, request.allowed_paths)
        if not set(patch_paths).issubset(planned_paths):
            raise CodingExperimentError("patch changes an unplanned path")
        _validate_patch_content(candidate.patch)
        gates = {
            "understand": candidate.understanding,
            "plan": candidate.plan,
            "test": candidate.tests,
            "review": candidate.review,
            "security_review": candidate.security_review,
            "benchmark": candidate.benchmark,
        }
        benchmark_ok = (
            candidate.benchmark.outcome == "passed"
            if request.benchmark_mode == "required"
            else candidate.benchmark.outcome == "not_relevant"
        )
        gates_ok = (
            candidate.understanding.outcome == "passed"
            and candidate.plan.outcome == "passed"
            and candidate.tests.outcome == "passed"
            and candidate.review.outcome == "passed"
            and candidate.security_review.outcome == "passed"
            and benchmark_ok
        )
        applicable, apply_reason = self._git_apply_check(root, candidate.patch)
        accepted = gates_ok and applicable
        stage_results: list[dict[str, object]] = []
        for stage in STAGES:
            if stage == "implement":
                result = {
                    "stage": stage,
                    "outcome": "passed",
                    "summary": "bounded unified patch candidate",
                    "evidence_refs": [f"sha256:{_hash_text(candidate.patch)}"],
                    "provenance": "runtime_validated",
                }
            elif stage == "produce_patch":
                result = {
                    "stage": stage,
                    "outcome": "passed" if accepted else "failed",
                    "summary": apply_reason,
                    "evidence_refs": [f"git-apply-check:{int(applicable)}"],
                    "provenance": "runtime_validated",
                }
            else:
                evidence = gates[stage]
                result = {
                    "stage": stage,
                    "outcome": evidence.outcome,
                    "summary": evidence.summary,
                    "evidence_refs": list(evidence.evidence_refs),
                    "provenance": "adapter_asserted",
                }
            stage_results.append(result)
        return (
            {
                "iteration": iteration,
                "status": "passed" if accepted else "failed",
                "reason": (
                    "all_patch_gates_passed"
                    if accepted
                    else "one_or_more_gates_failed"
                ),
                "patch_hash": _hash_text(candidate.patch),
                "changed_paths": list(patch_paths),
                "stages": stage_results,
            },
            accepted,
        )

    def _validate_repository(self, request: CodingExperimentRequest) -> Path:
        root = request.repository_root
        if not root.is_dir():
            raise CodingExperimentError("repository_root does not exist")
        resolved = self._git(
            root, "rev-parse", "--show-toplevel"
        ).stdout.strip()
        if Path(resolved).resolve() != root:
            raise CodingExperimentError("repository_root must be the Git top level")
        head = self._git(root, "rev-parse", "HEAD").stdout.strip()
        if head != request.baseline_commit:
            raise CodingExperimentError("baseline_commit is not current HEAD")
        status = self._git(root, "status", "--porcelain=v1").stdout
        if status:
            raise CodingExperimentError(
                "authoritative repository must be clean before experiment"
            )
        return root

    def _repository_snapshot(self, root: Path) -> tuple[str, str]:
        return (
            self._git(root, "rev-parse", "HEAD").stdout.strip(),
            self._git(root, "status", "--porcelain=v1").stdout,
        )

    def _git_apply_check(
        self, root: Path, patch: str
    ) -> tuple[bool, str]:
        result = self._git(
            root,
            "apply",
            "--check",
            "--whitespace=error-all",
            "-",
            input_text=patch,
            check=False,
        )
        return (
            result.returncode == 0,
            "git apply --check passed"
            if result.returncode == 0
            else "git apply --check failed",
        )

    def _git(
        self,
        root: Path,
        *arguments: str,
        input_text: str | None = None,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        environment_keys = {
            "PATH", "PATHEXT", "SYSTEMROOT", "WINDIR", "COMSPEC",
            "TMP", "TEMP", "TMPDIR",
        }
        environment = {
            key: value for key, value in os.environ.items()
            if key.upper() in environment_keys
        }
        result = subprocess.run(
            [self.git_executable, "-C", str(root), *arguments],
            shell=False,
            stdin=None if input_text is not None else subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            input=input_text,
            timeout=30,
            check=False,
            env=environment,
        )
        if check and result.returncode != 0:
            raise CodingExperimentError(
                f"Git validation failed: {' '.join(arguments[:2])}"
            )
        return result

    def _adapter_identity(self) -> dict[str, object]:
        identity = dict(self.adapter.identity())
        if identity.get("available") is not True:
            return {"available": False}
        required = {
            "available": True,
            "isolation": "disposable-worktree",
            "network": "none",
            "deployment": "forbidden",
            "mutation_target": "patch-only",
        }
        if any(identity.get(key) != value for key, value in required.items()):
            raise CodingExperimentError(
                "coding adapter does not satisfy the isolation contract"
            )
        encoded = json.dumps(identity, sort_keys=True)
        if len(encoded.encode("utf-8")) > 4_000:
            raise CodingExperimentError("adapter identity is too large")
        assert_secret_free(encoded, "coding adapter identity")
        return identity

    @staticmethod
    def _enforce_scope(
        changed_paths: tuple[str, ...], allowed_paths: tuple[str, ...]
    ) -> None:
        outside = [
            path for path in changed_paths
            if not any(
                path == allowed or path.startswith(f"{allowed}/")
                for allowed in allowed_paths
            )
        ]
        if outside:
            raise CodingExperimentError(
                f"patch expands beyond allowed_paths: {outside}"
            )

    def _safe_output_directory(self) -> None:
        self.output_directory.mkdir(parents=True, exist_ok=True)
        if self.output_directory.is_symlink():
            raise CodingExperimentError(
                "coding experiment output cannot be a symlink"
            )

    def _retain(self, run: CodingExperimentRun) -> CodingExperimentRun:
        self._safe_output_directory()
        path = self.output_directory / f"{run.id}.json"
        if path.exists() or path.is_symlink():
            raise CodingExperimentError("coding report already exists")
        payload = run.as_dict()
        encoded = json.dumps(payload, indent=2, sort_keys=True) + "\n"
        assert_secret_free(encoded, "coding experiment report")
        path.write_text(encoded, encoding="utf-8", newline="\n")
        return run


def _normalize_scope_path(value: str) -> str:
    path = _normalize_changed_path(value)
    if path in {".", ".git"} or path.startswith(".git/"):
        raise CodingExperimentError("allowed_paths must be bounded source paths")
    return path


def _normalize_changed_path(value: str) -> str:
    if not isinstance(value, str):
        raise CodingExperimentError("changed path must be text")
    normalized = value.replace("\\", "/").strip("/")
    path = PurePosixPath(normalized)
    if (
        not normalized
        or value.startswith(("/", "\\"))
        or ":" in normalized
        or any(part in {"", ".", ".."} for part in path.parts)
        or normalized.startswith(".git/")
        or normalized == ".git"
        or len(normalized) > 512
    ):
        raise CodingExperimentError("changed path is unsafe or unbounded")
    return path.as_posix()


def _patch_paths(patch: str) -> tuple[str, ...]:
    if not isinstance(patch, str) or not patch.strip():
        raise CodingExperimentError("patch cannot be empty")
    if len(patch.encode("utf-8")) > MAX_PATCH_BYTES:
        raise CodingExperimentError("patch exceeds the size limit")
    matches = PATCH_HEADER.findall(patch)
    if not matches or len(matches) > MAX_CHANGED_PATHS:
        raise CodingExperimentError("patch requires 1..64 Git diff headers")
    paths: list[str] = []
    for before, after in matches:
        paths.extend(
            (_normalize_changed_path(before), _normalize_changed_path(after))
        )
    header_paths = set(paths)
    marker_paths = [
        item for item in PATCH_FILE_MARKER.findall(patch)
        if item != "/dev/null"
    ]
    normalized_markers = tuple(
        _normalize_changed_path(item) for item in marker_paths
    )
    if not normalized_markers or any(
        item not in header_paths for item in normalized_markers
    ):
        raise CodingExperimentError(
            "patch file markers do not match Git diff headers"
        )
    paths.extend(normalized_markers)
    return tuple(dict.fromkeys(paths))


def _validate_patch_content(patch: str) -> None:
    if "\0" in patch or "GIT binary patch" in patch:
        raise CodingExperimentError("binary patches are forbidden")
    forbidden_headers = (
        "\nrename from ",
        "\nrename to ",
        "\ncopy from ",
        "\ncopy to ",
        "\nSubmodule ",
    )
    if any(header in f"\n{patch}" for header in forbidden_headers):
        raise CodingExperimentError("rename, copy, and submodule patches are forbidden")
    if "\n@@ " not in patch and not patch.startswith("@@ "):
        raise CodingExperimentError("patch requires contextual unified hunks")
    findings = detect_secret_material(patch)
    if findings:
        raise CodingExperimentError(
            "patch contains secret material: " + ",".join(findings)
        )


def _blocked_stages(reason: str) -> list[dict[str, object]]:
    return [
        {
            "stage": stage,
            "outcome": "blocked",
            "summary": reason,
            "evidence_refs": ["adapter:unavailable_or_invalid"],
            "provenance": "trusted_runtime",
        }
        for stage in STAGES
    ]
