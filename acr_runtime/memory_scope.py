from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from enum import Enum


class MemoryScopeKind(str, Enum):
    GLOBAL = "global"
    ORGANIZATION = "organization"
    USER = "user"
    PROJECT = "project"
    REPOSITORY = "repository"
    TASK = "task"
    AGENT = "agent"


ALLOWED_PARENT_KINDS: dict[MemoryScopeKind, frozenset[MemoryScopeKind]] = {
    MemoryScopeKind.GLOBAL: frozenset(),
    MemoryScopeKind.ORGANIZATION: frozenset({MemoryScopeKind.GLOBAL}),
    MemoryScopeKind.USER: frozenset({MemoryScopeKind.ORGANIZATION}),
    MemoryScopeKind.PROJECT: frozenset(
        {
            MemoryScopeKind.GLOBAL,
            MemoryScopeKind.ORGANIZATION,
            MemoryScopeKind.USER,
        }
    ),
    MemoryScopeKind.REPOSITORY: frozenset({MemoryScopeKind.PROJECT}),
    MemoryScopeKind.TASK: frozenset(
        {MemoryScopeKind.PROJECT, MemoryScopeKind.REPOSITORY}
    ),
    MemoryScopeKind.AGENT: frozenset(
        {
            MemoryScopeKind.ORGANIZATION,
            MemoryScopeKind.USER,
            MemoryScopeKind.PROJECT,
            MemoryScopeKind.REPOSITORY,
            MemoryScopeKind.TASK,
        }
    ),
}


@dataclass(frozen=True)
class MemoryScope:
    id: str
    kind: MemoryScopeKind
    parent_id: str | None
    created_at: str


class MemoryScopeRegistry:
    """Stores an explicit, immutable scope tree used by memory retrieval."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    @staticmethod
    def _record(row: sqlite3.Row) -> MemoryScope:
        return MemoryScope(
            id=row["id"],
            kind=MemoryScopeKind(row["kind"]),
            parent_id=row["parent_id"],
            created_at=row["created_at"],
        )

    def get(self, scope_id: str) -> MemoryScope | None:
        row = self.connection.execute(
            "SELECT * FROM memory_scopes WHERE id = ?", (scope_id,)
        ).fetchone()
        return self._record(row) if row else None

    def register(
        self,
        scope_id: str,
        kind: MemoryScopeKind,
        *,
        parent_id: str | None = None,
    ) -> MemoryScope:
        scope_id = scope_id.strip()
        if not scope_id or len(scope_id) > 255:
            raise ValueError("Memory scope id must contain 1..255 characters")
        if not isinstance(kind, MemoryScopeKind):
            raise ValueError("Memory scope kind must use the closed vocabulary")
        if kind is MemoryScopeKind.GLOBAL:
            if scope_id != "global" or parent_id is not None:
                raise ValueError("The global scope must be the parentless 'global' root")
        else:
            if parent_id is None or not parent_id.strip():
                raise ValueError("Non-global memory scopes require an explicit parent")
            if scope_id == "global" or scope_id == parent_id:
                raise ValueError("Memory scopes cannot replace or parent themselves")
            parent = self.get(parent_id)
            if parent is None:
                raise KeyError(parent_id)
            if parent.kind not in ALLOWED_PARENT_KINDS[kind]:
                allowed = ", ".join(
                    sorted(item.value for item in ALLOWED_PARENT_KINDS[kind])
                )
                raise ValueError(
                    f"{kind.value} scope cannot descend from {parent.kind.value}; "
                    f"allowed parents: {allowed}"
                )
        existing = self.get(scope_id)
        if existing is not None:
            if existing.kind is not kind or existing.parent_id != parent_id:
                raise ValueError("Memory scope id already has a different definition")
            return existing
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO memory_scopes(id, kind, parent_id, created_at)
                VALUES (?, ?, ?, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
                """,
                (scope_id, kind.value, parent_id),
            )
        created = self.get(scope_id)
        if created is None:
            raise RuntimeError("Created memory scope could not be reloaded")
        return created

    def ensure_legacy(self, scope_id: str) -> MemoryScope:
        """Register a pre-hierarchy flat scope as an isolated project sibling."""
        existing = self.get(scope_id)
        if existing is not None:
            return existing
        if scope_id == "global":
            root = self.get("global")
            if root is None:
                raise RuntimeError("Global memory scope is unavailable")
            return root
        return self.register(
            scope_id, MemoryScopeKind.PROJECT, parent_id="global"
        )

    def ancestors(self, scope_id: str) -> tuple[MemoryScope, ...]:
        """Return self then parents; unknown scopes remain exact and isolated."""
        current = self.get(scope_id)
        if current is None:
            return ()
        result: list[MemoryScope] = []
        seen: set[str] = set()
        while current is not None:
            if current.id in seen:
                raise RuntimeError("Memory scope hierarchy contains a cycle")
            seen.add(current.id)
            result.append(current)
            current = self.get(current.parent_id) if current.parent_id else None
        return tuple(result)

    def visible_scope_ids(
        self, scope_id: str, *, include_ancestors: bool
    ) -> tuple[str, ...]:
        if not include_ancestors:
            return (scope_id,)
        ancestors = self.ancestors(scope_id)
        if ancestors:
            return tuple(item.id for item in ancestors)
        # Compatibility for callers querying an unregistered legacy scope.
        return (scope_id, "global") if scope_id != "global" else ("global",)

    def list(self) -> tuple[MemoryScope, ...]:
        rows = self.connection.execute(
            """
            SELECT * FROM memory_scopes
            ORDER BY CASE kind
                WHEN 'global' THEN 0 WHEN 'organization' THEN 1
                WHEN 'user' THEN 2 WHEN 'project' THEN 3
                WHEN 'repository' THEN 4 WHEN 'task' THEN 5
                ELSE 6 END, id
            """
        ).fetchall()
        return tuple(self._record(row) for row in rows)
