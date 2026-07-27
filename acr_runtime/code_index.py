from __future__ import annotations

import ast
import hashlib
import json
import multiprocessing
import os
import re
import sqlite3
import stat
import subprocess
import threading
import tomllib
import unicodedata
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

from .scoring import estimate_tokens
from .secret_management import detect_secret_material, redact_secret_text
from .content_security import (
    ContentAssessmentRequest,
    ContentSecurityController,
    detect_suspicious_instructions,
)

PARSER_VERSION = "acr-structural-v1"
DEFAULT_MAX_FILES = 10_000
HARD_MAX_FILES = 20_000
DEFAULT_MAX_FILE_BYTES = 512 * 1024
HARD_MAX_FILE_BYTES = 1024 * 1024
DEFAULT_MAX_TOTAL_BYTES = 50 * 1024 * 1024
HARD_MAX_TOTAL_BYTES = 100 * 1024 * 1024
DEFAULT_CONTEXT_TOKENS = 4_000
HARD_MAX_CONTEXT_TOKENS = 20_000
DEFAULT_CONTEXT_FILES = 12
HARD_MAX_CONTEXT_FILES = 24
MAX_SYMBOLS_PER_FILE = 2_000
MAX_REFERENCES_PER_FILE = 10_000
MAX_GIT_OUTPUT_BYTES = 4 * 1024 * 1024
MAX_PYTHON_AST_BYTES = 256 * 1024
MAX_SOURCE_LINES = 20_000
MAX_SOURCE_LINE_CHARS = 100_000

_SOURCE_SUFFIXES = {
    ".py": "python",
    ".pyi": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
}
_DOCUMENT_SUFFIXES = {".md", ".mdx", ".rst", ".txt"}
_CONFIG_SUFFIXES = {".toml", ".json", ".yaml", ".yml", ".ini", ".cfg"}
_CONFIG_NAMES = {
    "package.json",
    "pyproject.toml",
    "tsconfig.json",
    "vite.config.ts",
    "vite.config.js",
    "webpack.config.js",
    "ruff.toml",
    "mypy.ini",
    "pytest.ini",
}
_DENIED_PARTS = {
    ".git",
    ".acr",
    ".venv",
    "venv",
    "node_modules",
    "__pycache__",
    "dist",
    "build",
    "coverage",
    ".next",
    ".turbo",
    ".cache",
}
_DENIED_NAMES = {
    ".env",
    ".npmrc",
    ".pypirc",
    ".netrc",
    "credentials",
    "credentials.json",
    "id_rsa",
    "id_ed25519",
}
_DENIED_SUFFIXES = {
    ".pem",
    ".key",
    ".p12",
    ".pfx",
    ".crt",
    ".cer",
    ".db",
    ".sqlite",
    ".sqlite3",
    ".zip",
    ".tar",
    ".gz",
    ".7z",
    ".exe",
    ".dll",
    ".so",
    ".dylib",
    ".wasm",
}
_JS_CLASS = re.compile(
    r"^\s*(?:export\s+(?:default\s+)?)?class\s+([A-Za-z_$][\w$]*)"
)
_JS_FUNCTION = re.compile(
    r"^\s*(?:export\s+(?:default\s+)?)?"
    r"(?:async\s+)?function\*?\s+([A-Za-z_$][\w$]*)\s*\(([^)]*)\)"
)
_JS_ARROW = re.compile(
    r"^\s*(?:export\s+)?(?:const|let|var)\s+"
    r"([A-Za-z_$][\w$]*)\s*=\s*(?:async\s+)?(?:\(([^)]*)\)|"
    r"([A-Za-z_$][\w$]*))\s*=>"
)
_JS_INTERFACE = re.compile(
    r"^\s*(?:export\s+)?(?:interface|type)\s+([A-Za-z_$][\w$]*)"
)
_JS_IMPORT_FROM = re.compile(
    r"^\s*import\s+(.+?)\s+from\s+['\"]([^'\"]+)['\"]"
)
_JS_IMPORT_ONLY = re.compile(r"^\s*import\s+['\"]([^'\"]+)['\"]")
_JS_REQUIRE = re.compile(
    r"^\s*(?:const|let|var)\s+(.+?)\s*=\s*require\(['\"]([^'\"]+)['\"]\)"
)
_DOC_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
_DOC_CODE_REFERENCE = re.compile(r"`([A-Za-z_][A-Za-z0-9_.]{1,255})`")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _stable_id(namespace: str, *parts: str) -> str:
    material = "\0".join((namespace, *parts))
    return str(uuid.uuid5(uuid.NAMESPACE_URL, material))


def _hash_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _manifest_entry(
    manifest: Any,
    kind: bytes,
    relative_path: str,
    value: str,
) -> None:
    encoded = relative_path.encode("utf-8")
    manifest.update(kind)
    manifest.update(len(encoded).to_bytes(8, "big"))
    manifest.update(encoded)
    manifest.update(b"\0")
    manifest.update(value.encode("ascii"))
    manifest.update(b"\0")


def _repository_key(root: Path) -> str:
    normalized = os.path.normcase(str(root))
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _safe_relative(value: str) -> str:
    if not value or any(
        unicodedata.category(char) in {"Cc", "Cf"} for char in value
    ):
        raise ValueError("repository path contains unsupported characters")
    path = PurePosixPath(value.replace("\\", "/"))
    if path.is_absolute() or not path.parts or any(
        part in {"", ".", ".."}
        or part != part.strip()
        or part.endswith(".")
        or ":" in part
        for part in path.parts
    ):
        raise ValueError("repository path must be normalized and relative")
    return path.as_posix()


def _contains(root: Path, candidate: Path) -> bool:
    try:
        return os.path.commonpath((str(root), str(candidate))) == str(root)
    except ValueError:
        return False


def _is_reparse_point(path: Path) -> bool:
    attributes = getattr(path.lstat(), "st_file_attributes", 0)
    flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return bool(attributes & flag)


def _path_is_safe(root: Path, relative_path: str) -> Path | None:
    candidate = root
    try:
        for part in PurePosixPath(relative_path).parts:
            candidate = candidate / part
            details = candidate.lstat()
            if stat.S_ISLNK(details.st_mode) or _is_reparse_point(candidate):
                return None
        resolved = candidate.resolve(strict=True)
    except (OSError, RuntimeError):
        return None
    if not _contains(root, resolved):
        return None
    try:
        if not stat.S_ISREG(resolved.stat().st_mode):
            return None
    except OSError:
        return None
    return resolved


def _denied_path(relative_path: str) -> bool:
    path = PurePosixPath(relative_path)
    lowered = tuple(part.casefold() for part in path.parts)
    name = lowered[-1]
    suffix = path.suffix.casefold()
    return (
        any(part in _DENIED_PARTS for part in lowered)
        or name in _DENIED_NAMES
        or name.startswith(".env.")
        or suffix in _DENIED_SUFFIXES
    )


def _classify(relative_path: str) -> tuple[str, str] | None:
    path = PurePosixPath(relative_path)
    name = path.name.casefold()
    suffix = path.suffix.casefold()
    test = (
        name.startswith("test_")
        or name.endswith((".test.ts", ".test.tsx", ".spec.ts", ".spec.tsx"))
        or "tests" in {part.casefold() for part in path.parts[:-1]}
    )
    if suffix in _SOURCE_SUFFIXES:
        return _SOURCE_SUFFIXES[suffix], "test" if test else "source"
    if suffix in _DOCUMENT_SUFFIXES:
        if suffix in {".md", ".mdx"}:
            return "markdown", "documentation"
        return ("text" if suffix == ".txt" else "rst"), "documentation"
    if name in _CONFIG_NAMES or suffix in _CONFIG_SUFFIXES:
        return "configuration", "configuration"
    return None


