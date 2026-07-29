from __future__ import annotations

import argparse
import ast
import json
import tomllib
from collections import deque
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable


@dataclass(frozen=True)
class ForbiddenBoundary:
    name: str
    internal: tuple[str, ...]
    external: tuple[str, ...]


@dataclass(frozen=True)
class ArchitecturePolicy:
    root_package: str
    core_modules: tuple[str, ...]
    boundaries: tuple[ForbiddenBoundary, ...]


@dataclass(frozen=True)
class ImportViolation:
    boundary: str
    core_module: str
    forbidden_module: str
    path: tuple[str, ...]


@dataclass(frozen=True)
class ArchitectureReport:
    valid: bool
    root_package: str
    modules_scanned: int
    imports_scanned: int
    core_modules: tuple[str, ...]
    violations: tuple[ImportViolation, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "valid": self.valid,
            "root_package": self.root_package,
            "modules_scanned": self.modules_scanned,
            "imports_scanned": self.imports_scanned,
            "core_modules": list(self.core_modules),
            "violations": [
                {
                    **asdict(violation),
                    "path": list(violation.path),
                }
                for violation in self.violations
            ],
        }


@dataclass(frozen=True)
class _SourceModule:
    name: str
    path: Path
    package: str


def _module_name(root: Path, path: Path, root_package: str) -> tuple[str, str]:
    relative = path.relative_to(root)
    parts = relative.with_suffix("").parts
    if parts[-1] == "__init__":
        module = ".".join((root_package, *parts[:-1]))
        return module, module
    module = ".".join((root_package, *parts))
    return module, module.rsplit(".", 1)[0]


def _discover_modules(repository: Path, root_package: str) -> dict[str, _SourceModule]:
    package_root = repository.joinpath(*root_package.split("."))
    if not package_root.is_dir():
        raise ValueError(f"Root package does not exist: {root_package}")
    discovered: dict[str, _SourceModule] = {}
    for path in sorted(package_root.rglob("*.py")):
        name, package = _module_name(package_root, path, root_package)
        if name in discovered:
            raise ValueError(f"Duplicate module path: {name}")
        discovered[name] = _SourceModule(name=name, path=path, package=package)
    if not discovered:
        raise ValueError(f"Root package contains no Python modules: {root_package}")
    return discovered


def _strict_string_list(value: object, field: str) -> tuple[str, ...]:
    if (
        not isinstance(value, list)
        or any(not isinstance(item, str) or not item.strip() for item in value)
        or len(set(value)) != len(value)
    ):
        raise ValueError(f"{field} must be a unique list of non-empty strings")
    return tuple(value)


def load_policy(path: str | Path) -> ArchitecturePolicy:
    source = Path(path)
    payload = tomllib.loads(source.read_text(encoding="utf-8"))
    if set(payload) != {"schema_version", "root_package", "core_domain", "forbidden"}:
        raise ValueError("Architecture policy has unknown or missing top-level fields")
    if payload["schema_version"] != 1:
        raise ValueError("Unsupported architecture policy schema")
    root_package = payload["root_package"]
    if not isinstance(root_package, str) or not root_package.strip():
        raise ValueError("root_package must be a non-empty string")
    core = payload["core_domain"]
    if not isinstance(core, dict) or set(core) != {"modules"}:
        raise ValueError("core_domain must contain only modules")
    core_modules = _strict_string_list(core["modules"], "core_domain.modules")
    if not core_modules:
        raise ValueError("At least one core domain module is required")
    forbidden = payload["forbidden"]
    if not isinstance(forbidden, dict) or not forbidden:
        raise ValueError("At least one forbidden boundary is required")
    boundaries: list[ForbiddenBoundary] = []
    for name, settings in forbidden.items():
        if (
            not isinstance(name, str)
            or not name.strip()
            or not isinstance(settings, dict)
            or set(settings) != {"internal", "external"}
        ):
            raise ValueError("Each forbidden boundary requires internal and external")
        internal = _strict_string_list(
            settings["internal"], f"forbidden.{name}.internal"
        )
        external = _strict_string_list(
            settings["external"], f"forbidden.{name}.external"
        )
        if not internal and not external:
            raise ValueError(f"Forbidden boundary {name} cannot be empty")
        boundaries.append(
            ForbiddenBoundary(name=name, internal=internal, external=external)
        )
    prefixes = (*core_modules, *(item for b in boundaries for item in b.internal))
    if any(
        module != root_package and not module.startswith(f"{root_package}.")
        for module in prefixes
    ):
        raise ValueError("Internal policy modules must belong to root_package")
    return ArchitecturePolicy(
        root_package=root_package,
        core_modules=core_modules,
        boundaries=tuple(boundaries),
    )


def _resolve_relative(package: str, level: int, module: str | None) -> str:
    parts = package.split(".")
    if level > len(parts):
        return ""
    base = parts[: len(parts) - level + 1]
    if module:
        base.extend(module.split("."))
    return ".".join(base)


