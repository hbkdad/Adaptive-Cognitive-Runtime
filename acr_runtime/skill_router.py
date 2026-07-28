from __future__ import annotations

import itertools
import json
import sqlite3
from dataclasses import asdict, dataclass
from typing import Callable

from .scoring import query_terms
from .skill_registry import SkillRegistry


@dataclass(frozen=True)
class SkillRouterConfig:
    candidate_limit: int = 12
    max_skills: int = 4
    minimum_benefit: float = 0.08
    overlap_threshold: float = 0.60
    overhead_weight: float = 0.20
    set_size_penalty: float = 0.02

    def __post_init__(self) -> None:
        if self.candidate_limit < 1:
            raise ValueError("candidate_limit must be positive")
        if self.max_skills < 1:
            raise ValueError("max_skills must be positive")
        if not 0 <= self.minimum_benefit <= 1:
            raise ValueError("minimum_benefit must be 0..1")
        if not 0 <= self.overlap_threshold <= 1:
            raise ValueError("overlap_threshold must be 0..1")
        if self.overhead_weight < 0 or self.set_size_penalty < 0:
            raise ValueError("router penalties cannot be negative")


@dataclass(frozen=True)
class RoutedSkill:
    id: str
    manifest_id: str
    name: str
    version: str
    applicability: float
    expected_benefit: float
    token_overhead: int
    historical_success: float
    reliability: float
    overlap_penalty: float
    final_score: float
    selected: bool
    reason: str
    rejection_reason: str | None
    dependency_ids: tuple[str, ...]


@dataclass(frozen=True)
class SkillRoute:
    task: str
    task_class: str
    token_budget: int
    semantic_available: bool
    candidates: tuple[RoutedSkill, ...]

    @property
    def selected(self) -> tuple[RoutedSkill, ...]:
        return tuple(item for item in self.candidates if item.selected)

    @property
    def rejected(self) -> tuple[RoutedSkill, ...]:
        return tuple(item for item in self.candidates if not item.selected)

    def as_dict(self) -> dict[str, object]:
        return {
            "task_class": self.task_class,
            "token_budget": self.token_budget,
            "semantic_available": self.semantic_available,
            "selected": [asdict(item) for item in self.selected],
            "rejected": [asdict(item) for item in self.rejected],
        }


@dataclass
class _Candidate:
    row: sqlite3.Row
    applicability: float
    benefit: float
    historical: float
    coverage: frozenset[str]
    dependency_ids: tuple[str, ...]
    unavailable_dependency: bool = False


