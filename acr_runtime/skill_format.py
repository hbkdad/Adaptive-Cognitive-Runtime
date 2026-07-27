from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any

from .scoring import estimate_tokens
from .secret_management import detect_secret_material


class SkillStatus(str, Enum):
    EXPERIMENTAL = "experimental"
    QUARANTINED = "quarantined"
    ACTIVE = "active"
    DEPRECATED = "deprecated"
    RETIRED = "retired"


SEMVER = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$"
)
IDENTIFIER = re.compile(r"^[a-z0-9][a-z0-9._-]{1,127}$")
REQUIRED_FIELDS = frozenset(
    {
        "id", "name", "version", "description", "task_classes", "inputs",
        "outputs", "dependencies", "permissions", "tools", "models",
        "token_estimate", "applicability", "contraindications",
        "verification", "author", "origin", "created_at", "updated_at",
        "status", "reliability",
    }
)
REQUIRED_DIRECTORIES = ("examples", "tests", "scripts", "assets")
MAX_INSTRUCTIONS_TOKENS = 4_000
MAX_PACKAGE_BYTES = 1_000_000


@dataclass(frozen=True)
class SkillManifest:
    id: str
    name: str
    version: str
    description: str
    task_classes: tuple[str, ...]
    inputs: dict[str, str]
    outputs: dict[str, str]
    dependencies: tuple[str, ...]
    permissions: tuple[str, ...]
    tools: tuple[str, ...]
    models: tuple[str, ...]
    token_estimate: int
    applicability: tuple[str, ...]
    contraindications: tuple[str, ...]
    verification: tuple[str, ...]
    author: str
    origin: str
    created_at: str
    updated_at: str
    status: SkillStatus
    reliability: float


@dataclass(frozen=True)
class SkillPackage:
    root: Path
    manifest: SkillManifest
    instructions: str
    history: tuple[dict[str, Any], ...]
    content_hash: str
    actual_instruction_tokens: int


class SkillFormatError(ValueError):
    pass