def _known_target(name: str, known_modules: set[str]) -> str:
    candidate = name
    while candidate:
        if candidate in known_modules:
            return candidate
        candidate = candidate.rpartition(".")[0]
    return name


def _literal_dynamic_imports(tree: ast.AST) -> Iterable[str]:
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not node.args:
            continue
        function = node.func
        is_import = isinstance(function, ast.Name) and function.id == "__import__"
        is_import_module = (
            isinstance(function, ast.Attribute)
            and function.attr == "import_module"
            and isinstance(function.value, ast.Name)
            and function.value.id == "importlib"
        )
        if (is_import or is_import_module) and isinstance(
            node.args[0], ast.Constant
        ) and isinstance(node.args[0].value, str):
            yield node.args[0].value


def _imports_for(
    source: _SourceModule, known_modules: set[str]
) -> tuple[str, ...]:
    try:
        tree = ast.parse(
            source.path.read_text(encoding="utf-8-sig"),
            filename=str(source.path),
        )
    except (OSError, SyntaxError, UnicodeError) as error:
        raise ValueError(f"Cannot parse {source.path}: {error}") from error
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = (
                _resolve_relative(source.package, node.level, node.module)
                if node.level
                else (node.module or "")
            )
            if not base:
                continue
            for alias in node.names:
                candidate = f"{base}.{alias.name}" if alias.name != "*" else base
                imports.add(
                    candidate if candidate in known_modules else base
                )
    imports.update(_literal_dynamic_imports(tree))
    return tuple(
        sorted(_known_target(name, known_modules) for name in imports if name)
    )


def _matches(module: str, prefixes: tuple[str, ...]) -> bool:
    return any(module == prefix or module.startswith(f"{prefix}.") for prefix in prefixes)


def _reachable_paths(
    graph: dict[str, tuple[str, ...]], source: str
) -> dict[str, tuple[str, ...]]:
    queue: deque[tuple[str, ...]] = deque([(source,)])
    visited = {source}
    paths: dict[str, tuple[str, ...]] = {}
    while queue:
        path = queue.popleft()
        for dependency in graph.get(path[-1], ()):
            candidate = (*path, dependency)
            paths.setdefault(dependency, candidate)
            if dependency in graph and dependency not in visited:
                visited.add(dependency)
                queue.append(candidate)
    return paths


def check_architecture(
    repository: str | Path = ".",
    policy_path: str | Path = "architecture-boundaries.toml",
) -> ArchitectureReport:
    root = Path(repository).resolve()
    policy_source = Path(policy_path)
    if not policy_source.is_absolute():
        policy_source = root / policy_source
    policy = load_policy(policy_source)
    modules = _discover_modules(root, policy.root_package)
    known = set(modules)
    missing_core = sorted(set(policy.core_modules) - known)
    if missing_core:
        raise ValueError(f"Declared core modules do not exist: {missing_core}")
    declared_internal = {
        module for boundary in policy.boundaries for module in boundary.internal
    }
    missing_internal = sorted(declared_internal - known)
    if missing_internal:
        raise ValueError(
            f"Declared forbidden internal modules do not exist: {missing_internal}"
        )
    overlap = sorted(set(policy.core_modules) & declared_internal)
    if overlap:
        raise ValueError(f"Core modules cannot also be forbidden: {overlap}")
    graph = {
        name: _imports_for(source, known)
        for name, source in sorted(modules.items())
    }
    violations: list[ImportViolation] = []
    for core_module in policy.core_modules:
        paths = _reachable_paths(graph, core_module)
        for boundary in policy.boundaries:
            forbidden = (*boundary.internal, *boundary.external)
            for target, path in paths.items():
                if not _matches(target, forbidden):
                    continue
                violations.append(
                    ImportViolation(
                        boundary=boundary.name,
                        core_module=core_module,
                        forbidden_module=target,
                        path=path,
                    )
                )
    ordered = tuple(
        sorted(
            violations,
            key=lambda item: (
                item.core_module,
                item.boundary,
                len(item.path),
                item.path,
            ),
        )
    )
    return ArchitectureReport(
        valid=not ordered,
        root_package=policy.root_package,
        modules_scanned=len(modules),
        imports_scanned=sum(len(imports) for imports in graph.values()),
        core_modules=policy.core_modules,
        violations=ordered,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="acr-architecture",
        description="Enforce ACR core-domain dependency boundaries.",
    )
    parser.add_argument("--repository", default=".")
    parser.add_argument("--policy", default="architecture-boundaries.toml")
    parser.add_argument(
        "command",
        choices=("check",),
        help="Check direct and transitive imports against the policy.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        report = check_architecture(args.repository, args.policy)
    except (OSError, ValueError, tomllib.TOMLDecodeError) as error:
        print(json.dumps({"valid": False, "error": str(error)}, indent=2))
        return 2
    print(json.dumps(report.to_dict(), indent=2))
    return 0 if report.valid else 1


if __name__ == "__main__":
    raise SystemExit(main())
