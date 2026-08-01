from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Mapping, Sequence

from .secret_management import SecretBoundaryError, assert_secret_free


PROJECT_KINDS = (
    "milestone",
    "completed_work",
    "decision",
    "blocker",
    "dependency",
    "technical_debt",
    "benchmark",
    "next_work",
)
ITEM_STATUSES = (
    "planned",
    "in_progress",
    "blocked",
    "completed",
    "deferred",
    "cancelled",
)
PROJECT_STATUSES = ("active", "paused", "completed", "archived")
PROJECT_TRANSITIONS = {
    "active": frozenset({"paused", "completed", "archived"}),
    "paused": frozenset({"active", "completed", "archived"}),
    "completed": frozenset({"active", "archived"}),
    "archived": frozenset(),
}
_PROJECT_KEY = re.compile(r"^[a-z0-9][a-z0-9._-]{1,63}$")
_REFERENCE = re.compile(r"^[a-z][a-z0-9_.-]{0,31}:[^\s]{1,240}$")
_UUID = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-"
    r"[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)


class ProjectStateError(ValueError):
    pass


class ProjectStateConflict(ProjectStateError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _text(value: object, field: str, maximum: int) -> str:
    if not isinstance(value, str):
        raise ProjectStateError(f"{field} must be text")
    normalized = value.strip()
    if len(normalized) < 2 or len(normalized) > maximum:
        raise ProjectStateError(f"{field} must be 2..{maximum} characters")
    if any(ord(character) < 32 and character not in "\n\t" for character in normalized):
        raise ProjectStateError(f"{field} contains control characters")
    try:
        assert_secret_free(normalized, f"project state {field}")
    except SecretBoundaryError as exc:
        raise ProjectStateError(f"{field} contains secret material") from exc
    return normalized


def _closed(payload: object, fields: set[str], label: str) -> Mapping[str, object]:
    if not isinstance(payload, Mapping):
        raise ProjectStateError(f"{label} must be an object")
    unknown = set(payload) - fields
    if unknown:
        raise ProjectStateError(f"{label} contains unknown fields: {sorted(unknown)}")
    return payload


def _project_key(value: object) -> str:
    if not isinstance(value, str) or not _PROJECT_KEY.fullmatch(value):
        raise ProjectStateError(
            "project_key must be 2..64 lowercase letters, digits, dot, dash, or underscore"
        )
    return value


def _actor_hash(actor: object) -> str:
    actor_text = _text(actor, "actor", 160)
    return hashlib.sha256(actor_text.encode("utf-8")).hexdigest()


def _references(value: object) -> tuple[str, ...]:
    if not isinstance(value, list) or len(value) > 16:
        raise ProjectStateError("evidence must be a list of at most 16 references")
    result: list[str] = []
    for entry in value:
        if not isinstance(entry, str) or not _REFERENCE.fullmatch(entry):
            raise ProjectStateError("evidence entries must be bounded type:value references")
        try:
            assert_secret_free(entry, "project state evidence")
        except SecretBoundaryError as exc:
            raise ProjectStateError(
                "evidence contains secret material"
            ) from exc
        result.append(entry)
    if len(set(result)) != len(result):
        raise ProjectStateError("evidence references must be unique")
    return tuple(result)


def _dependencies(value: object) -> tuple[str, ...]:
    if not isinstance(value, list) or len(value) > 32:
        raise ProjectStateError("dependencies must be a list of at most 32 item ids")
    result = tuple(value)
    if any(not isinstance(item_id, str) or not _UUID.fullmatch(item_id) for item_id in result):
        raise ProjectStateError("dependencies must contain UUID item ids")
    if len(set(result)) != len(result):
        raise ProjectStateError("dependencies must be unique")
    return result


@dataclass(frozen=True)
class ProjectCreate:
    project_key: str
    name: str
    objective: str
    scope: str

    @classmethod
    def from_dict(cls, payload: object) -> "ProjectCreate":
        data = _closed(
            payload,
            {"schema_version", "project_key", "name", "objective", "scope"},
            "project create request",
        )
        if data.get("schema_version") != 1 or set(data) != {
            "schema_version",
            "project_key",
            "name",
            "objective",
            "scope",
        }:
            raise ProjectStateError("project create request requires the version 1 schema")
        return cls(
            project_key=_project_key(data["project_key"]),
            name=_text(data["name"], "name", 160),
            objective=_text(data["objective"], "objective", 2000),
            scope=_text(data["scope"], "scope", 160),
        )


@dataclass(frozen=True)
class ProjectItemCreate:
    kind: str
    title: str
    detail: str
    status: str
    priority: int
    evidence: tuple[str, ...]
    dependencies: tuple[str, ...]

    @classmethod
    def from_dict(cls, payload: object) -> "ProjectItemCreate":
        fields = {
            "schema_version",
            "kind",
            "title",
            "detail",
            "status",
            "priority",
            "evidence",
            "dependencies",
        }
        data = _closed(payload, fields, "project item request")
        if data.get("schema_version") != 1 or set(data) != fields:
            raise ProjectStateError("project item request requires the version 1 schema")
        kind = data["kind"]
        status = data["status"]
        priority = data["priority"]
        if kind not in PROJECT_KINDS:
            raise ProjectStateError(f"kind must be one of {list(PROJECT_KINDS)}")
        if status not in ITEM_STATUSES:
            raise ProjectStateError(f"status must be one of {list(ITEM_STATUSES)}")
        if not isinstance(priority, int) or isinstance(priority, bool) or not 0 <= priority <= 100:
            raise ProjectStateError("priority must be an integer from 0 to 100")
        item = cls(
            kind=str(kind),
            title=_text(data["title"], "title", 240),
            detail=_text(data["detail"], "detail", 4000),
            status=str(status),
            priority=priority,
            evidence=_references(data["evidence"]),
            dependencies=_dependencies(data["dependencies"]),
        )
        item._validate_semantics()
        return item

    def _validate_semantics(self) -> None:
        if self.kind in {"completed_work", "decision"} and self.status != "completed":
            raise ProjectStateError(f"{self.kind} items must be completed")
        if self.kind == "blocker" and self.status not in {"blocked", "completed"}:
            raise ProjectStateError("blocker items must be blocked or completed")
        if self.kind == "benchmark" and self.status == "completed" and not self.evidence:
            raise ProjectStateError("completed benchmarks require evidence")


@dataclass(frozen=True)
class ProjectItemUpdate:
    expected_revision: int
    status: str
    detail: str
    priority: int
    evidence: tuple[str, ...]
    dependencies: tuple[str, ...]

    @classmethod
    def from_dict(cls, payload: object) -> "ProjectItemUpdate":
        fields = {
            "schema_version",
            "expected_revision",
            "status",
            "detail",
            "priority",
            "evidence",
            "dependencies",
        }
        data = _closed(payload, fields, "project item update")
        if data.get("schema_version") != 1 or set(data) != fields:
            raise ProjectStateError("project item update requires the version 1 schema")
        revision = data["expected_revision"]
        priority = data["priority"]
        status = data["status"]
        if not isinstance(revision, int) or isinstance(revision, bool) or revision < 1:
            raise ProjectStateError("expected_revision must be a positive integer")
        if status not in ITEM_STATUSES:
            raise ProjectStateError(f"status must be one of {list(ITEM_STATUSES)}")
        if not isinstance(priority, int) or isinstance(priority, bool) or not 0 <= priority <= 100:
            raise ProjectStateError("priority must be an integer from 0 to 100")
        return cls(
            expected_revision=revision,
            status=str(status),
            detail=_text(data["detail"], "detail", 4000),
            priority=priority,
            evidence=_references(data["evidence"]),
            dependencies=_dependencies(data["dependencies"]),
        )


class ProjectStateManager:
    """Structured cross-session project state, separate from semantic memory."""

    def __init__(
        self,
        connection: sqlite3.Connection,
        *,
        mutation_guard: Callable[[str], None] | None = None,
    ) -> None:
        self.connection = connection
        self.mutation_guard = mutation_guard

    def _guard(self) -> None:
        if self.mutation_guard is not None:
            self.mutation_guard("project_state_write")

    def create(self, spec: ProjectCreate, *, actor: str) -> dict[str, object]:
        self._guard()
        project_id = str(uuid.uuid4())
        now = _now()
        try:
            self.connection.execute("BEGIN IMMEDIATE")
            self.connection.execute(
                """
                INSERT INTO project_states(
                    id, project_key, name, objective, scope, status,
                    revision, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, 'active', 1, ?, ?)
                """,
                (
                    project_id,
                    spec.project_key,
                    spec.name,
                    spec.objective,
                    spec.scope,
                    now,
                    now,
                ),
            )
            self._event(
                project_id,
                revision=1,
                event_type="project_created",
                actor_hash=_actor_hash(actor),
                details={"status": "active"},
                created_at=now,
            )
            self.connection.commit()
        except sqlite3.IntegrityError as exc:
            self.connection.rollback()
            raise ProjectStateConflict("project_key already exists") from exc
        except Exception:
            self.connection.rollback()
            raise
        return self.snapshot(spec.project_key)

    def update_status(
        self,
        project_key: str,
        *,
        expected_revision: int,
        status: str,
        actor: str,
    ) -> dict[str, object]:
        self._guard()
        key = _project_key(project_key)
        if status not in PROJECT_STATUSES:
            raise ProjectStateError(f"status must be one of {list(PROJECT_STATUSES)}")
        if not isinstance(expected_revision, int) or expected_revision < 1:
            raise ProjectStateError("expected_revision must be a positive integer")
        now = _now()
        try:
            self.connection.execute("BEGIN IMMEDIATE")
            project = self._project_row(key)
            if project["revision"] != expected_revision:
                raise ProjectStateConflict("project revision changed")
            if status == project["status"] or status not in PROJECT_TRANSITIONS[
                project["status"]
            ]:
                raise ProjectStateError(
                    f"invalid project status transition: {project['status']} -> {status}"
                )
            revision = expected_revision + 1
            self.connection.execute(
                """
                UPDATE project_states
                SET status=?, revision=?, updated_at=?
                WHERE id=? AND revision=?
                """,
                (status, revision, now, project["id"], expected_revision),
            )
            self._event(
                project["id"],
                revision=revision,
                event_type="project_updated",
                actor_hash=_actor_hash(actor),
                details={"status": status},
                created_at=now,
            )
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise
        return self.snapshot(key)

    def add_item(
        self,
        project_key: str,
        spec: ProjectItemCreate,
        *,
        expected_project_revision: int,
        actor: str,
    ) -> dict[str, object]:
        self._guard()
        key = _project_key(project_key)
        item_id = str(uuid.uuid4())
        now = _now()
        try:
            self.connection.execute("BEGIN IMMEDIATE")
            project = self._project_row(key)
            self._expect_project_revision(project, expected_project_revision)
            self._require_active(project)
            count = self.connection.execute(
                "SELECT COUNT(*) FROM project_state_items WHERE project_id=?",
                (project["id"],),
            ).fetchone()[0]
            if count >= 512:
                raise ProjectStateError("project item limit reached")
            self._require_dependencies(project["id"], spec.dependencies)
            self.connection.execute(
                """
                INSERT INTO project_state_items(
                    id, project_id, kind, title, detail, status, priority,
                    evidence_json, revision, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
                """,
                (
                    item_id,
                    project["id"],
                    spec.kind,
                    spec.title,
                    spec.detail,
                    spec.status,
                    spec.priority,
                    json.dumps(spec.evidence, separators=(",", ":")),
                    now,
                    now,
                ),
            )
            self._replace_dependencies(
                project["id"], item_id, spec.dependencies, now
            )
            project_revision = expected_project_revision + 1
            self._bump_project(project["id"], expected_project_revision, project_revision, now)
            self._event(
                project["id"],
                revision=project_revision,
                event_type="item_added",
                actor_hash=_actor_hash(actor),
                details={"kind": spec.kind, "item_revision": 1},
                item_id=item_id,
                created_at=now,
            )
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise
        return self.item(key, item_id)

    def update_item(
        self,
        project_key: str,
        item_id: str,
        spec: ProjectItemUpdate,
        *,
        expected_project_revision: int,
        actor: str,
    ) -> dict[str, object]:
        self._guard()
        key = _project_key(project_key)
        if not _UUID.fullmatch(item_id):
            raise ProjectStateError("item_id must be a UUID")
        now = _now()
        try:
            self.connection.execute("BEGIN IMMEDIATE")
            project = self._project_row(key)
            self._expect_project_revision(project, expected_project_revision)
            self._require_active(project)
            item = self._item_row(project["id"], item_id)
            if item["revision"] != spec.expected_revision:
                raise ProjectStateConflict("item revision changed")
            self._validate_update_semantics(item["kind"], spec)
            self._require_dependencies(project["id"], spec.dependencies)
            self._reject_cycle(project["id"], item_id, spec.dependencies)
            item_revision = spec.expected_revision + 1
            self.connection.execute(
                """
                UPDATE project_state_items
                SET detail=?, status=?, priority=?, evidence_json=?,
                    revision=?, updated_at=?
                WHERE project_id=? AND id=? AND revision=?
                """,
                (
                    spec.detail,
                    spec.status,
                    spec.priority,
                    json.dumps(spec.evidence, separators=(",", ":")),
                    item_revision,
                    now,
                    project["id"],
                    item_id,
                    spec.expected_revision,
                ),
            )
            self._replace_dependencies(
                project["id"], item_id, spec.dependencies, now
            )
            project_revision = expected_project_revision + 1
            self._bump_project(project["id"], expected_project_revision, project_revision, now)
            self._event(
                project["id"],
                revision=project_revision,
                event_type="item_updated",
                actor_hash=_actor_hash(actor),
                details={
                    "status": spec.status,
                    "item_revision": item_revision,
                    "dependency_count": len(spec.dependencies),
                },
                item_id=item_id,
                created_at=now,
            )
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise
        return self.item(key, item_id)

    def list_projects(self, *, limit: int = 50) -> list[dict[str, object]]:
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 100:
            raise ProjectStateError("limit must be 1..100")
        rows = self.connection.execute(
            """
            SELECT project_key, name, scope, status, revision, created_at, updated_at
            FROM project_states
            ORDER BY updated_at DESC, project_key
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [dict(row) for row in rows]

    def snapshot(
        self, project_key: str, *, event_limit: int = 20
    ) -> dict[str, object]:
        key = _project_key(project_key)
        if not isinstance(event_limit, int) or not 0 <= event_limit <= 50:
            raise ProjectStateError("event_limit must be 0..50")
        project = self._project_row(key)
        rows = self.connection.execute(
            """
            SELECT id, kind, title, detail, status, priority, evidence_json,
                   revision, created_at, updated_at
            FROM project_state_items
            WHERE project_id=?
            ORDER BY kind, priority DESC, created_at, id
            """,
            (project["id"],),
        ).fetchall()
        dependencies = self.connection.execute(
            """
            SELECT item_id, depends_on_item_id
            FROM project_state_item_dependencies
            WHERE project_id=?
            ORDER BY item_id, depends_on_item_id
            """,
            (project["id"],),
        ).fetchall()
        by_item: dict[str, list[str]] = {}
        for relation in dependencies:
            by_item.setdefault(relation["item_id"], []).append(
                relation["depends_on_item_id"]
            )
        items = []
        counts = {kind: 0 for kind in PROJECT_KINDS}
        for row in rows:
            item = dict(row)
            item["evidence"] = json.loads(item.pop("evidence_json"))
            item["dependencies"] = by_item.get(item["id"], [])
            items.append(item)
            counts[item["kind"]] += 1
        events = []
        if event_limit:
            events = [
                {
                    **dict(row),
                    "details": json.loads(row["details_json"]),
                }
                for row in self.connection.execute(
                    """
                    SELECT sequence, project_revision, event_type, item_id,
                           actor_hash, details_json, created_at
                    FROM project_state_events
                    WHERE project_id=?
                    ORDER BY sequence DESC
                    LIMIT ?
                    """,
                    (project["id"], event_limit),
                ).fetchall()
            ]
            for event in events:
                event.pop("details_json", None)
        return {
            "schema_version": 1,
            "content_trust": "operator_authored_untrusted_data",
            "project": dict(project),
            "counts": counts,
            "items": items,
            "recommended_next_work": self.recommend(key, limit=10),
            "recent_events": events,
        }

    def item(self, project_key: str, item_id: str) -> dict[str, object]:
        key = _project_key(project_key)
        if not _UUID.fullmatch(item_id):
            raise ProjectStateError("item_id must be a UUID")
        project = self._project_row(key)
        row = self._item_row(project["id"], item_id)
        result = dict(row)
        result["evidence"] = json.loads(result.pop("evidence_json"))
        result["dependencies"] = [
            relation[0]
            for relation in self.connection.execute(
                """
                SELECT depends_on_item_id
                FROM project_state_item_dependencies
                WHERE project_id=? AND item_id=?
                ORDER BY depends_on_item_id
                """,
                (project["id"], item_id),
            ).fetchall()
        ]
        return result

    def recommend(self, project_key: str, *, limit: int = 10) -> list[dict[str, object]]:
        key = _project_key(project_key)
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 25:
            raise ProjectStateError("limit must be 1..25")
        project = self._project_row(key)
        rows = self.connection.execute(
            """
            SELECT id, title, detail, status, priority, revision
            FROM project_state_items
            WHERE project_id=? AND kind='next_work'
              AND status IN ('planned', 'in_progress', 'blocked')
            ORDER BY
                CASE status WHEN 'in_progress' THEN 0 WHEN 'planned' THEN 1 ELSE 2 END,
                priority DESC, created_at, id
            """,
            (project["id"],),
        ).fetchall()
        result = []
        for row in rows:
            blockers = self.connection.execute(
                """
                SELECT dependency.id, dependency.title, dependency.status
                FROM project_state_item_dependencies AS relation
                JOIN project_state_items AS dependency
                  ON dependency.project_id=relation.project_id
                 AND dependency.id=relation.depends_on_item_id
                WHERE relation.project_id=? AND relation.item_id=?
                  AND dependency.status <> 'completed'
                ORDER BY dependency.priority DESC, dependency.id
                """,
                (project["id"], row["id"]),
            ).fetchall()
            result.append(
                {
                    **dict(row),
                    "ready": not blockers and row["status"] != "blocked",
                    "blocked_by": [dict(blocker) for blocker in blockers],
                }
            )
        ready = [item for item in result if item["ready"]]
        waiting = [item for item in result if not item["ready"]]
        return (ready + waiting)[:limit]

    def _project_row(self, project_key: str) -> sqlite3.Row:
        row = self.connection.execute(
            "SELECT * FROM project_states WHERE project_key=?",
            (project_key,),
        ).fetchone()
        if row is None:
            raise ProjectStateError(f"unknown project: {project_key}")
        return row

    def _item_row(self, project_id: str, item_id: str) -> sqlite3.Row:
        row = self.connection.execute(
            """
            SELECT id, kind, title, detail, status, priority, evidence_json,
                   revision, created_at, updated_at
            FROM project_state_items
            WHERE project_id=? AND id=?
            """,
            (project_id, item_id),
        ).fetchone()
        if row is None:
            raise ProjectStateError(f"unknown project item: {item_id}")
        return row

    @staticmethod
    def _expect_project_revision(project: sqlite3.Row, expected: int) -> None:
        if not isinstance(expected, int) or isinstance(expected, bool) or expected < 1:
            raise ProjectStateError("expected_project_revision must be positive")
        if project["revision"] != expected:
            raise ProjectStateConflict("project revision changed")

    @staticmethod
    def _require_active(project: sqlite3.Row) -> None:
        if project["status"] != "active":
            raise ProjectStateConflict(
                "project must be active before item state can change"
            )

    def _require_dependencies(
        self, project_id: str, dependencies: Sequence[str]
    ) -> None:
        if not dependencies:
            return
        placeholders = ",".join("?" for _ in dependencies)
        found = self.connection.execute(
            f"""
            SELECT id FROM project_state_items
            WHERE project_id=? AND id IN ({placeholders})
            """,
            (project_id, *dependencies),
        ).fetchall()
        if {row["id"] for row in found} != set(dependencies):
            raise ProjectStateError("dependencies must exist in the same project")

    def _replace_dependencies(
        self,
        project_id: str,
        item_id: str,
        dependencies: Sequence[str],
        created_at: str,
    ) -> None:
        self.connection.execute(
            """
            DELETE FROM project_state_item_dependencies
            WHERE project_id=? AND item_id=?
            """,
            (project_id, item_id),
        )
        self.connection.executemany(
            """
            INSERT INTO project_state_item_dependencies(
                project_id, item_id, depends_on_item_id, created_at
            ) VALUES (?, ?, ?, ?)
            """,
            (
                (project_id, item_id, dependency_id, created_at)
                for dependency_id in dependencies
            ),
        )

    def _reject_cycle(
        self, project_id: str, item_id: str, dependencies: Sequence[str]
    ) -> None:
        if item_id in dependencies:
            raise ProjectStateError("an item cannot depend on itself")
        graph: dict[str, set[str]] = {}
        for row in self.connection.execute(
            """
            SELECT item_id, depends_on_item_id
            FROM project_state_item_dependencies
            WHERE project_id=?
            """,
            (project_id,),
        ).fetchall():
            graph.setdefault(row["item_id"], set()).add(row["depends_on_item_id"])
        graph[item_id] = set(dependencies)

        def reaches(start: str, target: str, seen: set[str]) -> bool:
            if start == target:
                return True
            if start in seen:
                return False
            seen.add(start)
            return any(reaches(child, target, seen) for child in graph.get(start, ()))

        if any(reaches(dependency, item_id, set()) for dependency in dependencies):
            raise ProjectStateError("project item dependencies cannot form a cycle")

    @staticmethod
    def _validate_update_semantics(
        kind: str, spec: ProjectItemUpdate
    ) -> None:
        if kind in {"completed_work", "decision"} and spec.status != "completed":
            raise ProjectStateError(f"{kind} items must remain completed")
        if kind == "blocker" and spec.status not in {"blocked", "completed"}:
            raise ProjectStateError("blocker items must be blocked or completed")
        if kind == "benchmark" and spec.status == "completed" and not spec.evidence:
            raise ProjectStateError("completed benchmarks require evidence")

    def _bump_project(
        self,
        project_id: str,
        expected_revision: int,
        revision: int,
        updated_at: str,
    ) -> None:
        cursor = self.connection.execute(
            """
            UPDATE project_states
            SET revision=?, updated_at=?
            WHERE id=? AND revision=?
            """,
            (revision, updated_at, project_id, expected_revision),
        )
        if cursor.rowcount != 1:
            raise ProjectStateConflict("project revision changed")

    def _event(
        self,
        project_id: str,
        *,
        revision: int,
        event_type: str,
        actor_hash: str,
        details: Mapping[str, object],
        created_at: str,
        item_id: str | None = None,
    ) -> None:
        self.connection.execute(
            """
            INSERT INTO project_state_events(
                id, project_id, sequence, project_revision, event_type,
                item_id, actor_hash, details_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(uuid.uuid4()),
                project_id,
                revision,
                revision,
                event_type,
                item_id,
                actor_hash,
                json.dumps(details, sort_keys=True, separators=(",", ":")),
                created_at,
            ),
        )
