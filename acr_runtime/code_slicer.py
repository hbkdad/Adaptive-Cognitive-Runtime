from __future__ import annotations

import ast
import builtins
import hashlib
import json
import multiprocessing
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .code_index import (
    HARD_MAX_CONTEXT_TOKENS,
    CodebaseIndexer,
    IndexPolicy,
    _hash_bytes,
    _path_is_safe,
    _repository_key,
    _safe_relative,
)
from .content_security import (
    ContentAssessmentRequest,
    ContentSecurityController,
    detect_suspicious_instructions,
)
from .scoring import estimate_tokens
from .secret_management import detect_secret_material

SLICER_VERSION = "acr-python-slicer-v1"
PYTHON_GRAMMAR = f"{sys.version_info.major}.{sys.version_info.minor}"
DEFAULT_MAX_DEPENDENCIES = 16
HARD_MAX_DEPENDENCIES = 48
MAX_UNRESOLVED_NAMES = 100
MAX_SLICE_SOURCE_BYTES = 256 * 1024
MAX_AST_NODES = 100_000
MAX_AST_DEPTH = 400

_BUILTIN_NAMES = frozenset(dir(builtins)) | {
    "__annotations__",
    "__class__",
    "__file__",
    "__name__",
    "__package__",
}


@dataclass(frozen=True)
class PythonSliceRequest:
    query: str
    max_tokens: int = 4_000
    max_dependencies: int = DEFAULT_MAX_DEPENDENCIES

    def __post_init__(self) -> None:
        if (
            not self.query.strip()
            or self.query != self.query.strip()
            or len(self.query) > 256
        ):
            raise ValueError(
                "query must be non-empty, trimmed, and at most 256 chars"
            )
        if not 64 <= self.max_tokens <= HARD_MAX_CONTEXT_TOKENS:
            raise ValueError(
                f"max_tokens must be 64..{HARD_MAX_CONTEXT_TOKENS}"
            )
        if not 0 <= self.max_dependencies <= HARD_MAX_DEPENDENCIES:
            raise ValueError(
                f"max_dependencies must be 0..{HARD_MAX_DEPENDENCIES}"
            )


@dataclass(frozen=True)
class PythonSliceResult:
    status: str
    complete: bool
    semantic_closure: bool
    query: str
    repository_id: str | None
    index: dict[str, object] | None
    target: dict[str, object] | None
    source: str | None
    segments: tuple[dict[str, object], ...]
    unresolved_names: tuple[str, ...]
    warnings: tuple[str, ...]
    omitted: dict[str, int]
    budget: dict[str, object]
    comparison: dict[str, object] | None

    def as_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "complete": self.complete,
            "semantic_closure": self.semantic_closure,
            "query": self.query,
            "repository_id": self.repository_id,
            "index": self.index,
            "target": self.target,
            "source": self.source,
            "segments": list(self.segments),
            "unresolved_names": list(self.unresolved_names),
            "warnings": list(self.warnings),
            "omitted": self.omitted,
            "budget": self.budget,
            "comparison": self.comparison,
        }


def _definition_start(node: ast.AST) -> int:
    decorators = getattr(node, "decorator_list", ())
    return min(
        [int(getattr(node, "lineno", 1))]
        + [
            int(getattr(item, "lineno", getattr(node, "lineno", 1)))
            for item in decorators
        ]
    )


def _bound_names(target: ast.AST) -> set[str]:
    names: set[str] = set()
    if isinstance(target, (ast.FunctionDef, ast.AsyncFunctionDef)):
        arguments = target.args
        names.update(item.arg for item in arguments.posonlyargs)
        names.update(item.arg for item in arguments.args)
        names.update(item.arg for item in arguments.kwonlyargs)
        if arguments.vararg is not None:
            names.add(arguments.vararg.arg)
        if arguments.kwarg is not None:
            names.add(arguments.kwarg.arg)
    return names