class SkillPackageLoader:
    """Loads the JSON-compatible, dependency-free ACR Skill YAML profile."""

    def load(self, root: str | Path) -> SkillPackage:
        directory = Path(root).resolve()
        if not directory.is_dir():
            raise SkillFormatError("Skill package directory does not exist")
        required_files = (
            directory / "SKILL.yaml",
            directory / "instructions.md",
            directory / "history.jsonl",
        )
        for path in required_files:
            if not path.is_file():
                raise SkillFormatError(f"Missing required file: {path.name}")
        for name in REQUIRED_DIRECTORIES:
            if not (directory / name).is_dir():
                raise SkillFormatError(f"Missing required directory: {name}/")
        files = self._safe_files(directory)
        total_bytes = sum(path.stat().st_size for path in files)
        if total_bytes > MAX_PACKAGE_BYTES:
            raise SkillFormatError("Skill package exceeds the size limit")
        for path in files:
            try:
                content = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            findings = detect_secret_material(content)
            if findings:
                relative = path.relative_to(directory).as_posix()
                raise SkillFormatError(
                    "Skill package contains secret material in "
                    f"{relative}: {','.join(findings)}"
                )

        payload = self._load_manifest(required_files[0])
        manifest = self._validate_manifest(payload)
        instructions = required_files[1].read_text(encoding="utf-8").strip()
        if not instructions:
            raise SkillFormatError("instructions.md cannot be empty")
        actual_tokens = estimate_tokens(instructions)
        if actual_tokens > MAX_INSTRUCTIONS_TOKENS:
            raise SkillFormatError("Skill instructions are too large to be composable")
        tolerance = max(32, round(actual_tokens * 0.5))
        if abs(manifest.token_estimate - actual_tokens) > tolerance:
            raise SkillFormatError(
                "token_estimate materially differs from instructions.md"
            )
        history = self._load_history(required_files[2])
        digest = hashlib.sha256()
        for path in files:
            relative = path.relative_to(directory).as_posix()
            digest.update(relative.encode("utf-8"))
            digest.update(b"\0")
            digest.update(hashlib.sha256(path.read_bytes()).digest())
        return SkillPackage(
            root=directory,
            manifest=manifest,
            instructions=instructions,
            history=history,
            content_hash=digest.hexdigest(),
            actual_instruction_tokens=actual_tokens,
        )

    @staticmethod
    def _safe_files(directory: Path) -> tuple[Path, ...]:
        files: list[Path] = []
        for path in directory.rglob("*"):
            if path.is_symlink():
                raise SkillFormatError("Skill packages cannot contain symlinks")
            resolved = path.resolve()
            if directory not in resolved.parents and resolved != directory:
                raise SkillFormatError("Skill package path escapes its root")
            if path.is_file():
                files.append(path)
        return tuple(sorted(files, key=lambda item: item.relative_to(directory).as_posix()))

    @staticmethod
    def _load_manifest(path: Path) -> dict[str, Any]:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as error:
            raise SkillFormatError(
                "SKILL.yaml must use the JSON-compatible YAML 1.2 profile"
            ) from error
        if not isinstance(payload, dict):
            raise SkillFormatError("SKILL.yaml must contain a mapping")
        return payload

    def _validate_manifest(self, payload: dict[str, Any]) -> SkillManifest:
        missing = REQUIRED_FIELDS - payload.keys()
        unknown = payload.keys() - REQUIRED_FIELDS
        if missing:
            raise SkillFormatError(f"Missing manifest fields: {sorted(missing)}")
        if unknown:
            raise SkillFormatError(f"Unknown manifest fields: {sorted(unknown)}")
        identifier = self._string(payload, "id")
        if not IDENTIFIER.fullmatch(identifier):
            raise SkillFormatError("Skill id must be a stable lowercase identifier")
        version = self._string(payload, "version")
        if not SEMVER.fullmatch(version):
            raise SkillFormatError("Skill version must follow Semantic Versioning")
        created = self._timestamp(payload, "created_at")
        updated = self._timestamp(payload, "updated_at")
        if updated < created:
            raise SkillFormatError("updated_at cannot precede created_at")
        reliability = payload["reliability"]
        if isinstance(reliability, bool) or not isinstance(reliability, (int, float)):
            raise SkillFormatError("reliability must be numeric")
        if not 0 <= float(reliability) <= 1:
            raise SkillFormatError("reliability must be 0..1")
        token_estimate = payload["token_estimate"]
        if isinstance(token_estimate, bool) or not isinstance(token_estimate, int):
            raise SkillFormatError("token_estimate must be an integer")
        if not 1 <= token_estimate <= MAX_INSTRUCTIONS_TOKENS:
            raise SkillFormatError("token_estimate is outside the composable limit")
        dependencies = self._strings(payload, "dependencies")
        if any(
            "@" not in item
            or not IDENTIFIER.fullmatch(item.rsplit("@", 1)[0])
            or not SEMVER.fullmatch(item.rsplit("@", 1)[1])
            for item in dependencies
        ):
            raise SkillFormatError(
                "dependencies must use stable-id@semantic-version"
            )
        if any(item.split("@", 1)[0] == identifier for item in dependencies):
            raise SkillFormatError("A skill cannot depend on itself")
        try:
            status = SkillStatus(self._string(payload, "status"))
        except ValueError as error:
            raise SkillFormatError("Unsupported skill status") from error
        return SkillManifest(
            id=identifier,
            name=self._string(payload, "name"),
            version=version,
            description=self._string(payload, "description"),
            task_classes=self._strings(payload, "task_classes", nonempty=True),
            inputs=self._interface(payload, "inputs"),
            outputs=self._interface(payload, "outputs"),
            dependencies=dependencies,
            permissions=self._strings(payload, "permissions"),
            tools=self._strings(payload, "tools"),
            models=self._strings(payload, "models"),
            token_estimate=token_estimate,
            applicability=self._strings(payload, "applicability", nonempty=True),
            contraindications=self._strings(payload, "contraindications"),
            verification=self._strings(payload, "verification", nonempty=True),
            author=self._string(payload, "author"),
            origin=self._string(payload, "origin"),
            created_at=created.isoformat(),
            updated_at=updated.isoformat(),
            status=status,
            reliability=float(reliability),
        )

    @staticmethod
    def _string(payload: dict[str, Any], field: str) -> str:
        value = payload[field]
        if not isinstance(value, str) or not value.strip():
            raise SkillFormatError(f"{field} must be a non-empty string")
        return value.strip()

    @staticmethod
    def _strings(
        payload: dict[str, Any], field: str, *, nonempty: bool = False
    ) -> tuple[str, ...]:
        value = payload[field]
        if not isinstance(value, list) or any(
            not isinstance(item, str) or not item.strip() for item in value
        ):
            raise SkillFormatError(f"{field} must be a list of strings")
        normalized = tuple(dict.fromkeys(item.strip() for item in value))
        if nonempty and not normalized:
            raise SkillFormatError(f"{field} cannot be empty")
        return normalized

    @staticmethod
    def _interface(payload: dict[str, Any], field: str) -> dict[str, str]:
        value = payload[field]
        if not isinstance(value, dict) or any(
            not isinstance(key, str)
            or not key.strip()
            or not isinstance(item, str)
            or not item.strip()
            for key, item in value.items()
        ):
            raise SkillFormatError(f"{field} must map names to type descriptions")
        return {key.strip(): item.strip() for key, item in value.items()}

    @staticmethod
    def _timestamp(payload: dict[str, Any], field: str) -> datetime:
        value = SkillPackageLoader._string(payload, field)
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as error:
            raise SkillFormatError(f"{field} must be an ISO-8601 timestamp") from error
        if parsed.tzinfo is None:
            raise SkillFormatError(f"{field} must include a timezone")
        return parsed

    @staticmethod
    def _load_history(path: Path) -> tuple[dict[str, Any], ...]:
        records: list[dict[str, Any]] = []
        for number, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), start=1
        ):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as error:
                raise SkillFormatError(
                    f"history.jsonl line {number} is not valid JSON"
                ) from error
            if not isinstance(record, dict):
                raise SkillFormatError(
                    f"history.jsonl line {number} must be an object"
                )
            records.append(record)
        return tuple(records)
