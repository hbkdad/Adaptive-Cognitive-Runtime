from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

from .scoring import query_terms
from .skill_format import SkillPackage, SkillPackageLoader


class SkillSemanticIndex(Protocol):
    def search(self, query: str, *, limit: int) -> dict[str, float]: ...


@dataclass(frozen=True)
class SkillSearchResult:
    id: str
    manifest_id: str
    name: str
    version: str
    description: str
    lifecycle_status: str
    reliability: float
    keyword_rank: int | None
    semantic_score: float | None
    combined_score: float
    reason: str


class SkillRegistry:
    def __init__(
        self,
        connection: sqlite3.Connection,
        *,
        loader: SkillPackageLoader | None = None,
        semantic_index: SkillSemanticIndex | None = None,
    ) -> None:
        self.connection = connection
        self.loader = loader or SkillPackageLoader()
        self.semantic_index = semantic_index

    def admit(self, directory: str | Path) -> dict[str, object]:
        package = self.loader.load(directory)
        manifest = package.manifest
        existing = self.connection.execute(
            """
            SELECT id, content_hash FROM skills
            WHERE (manifest_id = ? OR name = ?) AND version = ?
            """,
            (manifest.id, manifest.name, manifest.version),
        ).fetchone()
        if existing:
            if existing["content_hash"] == package.content_hash:
                return self.inspect(existing["id"])
            raise ValueError(
                "Published skill version is immutable; increment the version"
            )
        record_id = str(uuid.uuid4())
        manifest_payload = asdict(manifest)
        manifest_payload["status"] = manifest.status.value
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO skills(
                    id, name, version, description, instructions, tags_json,
                    status, token_cost, created_at, manifest_id, manifest_json,
                    package_path, content_hash, lifecycle_status, reliability,
                    task_classes_json, permissions_json, models_json,
                    applicability_json, contraindications_json,
                    verification_json
                ) VALUES (
                    ?, ?, ?, ?, ?, ?, 'quarantine', ?, ?, ?, ?, ?, ?, 
                    'quarantined', ?, ?, ?, ?, ?, ?, ?
                )
                """,
                (
                    record_id, manifest.name, manifest.version,
                    manifest.description, package.instructions,
                    json.dumps(list(manifest.task_classes)),
                    package.actual_instruction_tokens, manifest.created_at,
                    manifest.id,
                    json.dumps(manifest_payload, sort_keys=True),
                    str(package.root), package.content_hash,
                    manifest.reliability,
                    json.dumps(list(manifest.task_classes)),
                    json.dumps(list(manifest.permissions)),
                    json.dumps(list(manifest.models)),
                    json.dumps(list(manifest.applicability)),
                    json.dumps(list(manifest.contraindications)),
                    json.dumps(list(manifest.verification)),
                ),
            )
            self._index(record_id)
            self._history(
                record_id,
                "admitted",
                None,
                "quarantined",
                {"manifest_status": manifest.status.value},
            )
        return self.inspect(record_id)

    def list(self) -> list[dict[str, object]]:
        rows = self.connection.execute(
            """
            SELECT id, manifest_id, name, version, description,
                   status, lifecycle_status, reliability, verification_status,
                   use_count, success_count,
                   use_count AS uses, success_count AS successful_uses,
                   failure_count AS failures,
                   CASE WHEN use_count = 0 THEN 0
                        ELSE CAST(total_tokens AS REAL) / use_count END
                        AS average_tokens,
                   CASE WHEN use_count = 0 THEN 0
                        ELSE total_cost / use_count END AS average_cost,
                   CASE WHEN use_count = 0 THEN 0
                        ELSE CAST(total_latency_ms AS REAL) / use_count END
                        AS average_latency_ms,
                   last_used
            FROM skills
            ORDER BY manifest_id, version
            """
        ).fetchall()
        records = [dict(row) for row in rows]
        records.sort(
            key=lambda row: (
                row["manifest_id"] or row["name"],
                self._semver_key(row["version"]),
            )
        )
        return records

    def inspect(self, reference: str) -> dict[str, object]:
        row = self._resolve(reference)
        performance = self.connection.execute(
            """
            SELECT task_class, model, uses, successful_uses, failures,
                   CASE WHEN uses = 0 THEN 0
                        ELSE CAST(total_tokens AS REAL) / uses END
                        AS average_tokens,
                   CASE WHEN uses = 0 THEN 0
                        ELSE total_cost / uses END AS average_cost,
                   CASE WHEN uses = 0 THEN 0
                        ELSE CAST(total_latency_ms AS REAL) / uses END
                        AS average_latency_ms,
                   last_used
            FROM skill_performance WHERE skill_id = ?
            ORDER BY task_class, model
            """,
            (row["id"],),
        ).fetchall()
        result = dict(row)
        for field in (
            "tags_json", "manifest_json", "task_classes_json",
            "permissions_json", "models_json", "applicability_json",
            "contraindications_json", "verification_json",
        ):
            result[field.removesuffix("_json")] = json.loads(result.pop(field))
        result["performance"] = [dict(item) for item in performance]
        return result

    def search(
        self,
        query: str,
        *,
        limit: int = 10,
        lifecycle_statuses: frozenset[str] | None = None,
    ) -> dict[str, object]:
        if limit < 1:
            raise ValueError("limit must be positive")
        terms = query_terms(query)
        keyword: list[sqlite3.Row] = []
        if terms:
            expression = " OR ".join(f'"{term}"' for term in terms)
            retrieval_limit = limit * (20 if lifecycle_statuses else 3)
            keyword = self.connection.execute(
                """
                SELECT skill_id, rank FROM skills_fts
                WHERE skills_fts MATCH ? ORDER BY rank LIMIT ?
                """,
                (expression, retrieval_limit),
            ).fetchall()
        keyword_rank = {
            row["skill_id"]: index for index, row in enumerate(keyword, start=1)
        }
        semantic = (
            self.semantic_index.search(query, limit=limit * 3)
            if self.semantic_index is not None
            else {}
        )
        if any(not 0 <= score <= 1 for score in semantic.values()):
            raise ValueError("Semantic skill scores must be 0..1")
        ids = set(keyword_rank) | set(semantic)
        results: list[SkillSearchResult] = []
        for skill_id in ids:
            row = self.connection.execute(
                """
                SELECT id, manifest_id, name, version, description,
                       lifecycle_status, reliability
                FROM skills WHERE id = ?
                """,
                (skill_id,),
            ).fetchone()
            if (
                row is None
                or row["lifecycle_status"] == "retired"
                or (
                    lifecycle_statuses is not None
                    and row["lifecycle_status"] not in lifecycle_statuses
                )
            ):
                continue
            lexical = (
                1 / keyword_rank[skill_id] if skill_id in keyword_rank else 0.0
            )
            semantic_score = semantic.get(skill_id)
            combined = (
                0.65 * lexical
                + 0.35 * (semantic_score if semantic_score is not None else 0)
            )
            reasons = []
            if lexical:
                reasons.append(f"keyword_rank={keyword_rank[skill_id]}")
            if semantic_score is not None:
                reasons.append(f"semantic={semantic_score:.3f}")
            results.append(
                SkillSearchResult(
                    **dict(row),
                    keyword_rank=keyword_rank.get(skill_id),
                    semantic_score=semantic_score,
                    combined_score=combined,
                    reason=", ".join(reasons),
                )
            )
        results.sort(
            key=lambda item: (-item.combined_score, item.manifest_id, item.version)
        )
        return {
            "semantic_available": self.semantic_index is not None,
            "results": [asdict(item) for item in results[:limit]],
        }

    def test(self, reference: str) -> dict[str, object]:
        row = self._resolve(reference)
        package_path = row["package_path"]
        if not package_path:
            outcome, detail = "failed", "legacy skill has no v1 package"
        else:
            try:
                package = self.loader.load(package_path)
                if package.content_hash != row["content_hash"]:
                    raise ValueError("package content hash changed")
                outcome = "static_passed"
                detail = (
                    "format, layout, integrity, and verification declarations "
                    "passed; commands were not executed"
                )
            except (ValueError, OSError) as error:
                outcome, detail = "failed", type(error).__name__
        with self.connection:
            self.connection.execute(
                "UPDATE skills SET verification_status = ? WHERE id = ?",
                (outcome, row["id"]),
            )
            self._history(
                row["id"], "tested", row["lifecycle_status"],
                row["lifecycle_status"], {"outcome": outcome, "detail": detail},
            )
        return {
            "id": row["id"],
            "manifest_id": row["manifest_id"],
            "verification_status": outcome,
            "detail": detail,
            "executed": False,
        }

    def activate(self, reference: str) -> dict[str, object]:
        row = self._resolve(reference)
        if row["lifecycle_status"] == "retired":
            raise ValueError("Retired skills cannot be reactivated")
        if row["verification_status"] != "static_passed":
            raise ValueError("Skill must pass registry testing before activation")
        package_path = row["package_path"]
        if not package_path:
            raise ValueError("Only validated v1 packages can be activated")
        package = self.loader.load(package_path)
        if package.content_hash != row["content_hash"]:
            raise ValueError("Skill package changed after verification")
        return self._transition(row, "active", legacy_status="active")

    def quarantine(self, reference: str) -> dict[str, object]:
        row = self._resolve(reference)
        if row["lifecycle_status"] == "retired":
            raise ValueError("Retired skills cannot leave the terminal state")
        return self._transition(row, "quarantined", legacy_status="quarantine")

    def retire(self, reference: str) -> dict[str, object]:
        row = self._resolve(reference)
        return self._transition(row, "retired", legacy_status="deprecated")

    def history(self, reference: str) -> list[dict[str, object]]:
        row = self._resolve(reference)
        records = self.connection.execute(
            """
            SELECT event, from_status, to_status, details_json, created_at
            FROM skill_registry_history WHERE skill_id = ?
            ORDER BY created_at, rowid
            """,
            (row["id"],),
        ).fetchall()
        result = []
        for item in records:
            record = dict(item)
            record["details"] = json.loads(record.pop("details_json"))
            result.append(record)
        return result

    def _transition(
        self, row: sqlite3.Row, target: str, *, legacy_status: str
    ) -> dict[str, object]:
        source = row["lifecycle_status"]
        if source == target:
            return self.inspect(row["id"])
        with self.connection:
            self.connection.execute(
                """
                UPDATE skills SET lifecycle_status = ?, status = ?
                WHERE id = ?
                """,
                (target, legacy_status, row["id"]),
            )
            self._history(row["id"], "status_changed", source, target, {})
        return self.inspect(row["id"])

    def _resolve(self, reference: str) -> sqlite3.Row:
        if "@" in reference:
            manifest_id, version = reference.rsplit("@", 1)
            row = self.connection.execute(
                """
                SELECT * FROM skills
                WHERE manifest_id = ? AND version = ?
                """,
                (manifest_id, version),
            ).fetchone()
            if row is None:
                raise KeyError(reference)
            return row
        row = self.connection.execute(
            "SELECT * FROM skills WHERE id = ?", (reference,)
        ).fetchone()
        if row is not None:
            return row
        rows = self.connection.execute(
            """
            SELECT * FROM skills
            WHERE manifest_id = ? OR name = ?
            """,
            (reference, reference),
        ).fetchall()
        if not rows:
            raise KeyError(reference)
        return max(rows, key=lambda item: self._semver_key(item["version"]))

    @staticmethod
    def _semver_key(version: str) -> tuple[object, ...]:
        core = version.partition("+")[0]
        numeric, separator, prerelease = core.partition("-")
        major, minor, patch = (int(item) for item in numeric.split("."))
        identifiers = tuple(
            (0, int(item)) if item.isdigit() else (1, item)
            for item in prerelease.split(".")
            if item
        )
        return (major, minor, patch, int(not separator), identifiers)

    def _index(self, skill_id: str) -> None:
        row = self.connection.execute(
            """
            SELECT id, name, description, task_classes_json,
                   applicability_json FROM skills WHERE id = ?
            """,
            (skill_id,),
        ).fetchone()
        self.connection.execute(
            "DELETE FROM skills_fts WHERE skill_id = ?", (skill_id,)
        )
        self.connection.execute(
            """
            INSERT INTO skills_fts(
                skill_id, name, description, task_classes, applicability
            ) VALUES (?, ?, ?, ?, ?)
            """,
            tuple(row),
        )

    def _history(
        self,
        skill_id: str,
        event: str,
        source: str | None,
        target: str | None,
        details: dict[str, object],
    ) -> None:
        self.connection.execute(
            """
            INSERT INTO skill_registry_history(
                id, skill_id, event, from_status, to_status,
                details_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(uuid.uuid4()), skill_id, event, source, target,
                json.dumps(details, sort_keys=True),
                datetime.now(timezone.utc).isoformat(),
            ),
        )