class _ReferenceCollector(ast.NodeVisitor):
    def __init__(self, root: ast.AST) -> None:
        self.root = root
        self.loads: set[str] = set()
        self.definition_time_loads: set[str] = set()
        self.locals = _bound_names(root)
        self.globals: set[str] = set()
        self.nonlocals: set[str] = set()
        self.self_methods: set[str] = set()
        self.nested_scopes = 0
        self.dynamic_lookup = False

    def visit_FunctionDef(self, node: ast.FunctionDef) -> Any:
        if node is self.root:
            self._visit_function_parts(node)
            return
        self.locals.add(node.name)
        self.nested_scopes += 1

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> Any:
        if node is self.root:
            self._visit_function_parts(node)
            return
        self.locals.add(node.name)
        self.nested_scopes += 1

    def visit_ClassDef(self, node: ast.ClassDef) -> Any:
        if node is self.root:
            for decorator in node.decorator_list:
                self._visit_definition_time(decorator)
            for base in node.bases:
                self._visit_definition_time(base)
            for keyword in node.keywords:
                self._visit_definition_time(keyword.value)
            for statement in node.body:
                self.visit(statement)
            return
        self.locals.add(node.name)
        self.nested_scopes += 1

    def _visit_function_parts(
        self, node: ast.FunctionDef | ast.AsyncFunctionDef
    ) -> None:
        for decorator in node.decorator_list:
            self._visit_definition_time(decorator)
        for default in node.args.defaults:
            self._visit_definition_time(default)
        for default in node.args.kw_defaults:
            if default is not None:
                self._visit_definition_time(default)
        for argument in (
            *node.args.posonlyargs,
            *node.args.args,
            *node.args.kwonlyargs,
        ):
            if argument.annotation is not None:
                self._visit_definition_time(argument.annotation)
        if node.args.vararg is not None and node.args.vararg.annotation is not None:
            self._visit_definition_time(node.args.vararg.annotation)
        if node.args.kwarg is not None and node.args.kwarg.annotation is not None:
            self._visit_definition_time(node.args.kwarg.annotation)
        if node.returns is not None:
            self._visit_definition_time(node.returns)
        for statement in node.body:
            self.visit(statement)

    def _visit_definition_time(self, node: ast.AST) -> None:
        before = set(self.loads)
        self.visit(node)
        self.definition_time_loads.update(self.loads - before)

    def visit_Name(self, node: ast.Name) -> Any:
        if isinstance(node.ctx, ast.Load):
            self.loads.add(node.id)
        elif isinstance(node.ctx, (ast.Store, ast.Del)):
            self.locals.add(node.id)

    def visit_Global(self, node: ast.Global) -> Any:
        self.globals.update(node.names)

    def visit_Nonlocal(self, node: ast.Nonlocal) -> Any:
        self.nonlocals.update(node.names)

    def visit_Call(self, node: ast.Call) -> Any:
        if (
            isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id in {"self", "cls"}
        ):
            self.self_methods.add(node.func.attr)
        if (
            isinstance(node.func, ast.Name)
            and node.func.id
            in {"eval", "exec", "globals", "locals", "getattr", "__import__"}
        ) or (
            isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "importlib"
        ):
            self.dynamic_lookup = True
        self.generic_visit(node)

    @property
    def global_references(self) -> set[str]:
        return (
            (self.loads - self.locals)
            | self.definition_time_loads
            | self.globals
            | self.nonlocals
        ) - _BUILTIN_NAMES


def _collect_enclosed_references(
    root: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef,
) -> tuple[set[str], set[str], set[str], set[str], bool, int]:
    references: set[str] = set()
    definition_time_references: set[str] = set()
    root_locals: set[str] = set()
    methods: set[str] = set()
    dynamic = False
    nested_count = 0
    queue: list[
        tuple[
            ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef,
            set[str],
        ]
    ] = [(root, set())]
    while queue:
        node, outer_locals = queue.pop()
        collector = _ReferenceCollector(node)
        collector.visit(node)
        if node is root:
            root_locals = set(collector.locals)
        references.update(collector.global_references - outer_locals)
        definition_time_references.update(
            collector.definition_time_loads - _BUILTIN_NAMES
        )
        methods.update(collector.self_methods)
        dynamic = dynamic or collector.dynamic_lookup
        children = [
            statement
            for statement in node.body
            if isinstance(
                statement,
                (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef),
            )
        ]
        nested_count += len(children)
        child_outer = outer_locals | collector.locals
        queue.extend((child, child_outer) for child in children)
    return (
        references,
        definition_time_references,
        root_locals,
        methods,
        dynamic,
        nested_count,
    )


def _binding_names(node: ast.AST) -> tuple[str, ...]:
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return (node.name,)
    if isinstance(node, (ast.Assign, ast.AnnAssign)):
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        names: list[str] = []
        for target in targets:
            if isinstance(target, ast.Name):
                names.append(target.id)
        return tuple(names)
    return ()