@dataclass(frozen=True)
class IndexPolicy:
    max_files: int = DEFAULT_MAX_FILES
    max_file_bytes: int = DEFAULT_MAX_FILE_BYTES
    max_total_bytes: int = DEFAULT_MAX_TOTAL_BYTES
    include_untracked: bool = False
    allow_non_git: bool = False

    def __post_init__(self) -> None:
        if not 1 <= self.max_files <= HARD_MAX_FILES:
            raise ValueError(f"max_files must be 1..{HARD_MAX_FILES}")
        if not 1 <= self.max_file_bytes <= HARD_MAX_FILE_BYTES:
            raise ValueError(
                f"max_file_bytes must be 1..{HARD_MAX_FILE_BYTES}"
            )
        if not 1 <= self.max_total_bytes <= HARD_MAX_TOTAL_BYTES:
            raise ValueError(
                f"max_total_bytes must be 1..{HARD_MAX_TOTAL_BYTES}"
            )

    def as_dict(self) -> dict[str, object]:
        return {
            "max_files": self.max_files,
            "max_file_bytes": self.max_file_bytes,
            "max_total_bytes": self.max_total_bytes,
            "include_untracked": self.include_untracked,
            "allow_non_git": self.allow_non_git,
        }


@dataclass(frozen=True)
class _Symbol:
    id: str
    parent_id: str | None
    name: str
    qualified_name: str
    kind: str
    interface: str
    start_line: int
    end_line: int


@dataclass(frozen=True)
class _Import:
    id: str
    module: str
    imported_name: str | None
    alias: str | None
    kind: str
    line: int


@dataclass(frozen=True)
class _Reference:
    id: str
    caller_id: str | None
    target_name: str
    kind: str
    line: int


@dataclass(frozen=True)
class _Dependency:
    id: str
    ecosystem: str
    name: str
    scope: str


@dataclass
class _File:
    id: str
    relative_path: str
    absolute_path: Path = field(repr=False)
    language: str
    kind: str
    size_bytes: int
    mtime_ns: int
    line_count: int
    content_hash: str
    parse_status: str
    error_kind: str | None
    symbols: list[_Symbol] = field(default_factory=list)
    imports: list[_Import] = field(default_factory=list)
    references: list[_Reference] = field(default_factory=list)
    dependencies: list[_Dependency] = field(default_factory=list)


@dataclass(frozen=True)
class CodeIndexResult:
    repository_id: str
    run_id: str
    generation: int
    status: str
    discovery_mode: str
    snapshot_hash: str
    parser_version: str
    counts: dict[str, int]
    skip_counts: dict[str, int]
    indexed_at: str

    def as_dict(self) -> dict[str, object]:
        return {
            "repository_id": self.repository_id,
            "run_id": self.run_id,
            "generation": self.generation,
            "status": self.status,
            "discovery_mode": self.discovery_mode,
            "snapshot_hash": self.snapshot_hash,
            "parser_version": self.parser_version,
            "counts": self.counts,
            "skip_counts": self.skip_counts,
            "indexed_at": self.indexed_at,
            "source_bodies_persisted": False,
            "structural_metadata_persisted": True,
        }


@dataclass(frozen=True)
class CodeContextRequest:
    query: str
    max_tokens: int = DEFAULT_CONTEXT_TOKENS
    max_files: int = DEFAULT_CONTEXT_FILES

    def __post_init__(self) -> None:
        if (
            not self.query.strip()
            or self.query != self.query.strip()
            or len(self.query) > 256
        ):
            raise ValueError("query must be non-empty, trimmed, and at most 256 chars")
        if not 64 <= self.max_tokens <= HARD_MAX_CONTEXT_TOKENS:
            raise ValueError(
                f"max_tokens must be 64..{HARD_MAX_CONTEXT_TOKENS}"
            )
        if not 1 <= self.max_files <= HARD_MAX_CONTEXT_FILES:
            raise ValueError(f"max_files must be 1..{HARD_MAX_CONTEXT_FILES}")


@dataclass(frozen=True)
class CodeContextResult:
    status: str
    complete: bool
    semantic_closure: bool
    query: str
    repository_id: str | None
    index: dict[str, object] | None
    target: dict[str, object] | None
    items: tuple[dict[str, object], ...]
    candidates: tuple[dict[str, object], ...]
    budget: dict[str, object]
    warnings: tuple[str, ...]
    omitted: dict[str, int]

    def as_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "complete": self.complete,
            "semantic_closure": self.semantic_closure,
            "query": self.query,
            "repository_id": self.repository_id,
            "index": self.index,
            "target": self.target,
            "items": list(self.items),
            "candidates": list(self.candidates),
            "budget": self.budget,
            "warnings": list(self.warnings),
            "omitted": self.omitted,
        }


