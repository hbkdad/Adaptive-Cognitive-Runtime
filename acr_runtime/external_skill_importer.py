from __future__ import annotations

import hashlib
import json
import re
import shutil
import tempfile
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

from .scoring import estimate_tokens
from .secret_management import detect_secret_material
from .skill_format import (
    IDENTIFIER,
    MAX_INSTRUCTIONS_TOKENS,
    SkillPackage,
    SkillPackageLoader,
)
from .skill_registry import SkillRegistry
from .skill_validator import (
    ADVERSARIAL_SKILL_CASES,
    SandboxAdapter,
    UnavailableSandbox,
)
from .content_security import detect_suspicious_instructions


MAX_SOURCE_BYTES = 1_000_000
MAX_SOURCE_FILES = 128
SUPPORTED_RESOURCE_DIRECTORIES = ("scripts", "references", "assets")
BLOCKING_INSTRUCTION_FINDINGS = frozenset(
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
TOOL_MAPPINGS = {
    "read": ("filesystem.read", "filesystem:read"),
    "glob": ("filesystem.glob", "filesystem:read"),
    "grep": ("filesystem.search", "filesystem:read"),
    "write": ("filesystem.write", "filesystem:write"),
    "edit": ("filesystem.edit", "filesystem:write"),
    "webfetch": ("network.fetch", "network:outbound"),
    "websearch": ("network.search", "network:outbound"),
}
DEFAULT_ALLOWED_PERMISSIONS = frozenset({"filesystem:read"})


class ExternalSkillImportError(ValueError):
    pass


@dataclass(frozen=True)
class ParsedAgentSkill:
    name: str
    description: str
    instructions: str
    license: str | None
    compatibility: str | None
    metadata: dict[str, str]
    allowed_tools: tuple[str, ...]


@dataclass(frozen=True)
class ExternalSkillImportResult:
    source_format: str
    source_hash: str
    package_path: str
    content_hash: str
    mapped_tools: tuple[str, ...]
    mapped_permissions: tuple[str, ...]
    dependencies: tuple[str, ...]
    resource_files: tuple[str, ...]
    scan_findings: tuple[str, ...]
    sandbox_stages: tuple[dict[str, object], ...]
    registry_record: dict[str, object]

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


class DependencyLookup(Protocol):
    def __call__(self, manifest_id: str, version: str) -> bool: ...


class AgentSkillParser:
    """Parse the strict, source-bound subset of the Agent Skills standard."""

    _NAME = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    _ALLOWED_FIELDS = {
        "name", "description", "license", "compatibility", "metadata",
        "allowed-tools",
    }

    def parse(self, path: Path) -> ParsedAgentSkill:
        text = path.read_text(encoding="utf-8")
        lines = text.splitlines()
        if not lines or lines[0].strip() != "---":
            raise ExternalSkillImportError(
                "SKILL.md must start with YAML frontmatter"
            )
        try:
            end = next(
                index for index, line in enumerate(lines[1:], start=1)
                if line.strip() == "---"
            )
        except StopIteration as error:
            raise ExternalSkillImportError(
                "SKILL.md frontmatter is not terminated"
            ) from error
        frontmatter = self._parse_frontmatter(lines[1:end])
        unknown = set(frontmatter) - self._ALLOWED_FIELDS
        if unknown:
            raise ExternalSkillImportError(
                f"Unsupported Agent Skills fields: {sorted(unknown)}"
            )
        name = self._required(frontmatter, "name")
        description = self._required(frontmatter, "description")
        if len(name) > 64 or not self._NAME.fullmatch(name):
            raise ExternalSkillImportError("Agent skill name is invalid")
        if len(description) > 1_024:
            raise ExternalSkillImportError("Agent skill description is too long")
        compatibility = frontmatter.get("compatibility")
        if isinstance(compatibility, str) and len(compatibility) > 500:
            raise ExternalSkillImportError("compatibility is too long")
        metadata = frontmatter.get("metadata", {})
        if not isinstance(metadata, dict):
            raise ExternalSkillImportError("metadata must be a string mapping")
        instructions = "\n".join(lines[end + 1 :]).strip()
        if not instructions:
            raise ExternalSkillImportError("SKILL.md instructions cannot be empty")
        tools = frontmatter.get("allowed-tools", "")
        if not isinstance(tools, str):
            raise ExternalSkillImportError("allowed-tools must be a string")
        return ParsedAgentSkill(
            name=name,
            description=description,
            instructions=instructions,
            license=self._optional(frontmatter, "license"),
            compatibility=self._optional(frontmatter, "compatibility"),
            metadata=metadata,
            allowed_tools=tuple(item for item in tools.split() if item),
        )

    @staticmethod
    def _scalar(value: str) -> str:
        value = value.strip()
        if not value:
            raise ExternalSkillImportError("Frontmatter values cannot be empty")
        if value.startswith(("[", "{", "!", "&", "*", "|", ">")):
            raise ExternalSkillImportError(
                "Complex YAML, tags, anchors, and multiline scalars are unsupported"
            )
        if value[0:1] in {'"', "'"}:
            if value[0] == '"':
                try:
                    parsed = json.loads(value)
                except json.JSONDecodeError as error:
                    raise ExternalSkillImportError(
                        "Invalid quoted frontmatter value"
                    ) from error
            else:
                if not value.endswith("'"):
                    raise ExternalSkillImportError(
                        "Invalid quoted frontmatter value"
                    )
                parsed = value[1:-1].replace("''", "'")
            if not isinstance(parsed, str) or not parsed.strip():
                raise ExternalSkillImportError(
                    "Frontmatter values must be non-empty strings"
                )
            return parsed.strip()
        return value

    def _parse_frontmatter(
        self, lines: list[str]
    ) -> dict[str, str | dict[str, str]]:
        result: dict[str, str | dict[str, str]] = {}
        metadata: dict[str, str] | None = None
        for line in lines:
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            if "\t" in line:
                raise ExternalSkillImportError("Tabs are unsupported in frontmatter")
            if line.startswith("  "):
                if metadata is None or ":" not in line.strip():
                    raise ExternalSkillImportError(
                        "Only one-level metadata mappings are supported"
                    )
                key, value = line.strip().split(":", 1)
                key = key.strip()
                if not key or key in metadata:
                    raise ExternalSkillImportError(
                        "Metadata keys must be unique and non-empty"
                    )
                metadata[key] = self._scalar(value)
                continue
            if line.startswith(" "):
                raise ExternalSkillImportError("Invalid frontmatter indentation")
            if ":" not in line:
                raise ExternalSkillImportError("Frontmatter entries require ':'")
            key, value = line.split(":", 1)
            key = key.strip()
            if not key or key in result:
                raise ExternalSkillImportError(
                    "Frontmatter keys must be unique and non-empty"
                )
            if key == "metadata":
                if value.strip():
                    raise ExternalSkillImportError(
                        "metadata must use a one-level mapping"
                    )
                metadata = {}
                result[key] = metadata
            else:
                metadata = None
                result[key] = self._scalar(value)
        return result

    @staticmethod
    def _required(
        payload: dict[str, str | dict[str, str]], field: str
    ) -> str:
        value = payload.get(field)
        if not isinstance(value, str) or not value:
            raise ExternalSkillImportError(f"{field} is required")
        return value

    @staticmethod
    def _optional(
        payload: dict[str, str | dict[str, str]], field: str
    ) -> str | None:
        value = payload.get(field)
        if value is None:
            return None
        if not isinstance(value, str):
            raise ExternalSkillImportError(f"{field} must be a string")
        return value


class ExternalSkillImporter:
    """Normalize one local Agent Skill and admit it only after closed testing."""

    def __init__(
        self,
        registry: SkillRegistry,
        skills_directory: str | Path,
        *,
        loader: SkillPackageLoader | None = None,
        sandbox: SandboxAdapter | None = None,
        allowed_permissions: frozenset[str] = DEFAULT_ALLOWED_PERMISSIONS,
    ) -> None:
        self.registry = registry
        self.skills_directory = Path(skills_directory).resolve()
        self.loader = loader or SkillPackageLoader()
        self.sandbox = sandbox or UnavailableSandbox()
        self.allowed_permissions = allowed_permissions
        self.parser = AgentSkillParser()

    def import_local(
        self, source: str | Path, *, source_label: str = "local"
    ) -> ExternalSkillImportResult:
        root, skill_file = self._resolve_source(source)
        files = self._safe_source_files(root)
        source_hash = self._source_hash(root, files)
        parsed = self.parser.parse(skill_file)
        findings = self._scan(root, files)
        if findings:
            raise ExternalSkillImportError(
                "External skill security scan failed: " + ",".join(findings)
            )
        tools, permissions = self._map_tools(parsed.allowed_tools)
        denied = sorted(set(permissions) - self.allowed_permissions)
        if denied:
            raise ExternalSkillImportError(
                f"Mapped permissions require an explicit policy grant: {denied}"
            )
        dependencies = self._dependencies(parsed.metadata)
        missing = [
            item for item in dependencies
            if not self._dependency_is_active(item)
        ]
        if missing:
            raise ExternalSkillImportError(
                f"Dependencies are missing or inactive: {missing}"
            )
        package_path, resources, created = self._normalize(
            root,
            parsed,
            source_hash=source_hash,
            source_label=source_label,
            tools=tools,
            permissions=permissions,
            dependencies=dependencies,
        )
        current_files = self._safe_source_files(root)
        if self._source_hash(root, current_files) != source_hash:
            if created:
                shutil.rmtree(package_path)
            raise ExternalSkillImportError(
                "External skill changed while it was being imported"
            )
        normalized_findings = self._scan(
            package_path, self._safe_source_files(package_path)
        )
        if normalized_findings:
            if created:
                shutil.rmtree(package_path)
            raise ExternalSkillImportError(
                "Normalized skill security scan failed: "
                + ",".join(normalized_findings)
            )
        package = self.loader.load(package_path)
        sandbox_results = self._sandbox_test(package)
        failed = [
            item for item in sandbox_results if item["outcome"] != "passed"
        ]
        if failed:
            if created:
                shutil.rmtree(package_path)
            raise ExternalSkillImportError(
                "Sandbox validation did not pass; package remains unregistered: "
                + ",".join(str(item["stage"]) for item in failed)
            )
        record = self.registry.admit(package_path)
        if record["lifecycle_status"] != "quarantined":
            raise RuntimeError("Imported skill was not quarantined")
        public_record = {
            field: record[field]
            for field in (
                "id", "manifest_id", "name", "version", "status",
                "lifecycle_status", "reliability", "verification_status",
                "content_hash", "package_path",
            )
        }
        return ExternalSkillImportResult(
            source_format="agent-skills-v1",
            source_hash=source_hash,
            package_path=str(package_path),
            content_hash=package.content_hash,
            mapped_tools=tools,
            mapped_permissions=permissions,
            dependencies=dependencies,
            resource_files=resources,
            scan_findings=(),
            sandbox_stages=tuple(sandbox_results),
            registry_record=public_record,
        )

    @staticmethod
    def _resolve_source(source: str | Path) -> tuple[Path, Path]:
        path = Path(source).resolve()
        root = path if path.is_dir() else path.parent
        skill_file = root / "SKILL.md" if path.is_dir() else path
        if skill_file.name != "SKILL.md" or not skill_file.is_file():
            raise ExternalSkillImportError(
                "Source must be an Agent Skills directory or SKILL.md"
            )
        if skill_file.is_symlink():
            raise ExternalSkillImportError("External skill symlinks are forbidden")
        return root, skill_file

    @staticmethod
    def _safe_source_files(root: Path) -> tuple[Path, ...]:
        files: list[Path] = []
        total = 0
        for path in root.rglob("*"):
            if path.is_symlink():
                raise ExternalSkillImportError("External skill symlinks are forbidden")
            resolved = path.resolve()
            if root not in resolved.parents and resolved != root:
                raise ExternalSkillImportError("External skill path escapes its root")
            if path.is_file():
                files.append(path)
                total += path.stat().st_size
        if len(files) > MAX_SOURCE_FILES:
            raise ExternalSkillImportError("External skill has too many files")
        if total > MAX_SOURCE_BYTES:
            raise ExternalSkillImportError("External skill exceeds the size limit")
        return tuple(
            sorted(files, key=lambda item: item.relative_to(root).as_posix())
        )

    @staticmethod
    def _source_hash(root: Path, files: tuple[Path, ...]) -> str:
        digest = hashlib.sha256()
        for path in files:
            digest.update(path.relative_to(root).as_posix().encode("utf-8"))
            digest.update(b"\0")
            digest.update(hashlib.sha256(path.read_bytes()).digest())
        return digest.hexdigest()

    @staticmethod
    def _scan(root: Path, files: tuple[Path, ...]) -> tuple[str, ...]:
        findings: list[str] = []
        executable_binary_suffixes = {
            ".com", ".dll", ".dylib", ".exe", ".jar", ".msi", ".scr", ".so",
        }
        dangerous_code = (
            "shell=true", "os.system(", "subprocess.popen(", "eval(", "exec(",
        )
        for path in files:
            relative = path.relative_to(root).as_posix()
            if path.suffix.casefold() in executable_binary_suffixes:
                findings.append(f"{relative}:executable_binary")
                continue
            try:
                content = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                if relative.startswith(("scripts/", "references/")):
                    findings.append(f"{relative}:unscannable_text")
                continue
            findings.extend(
                f"{relative}:secret_material:{kind}"
                for kind in detect_secret_material(content)
            )
            findings.extend(
                f"{relative}:prompt_injection:{kind}"
                for kind in detect_suspicious_instructions(content)
                if kind in BLOCKING_INSTRUCTION_FINDINGS
                or kind.startswith("secret_material:")
            )
            if path.suffix.casefold() in {".py", ".ps1", ".sh", ".bat", ".cmd"}:
                compact = content.casefold().replace(" ", "")
                findings.extend(
                    f"{relative}:dangerous_execution"
                    for pattern in dangerous_code
                    if pattern.replace(" ", "") in compact
                )
        return tuple(dict.fromkeys(findings))

    @staticmethod
    def _map_tools(
        declarations: tuple[str, ...]
    ) -> tuple[tuple[str, ...], tuple[str, ...]]:
        tools: list[str] = []
        permissions: list[str] = []
        unknown: list[str] = []
        for declaration in declarations:
            base = declaration.split("(", 1)[0].casefold()
            if base == "bash":
                mapped = ("shell.execute", "shell:execute")
            elif base.startswith("mcp__"):
                mapped = (declaration, "external:mcp")
            else:
                mapped = TOOL_MAPPINGS.get(base)
            if mapped is None:
                unknown.append(declaration)
                continue
            tools.append(mapped[0])
            permissions.append(mapped[1])
        if unknown:
            raise ExternalSkillImportError(
                f"Unmapped external tools are denied: {unknown}"
            )
        return tuple(dict.fromkeys(tools)), tuple(dict.fromkeys(permissions))

    @staticmethod
    def _dependencies(metadata: dict[str, str]) -> tuple[str, ...]:
        raw = metadata.get("acr-dependencies", "")
        dependencies = tuple(
            dict.fromkeys(item.strip() for item in raw.split(",") if item.strip())
        )
        for item in dependencies:
            if "@" not in item:
                raise ExternalSkillImportError(
                    "acr-dependencies must use id@semantic-version"
                )
            identifier, version = item.rsplit("@", 1)
            if not IDENTIFIER.fullmatch(identifier) or not re.fullmatch(
                r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)", version
            ):
                raise ExternalSkillImportError(
                    "acr-dependencies must use id@semantic-version"
                )
        return dependencies

    def _dependency_is_active(self, dependency: str) -> bool:
        identifier, version = dependency.rsplit("@", 1)
        row = self.registry.connection.execute(
            """
            SELECT 1 FROM skills
            WHERE manifest_id = ? AND version = ? AND lifecycle_status = 'active'
            """,
            (identifier, version),
        ).fetchone()
        return row is not None

    def _normalize(
        self,
        source_root: Path,
        parsed: ParsedAgentSkill,
        *,
        source_hash: str,
        source_label: str,
        tools: tuple[str, ...],
        permissions: tuple[str, ...],
        dependencies: tuple[str, ...],
    ) -> tuple[Path, tuple[str, ...], bool]:
        imports = self.skills_directory / "imports"
        imports.mkdir(parents=True, exist_ok=True)
        if imports.is_symlink():
            raise ExternalSkillImportError("Import directory cannot be a symlink")
        target = imports / f"{parsed.name}-0.1.0-{source_hash[:12]}"
        if target.is_symlink():
            raise ExternalSkillImportError("Normalized package cannot be a symlink")
        if target.exists():
            self.loader.load(target)
            resources = self._normalized_resources(target)
            return target, resources, False
        temporary = Path(tempfile.mkdtemp(prefix=".import-", dir=imports))
        try:
            for name in ("examples", "tests", "scripts", "assets", "references"):
                (temporary / name).mkdir()
            resources = self._copy_resources(source_root, temporary)
            timestamp = datetime.now(timezone.utc).isoformat()
            token_estimate = estimate_tokens(parsed.instructions)
            if not 1 <= token_estimate <= MAX_INSTRUCTIONS_TOKENS:
                raise ExternalSkillImportError(
                    "Imported instructions exceed the composable token limit"
                )
            author = parsed.metadata.get("author", "external-unknown")
            source_kind = re.sub(
                r"[^a-z0-9._-]+", "-", source_label.casefold()
            ).strip("-")[:64] or "external"
            manifest = {
                "id": parsed.name,
                "name": parsed.name,
                "version": "0.1.0",
                "description": parsed.description,
                "task_classes": ["imported-agent-skill"],
                "inputs": {"task": "user task within imported skill scope"},
                "outputs": {"result": "skill-defined result"},
                "dependencies": list(dependencies),
                "permissions": list(permissions),
                "tools": list(tools),
                "models": ["any"],
                "token_estimate": token_estimate,
                "applicability": [parsed.description],
                "contraindications": [
                    "use before explicit activation",
                    "permissions outside the imported declaration",
                ],
                "verification": ["python tests/import_smoke.py"],
                "author": author,
                "origin": (
                    f"external-agent-skills:{source_kind}:{source_hash}"
                ),
                "created_at": timestamp,
                "updated_at": timestamp,
                "status": "quarantined",
                "reliability": 0.0,
            }
            (temporary / "SKILL.yaml").write_text(
                json.dumps(manifest, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            (temporary / "instructions.md").write_text(
                parsed.instructions + "\n", encoding="utf-8"
            )
            history = {
                "event": "imported",
                "version": "0.1.0",
                "timestamp": timestamp,
                "source_format": "agent-skills-v1",
                "source_hash": source_hash,
                "license": parsed.license,
                "compatibility": parsed.compatibility,
            }
            (temporary / "history.jsonl").write_text(
                json.dumps(history, sort_keys=True) + "\n", encoding="utf-8"
            )
            (temporary / "tests" / "import_smoke.py").write_text(
                "from pathlib import Path\n"
                "root = Path(__file__).parents[1]\n"
                "assert (root / 'SKILL.yaml').is_file()\n"
                "assert (root / 'instructions.md').read_text(encoding='utf-8').strip()\n",
                encoding="utf-8",
            )
            self.loader.load(temporary)
            temporary.replace(target)
            return target, resources, True
        except Exception:
            shutil.rmtree(temporary, ignore_errors=True)
            raise

    @staticmethod
    def _copy_resources(
        source_root: Path, target: Path
    ) -> tuple[str, ...]:
        copied: list[str] = []
        for directory_name in SUPPORTED_RESOURCE_DIRECTORIES:
            source_directory = source_root / directory_name
            if not source_directory.is_dir():
                continue
            target_directory = target / directory_name
            for source_path in source_directory.rglob("*"):
                if not source_path.is_file():
                    continue
                relative = source_path.relative_to(source_directory)
                destination = target_directory / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source_path, destination)
                copied.append(destination.relative_to(target).as_posix())
        return tuple(sorted(copied))

    @staticmethod
    def _normalized_resources(target: Path) -> tuple[str, ...]:
        files: list[str] = []
        for name in ("scripts", "assets", "references"):
            directory = target / name
            files.extend(
                path.relative_to(target).as_posix()
                for path in directory.rglob("*")
                if path.is_file()
            )
        return tuple(sorted(files))

    def _sandbox_test(
        self, package: SkillPackage
    ) -> list[dict[str, object]]:
        stages = (
            ("sandbox_execution", ("package_smoke_test",)),
            ("unit_tests", package.manifest.verification),
            ("adversarial_tests", ADVERSARIAL_SKILL_CASES),
        )
        results: list[dict[str, object]] = []
        for stage, cases in stages:
            evidence = self.sandbox.run(package, stage=stage, cases=cases)
            results.append(
                {
                    "stage": stage,
                    "outcome": evidence.outcome,
                    "score": evidence.score,
                    "details": evidence.details,
                }
            )
            if evidence.outcome != "passed":
                break
        return results