def _import_bindings(node: ast.AST) -> tuple[str, ...]:
    if isinstance(node, ast.Import):
        return tuple(
            item.asname or item.name.split(".", 1)[0] for item in node.names
        )
    if isinstance(node, ast.ImportFrom):
        return tuple(item.asname or item.name for item in node.names)
    return ()


def _qualified_nodes(
    tree: ast.Module,
) -> tuple[dict[str, ast.AST], dict[str, ast.AST]]:
    result: dict[str, ast.AST] = {}
    enclosing_units: dict[str, ast.AST] = {}

    def visit_body(
        body: list[ast.stmt],
        parents: tuple[str, ...],
        enclosing: ast.AST | None,
    ) -> None:
        for statement in body:
            if isinstance(
                statement, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
            ):
                qualified = ".".join((*parents, statement.name))
                result[qualified] = statement
                unit = enclosing or statement
                enclosing_units[qualified] = unit
                visit_body(statement.body, (*parents, statement.name), unit)

    visit_body(tree.body, (), None)
    return result, enclosing_units


def _module_imports(tree: ast.Module) -> list[ast.AST]:
    return [
        statement
        for statement in tree.body
        if isinstance(statement, (ast.Import, ast.ImportFrom))
    ]


def _validate_ast_limits(tree: ast.AST) -> str | None:
    count = 0
    stack: list[tuple[ast.AST, int]] = [(tree, 1)]
    while stack:
        node, depth = stack.pop()
        count += 1
        if count > MAX_AST_NODES:
            return "ast_node_limit"
        if depth > MAX_AST_DEPTH:
            return "ast_depth_limit"
        stack.extend((child, depth + 1) for child in ast.iter_child_nodes(node))
    return None