class SkillRouter:
    """Metadata-first, exact bounded selection of the smallest useful skill set."""

    def __init__(
        self,
        connection: sqlite3.Connection,
        registry: SkillRegistry,
        *,
        config: SkillRouterConfig | None = None,
        config_provider: Callable[[str], SkillRouterConfig] | None = None,
    ) -> None:
        self.connection = connection
        self.registry = registry
        self.config = config or SkillRouterConfig()
        self.config_provider = config_provider

    def route(
        self,
        task: str,
        *,
        task_class: str = "general",
        token_budget: int,
        scope: str = "global",
    ) -> SkillRoute:
        if self.config_provider is not None:
            return SkillRouter(
                self.connection,
                self.registry,
                config=self.config_provider(scope),
            ).route(
                task,
                task_class=task_class,
                token_budget=token_budget,
                scope=scope,
            )
        if not task.strip():
            raise ValueError("task cannot be empty")
        if not task_class.strip():
            raise ValueError("task_class cannot be empty")
        if token_budget < 0:
            raise ValueError("token_budget cannot be negative")
        search = self.registry.search(
            task,
            limit=self.config.candidate_limit,
            lifecycle_statuses=frozenset({"active"}),
        )
        search_results = {
            item["id"]: item for item in search["results"]
        }
        rows: dict[str, sqlite3.Row] = {}
        for skill_id in search_results:
            row = self.connection.execute(
                """
                SELECT * FROM skills
                WHERE id = ? AND status = 'active'
                  AND lifecycle_status = 'active'
                """,
                (skill_id,),
            ).fetchone()
            if row is not None:
                rows[skill_id] = row

        self._expand_dependencies(rows)
        task_terms = frozenset(query_terms(task))
        candidates: dict[str, _Candidate] = {}
        for skill_id, row in rows.items():
            metadata = " ".join(
                (
                    row["name"], row["description"],
                    " ".join(json.loads(row["task_classes_json"])),
                    " ".join(json.loads(row["applicability_json"])),
                )
            )
            coverage = frozenset(query_terms(metadata)) & task_terms
            search_score = float(
                search_results.get(skill_id, {}).get("combined_score", 0)
            )
            classes = set(json.loads(row["task_classes_json"]))
            class_match = float(task_class != "general" and task_class in classes)
            applicability = min(1.0, 0.7 * search_score + 0.3 * class_match)
            performance = self.connection.execute(
                """
                SELECT COALESCE(SUM(uses), 0) AS uses,
                       COALESCE(SUM(successful_uses), 0) AS successful_uses
                FROM skill_performance
                WHERE skill_id = ? AND task_class = ?
                """,
                (skill_id, task_class),
            ).fetchone()
            if performance["uses"]:
                historical = (
                    performance["successful_uses"] / performance["uses"]
                )
            elif row["use_count"]:
                historical = row["success_count"] / row["use_count"]
            else:
                historical = row["reliability"]
            benefit = applicability * (
                0.5 * row["reliability"] + 0.5 * historical
            )
            dependency_ids, unavailable = self._dependency_ids(row, rows)
            candidates[skill_id] = _Candidate(
                row=row,
                applicability=applicability,
                benefit=benefit,
                historical=historical,
                coverage=coverage,
                dependency_ids=dependency_ids,
                unavailable_dependency=unavailable,
            )

        required_dependency_ids = {
            dependency_id
            for item in candidates.values()
            for dependency_id in item.dependency_ids
        }
        eligible = [
            item for item in candidates.values()
            if (
                item.benefit >= self.config.minimum_benefit
                or item.row["id"] in required_dependency_ids
            )
            and not item.unavailable_dependency
        ]
        selected_ids = self._optimize(eligible, token_budget)
        routed: list[RoutedSkill] = []
        for skill_id, item in candidates.items():
            selected = skill_id in selected_ids
            overlaps = [
                self._overlap(item.coverage, candidates[other].coverage)
                for other in selected_ids
                if other != skill_id
            ]
            overlap = max(overlaps, default=0.0)
            overhead = item.row["token_cost"]
            final = item.benefit - self.config.overhead_weight * (
                overhead / max(1, token_budget)
            )
            if selected:
                reason = (
                    f"applicability={item.applicability:.3f}, "
                    f"benefit={item.benefit:.3f}, tokens={overhead}, "
                    f"historical={item.historical:.3f}"
                )
                rejection = None
            elif item.unavailable_dependency:
                reason, rejection = "", "dependency_unavailable"
            elif overhead > token_budget:
                reason, rejection = "", "token_budget"
            elif item.benefit < self.config.minimum_benefit:
                reason, rejection = "", "below_minimum_benefit"
            elif overlap >= self.config.overlap_threshold:
                reason, rejection = "", "overlap_without_measurable_gain"
            else:
                reason, rejection = "", "lower_marginal_value"
            routed.append(
                RoutedSkill(
                    id=skill_id,
                    manifest_id=item.row["manifest_id"] or item.row["name"],
                    name=item.row["name"],
                    version=item.row["version"],
                    applicability=item.applicability,
                    expected_benefit=item.benefit,
                    token_overhead=overhead,
                    historical_success=item.historical,
                    reliability=item.row["reliability"],
                    overlap_penalty=overlap,
                    final_score=final,
                    selected=selected,
                    reason=reason,
                    rejection_reason=rejection,
                    dependency_ids=item.dependency_ids,
                )
            )
        routed.sort(key=lambda item: (not item.selected, -item.final_score, item.id))
        return SkillRoute(
            task=task,
            task_class=task_class,
            token_budget=token_budget,
            semantic_available=bool(search["semantic_available"]),
            candidates=tuple(routed),
        )

    def _optimize(
        self, candidates: list[_Candidate], token_budget: int
    ) -> frozenset[str]:
        best: tuple[float, int, int, tuple[str, ...]] = (0.0, 0, 0, ())
        maximum = min(self.config.max_skills, len(candidates))
        for count in range(1, maximum + 1):
            for subset in itertools.combinations(candidates, count):
                ids = frozenset(item.row["id"] for item in subset)
                if any(
                    not set(item.dependency_ids).issubset(ids)
                    for item in subset
                ):
                    continue
                tokens = sum(item.row["token_cost"] for item in subset)
                if tokens > token_budget:
                    continue
                overlap_penalty = 0.0
                for left, right in itertools.combinations(subset, 2):
                    overlap = self._overlap(left.coverage, right.coverage)
                    dependent = (
                        right.row["id"] in left.dependency_ids
                        or left.row["id"] in right.dependency_ids
                    )
                    if overlap >= self.config.overlap_threshold and not dependent:
                        overlap_penalty += overlap * min(
                            left.benefit, right.benefit
                        )
                objective = (
                    sum(item.benefit for item in subset)
                    - self.config.overhead_weight
                    * (tokens / max(1, token_budget))
                    - overlap_penalty
                    - self.config.set_size_penalty * (count - 1)
                )
                stable_ids = tuple(sorted(ids))
                candidate_key = (objective, -count, -tokens, stable_ids)
                if candidate_key > best:
                    best = candidate_key
        return frozenset(best[3])

    def _expand_dependencies(self, rows: dict[str, sqlite3.Row]) -> None:
        changed = True
        while changed:
            changed = False
            for row in tuple(rows.values()):
                manifest = json.loads(row["manifest_json"])
                for dependency in manifest.get("dependencies", []):
                    manifest_id, version = dependency.rsplit("@", 1)
                    dependency_row = self.connection.execute(
                        """
                        SELECT * FROM skills
                        WHERE manifest_id = ? AND version = ?
                          AND status = 'active'
                          AND lifecycle_status = 'active'
                        """,
                        (manifest_id, version),
                    ).fetchone()
                    if dependency_row is not None and dependency_row["id"] not in rows:
                        rows[dependency_row["id"]] = dependency_row
                        changed = True

    @staticmethod
    def _dependency_ids(
        row: sqlite3.Row, rows: dict[str, sqlite3.Row]
    ) -> tuple[tuple[str, ...], bool]:
        manifest = json.loads(row["manifest_json"])
        resolved: list[str] = []
        unavailable = False
        for dependency in manifest.get("dependencies", []):
            manifest_id, version = dependency.rsplit("@", 1)
            match = next(
                (
                    item for item in rows.values()
                    if item["manifest_id"] == manifest_id
                    and item["version"] == version
                ),
                None,
            )
            if match is None:
                unavailable = True
            else:
                resolved.append(match["id"])
        return tuple(sorted(resolved)), unavailable

    @staticmethod
    def _overlap(left: frozenset[str], right: frozenset[str]) -> float:
        union = left | right
        return len(left & right) / len(union) if union else 0.0