class _PythonStructure(ast.NodeVisitor):
    def __init__(self, file_id: str) -> None:
        self.file_id = file_id
        self.symbols: list[_Symbol] = []
        self.imports: list[_Import] = []
        self.references: list[_Reference] = []
        self._stack: list[_Symbol] = []

    def _qualified(self, name: str) -> str:
        return ".".join((*[item.name for item in self._stack], name))

    def _symbol(
        self,
        node: ast.AST,
        *,
        name: str,
        kind: str,
        interface: str,
    ) -> _Symbol:
        qualified = self._qualified(name)
        decorators = getattr(node, "decorator_list", ())
        start = min(
            [getattr(node, "lineno", 1)]
            + [getattr(item, "lineno", getattr(node, "lineno", 1)) for item in decorators]
        )
        end = int(getattr(node, "end_lineno", getattr(node, "lineno", start)))
        symbol = _Symbol(
            id=_stable_id(
                "code-symbol", self.file_id, kind, qualified, str(start)
            ),
            parent_id=self._stack[-1].id if self._stack else None,
            name=name,
            qualified_name=qualified,
            kind=kind,
            interface=interface[:512],
            start_line=start,
            end_line=end,
        )
        if len(self.symbols) >= MAX_SYMBOLS_PER_FILE:
            raise ValueError("symbol_limit")
        self.symbols.append(symbol)
        return symbol

    @staticmethod
    def _arguments(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
        names = [item.arg for item in node.args.posonlyargs]
        if node.args.posonlyargs:
            names.append("/")
        names.extend(item.arg for item in node.args.args)
        if node.args.vararg is not None:
            names.append(f"*{node.args.vararg.arg}")
        elif node.args.kwonlyargs:
            names.append("*")
        names.extend(item.arg for item in node.args.kwonlyargs)
        if node.args.kwarg is not None:
            names.append(f"**{node.args.kwarg.arg}")
        return ", ".join(names)

    def visit_ClassDef(self, node: ast.ClassDef) -> Any:
        symbol = self._symbol(
            node, name=node.name, kind="class", interface=f"class {node.name}"
        )
        for base in node.bases:
            target = self._expression_name(base)
            if target:
                self._reference(symbol.id, target, "inheritance", node.lineno)
        self._stack.append(symbol)
        for statement in node.body:
            self.visit(statement)
        self._stack.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> Any:
        self._visit_function(node, asynchronous=False)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> Any:
        self._visit_function(node, asynchronous=True)

    def _visit_function(
        self, node: ast.FunctionDef | ast.AsyncFunctionDef, *, asynchronous: bool
    ) -> None:
        kind = "method" if self._stack and self._stack[-1].kind == "class" else "function"
        prefix = "async def" if asynchronous else "def"
        symbol = self._symbol(
            node,
            name=node.name,
            kind=kind,
            interface=f"{prefix} {node.name}({self._arguments(node)})",
        )
        self._stack.append(symbol)
        for statement in node.body:
            self.visit(statement)
        self._stack.pop()

    def visit_Import(self, node: ast.Import) -> Any:
        for item in node.names:
            if len(self.imports) >= MAX_REFERENCES_PER_FILE:
                raise ValueError("import_limit")
            self.imports.append(
                _Import(
                    id=_stable_id(
                        "code-import", self.file_id, str(node.lineno),
                        item.name, item.asname or "",
                    ),
                    module=item.name,
                    imported_name=None,
                    alias=item.asname,
                    kind="import",
                    line=node.lineno,
                )
            )

    def visit_ImportFrom(self, node: ast.ImportFrom) -> Any:
        module = "." * node.level + (node.module or "")
        for item in node.names:
            if len(self.imports) >= MAX_REFERENCES_PER_FILE:
                raise ValueError("import_limit")
            self.imports.append(
                _Import(
                    id=_stable_id(
                        "code-import", self.file_id, str(node.lineno),
                        module, item.name, item.asname or "",
                    ),
                    module=module,
                    imported_name=item.name,
                    alias=item.asname,
                    kind="from",
                    line=node.lineno,
                )
            )

    def visit_Call(self, node: ast.Call) -> Any:
        target = self._expression_name(node.func)
        if target:
            self._reference(
                self._stack[-1].id if self._stack else None,
                target,
                "call",
                node.lineno,
            )
        self.generic_visit(node)

    def _reference(
        self, caller_id: str | None, target: str, kind: str, line: int
    ) -> None:
        if len(self.references) >= MAX_REFERENCES_PER_FILE:
            raise ValueError("reference_limit")
        self.references.append(
            _Reference(
                id=_stable_id(
                    "code-reference", self.file_id, caller_id or "",
                    target, kind, str(line), str(len(self.references)),
                ),
                caller_id=caller_id,
                target_name=target[:512],
                kind=kind,
                line=line,
            )
        )

    @classmethod
    def _expression_name(cls, node: ast.AST) -> str | None:
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            base = cls._expression_name(node.value)
            return f"{base}.{node.attr}" if base else node.attr
        return None


def _python_parser_service(connection: Any) -> None:
    try:
        try:
            import resource

            memory_limit = 256 * 1024 * 1024
            resource.setrlimit(
                resource.RLIMIT_AS, (memory_limit, memory_limit)
            )
        except (ImportError, OSError, ValueError):
            pass
        while True:
            message = connection.recv()
            if message is None:
                break
            file_id, text = message
            try:
                tree = ast.parse(
                    text, filename="<repository-file>", type_comments=True
                )
                visitor = _PythonStructure(file_id)
                visitor.visit(tree)
                connection.send(
                    (
                        "ok",
                        visitor.symbols,
                        visitor.imports,
                        visitor.references,
                    )
                )
            except BaseException as error:
                kind = (
                    str(error)
                    if isinstance(error, ValueError)
                    and str(error) in {
                        "symbol_limit", "reference_limit", "import_limit",
                    }
                    else type(error).__name__.casefold()
                )
                connection.send(("error", kind))
    except (BrokenPipeError, EOFError, OSError):
        pass
    finally:
        connection.close()


class _PythonParserWorker:
    def __init__(self) -> None:
        self._context = multiprocessing.get_context("spawn")
        self._connection: Any = None
        self._process: Any = None

    def _start(self) -> None:
        parent, child = self._context.Pipe(duplex=True)
        process = self._context.Process(
            target=_python_parser_service,
            args=(child,),
            daemon=True,
        )
        process.start()
        child.close()
        self._connection = parent
        self._process = process

    def parse(self, file_id: str, text: str) -> tuple[Any, ...]:
        if self._process is None or not self._process.is_alive():
            self.close()
            self._start()
        try:
            self._connection.send((file_id, text))
            if not self._connection.poll(3):
                raise TimeoutError("ast_timeout")
            return self._connection.recv()
        except (BrokenPipeError, EOFError, OSError, TimeoutError) as error:
            kind = (
                "ast_timeout"
                if isinstance(error, TimeoutError)
                else "ast_worker_failure"
            )
            self.close()
            return ("error", kind)

    def close(self) -> None:
        connection = self._connection
        process = self._process
        self._connection = None
        self._process = None
        if connection is not None:
            if process is not None and process.is_alive():
                try:
                    connection.send(None)
                except (BrokenPipeError, EOFError, OSError):
                    pass
            connection.close()
        if process is not None:
            process.join(timeout=1)
            if process.is_alive():
                process.terminate()
                process.join(timeout=2)


class CodebaseIndexer:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection
        self._python_worker: _PythonParserWorker | None = None

    @staticmethod
    def _root(root: str | Path) -> Path:
        raw = Path(root)
        try:
            details = raw.lstat()
        except OSError as error:
            raise ValueError("repository root is not accessible") from error
        if stat.S_ISLNK(details.st_mode) or _is_reparse_point(raw):
            raise ValueError("repository root cannot be a symlink or reparse point")
        path = raw.resolve(strict=True)
        if not path.is_dir():
            raise ValueError("repository root must be a directory")
        return path

    @staticmethod
    def _git_paths(
        root: Path, include_untracked: bool, max_files: int
    ) -> tuple[str, ...]:
        command = ["git", "-C", str(root), "ls-files", "-z", "--cached"]
        if include_untracked:
            command.extend(("--others", "--exclude-standard"))
        command.append("--deduplicate")
        try:
            process = subprocess.Popen(
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                shell=False,
            )
        except OSError as error:
            raise ValueError("git repository discovery failed") from error
        paths: list[str] = []
        errors: list[Exception] = []
        assert process.stdout is not None

        def consume() -> None:
            pending = b""
            total = 0
            try:
                while True:
                    chunk = process.stdout.read(65_536)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > MAX_GIT_OUTPUT_BYTES:
                        raise ValueError(
                            "Git file listing exceeds the safe output limit"
                        )
                    pending += chunk
                    parts = pending.split(b"\0")
                    pending = parts.pop()
                    for item in parts:
                        if not item:
                            continue
                        try:
                            decoded = item.decode("utf-8", errors="strict")
                            paths.append(_safe_relative(decoded))
                        except (UnicodeDecodeError, ValueError) as error:
                            raise ValueError(
                                "Git file listing contains an unsafe path"
                            ) from error
                        if len(paths) > max_files:
                            raise ValueError(
                                "repository file count exceeds max_files"
                            )
                if pending:
                    raise ValueError("Git file listing is not NUL terminated")
            except Exception as error:
                errors.append(error)

        reader = threading.Thread(target=consume, daemon=True)
        reader.start()
        reader.join(timeout=30)
        if reader.is_alive():
            process.kill()
            reader.join(timeout=5)
            process.wait()
            process.stdout.close()
            raise ValueError("Git repository discovery timed out")
        if errors:
            process.kill()
            process.wait()
            process.stdout.close()
            raise errors[0]
        try:
            return_code = process.wait(timeout=5)
            if return_code != 0:
                raise ValueError(
                    "repository root is not an accessible Git worktree"
                )
        except (subprocess.TimeoutExpired, ValueError):
            process.kill()
            process.wait()
            process.stdout.close()
            raise
        process.stdout.close()
        return tuple(sorted(set(paths), key=lambda value: (value.casefold(), value)))

    @staticmethod
    def _filesystem_paths(root: Path, max_files: int) -> tuple[str, ...]:
        paths: list[str] = []
        for directory, dirnames, filenames in os.walk(
            root, topdown=True, followlinks=False
        ):
            dirnames[:] = sorted(
                (
                    name
                    for name in dirnames
                    if name.casefold() not in _DENIED_PARTS
                    and not (Path(directory) / name).is_symlink()
                    and not _is_reparse_point(Path(directory) / name)
                ),
                key=str.casefold,
            )
            for name in sorted(filenames, key=str.casefold):
                candidate = Path(directory) / name
                relative = candidate.relative_to(root).as_posix()
                paths.append(_safe_relative(relative))
                if len(paths) > max_files:
                    raise ValueError("repository file count exceeds max_files")
        return tuple(sorted(set(paths), key=lambda value: (value.casefold(), value)))

    def _discover(
        self, root: Path, policy: IndexPolicy
    ) -> tuple[str, tuple[str, ...]]:
        try:
            probe = subprocess.run(
                ["git", "-C", str(root), "rev-parse", "--show-toplevel"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                shell=False,
                timeout=10,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise ValueError("Git repository discovery failed") from error
        if probe.returncode == 0:
            try:
                top_level = Path(
                    probe.stdout.decode("utf-8", errors="strict").strip()
                ).resolve(strict=True)
            except (UnicodeDecodeError, OSError, RuntimeError) as error:
                raise ValueError("Git repository root is invalid") from error
            if top_level != root:
                raise ValueError("repository root must be the Git worktree root")
            return "git", self._git_paths(
                root, policy.include_untracked, policy.max_files
            )
        if not policy.allow_non_git:
            raise ValueError("repository root is not an accessible Git worktree")
        return "filesystem", self._filesystem_paths(root, policy.max_files)

    @staticmethod
    def _read_file(path: Path, expected_size: int) -> bytes:
        before = path.lstat()
        if (
            before.st_size != expected_size
            or stat.S_ISLNK(before.st_mode)
            or _is_reparse_point(path)
        ):
            raise ValueError("changed_during_scan")
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(path, flags)
        except OSError as error:
            raise ValueError("changed_during_scan") from error
        with os.fdopen(descriptor, "rb") as handle:
            data = handle.read(expected_size + 1)
            after = os.fstat(handle.fileno())
        if len(data) != expected_size or len(data) > expected_size:
            raise ValueError("changed_during_scan")
        if (
            after.st_size != before.st_size
            or after.st_mtime_ns != before.st_mtime_ns
            or after.st_dev != before.st_dev
            or after.st_ino != before.st_ino
        ):
            raise ValueError("changed_during_scan")
        return data

    def _parse_python(self, file: _File, text: str) -> None:
        lines = text.splitlines()
        if (
            file.size_bytes > MAX_PYTHON_AST_BYTES
            or len(lines) > MAX_SOURCE_LINES
            or any(len(line) > MAX_SOURCE_LINE_CHARS for line in lines)
        ):
            file.parse_status = "unsupported"
            file.error_kind = "ast_resource_limit"
            return
        if self._python_worker is None:
            self._python_worker = _PythonParserWorker()
        payload = self._python_worker.parse(file.id, text)
        if not payload or payload[0] != "ok":
            file.parse_status = (
                "unsupported"
                if len(payload) > 1
                and payload[1] in {"ast_timeout", "ast_worker_failure"}
                else "invalid"
            )
            file.error_kind = (
                str(payload[1])[:64]
                if len(payload) > 1
                else "ast_worker_failure"
            )
            return
        file.symbols = list(payload[1])
        file.imports = list(payload[2])
        file.references = list(payload[3])

    @staticmethod
    def _parse_javascript(file: _File, text: str) -> None:
        def parameter_names(raw: str) -> str:
            names: list[str] = []
            for value in raw.split(","):
                candidate = value.strip().lstrip(".")
                candidate = re.split(r"[:?=]", candidate, 1)[0].strip()
                if re.fullmatch(r"[A-Za-z_$][\w$]*", candidate):
                    names.append(candidate)
            return ", ".join(names)

        lines = text.splitlines()
        in_block_comment = False
        for number, line in enumerate(lines, start=1):
            structural_line = line
            if in_block_comment:
                end = structural_line.find("*/")
                if end < 0:
                    continue
                structural_line = structural_line[end + 2:]
                in_block_comment = False
            while "/*" in structural_line:
                start = structural_line.find("/*")
                end = structural_line.find("*/", start + 2)
                if end < 0:
                    structural_line = structural_line[:start]
                    in_block_comment = True
                    break
                structural_line = (
                    structural_line[:start] + structural_line[end + 2:]
                )
            if structural_line.lstrip().startswith("//"):
                continue
            match = _JS_CLASS.match(structural_line)
            kind = "class"
            interface = ""
            args = ""
            if match is None:
                match = _JS_FUNCTION.match(structural_line)
                kind = "function"
                if match is not None:
                    args = parameter_names(match.group(2))
            if match is None:
                match = _JS_ARROW.match(structural_line)
                kind = "function"
                if match is not None:
                    raw_args = match.group(2) or match.group(3) or ""
                    args = parameter_names(raw_args)
            if match is None and file.language == "typescript":
                match = _JS_INTERFACE.match(structural_line)
                kind = "interface"
            if match is not None:
                if len(file.symbols) >= MAX_SYMBOLS_PER_FILE:
                    file.parse_status = "partial"
                    file.error_kind = "symbol_limit"
                    return
                name = match.group(1)
                interface = (
                    f"{kind} {name}"
                    if kind in {"class", "interface"}
                    else f"function {name}({args})"
                )
                file.symbols.append(
                    _Symbol(
                        id=_stable_id(
                            "code-symbol", file.id, kind, name, str(number)
                        ),
                        parent_id=None,
                        name=name,
                        qualified_name=name,
                        kind=kind,
                        interface=interface,
                        start_line=number,
                        end_line=number,
                    )
                )
            import_match = _JS_IMPORT_FROM.match(structural_line)
            imported: str | None = None
            alias: str | None = None
            import_kind = "import"
            module: str | None = None
            if import_match is not None:
                module = import_match.group(2)
            else:
                import_match = _JS_IMPORT_ONLY.match(structural_line)
                if import_match is not None:
                    module = import_match.group(1)
                else:
                    import_match = _JS_REQUIRE.match(structural_line)
                    if import_match is not None:
                        module = import_match.group(2)
                        import_kind = "require"
            if (
                module
                and re.fullmatch(r"[@A-Za-z0-9_$./-]{1,512}", module)
                and not detect_secret_material(module)
                and not detect_suspicious_instructions(module)
            ):
                if len(file.imports) >= MAX_REFERENCES_PER_FILE:
                    file.parse_status = "partial"
                    file.error_kind = "import_limit"
                    return
                file.imports.append(
                    _Import(
                        id=_stable_id(
                            "code-import", file.id, str(number),
                            module, imported or "",
                        ),
                        module=module,
                        imported_name=imported,
                        alias=alias,
                        kind=import_kind,
                        line=number,
                    )
                )
        file.parse_status = "partial"
        file.error_kind = "lexical_declarations_only"

    @staticmethod
    def _parse_document(file: _File, text: str) -> None:
        if file.language != "markdown":
            file.parse_status = "partial"
            file.error_kind = "headings_unavailable"
            return
        lines = text.splitlines()
        headings: list[tuple[int, str, str]] = []
        fenced_lines: set[int] = set()
        fence_marker: str | None = None
        for number, line in enumerate(lines, start=1):
            stripped = line.lstrip()
            marker = (
                "```" if stripped.startswith("```")
                else "~~~" if stripped.startswith("~~~")
                else None
            )
            if marker is not None:
                fenced_lines.add(number)
                if fence_marker is None:
                    fence_marker = marker
                elif fence_marker == marker:
                    fence_marker = None
                continue
            if fence_marker is not None:
                fenced_lines.add(number)
                continue
            match = _DOC_HEADING.match(line)
            if match is None:
                continue
            heading = match.group(2).strip()
            if (
                not heading
                or detect_secret_material(heading)
                or detect_suspicious_instructions(heading)
            ):
                continue
            headings.append((number, match.group(1), heading))
        for index, (number, marker, heading) in enumerate(headings):
            if len(file.symbols) >= MAX_SYMBOLS_PER_FILE:
                file.parse_status = "partial"
                file.error_kind = "symbol_limit"
                return
            end = (
                headings[index + 1][0] - 1
                if index + 1 < len(headings)
                else max(number, len(lines))
            )
            symbol = _Symbol(
                id=_stable_id(
                    "code-symbol", file.id, "documentation_section",
                    heading, str(number),
                ),
                parent_id=None,
                name=heading[:256],
                qualified_name=heading[:256],
                kind="documentation_section",
                interface=f"{marker} {heading}"[:512],
                start_line=number,
                end_line=end,
            )
            file.symbols.append(symbol)
            for line_number in range(number, end + 1):
                if line_number in fenced_lines:
                    continue
                for match in _DOC_CODE_REFERENCE.finditer(
                    lines[line_number - 1]
                ):
                    target = match.group(1)
                    if (
                        detect_secret_material(target)
                        or detect_suspicious_instructions(target)
                    ):
                        continue
                    if len(file.references) >= MAX_REFERENCES_PER_FILE:
                        file.parse_status = "partial"
                        file.error_kind = "reference_limit"
                        return
                    file.references.append(
                        _Reference(
                            id=_stable_id(
                                "code-reference", file.id, symbol.id, target,
                                "type_reference", str(line_number),
                                str(len(file.references)),
                            ),
                            caller_id=symbol.id,
                            target_name=target,
                            kind="type_reference",
                            line=line_number,
                        )
                    )

    @staticmethod
    def _dependency(
        file: _File, ecosystem: str, name: str, scope: str
    ) -> None:
        if len(file.dependencies) >= MAX_SYMBOLS_PER_FILE:
            file.parse_status = "partial"
            file.error_kind = "dependency_limit"
            return
        normalized = name.strip()
        if (
            not normalized
            or len(normalized) > 256
            or any(ord(char) < 32 for char in normalized)
            or re.fullmatch(r"[@A-Za-z0-9_.\-/]+", normalized) is None
            or detect_secret_material(normalized)
            or detect_suspicious_instructions(normalized)
        ):
            return
        file.dependencies.append(
            _Dependency(
                id=_stable_id(
                    "code-dependency", file.id, ecosystem, normalized, scope
                ),
                ecosystem=ecosystem,
                name=normalized,
                scope=scope,
            )
        )

    @classmethod
    def _parse_config(cls, file: _File, text: str) -> None:
        name = PurePosixPath(file.relative_path).name.casefold()
        file.symbols.append(
            _Symbol(
                id=_stable_id(
                    "code-symbol", file.id, "configuration", name, "1"
                ),
                parent_id=None,
                name=name,
                qualified_name=name,
                kind="configuration",
                interface=f"configuration {name}",
                start_line=1,
                end_line=max(1, file.line_count),
            )
        )
        try:
            if name == "package.json":
                payload = json.loads(text)
                if not isinstance(payload, dict):
                    raise ValueError("package_json_not_object")
                sections = (
                    ("dependencies", "runtime"),
                    ("devDependencies", "development"),
                    ("optionalDependencies", "optional"),
                    ("peerDependencies", "optional"),
                )
                for section, scope in sections:
                    values = payload.get(section, {})
                    if isinstance(values, dict):
                        for dependency in sorted(values):
                            cls._dependency(file, "npm", str(dependency), scope)
            elif name == "pyproject.toml":
                payload = tomllib.loads(text)
                project = payload.get("project", {})
                if isinstance(project, dict):
                    dependencies = project.get("dependencies", ())
                    if isinstance(dependencies, list):
                        for value in dependencies:
                            dependency = re.split(r"[\s<>=!~;\[]", str(value), 1)[0]
                            cls._dependency(file, "python", dependency, "runtime")
                    optional = project.get("optional-dependencies", {})
                    if isinstance(optional, dict):
                        for values in optional.values():
                            if isinstance(values, list):
                                for value in values:
                                    dependency = re.split(
                                        r"[\s<>=!~;\[]", str(value), 1
                                    )[0]
                                    cls._dependency(
                                        file, "python", dependency, "optional"
                                    )
        except (json.JSONDecodeError, tomllib.TOMLDecodeError, ValueError):
            file.parse_status = "invalid"
            file.error_kind = "invalid_dependency_manifest"

    def _scan(
        self, root: Path, policy: IndexPolicy
    ) -> tuple[str, list[_File], dict[str, int], int, int, str]:
        mode, paths = self._discover(root, policy)
        if len(paths) > policy.max_files:
            raise ValueError("repository file count exceeds max_files")
        files: list[_File] = []
        skip_counts: dict[str, int] = {}
        bytes_read = 0
        manifest = hashlib.sha256()
        for relative in paths:
            if (
                detect_secret_material(relative)
                or detect_suspicious_instructions(relative)
            ):
                skip_counts["unsafe_metadata"] = (
                    skip_counts.get("unsafe_metadata", 0) + 1
                )
                continue
            if _denied_path(relative):
                skip_counts["denied_path"] = skip_counts.get("denied_path", 0) + 1
                continue
            classification = _classify(relative)
            if classification is None:
                skip_counts["unsupported_type"] = (
                    skip_counts.get("unsupported_type", 0) + 1
                )
                continue
            absolute = _path_is_safe(root, relative)
            if absolute is None:
                skip_counts["unsafe_path"] = skip_counts.get("unsafe_path", 0) + 1
                continue
            details = absolute.stat()
            if details.st_size > policy.max_file_bytes:
                skip_counts["oversized"] = skip_counts.get("oversized", 0) + 1
                _manifest_entry(
                    manifest, b"O", relative, str(details.st_size)
                )
                continue
            if bytes_read + details.st_size > policy.max_total_bytes:
                raise ValueError("repository text exceeds max_total_bytes")
            try:
                data = self._read_file(absolute, details.st_size)
            except ValueError as error:
                raise ValueError(
                    "repository changed during scan; retry indexing"
                ) from error
            bytes_read += len(data)
            digest = _hash_bytes(data)
            _manifest_entry(manifest, b"F", relative, digest)
            if b"\0" in data[:8192]:
                skip_counts["binary"] = skip_counts.get("binary", 0) + 1
                continue
            try:
                text = data.decode("utf-8-sig", errors="strict")
            except UnicodeDecodeError:
                skip_counts["invalid_encoding"] = (
                    skip_counts.get("invalid_encoding", 0) + 1
                )
                continue
            if detect_secret_material(text):
                skip_counts["secret_material"] = (
                    skip_counts.get("secret_material", 0) + 1
                )
                continue
            language, kind = classification
            file_id = _stable_id("code-file", _repository_key(root), relative)
            record = _File(
                id=file_id,
                relative_path=relative,
                absolute_path=absolute,
                language=language,
                kind=kind,
                size_bytes=len(data),
                mtime_ns=max(0, details.st_mtime_ns),
                line_count=len(text.splitlines()),
                content_hash=digest,
                parse_status="indexed",
                error_kind=None,
            )
            if language == "python":
                self._parse_python(record, text)
            elif language in {"javascript", "typescript"}:
                self._parse_javascript(record, text)
            elif kind == "documentation":
                self._parse_document(record, text)
            elif kind == "configuration":
                self._parse_config(record, text)
            files.append(record)
        return (
            mode,
            files,
            skip_counts,
            len(paths),
            bytes_read,
            manifest.hexdigest(),
        )

    def snapshot(self, root: Path, policy: IndexPolicy) -> str:
        _, paths = self._discover(root, policy)
        bytes_read = 0
        manifest = hashlib.sha256()
        for relative in paths:
            if (
                detect_secret_material(relative)
                or detect_suspicious_instructions(relative)
                or _denied_path(relative)
                or _classify(relative) is None
            ):
                continue
            absolute = _path_is_safe(root, relative)
            if absolute is None:
                continue
            details = absolute.stat()
            if details.st_size > policy.max_file_bytes:
                _manifest_entry(
                    manifest, b"O", relative, str(details.st_size)
                )
                continue
            if bytes_read + details.st_size > policy.max_total_bytes:
                raise ValueError("repository text exceeds max_total_bytes")
            try:
                data = self._read_file(absolute, details.st_size)
            except ValueError as error:
                raise ValueError(
                    "repository changed during snapshot verification"
                ) from error
            bytes_read += len(data)
            _manifest_entry(manifest, b"F", relative, _hash_bytes(data))
        return manifest.hexdigest()

    def index(
        self, root: str | Path, *, policy: IndexPolicy | None = None
    ) -> CodeIndexResult:
        effective = policy or IndexPolicy()
        resolved = self._root(root)
        started_at = _utc_now()
        try:
            mode, files, skips, seen, bytes_read, snapshot = self._scan(
                resolved, effective
            )
        finally:
            if self._python_worker is not None:
                self._python_worker.close()
                self._python_worker = None
        repository_key = _repository_key(resolved)
        repository_id = _stable_id("code-repository", repository_key)
        row = self.connection.execute(
            "SELECT generation FROM code_repositories WHERE repository_key = ?",
            (repository_key,),
        ).fetchone()
        generation = (int(row[0]) if row is not None else 0) + 1
        run_id = _stable_id(
            "code-index-run", repository_id, str(generation), snapshot
        )
        completed_at = _utc_now()
        config_json = json.dumps(effective.as_dict(), sort_keys=True)
        counts = {
            "files_seen": seen,
            "files_indexed": len(files),
            "files_skipped": sum(skips.values()),
            "symbols_indexed": sum(len(item.symbols) for item in files),
            "imports_indexed": sum(len(item.imports) for item in files),
            "references_indexed": sum(len(item.references) for item in files),
            "dependencies_indexed": sum(
                len(item.dependencies) for item in files
            ),
            "bytes_read": bytes_read,
        }
        try:
            self.connection.execute("BEGIN IMMEDIATE")
            self.connection.execute(
                """
                INSERT INTO code_repositories(
                    id, repository_key, discovery_mode, snapshot_hash,
                    parser_version, index_config_json, generation,
                    current_run_id, indexed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(repository_key) DO UPDATE SET
                    discovery_mode=excluded.discovery_mode,
                    snapshot_hash=excluded.snapshot_hash,
                    parser_version=excluded.parser_version,
                    index_config_json=excluded.index_config_json,
                    generation=excluded.generation,
                    current_run_id=excluded.current_run_id,
                    indexed_at=excluded.indexed_at
                """,
                (
                    repository_id, repository_key, mode, snapshot,
                    PARSER_VERSION, config_json, generation, run_id, completed_at,
                ),
            )
            self.connection.execute(
                "DELETE FROM code_files WHERE repository_id = ?",
                (repository_id,),
            )
            self.connection.execute(
                """
                INSERT INTO code_index_runs(
                    id, repository_id, generation, discovery_mode,
                    snapshot_hash, parser_version, index_config_json, status,
                    files_seen, files_indexed, files_skipped, symbols_indexed,
                    imports_indexed, references_indexed, dependencies_indexed,
                    bytes_read, skip_counts_json, started_at, completed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'completed', ?, ?, ?, ?, ?, ?,
                          ?, ?, ?, ?, ?)
                """,
                (
                    run_id, repository_id, generation, mode, snapshot,
                    PARSER_VERSION, config_json, counts["files_seen"],
                    counts["files_indexed"], counts["files_skipped"],
                    counts["symbols_indexed"], counts["imports_indexed"],
                    counts["references_indexed"], counts["dependencies_indexed"],
                    counts["bytes_read"], json.dumps(skips, sort_keys=True),
                    started_at, completed_at,
                ),
            )
            for file in files:
                self._insert_file(repository_id, file)
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise
        return CodeIndexResult(
            repository_id=repository_id,
            run_id=run_id,
            generation=generation,
            status="completed",
            discovery_mode=mode,
            snapshot_hash=snapshot,
            parser_version=PARSER_VERSION,
            counts=counts,
            skip_counts=skips,
            indexed_at=completed_at,
        )

    def _insert_file(self, repository_id: str, file: _File) -> None:
        self.connection.execute(
            """
            INSERT INTO code_files(
                id, repository_id, relative_path, language, file_kind,
                size_bytes, mtime_ns, line_count, content_hash, parse_status,
                error_kind, is_test, is_documentation, is_configuration
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                file.id, repository_id, file.relative_path, file.language,
                file.kind, file.size_bytes, file.mtime_ns, file.line_count,
                file.content_hash, file.parse_status, file.error_kind,
                int(file.kind == "test"), int(file.kind == "documentation"),
                int(file.kind == "configuration"),
            ),
        )
        for symbol in file.symbols:
            self.connection.execute(
                """
                INSERT INTO code_symbols(
                    id, file_id, parent_symbol_id, name, qualified_name,
                    symbol_kind, interface, start_line, end_line
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    symbol.id, file.id, symbol.parent_id, symbol.name,
                    symbol.qualified_name, symbol.kind, symbol.interface,
                    symbol.start_line, symbol.end_line,
                ),
            )
        for item in file.imports:
            self.connection.execute(
                """
                INSERT INTO code_imports(
                    id, file_id, module, imported_name, alias, import_kind, line
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item.id, file.id, item.module, item.imported_name,
                    item.alias, item.kind, item.line,
                ),
            )
        for item in file.references:
            self.connection.execute(
                """
                INSERT INTO code_references(
                    id, file_id, caller_symbol_id, target_name,
                    reference_kind, line
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    item.id, file.id, item.caller_id, item.target_name,
                    item.kind, item.line,
                ),
            )
        for item in file.dependencies:
            self.connection.execute(
                """
                INSERT OR IGNORE INTO code_dependencies(
                    id, repository_id, source_file_id, ecosystem,
                    dependency_name, dependency_scope
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    item.id, repository_id, file.id, item.ecosystem,
                    item.name, item.scope,
                ),
            )


class StructuralCodeRetriever:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    @staticmethod
    def _root(root: str | Path) -> Path:
        return CodebaseIndexer._root(root)

    def _repository(self, root: Path) -> sqlite3.Row | None:
        return self.connection.execute(
            """
            SELECT * FROM code_repositories
            WHERE repository_key = ?
            """,
            (_repository_key(root),),
        ).fetchone()

    @staticmethod
    def _snippet(
        root: Path, row: sqlite3.Row, start: int, end: int
    ) -> tuple[str | None, bool, int, tuple[str, ...]]:
        relative = _safe_relative(str(row["relative_path"]))
        path = _path_is_safe(root, relative)
        if path is None:
            return None, True, 0, ()
        try:
            data = CodebaseIndexer._read_file(path, int(row["size_bytes"]))
        except (OSError, ValueError):
            return None, True, 0, ()
        if _hash_bytes(data) != row["content_hash"]:
            return None, True, 0, ()
        try:
            text = data.decode("utf-8-sig", errors="strict")
        except UnicodeDecodeError:
            return None, True, 0, ()
        lines = text.splitlines()
        if start < 1 or end < start or end > len(lines):
            return None, True, 0, ()
        raw = "\n".join(lines[start - 1:end])
        redacted = redact_secret_text(raw)
        signals = detect_suspicious_instructions(redacted)
        request = ContentAssessmentRequest(
            origin="document",
            source_id=relative,
            content=redacted,
            provenance=(f"sha256:{row['content_hash']}",),
        )
        framed = ContentSecurityController.frame_untrusted(
            request,
            {"content_hash": row["content_hash"]},
        )
        return framed, False, int(redacted != raw), signals

    def _symbols(
        self, repository_id: str, query: str
    ) -> list[sqlite3.Row]:
        folded = query.casefold()
        rows = self.connection.execute(
            """
            SELECT s.*, f.relative_path, f.language, f.file_kind,
                   f.content_hash, f.size_bytes, f.parse_status, f.error_kind
            FROM code_symbols s
            JOIN code_files f ON f.id = s.file_id
            WHERE f.repository_id = ?
              AND (
                  lower(s.qualified_name) = ?
                  OR lower(s.name) = ?
              )
            ORDER BY
              CASE WHEN lower(s.qualified_name) = ? THEN 0 ELSE 1 END,
              f.relative_path, s.qualified_name, s.start_line
            LIMIT 25
            """,
            (repository_id, folded, folded, folded),
        ).fetchall()
        return list(rows)

    def _candidate_payload(self, row: sqlite3.Row) -> dict[str, object]:
        return {
            "symbol_id": row["id"],
            "qualified_name": row["qualified_name"],
            "name": row["name"],
            "kind": row["symbol_kind"],
            "path": row["relative_path"],
            "language": row["language"],
            "span": {
                "start_line": row["start_line"],
                "end_line": row["end_line"],
            },
            "interface": row["interface"],
            "parse_status": row["parse_status"],
            "parser_note": row["error_kind"],
        }

    def retrieve(
        self, root: str | Path, request: CodeContextRequest
    ) -> CodeContextResult:
        resolved = self._root(root)
        repository = self._repository(resolved)
        byte_limit = min(256 * 1024, request.max_tokens * 8)
        empty_budget = {
            "estimated_tokens": {"used": 0, "limit": request.max_tokens},
            "files": {"used": 0, "limit": request.max_files},
            "bytes": {"used": 0, "limit": byte_limit},
            "estimator": "acr-estimate-tokens-v1",
        }
        if repository is None:
            return CodeContextResult(
                status="unavailable", complete=False, semantic_closure=False,
                query=request.query,
                repository_id=None, index=None, target=None, items=(),
                candidates=(), budget=empty_budget,
                warnings=("repository_not_indexed",), omitted={},
            )
        index = {
            "generation": repository["generation"],
            "run_id": repository["current_run_id"],
            "snapshot_hash": repository["snapshot_hash"],
            "parser_version": repository["parser_version"],
            "indexed_at": repository["indexed_at"],
        }
        try:
            policy_payload = json.loads(repository["index_config_json"])
            policy = IndexPolicy(**policy_payload)
            current_snapshot = CodebaseIndexer(self.connection).snapshot(
                resolved, policy
            )
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return CodeContextResult(
                status="unavailable", complete=False, semantic_closure=False,
                query=request.query,
                repository_id=repository["id"], index=index, target=None,
                items=(), candidates=(), budget=empty_budget,
                warnings=("snapshot_verification_failed",), omitted={},
            )
        if current_snapshot != repository["snapshot_hash"]:
            return CodeContextResult(
                status="stale", complete=False, semantic_closure=False,
                query=request.query,
                repository_id=repository["id"], index=index, target=None,
                items=(), candidates=(), budget=empty_budget,
                warnings=("repository_snapshot_changed_reindex_required",),
                omitted={},
            )
        candidates = self._symbols(repository["id"], request.query)
        if not candidates:
            return CodeContextResult(
                status="not_found", complete=True, semantic_closure=False,
                query=request.query,
                repository_id=repository["id"], index=index, target=None,
                items=(), candidates=(), budget=empty_budget, warnings=(),
                omitted={},
            )
        exact_qualified = (
            [
                item for item in candidates
                if item["qualified_name"].casefold() == request.query.casefold()
            ]
            if "." in request.query
            else []
        )
        selectable = exact_qualified or candidates
        if len(selectable) != 1:
            return CodeContextResult(
                status="ambiguous", complete=False, semantic_closure=False,
                query=request.query,
                repository_id=repository["id"], index=index, target=None,
                items=(), candidates=tuple(
                    self._candidate_payload(item) for item in selectable[:12]
                ),
                budget=empty_budget, warnings=("qualified_symbol_required",),
                omitted={"ambiguous_candidates": max(0, len(selectable) - 12)},
            )
        target_row = selectable[0]
        target_source, stale, redactions, target_signals = self._snippet(
            resolved, target_row, target_row["start_line"], target_row["end_line"]
        )
        if stale:
            return CodeContextResult(
                status="stale", complete=False, semantic_closure=False,
                query=request.query,
                repository_id=repository["id"], index=index,
                target=self._candidate_payload(target_row), items=(),
                candidates=(), budget=empty_budget,
                warnings=("target_hash_changed_reindex_required",), omitted={},
            )
        assert target_source is not None
        target_tokens = estimate_tokens(target_source)
        target_bytes = len(target_source.encode("utf-8"))
        if target_tokens > request.max_tokens or target_bytes > byte_limit:
            return CodeContextResult(
                status="unavailable", complete=False, semantic_closure=False,
                query=request.query,
                repository_id=repository["id"], index=index,
                target=self._candidate_payload(target_row), items=(),
                candidates=(), budget=empty_budget,
                warnings=("target_exceeds_budget",), omitted={},
            )
        target = {
            **self._candidate_payload(target_row),
            "relation": "target_definition",
            "confidence": (
                "exact"
                if target_row["parse_status"] == "indexed"
                else "lexical"
            ),
            "source": target_source,
            "redactions": redactions,
            "safety_signals": list(target_signals),
            "provenance": {
                "origin": "repository",
                "authority": "none",
                "hash_verified": True,
            },
        }
        related, relationship_omitted = self._related(
            repository["id"], target_row
        )
        items: list[dict[str, object]] = []
        used_tokens = target_tokens
        used_bytes = target_bytes
        used_files = {str(target_row["relative_path"])}
        omitted: dict[str, int] = dict(relationship_omitted)
        warnings: list[str] = []
        if target_row["parse_status"] != "indexed":
            warnings.append("target_parser_is_partial")
        if relationship_omitted:
            warnings.append("bounded_lexical_graph_is_not_semantic_closure")
        for relation, row, confidence, reason in related:
            relative = str(row["relative_path"])
            if relative not in used_files and len(used_files) >= request.max_files:
                omitted[relation] = omitted.get(relation, 0) + 1
                continue
            if relation == "candidate_config":
                used_files.add(relative)
                items.append(
                    {
                        "symbol_id": row["id"],
                        "qualified_name": row["qualified_name"],
                        "kind": row["symbol_kind"],
                        "path": relative,
                        "span": None,
                        "interface": row["interface"],
                        "relation": relation,
                        "confidence": confidence,
                        "reason": reason,
                        "source": None,
                        "metadata_only": True,
                        "redactions": 0,
                        "safety_signals": [],
                        "provenance": {
                            "origin": "repository",
                            "authority": "none",
                            "hash_verified": True,
                        },
                    }
                )
                continue
            source, neighbor_stale, neighbor_redactions, neighbor_signals = self._snippet(
                resolved, row, row["start_line"], row["end_line"]
            )
            if neighbor_stale or source is None:
                warnings.append("stale_neighbor")
                omitted[relation] = omitted.get(relation, 0) + 1
                continue
            tokens = estimate_tokens(source)
            source_bytes = len(source.encode("utf-8"))
            if (
                used_tokens + tokens > request.max_tokens
                or used_bytes + source_bytes > byte_limit
            ):
                omitted[relation] = omitted.get(relation, 0) + 1
                continue
            used_tokens += tokens
            used_bytes += source_bytes
            used_files.add(relative)
            items.append(
                {
                    "symbol_id": row["id"],
                    "qualified_name": row["qualified_name"],
                    "kind": row["symbol_kind"],
                    "path": relative,
                    "span": {
                        "start_line": row["start_line"],
                        "end_line": row["end_line"],
                    },
                    "interface": row["interface"],
                    "relation": relation,
                    "confidence": confidence,
                    "reason": reason,
                    "source": source,
                    "redactions": neighbor_redactions,
                    "safety_signals": list(neighbor_signals),
                    "provenance": {
                        "origin": "repository",
                        "authority": "none",
                        "hash_verified": True,
                    },
                }
            )
        complete = not omitted and not warnings
        status = "available" if complete else "partial"
        budget = {
            "estimated_tokens": {
                "used": used_tokens, "limit": request.max_tokens
            },
            "files": {"used": len(used_files), "limit": request.max_files},
            "bytes": {
                "used": used_bytes, "limit": byte_limit
            },
            "estimator": "acr-estimate-tokens-v1",
        }
        return CodeContextResult(
            status=status, complete=complete, semantic_closure=False,
            query=request.query,
            repository_id=repository["id"], index=index, target=target,
            items=tuple(items), candidates=(), budget=budget,
            warnings=tuple(sorted(set(warnings))), omitted=omitted,
        )

    def _related(
        self, repository_id: str, target: sqlite3.Row
    ) -> tuple[
        list[tuple[str, sqlite3.Row, str, str]],
        dict[str, int],
    ]:
        name = str(target["name"])
        qualified = str(target["qualified_name"])
        caller_rows = self.connection.execute(
            """
                SELECT DISTINCT s.*, f.relative_path, f.language, f.file_kind,
                   f.content_hash, f.size_bytes, f.parse_status, f.error_kind
            FROM code_references r
            JOIN code_symbols s ON s.id = r.caller_symbol_id
            JOIN code_files f ON f.id = s.file_id
            WHERE f.repository_id = ? AND r.reference_kind = 'call'
              AND (
                r.target_name = ?
                OR substr(r.target_name, -(length(?) + 1)) = '.' || ?
                OR r.target_name = ?
                OR substr(r.target_name, -(length(?) + 1)) = '.' || ?
              )
              AND s.id != ?
            ORDER BY
                CASE WHEN f.is_test = 1 THEN 1 ELSE 0 END,
                f.relative_path, s.qualified_name, s.start_line
            LIMIT 101
            """,
            (
                repository_id,
                name, name, name,
                qualified, qualified, qualified,
                target["id"],
            ),
        ).fetchall()
        omitted: dict[str, int] = {}
        if len(caller_rows) > 100:
            omitted["lexical_call_site"] = len(caller_rows) - 100
        callers = caller_rows[:100]
        callee_names = self.connection.execute(
            """
            SELECT DISTINCT target_name
            FROM code_references
            WHERE caller_symbol_id = ? AND reference_kind = 'call'
            ORDER BY target_name
            LIMIT 101
            """,
            (target["id"],),
        ).fetchall()
        if len(callee_names) > 100:
            omitted["possible_callee"] = len(callee_names) - 100
            callee_names = callee_names[:100]
        callees: list[sqlite3.Row] = []
        for item in callee_names:
            tail = str(item["target_name"]).rsplit(".", 1)[-1]
            matches = self.connection.execute(
                """
                SELECT s.*, f.relative_path, f.language, f.file_kind,
                       f.content_hash, f.size_bytes, f.parse_status, f.error_kind
                FROM code_symbols s
                JOIN code_files f ON f.id = s.file_id
                WHERE f.repository_id = ? AND s.name = ? AND s.id != ?
                ORDER BY
                    CASE WHEN f.id = ? THEN 0 ELSE 1 END,
                    f.relative_path, s.qualified_name, s.start_line
                LIMIT 2
                """,
                (repository_id, tail, target["id"], target["file_id"]),
            ).fetchall()
            if len(matches) == 1:
                callees.append(matches[0])
            else:
                omitted["unresolved_callee"] = (
                    omitted.get("unresolved_callee", 0) + 1
                )
        related: list[tuple[str, sqlite3.Row, str, str]] = []
        for row in callers:
            relation = (
                "test_lexical_call_site"
                if row["file_kind"] == "test"
                else "lexical_call_site"
            )
            related.append(
                (
                    relation, row, "lexical",
                    "Python AST call name matched; receiver types are not inferred",
                )
            )
        for row in callees:
            related.append(
                (
                    "possible_callee", row, "lexical",
                    "Unique indexed symbol matched the target call name",
                )
            )
        doc_rows = self.connection.execute(
            """
            SELECT DISTINCT s.*, f.relative_path, f.language, f.file_kind,
                   f.content_hash, f.size_bytes, f.parse_status, f.error_kind
            FROM code_references r
            JOIN code_symbols s ON s.id = r.caller_symbol_id
            JOIN code_files f ON f.id = s.file_id
            WHERE f.repository_id = ?
              AND r.reference_kind = 'type_reference'
              AND (r.target_name = ? OR r.target_name = ?)
            ORDER BY f.relative_path, s.start_line
            LIMIT 21
            """,
            (repository_id, name, qualified),
        ).fetchall()
        if len(doc_rows) > 20:
            omitted["exact_text_reference"] = len(doc_rows) - 20
            doc_rows = doc_rows[:20]
        for row in doc_rows:
            related.append(
                (
                    "exact_text_reference", row, "exact",
                    "Documentation section contains an exact code reference",
                )
            )
        config_names: tuple[str, ...]
        if target["language"] == "python":
            config_names = ("pyproject.toml", "pytest.ini", "mypy.ini", "ruff.toml")
        elif target["language"] == "typescript":
            config_names = ("tsconfig.json", "vite.config.ts")
        elif target["language"] == "javascript":
            config_names = ("package.json", "vite.config.js", "webpack.config.js")
        else:
            config_names = ()
        if config_names:
            placeholders = ",".join("?" for _ in config_names)
            config_rows = self.connection.execute(
                f"""
                SELECT s.*, f.relative_path, f.language, f.file_kind,
                       f.content_hash, f.size_bytes, f.parse_status, f.error_kind
                FROM code_symbols s
                JOIN code_files f ON f.id = s.file_id
                WHERE f.repository_id = ?
                  AND s.symbol_kind = 'configuration'
                  AND s.name IN ({placeholders})
                ORDER BY
                  CASE
                    WHEN instr(?, '/') > 0
                     AND substr(f.relative_path, 1, instr(?, '/') - 1)
                         = substr(?, 1, instr(?, '/') - 1)
                    THEN 0 ELSE 1
                  END,
                  f.relative_path
                LIMIT 5
                """,
                (
                    repository_id, *config_names,
                    target["relative_path"], target["relative_path"],
                    target["relative_path"], target["relative_path"],
                ),
            ).fetchall()
            if len(config_rows) > 4:
                omitted["candidate_config"] = len(config_rows) - 4
                config_rows = config_rows[:4]
            for row in config_rows:
                related.append(
                    (
                        "candidate_config", row, "heuristic",
                        "Nearest indexed configuration candidate for the target language",
                    )
                )
        relation_order = {
            "lexical_call_site": 0,
            "possible_callee": 1,
            "test_lexical_call_site": 2,
            "candidate_config": 3,
            "exact_text_reference": 4,
        }
        related.sort(
            key=lambda item: (
                relation_order[item[0]],
                str(item[1]["relative_path"]).casefold(),
                str(item[1]["qualified_name"]).casefold(),
                int(item[1]["start_line"]),
            )
        )
        return related, omitted