def _slice_plan(
    source: str,
    qualified_name: str,
    expected_start: int,
    max_dependencies: int,
) -> dict[str, object]:
    tree = ast.parse(source, filename="<repository-file>", type_comments=True)
    limit_error = _validate_ast_limits(tree)
    if limit_error:
        return {"status": limit_error}
    definitions, enclosing_units = _qualified_nodes(tree)
    target = definitions.get(qualified_name)
    if target is None or _definition_start(target) != expected_start:
        return {"status": "target_mismatch"}
    if not isinstance(
        target, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
    ):
        return {"status": "unsupported_target"}

    target_unit = enclosing_units[qualified_name]

    import_map: dict[str, ast.AST] = {}
    future_imports: list[ast.AST] = []
    for item in _module_imports(tree):
        if isinstance(item, ast.ImportFrom) and item.module == "__future__":
            future_imports.append(item)
        for name in _import_bindings(item):
            import_map.setdefault(name, item)

    module_bindings: dict[str, ast.AST] = {}
    for statement in tree.body:
        for name in _binding_names(statement):
            module_bindings.setdefault(name, statement)

    collector = _ReferenceCollector(target)
    collector.visit(target)
    (
        unit_references,
        unit_definition_time_references,
        unit_locals,
        unit_methods,
        unit_dynamic,
        unit_nested_count,
    ) = _collect_enclosed_references(target_unit)
    unresolved = set(collector.nonlocals) - unit_locals
    segments: list[dict[str, object]] = [
        {
            "relation": "target_definition",
            "start_line": _definition_start(target_unit),
            "end_line": int(
                getattr(target_unit, "end_lineno", expected_start)
            ),
            "required": True,
            "priority": 0,
        }
    ]
    selected_nodes: set[int] = {id(target_unit)}
    dependency_count = 0
    pending_names = (
        set(collector.global_references)
        | unit_references
    )
    pending_names -= unit_locals
    pending_names.update(unit_definition_time_references)
    nested_scope_count = unit_nested_count
    dynamic_lookup = collector.dynamic_lookup or unit_dynamic
    analysis_limit_reached = False
    wildcard_import = any(
        isinstance(item, ast.ImportFrom)
        and any(alias.name == "*" for alias in item.names)
        for item in _module_imports(tree)
    )

    if isinstance(target_unit, ast.ClassDef):
        methods = {
            statement.name: statement
            for statement in target_unit.body
            if isinstance(
                statement, (ast.FunctionDef, ast.AsyncFunctionDef)
            )
        }
        method_queue = list(collector.self_methods | unit_methods)
        seen_methods: set[str] = set()
        while method_queue and len(seen_methods) < HARD_MAX_DEPENDENCIES:
            method_name = method_queue.pop()
            if method_name in seen_methods:
                continue
            seen_methods.add(method_name)
            method = methods.get(method_name)
            if method is None:
                unresolved.add(
                    f"{target_unit.name}.{method_name}"
                )
                continue
            method_collector = _ReferenceCollector(method)
            method_collector.visit(method)
            pending_names.update(method_collector.global_references)
            method_queue.extend(method_collector.self_methods)
            nested_scope_count += method_collector.nested_scopes
            dynamic_lookup = (
                dynamic_lookup or method_collector.dynamic_lookup
            )
        if method_queue:
            analysis_limit_reached = True

    def add_node(node: ast.AST, relation: str, priority: int) -> bool:
        nonlocal dependency_count
        if id(node) in selected_nodes:
            return True
        if dependency_count >= max_dependencies:
            return False
        selected_nodes.add(id(node))
        dependency_count += 1
        segments.append(
            {
                "relation": relation,
                "start_line": _definition_start(node),
                "end_line": int(
                    getattr(node, "end_lineno", getattr(node, "lineno", 1))
                ),
                "required": False,
                "priority": priority,
            }
        )
        return True

    for item in future_imports:
        if not add_node(item, "future_import", 1):
            analysis_limit_reached = True

    processed_names: set[str] = set()
    while pending_names:
        name = min(pending_names)
        pending_names.remove(name)
        if name in processed_names:
            continue
        processed_names.add(name)
        imported = import_map.get(name)
        if imported is not None:
            if not add_node(imported, "relevant_import", 1):
                analysis_limit_reached = True
            continue
        definition = module_bindings.get(name)
        if definition is not None and definition is not target:
            if add_node(definition, "module_dependency", 2):
                dependency_collector = _ReferenceCollector(definition)
                dependency_collector.visit(definition)
                for dependency_name in dependency_collector.global_references:
                    imported_dependency = import_map.get(dependency_name)
                    if imported_dependency is not None:
                        if not add_node(
                            imported_dependency, "relevant_import", 1
                        ):
                            analysis_limit_reached = True
                    elif dependency_name not in processed_names:
                        pending_names.add(dependency_name)
                nested_scope_count += dependency_collector.nested_scopes
                dynamic_lookup = (
                    dynamic_lookup or dependency_collector.dynamic_lookup
                )
            else:
                analysis_limit_reached = True
            continue
        unresolved.add(name)

    segments.sort(
        key=lambda item: (
            int(item["priority"]),
            int(item["start_line"]),
            int(item["end_line"]),
        )
    )
    return {
        "status": "ok",
        "segments": segments,
        "unresolved": sorted(unresolved)[:MAX_UNRESOLVED_NAMES],
        "unresolved_omitted": max(0, len(unresolved) - MAX_UNRESOLVED_NAMES),
        "nested_scope_count": nested_scope_count,
        "dynamic_lookup": dynamic_lookup,
        "wildcard_import": wildcard_import,
        "analysis_limit_reached": analysis_limit_reached,
    }


def _slice_worker(
    connection: Any,
    source: str,
    qualified_name: str,
    expected_start: int,
    max_dependencies: int,
) -> None:
    try:
        try:
            import resource

            memory_limit = 256 * 1024 * 1024
            resource.setrlimit(
                resource.RLIMIT_AS, (memory_limit, memory_limit)
            )
        except (ImportError, OSError, ValueError):
            pass
        connection.send(
            (
                "ok",
                _slice_plan(
                    source,
                    qualified_name,
                    expected_start,
                    max_dependencies,
                ),
            )
        )
    except BaseException as error:
        try:
            connection.send(("error", type(error).__name__.casefold()))
        except (BrokenPipeError, EOFError, OSError):
            pass
    finally:
        connection.close()


