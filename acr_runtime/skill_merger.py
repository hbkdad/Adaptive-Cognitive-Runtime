from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import asdict, dataclass
from difflib import SequenceMatcher
from itertools import combinations
from typing import Protocol

from .memory import utc_now
from .scoring import query_terms
from .skill_registry import SkillRegistry


RECOMMENDATIONS = frozenset(
    {"KEEP_SEPARATE", "MERGE", "DEPRECATE_ONE", "COMPOSE"}
)


class SkillSemanticSimilarity(Protocol):
    def similarity(self, left: str, right: str) -> float: ...


@dataclass(frozen=True)
class SkillMergePair:
    id: str
    run_id: str
    left_skill_id: str
    right_skill_id: str
    recommendation: str
    deprecate_skill_id: str | None
    active_involved: bool
    automatic_action_allowed: bool
    evidence: dict[str, object]
    created_at: str

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class SkillMergeAnalysis:
    id: str
    requested_skill_id: str | None
    policy: dict[str, object]
    skill_count: int
    pair_count: int
    pairs: tuple[SkillMergePair, ...]
    created_at: str

    def as_dict(self) -> dict[str, object]:
        return {
            **asdict(self),
            "pairs": [pair.as_dict() for pair in self.pairs],
        }


class SkillMerger:
    """Retained, advisory-only redundancy and composition analysis."""

    POLICY = {
        "name": "conservative_skill_merger_v1",
        "maximum_skills": 100,
        "minimum_performance_uses": 3,
        "automatic_actions": False,
        "active_skill_auto_merge": False,
        "semantic_fallback": "unavailable_not_lexical_proxy",
        "thresholds": {
            "deprecate_semantic": 0.90,
            "deprecate_task": 0.80,
            "deprecate_procedure": 0.80,
            "merge_semantic": 0.85,
            "merge_task": 0.65,
            "merge_procedure": 0.70,
            "merge_dependencies": 0.50,
            "compose_semantic": 0.55,
            "compose_task": 0.30,
            "compose_max_procedure": 0.70,
            "compose_max_dependencies": 0.80,
        },
    }

    def __init__(
        self,
        connection: sqlite3.Connection,
        registry: SkillRegistry,
        *,
        semantic_similarity: SkillSemanticSimilarity | None = None,
    ) -> None:
        self.connection = connection
        self.registry = registry
        self.semantic_similarity = semantic_similarity

    @staticmethod
    def _jaccard(left: set[str], right: set[str]) -> float:
        if not left and not right:
            return 1.0
        union = left | right
        return len(left & right) / len(union) if union else 0.0

    @staticmethod
    def _terms(values: object) -> set[str]:
        if isinstance(values, str):
            text = values
        elif isinstance(values, (list, tuple)):
            text = " ".join(str(value) for value in values)
        else:
            text = ""
        return set(query_terms(text))

    @classmethod
    def _procedure_similarity(cls, left: str, right: str) -> float:
        left_terms = query_terms(left)
        right_terms = query_terms(right)
        if not left_terms and not right_terms:
            return 1.0
        if not left_terms or not right_terms:
            return 0.0
        sequence = SequenceMatcher(
            None, tuple(left_terms), tuple(right_terms), autojunk=False
        ).ratio()
        token_overlap = cls._jaccard(set(left_terms), set(right_terms))
        return round((sequence + token_overlap) / 2, 6)

    @staticmethod
    def _performance(skill: dict[str, object]) -> dict[str, object]:
        uses = int(skill["use_count"])
        successes = int(skill["success_count"])
        failures = int(skill["failure_count"])
        return {
            "uses": uses,
            "successes": successes,
            "failures": failures,
            "success_rate": successes / uses if uses else None,
            "reliability": float(skill["reliability"]),
            "average_tokens": (
                float(skill["total_tokens"]) / uses if uses else None
            ),
            "average_cost": (
                float(skill["total_cost"]) / uses if uses else None
            ),
            "average_latency_ms": (
                float(skill["total_latency_ms"]) / uses if uses else None
            ),
        }

    @classmethod
    def _dominance(
        cls,
        left_id: str,
        left: dict[str, object],
        right_id: str,
        right: dict[str, object],
    ) -> tuple[str | None, dict[str, object]]:
        minimum = int(cls.POLICY["minimum_performance_uses"])
        sufficient = (
            int(left["uses"]) >= minimum and int(right["uses"]) >= minimum
        )
        detail: dict[str, object] = {
            "sufficient_history": sufficient,
            "minimum_uses": minimum,
            "dominant_skill_id": None,
        }
        if not sufficient:
            return None, detail

        def dominates(
            candidate: dict[str, object], incumbent: dict[str, object]
        ) -> bool:
            higher = ("success_rate", "reliability")
            lower = ("average_tokens", "average_cost", "average_latency_ms")
            weak = [
                float(candidate[key]) >= float(incumbent[key])
                for key in higher
            ] + [
                float(candidate[key]) <= float(incumbent[key])
                for key in lower
            ]
            strict = [
                float(candidate[key]) > float(incumbent[key])
                for key in higher
            ] + [
                float(candidate[key]) < float(incumbent[key])
                for key in lower
            ]
            return all(weak) and any(strict)

        if dominates(left, right):
            detail["dominant_skill_id"] = left_id
            return right_id, detail
        if dominates(right, left):
            detail["dominant_skill_id"] = right_id
            return left_id, detail
        return None, detail

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
        return major, minor, patch, int(not separator), identifiers

    def _semantic_evidence(
        self, left: dict[str, object], right: dict[str, object]
    ) -> dict[str, object]:
        left_text = " ".join(
            (
                str(left["description"]),
                " ".join(left["task_classes"]),
                " ".join(left["applicability"]),
            )
        )
        right_text = " ".join(
            (
                str(right["description"]),
                " ".join(right["task_classes"]),
                " ".join(right["applicability"]),
            )
        )
        lexical_proxy = self._jaccard(
            self._terms(left_text), self._terms(right_text)
        )
        if self.semantic_similarity is None:
            return {
                "available": False,
                "score": None,
                "method": None,
                "unavailable_reason": "no_trusted_semantic_adapter",
                "lexical_proxy": round(lexical_proxy, 6),
                "lexical_proxy_used_for_semantic_decision": False,
            }
        score = float(self.semantic_similarity.similarity(left_text, right_text))
        if not 0 <= score <= 1:
            raise ValueError("Semantic similarity must be between 0 and 1")
        return {
            "available": True,
            "score": score,
            "method": type(self.semantic_similarity).__name__,
            "unavailable_reason": None,
            "lexical_proxy": round(lexical_proxy, 6),
            "lexical_proxy_used_for_semantic_decision": False,
        }

    def _analyze_pair(
        self, left: dict[str, object], right: dict[str, object]
    ) -> tuple[str, str | None, dict[str, object]]:
        semantic = self._semantic_evidence(left, right)
        task_overlap = self._jaccard(
            set(left["task_classes"]), set(right["task_classes"])
        )
        procedure_similarity = self._procedure_similarity(
            str(left["instructions"]), str(right["instructions"])
        )
        left_dependencies = set(left["manifest"]["dependencies"])
        right_dependencies = set(right["manifest"]["dependencies"])
        dependency_similarity = self._jaccard(
            left_dependencies, right_dependencies
        )
        left_performance = self._performance(left)
        right_performance = self._performance(right)
        dominated, dominance = self._dominance(
            str(left["id"]),
            left_performance,
            str(right["id"]),
            right_performance,
        )
        same_lineage = left["manifest_id"] == right["manifest_id"]
        semantic_score = semantic["score"]
        thresholds = self.POLICY["thresholds"]
        recommendation = "KEEP_SEPARATE"
        deprecate: str | None = None
        reasons: list[str] = []

        redundant = (
            semantic_score is not None
            and float(semantic_score) >= thresholds["deprecate_semantic"]
            and task_overlap >= thresholds["deprecate_task"]
            and procedure_similarity >= thresholds["deprecate_procedure"]
        )
        if redundant and dominated is not None:
            recommendation = "DEPRECATE_ONE"
            deprecate = dominated
            reasons.append("high_overlap_and_performance_dominance")
        elif same_lineage:
            older = min(
                (left, right),
                key=lambda item: self._semver_key(str(item["version"])),
            )
            newer = right if older is left else left
            if (
                newer["lifecycle_status"] == "active"
                and older["lifecycle_status"] != "active"
                and task_overlap >= thresholds["deprecate_task"]
                and procedure_similarity >= 0.75
            ):
                recommendation = "DEPRECATE_ONE"
                deprecate = str(older["id"])
                reasons.append("active_successor_with_matching_scope")
            else:
                reasons.append("lineage_requires_validated_active_successor")
        elif (
            semantic_score is not None
            and float(semantic_score) >= thresholds["merge_semantic"]
            and task_overlap >= thresholds["merge_task"]
            and procedure_similarity >= thresholds["merge_procedure"]
            and dependency_similarity >= thresholds["merge_dependencies"]
        ):
            recommendation = "MERGE"
            reasons.append("high_multidimensional_redundancy")
        elif (
            semantic_score is not None
            and float(semantic_score) >= thresholds["compose_semantic"]
            and task_overlap >= thresholds["compose_task"]
            and procedure_similarity < thresholds["compose_max_procedure"]
            and dependency_similarity < thresholds["compose_max_dependencies"]
        ):
            recommendation = "COMPOSE"
            reasons.append("related_scope_with_complementary_procedures")
        else:
            reasons.append(
                "insufficient_evidence_for_irreversible_consolidation"
            )
        active_involved = (
            left["lifecycle_status"] == "active"
            or right["lifecycle_status"] == "active"
        )
        evidence = {
            "skills": {
                "left": {
                    "id": left["id"],
                    "manifest_id": left["manifest_id"],
                    "version": left["version"],
                    "content_hash": left["content_hash"],
                    "lifecycle_status": left["lifecycle_status"],
                },
                "right": {
                    "id": right["id"],
                    "manifest_id": right["manifest_id"],
                    "version": right["version"],
                    "content_hash": right["content_hash"],
                    "lifecycle_status": right["lifecycle_status"],
                },
            },
            "semantic_overlap": semantic,
            "task_overlap": {
                "score": round(task_overlap, 6),
                "left": left["task_classes"],
                "right": right["task_classes"],
            },
            "procedure_similarity": {
                "score": procedure_similarity,
                "method": "ordered_token_sequence_and_jaccard_v1",
            },
            "dependencies": {
                "score": round(dependency_similarity, 6),
                "left": sorted(left_dependencies),
                "right": sorted(right_dependencies),
            },
            "performance_history": {
                "left": left_performance,
                "right": right_performance,
                "dominance": dominance,
            },
            "same_manifest_lineage": same_lineage,
            "reason_codes": reasons,
            "human_review_required": True,
            "active_skill_protected_from_automatic_merge": active_involved,
            "automatic_action_taken": False,
        }
        return recommendation, deprecate, evidence

    def analyze(
        self,
        *,
        reference: str | None = None,
        limit: int = 50,
    ) -> SkillMergeAnalysis:
        maximum = int(self.POLICY["maximum_skills"])
        if not 1 <= limit <= maximum:
            raise ValueError(f"limit must be between 1 and {maximum}")
        listed = [
            item
            for item in self.registry.list()
            if item["lifecycle_status"] != "retired"
        ]
        requested_skill_id: str | None = None
        if reference is not None:
            requested = self.registry.inspect(reference)
            if requested["lifecycle_status"] == "retired":
                raise ValueError("Retired skills are excluded from merge analysis")
            requested_skill_id = str(requested["id"])
            ids = [requested_skill_id] + [
                str(item["id"])
                for item in listed
                if item["id"] != requested_skill_id
            ][: max(limit - 1, 0)]
        else:
            ids = [str(item["id"]) for item in listed[:limit]]
        skills = [self.registry.inspect(skill_id) for skill_id in ids]
        pair_inputs = list(combinations(skills, 2))
        run_id = str(uuid.uuid4())
        now = utc_now()
        pair_records: list[
            tuple[str, str, str, str, str, str | None, int, str, str]
        ] = []
        for left, right in pair_inputs:
            if str(left["id"]) > str(right["id"]):
                left, right = right, left
            recommendation, deprecate, evidence = self._analyze_pair(
                left, right
            )
            active = int(
                left["lifecycle_status"] == "active"
                or right["lifecycle_status"] == "active"
            )
            pair_records.append(
                (
                    str(uuid.uuid4()),
                    run_id,
                    str(left["id"]),
                    str(right["id"]),
                    recommendation,
                    deprecate,
                    active,
                    json.dumps(evidence, sort_keys=True),
                    now,
                )
            )
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO skill_merge_analysis_runs(
                    id, requested_skill_id, policy_json, skill_count,
                    pair_count, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    requested_skill_id,
                    json.dumps(self.POLICY, sort_keys=True),
                    len(skills),
                    len(pair_records),
                    now,
                ),
            )
            self.connection.executemany(
                """
                INSERT INTO skill_merge_analysis_pairs(
                    id, run_id, left_skill_id, right_skill_id,
                    recommendation, deprecate_skill_id, active_involved,
                    automatic_action_allowed, evidence_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?, ?)
                """,
                pair_records,
            )
        return self.load(run_id)

    def load(self, run_id: str) -> SkillMergeAnalysis:
        run = self.connection.execute(
            "SELECT * FROM skill_merge_analysis_runs WHERE id = ?",
            (run_id,),
        ).fetchone()
        if run is None:
            raise KeyError(run_id)
        rows = self.connection.execute(
            """
            SELECT * FROM skill_merge_analysis_pairs
            WHERE run_id = ?
            ORDER BY recommendation, left_skill_id, right_skill_id
            """,
            (run_id,),
        ).fetchall()
        pairs = tuple(
            SkillMergePair(
                id=row["id"],
                run_id=row["run_id"],
                left_skill_id=row["left_skill_id"],
                right_skill_id=row["right_skill_id"],
                recommendation=row["recommendation"],
                deprecate_skill_id=row["deprecate_skill_id"],
                active_involved=bool(row["active_involved"]),
                automatic_action_allowed=bool(
                    row["automatic_action_allowed"]
                ),
                evidence=json.loads(row["evidence_json"]),
                created_at=row["created_at"],
            )
            for row in rows
        )
        return SkillMergeAnalysis(
            id=run["id"],
            requested_skill_id=run["requested_skill_id"],
            policy=json.loads(run["policy_json"]),
            skill_count=run["skill_count"],
            pair_count=run["pair_count"],
            pairs=pairs,
            created_at=run["created_at"],
        )