class PythonCodeSlicer:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    @staticmethod
    def _empty_budget(request: PythonSliceRequest) -> dict[str, object]:
        return {
            "estimated_tokens": {"used": 0, "limit": request.max_tokens},
            "dependencies": {
                "used": 0,
                "limit": request.max_dependencies,
            },
            "bytes": {"used": 0, "limit": MAX_SLICE_SOURCE_BYTES},
            "estimator": "acr-estimate-tokens-v1",
        }

    def _result(
        self,
        request: PythonSliceRequest,
        *,
        status: str,
        repository_id: str | None = None,
        index: dict[str, object] | None = None,
        target: dict[str, object] | None = None,
        warnings: tuple[str, ...] = (),
        candidates: int = 0,
    ) -> PythonSliceResult:
        omitted = {"ambiguous_candidates": candidates} if candidates else {}
        return PythonSliceResult(
            status=status,
            complete=False,
            semantic_closure=False,
            query=request.query,
            repository_id=repository_id,
            index=index,
            target=target,
            source=None,
            segments=(),
            unresolved_names=(),
            warnings=warnings,
            omitted=omitted,
            budget=self._empty_budget(request),
            comparison=None,
        )

    def slice(
        self,
        root: str | Path,
        request: PythonSliceRequest,
    ) -> PythonSliceResult:
        resolved = CodebaseIndexer._root(root)
        repository = self.connection.execute(
            """
            SELECT * FROM code_repositories WHERE repository_key = ?
            """,
            (_repository_key(resolved),),
        ).fetchone()
        if repository is None or repository["current_run_id"] is None:
            return self._result(
                request,
                status="unavailable",
                warnings=("repository_not_indexed",),
            )
        index = {
            "generation": repository["generation"],
            "run_id": repository["current_run_id"],
            "snapshot_hash": repository["snapshot_hash"],
            "parser_version": repository["parser_version"],
            "slicer_version": SLICER_VERSION,
            "python_grammar": PYTHON_GRAMMAR,
            "indexed_at": repository["indexed_at"],
        }
        try:
            policy = IndexPolicy(**json.loads(repository["index_config_json"]))
            snapshot = CodebaseIndexer(self.connection).snapshot(
                resolved, policy
            )
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return self._result(
                request,
                status="unavailable",
                repository_id=repository["id"],
                index=index,
                warnings=("snapshot_verification_failed",),
            )
        if snapshot != repository["snapshot_hash"]:
            return self._result(
                request,
                status="stale",
                repository_id=repository["id"],
                index=index,
                warnings=("repository_snapshot_changed_reindex_required",),
            )

        folded = request.query.casefold()
        rows = self.connection.execute(
            """
            SELECT s.*, f.relative_path, f.language, f.content_hash,
                   f.size_bytes, f.parse_status, f.error_kind
            FROM code_symbols s
            JOIN code_files f ON f.id = s.file_id
            WHERE f.repository_id = ?
              AND (
                  lower(s.qualified_name) = ?
                  OR lower(s.name) = ?
              )
              AND s.symbol_kind IN ('function', 'method', 'class')
            ORDER BY
              CASE WHEN lower(s.qualified_name) = ? THEN 0 ELSE 1 END,
              f.relative_path, s.qualified_name, s.start_line
            LIMIT 25
            """,
            (repository["id"], folded, folded, folded),
        ).fetchall()
        exact = (
            [
                row for row in rows
                if row["qualified_name"].casefold() == folded
            ]
            if "." in request.query
            else []
        )
        selectable = exact or rows
        if not selectable:
            return self._result(
                request,
                status="not_found",
                repository_id=repository["id"],
                index=index,
            )
        if len(selectable) != 1:
            return self._result(
                request,
                status="ambiguous",
                repository_id=repository["id"],
                index=index,
                warnings=("qualified_symbol_required",),
                candidates=max(0, len(selectable) - 12),
            )
        row = selectable[0]
        target = {
            "symbol_id": row["id"],
            "qualified_name": row["qualified_name"],
            "kind": row["symbol_kind"],
            "path": row["relative_path"],
            "span": {
                "start_line": row["start_line"],
                "end_line": row["end_line"],
            },
            "interface": row["interface"],
            "language": row["language"],
        }
        if row["language"] != "python" or row["parse_status"] != "indexed":
            return self._result(
                request,
                status="unavailable",
                repository_id=repository["id"],
                index=index,
                target=target,
                warnings=("python_indexed_target_required",),
            )

        relative = _safe_relative(str(row["relative_path"]))
        path = _path_is_safe(resolved, relative)
        if path is None:
            return self._result(
                request,
                status="stale",
                repository_id=repository["id"],
                index=index,
                target=target,
                warnings=("target_path_changed_reindex_required",),
            )
        try:
            data = CodebaseIndexer._read_file(path, int(row["size_bytes"]))
        except (OSError, ValueError):
            return self._result(
                request,
                status="stale",
                repository_id=repository["id"],
                index=index,
                target=target,
                warnings=("target_changed_reindex_required",),
            )
        if _hash_bytes(data) != row["content_hash"]:
            return self._result(
                request,
                status="stale",
                repository_id=repository["id"],
                index=index,
                target=target,
                warnings=("target_hash_changed_reindex_required",),
            )
        try:
            source = data.decode("utf-8-sig", errors="strict")
        except UnicodeDecodeError:
            return self._result(
                request,
                status="unavailable",
                repository_id=repository["id"],
                index=index,
                target=target,
                warnings=("target_encoding_changed",),
            )
        if detect_secret_material(source):
            return self._result(
                request,
                status="unavailable",
                repository_id=repository["id"],
                index=index,
                target=target,
                warnings=("secret_material_detected",),
            )

        plan = self._plan(
            source,
            str(row["qualified_name"]),
            int(row["start_line"]),
            request.max_dependencies,
        )
        if plan.get("status") != "ok":
            return self._result(
                request,
                status="unavailable",
                repository_id=repository["id"],
                index=index,
                target=target,
                warnings=(str(plan.get("status", "slice_plan_failed")),),
            )
        return self._render(
            request,
            repository_id=repository["id"],
            index=index,
            target=target,
            relative_path=relative,
            content_hash=str(row["content_hash"]),
            source=source,
            plan=plan,
        )

    @staticmethod
    def _plan(
        source: str,
        qualified_name: str,
        expected_start: int,
        max_dependencies: int,
    ) -> dict[str, object]:
        context = multiprocessing.get_context("spawn")
        parent, child = context.Pipe(duplex=False)
        process = context.Process(
            target=_slice_worker,
            args=(
                child,
                source,
                qualified_name,
                expected_start,
                max_dependencies,
            ),
            daemon=True,
        )
        process.start()
        child.close()
        try:
            if not parent.poll(4):
                process.terminate()
                process.join(timeout=2)
                return {"status": "ast_timeout"}
            payload = parent.recv()
        except (EOFError, OSError):
            return {"status": "ast_worker_failure"}
        finally:
            parent.close()
        process.join(timeout=2)
        if process.is_alive():
            process.terminate()
            process.join(timeout=2)
        if not payload or payload[0] != "ok":
            return {
                "status": (
                    str(payload[1])
                    if len(payload) > 1
                    else "ast_worker_failure"
                )
            }
        return dict(payload[1])

    @staticmethod
    def _frame(
        relative_path: str,
        content_hash: str,
        content: str,
    ) -> tuple[str, tuple[str, ...]]:
        signals = detect_suspicious_instructions(content)
        request = ContentAssessmentRequest(
            origin="document",
            source_id=relative_path,
            content=content,
            provenance=(f"sha256:{content_hash}",),
        )
        framed = ContentSecurityController.frame_untrusted(
            request,
            {"content_hash": content_hash},
        )
        return framed, signals

    def _render(
        self,
        request: PythonSliceRequest,
        *,
        repository_id: str,
        index: dict[str, object],
        target: dict[str, object],
        relative_path: str,
        content_hash: str,
        source: str,
        plan: dict[str, object],
    ) -> PythonSliceResult:
        lines = source.splitlines(keepends=True)
        candidates = list(plan["segments"])
        target_segment = next(
            item for item in candidates
            if item["relation"] == "target_definition"
        )
        optional = [
            item for item in candidates
            if item["relation"] != "target_definition"
        ]

        selected = [target_segment]
        omitted: dict[str, int] = {}

        def render_segments(items: list[dict[str, object]]) -> str:
            ordered = sorted(
                items,
                key=lambda item: (
                    int(item["start_line"]),
                    int(item["end_line"]),
                ),
            )
            blocks: list[str] = []
            for item in ordered:
                start = int(item["start_line"])
                end = int(item["end_line"])
                fragment = "".join(lines[start - 1:end])
                blocks.append(fragment)
            return "\n".join(blocks)

        target_raw = render_segments(selected)
        target_framed, _ = self._frame(
            relative_path, content_hash, target_raw
        )
        if (
            estimate_tokens(target_framed) > request.max_tokens
            or len(target_framed.encode("utf-8")) > MAX_SLICE_SOURCE_BYTES
        ):
            return self._result(
                request,
                status="unavailable",
                repository_id=repository_id,
                index=index,
                target=target,
                warnings=("target_exceeds_budget",),
            )

        for item in optional:
            trial = [*selected, item]
            trial_raw = render_segments(trial)
            trial_framed, _ = self._frame(
                relative_path, content_hash, trial_raw
            )
            if (
                estimate_tokens(trial_framed) <= request.max_tokens
                and len(trial_framed.encode("utf-8"))
                <= MAX_SLICE_SOURCE_BYTES
            ):
                selected.append(item)
            else:
                relation = str(item["relation"])
                omitted[relation] = omitted.get(relation, 0) + 1

        raw_slice = render_segments(selected)
        framed_slice, signals = self._frame(
            relative_path, content_hash, raw_slice
        )
        response_tokens = estimate_tokens(framed_slice)
        response_bytes = len(framed_slice.encode("utf-8"))
        slice_tokens = estimate_tokens(raw_slice)
        slice_bytes = len(raw_slice.encode("utf-8"))
        whole_tokens = estimate_tokens(source)
        whole_bytes = len(source.encode("utf-8"))
        saved_tokens = whole_tokens - slice_tokens
        comparison = {
            "baseline": "hash_verified_raw_whole_file",
            "whole_file_estimated_tokens": whole_tokens,
            "slice_source_estimated_tokens": slice_tokens,
            "response_estimated_tokens": response_tokens,
            "response_overhead_estimated_tokens": (
                response_tokens - slice_tokens
            ),
            "saved_estimated_tokens": saved_tokens,
            "token_savings_ratio": (
                round(saved_tokens / whole_tokens, 6)
                if whole_tokens
                else 0.0
            ),
            "whole_file_bytes": whole_bytes,
            "slice_source_bytes": slice_bytes,
            "response_bytes": response_bytes,
            "slice_is_smaller": slice_tokens < whole_tokens,
        }
        unresolved = tuple(str(item) for item in plan["unresolved"])
        warnings: list[str] = []
        if unresolved:
            warnings.append("unresolved_references")
        if bool(plan["dynamic_lookup"]):
            warnings.append("reflection_or_dynamic_lookup")
        if bool(plan["wildcard_import"]):
            warnings.append("wildcard_import_prevents_static_closure")
        if bool(plan["analysis_limit_reached"]):
            warnings.append("analysis_limit_reached")
        if omitted:
            warnings.append("slice_budget_omissions")
        if signals:
            warnings.append("suspicious_content_security_framed")
        unresolved_omitted = int(plan["unresolved_omitted"])
        if unresolved_omitted:
            omitted["unresolved_names"] = unresolved_omitted
        complete = not omitted and not bool(plan["analysis_limit_reached"])
        semantic_closure = (
            complete
            and not unresolved
            and not bool(plan["dynamic_lookup"])
            and not bool(plan["wildcard_import"])
        )
        status = (
            "available"
            if semantic_closure and not signals
            else "partial"
        )
        segments = tuple(
            {
                "relation": item["relation"],
                "reason": item["relation"],
                "path": relative_path,
                "span": {
                    "start_line": item["start_line"],
                    "end_line": item["end_line"],
                },
                "required": item["required"],
                "content_hash": hashlib.sha256(
                    "".join(
                        lines[
                            int(item["start_line"]) - 1:
                            int(item["end_line"])
                        ]
                    ).encode("utf-8")
                ).hexdigest(),
            }
            for item in sorted(
                selected,
                key=lambda item: (
                    int(item["priority"]),
                    int(item["start_line"]),
                    int(item["end_line"]),
                ),
            )
        )
        dependency_count = sum(
            1 for item in selected
            if item["relation"] != "target_definition"
        )
        return PythonSliceResult(
            status=status,
            complete=complete,
            semantic_closure=semantic_closure,
            query=request.query,
            repository_id=repository_id,
            index=index,
            target={
                **target,
                "provenance": {
                    "origin": "repository",
                    "authority": "none",
                    "hash_verified": True,
                },
                "safety_signals": list(signals),
                "exact_source_preserved": True,
            },
            source=framed_slice,
            segments=segments,
            unresolved_names=unresolved,
            warnings=tuple(warnings),
            omitted=omitted,
            budget={
                "estimated_tokens": {
                    "used": slice_tokens,
                    "response_used": response_tokens,
                    "limit": request.max_tokens,
                },
                "dependencies": {
                    "used": dependency_count,
                    "limit": request.max_dependencies,
                },
                "bytes": {
                    "used": response_bytes,
                    "limit": MAX_SLICE_SOURCE_BYTES,
                },
                "estimator": "acr-estimate-tokens-v1",
            },
            comparison=comparison,
        )
