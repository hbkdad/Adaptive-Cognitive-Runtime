from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Mapping

from .capability_vocab import CAPABILITIES
from .secret_management import SecretBoundaryError, assert_secret_free


MICRO = 1_000_000
MAX_CANDIDATES = 500
PROFILE_VERSION = "structured-v1"
FEATURE_WEIGHTS = {
    "intent": 250_000,
    "domain": 200_000,
    "required_capabilities": 150_000,
    "artifacts": 100_000,
    "tools": 100_000,
    "environment": 200_000,
}
_TOKEN = re.compile(r"^[a-z0-9][a-z0-9._:/-]{0,127}$")
_REFERENCE = re.compile(r"^[a-z][a-z0-9_.-]{0,31}:[^\s]{1,240}$")


class TaskSimilarityError(ValueError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _token(value: object, field: str) -> str:
    if not isinstance(value, str):
        raise TaskSimilarityError(f"{field} must be text")
    normalized = value.strip().casefold()
    if not _TOKEN.fullmatch(normalized):
        raise TaskSimilarityError(
            f"{field} must be a normalized 1..128 character token"
        )
    try:
        assert_secret_free(normalized, f"task similarity {field}")
    except SecretBoundaryError as exc:
        raise TaskSimilarityError(f"{field} contains secret material") from exc
    return normalized


def _tokens(
    value: object,
    field: str,
    *,
    maximum: int = 16,
    capabilities: bool = False,
) -> tuple[str, ...]:
    if not isinstance(value, list) or len(value) > maximum:
        raise TaskSimilarityError(f"{field} must be a list of at most {maximum} tokens")
    normalized = tuple(_token(item, field) for item in value)
    if len(set(normalized)) != len(normalized):
        raise TaskSimilarityError(f"{field} must not contain duplicates")
    if capabilities:
        unknown = sorted(set(normalized) - CAPABILITIES)
        if unknown:
            raise TaskSimilarityError(f"unknown required capabilities: {unknown}")
    return tuple(sorted(normalized))


def _evidence(value: object) -> tuple[str, ...]:
    if not isinstance(value, list) or not 1 <= len(value) <= 8:
        raise TaskSimilarityError("evidence must contain 1..8 references")
    result: list[str] = []
    for item in value:
        if not isinstance(item, str) or not _REFERENCE.fullmatch(item):
            raise TaskSimilarityError(
                "evidence entries must be bounded type:value references"
            )
        try:
            assert_secret_free(item, "task similarity evidence")
        except SecretBoundaryError as exc:
            raise TaskSimilarityError("evidence contains secret material") from exc
        result.append(item)
    if len(set(result)) != len(result):
        raise TaskSimilarityError("evidence references must be unique")
    return tuple(result)


def _closed(
    payload: object, fields: set[str], label: str
) -> Mapping[str, object]:
    if not isinstance(payload, Mapping):
        raise TaskSimilarityError(f"{label} must be an object")
    unknown = set(payload) - fields
    if unknown:
        raise TaskSimilarityError(f"{label} contains unknown fields: {sorted(unknown)}")
    return payload


def _jaccard(left: tuple[str, ...], right: tuple[str, ...]) -> int:
    if not left or not right:
        return 0
    left_set = set(left)
    right_set = set(right)
    union = left_set | right_set
    return len(left_set & right_set) * MICRO // len(union)


@dataclass(frozen=True)
class TaskFeatureProfile:
    task_id: str
    intent: str
    domain: str
    required_capabilities: tuple[str, ...]
    artifacts: tuple[str, ...]
    tools: tuple[str, ...]
    environment: tuple[str, ...]
    evidence: tuple[str, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.task_id, str) or not self.task_id.strip():
            raise TaskSimilarityError("task_id must be non-empty text")
        object.__setattr__(self, "task_id", self.task_id.strip())
        object.__setattr__(self, "intent", _token(self.intent, "intent"))
        object.__setattr__(self, "domain", _token(self.domain, "domain"))
        for field in (
            "required_capabilities",
            "artifacts",
            "tools",
            "environment",
        ):
            raw = getattr(self, field)
            object.__setattr__(
                self,
                field,
                _tokens(
                    list(raw),
                    field,
                    capabilities=(field == "required_capabilities"),
                ),
            )
        object.__setattr__(self, "evidence", _evidence(list(self.evidence)))

    @classmethod
    def from_dict(cls, payload: object) -> "TaskFeatureProfile":
        fields = {
            "schema_version",
            "task_id",
            "intent",
            "domain",
            "required_capabilities",
            "artifacts",
            "tools",
            "environment",
            "evidence",
        }
        data = _closed(payload, fields, "task feature profile")
        if data.get("schema_version") != 1 or set(data) != fields:
            raise TaskSimilarityError(
                "task feature profile requires the complete version 1 schema"
            )
        for field in (
            "required_capabilities",
            "artifacts",
            "tools",
            "environment",
        ):
            if not isinstance(data[field], list):
                raise TaskSimilarityError(f"{field} must be a list")
        if not isinstance(data["evidence"], list):
            raise TaskSimilarityError("evidence must be a list")
        return cls(
            task_id=data["task_id"],
            intent=data["intent"],
            domain=data["domain"],
            required_capabilities=tuple(data["required_capabilities"]),
            artifacts=tuple(data["artifacts"]),
            tools=tuple(data["tools"]),
            environment=tuple(data["environment"]),
            evidence=tuple(data["evidence"]),
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "profile_version": PROFILE_VERSION,
            "task_id": self.task_id,
            "intent": self.intent,
            "domain": self.domain,
            "required_capabilities": list(self.required_capabilities),
            "artifacts": list(self.artifacts),
            "tools": list(self.tools),
            "environment": list(self.environment),
            "evidence": list(self.evidence),
        }

    @property
    def profile_hash(self) -> str:
        payload = self.as_dict()
        payload.pop("profile_version")
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()


@dataclass(frozen=True)
class TaskAnalogy:
    task_id: str
    status: str
    critic_score: float | None
    completed_at: str
    score_micros: int
    breakdown_micros: Mapping[str, int]
    profile: TaskFeatureProfile

    def as_dict(self) -> dict[str, object]:
        return {
            "task_id": self.task_id,
            "status": self.status,
            "critic_score": self.critic_score,
            "completed_at": self.completed_at,
            "score_micros": self.score_micros,
            "breakdown_micros": dict(self.breakdown_micros),
            "profile": self.profile.as_dict(),
            "analogy_only": True,
            "execution_authority": False,
        }


@dataclass(frozen=True)
class TaskSimilarityResult:
    target_task_id: str
    scope: str
    candidates_considered: int
    minimum_score_micros: int
    analogies: tuple[TaskAnalogy, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "target_task_id": self.target_task_id,
            "scope": self.scope,
            "profile_version": PROFILE_VERSION,
            "method": "weighted-structured-features",
            "embedding_used": False,
            "feature_weights_micros": dict(FEATURE_WEIGHTS),
            "candidates_considered": self.candidates_considered,
            "minimum_score_micros": self.minimum_score_micros,
            "analogies": [item.as_dict() for item in self.analogies],
            "analogy_only": True,
            "execution_authority": False,
        }


class TaskSimilarityEngine:
    def __init__(
        self,
        connection: sqlite3.Connection,
        *,
        mutation_guard: Callable[[str], None] | None = None,
    ) -> None:
        self.connection = connection
        self.mutation_guard = mutation_guard

    def add_profile(self, profile: TaskFeatureProfile) -> TaskFeatureProfile:
        if self.mutation_guard is not None:
            self.mutation_guard("task_profile_write")
        task = self.connection.execute(
            "SELECT id FROM tasks WHERE id=?", (profile.task_id,)
        ).fetchone()
        if task is None:
            raise TaskSimilarityError(f"unknown task: {profile.task_id}")
        try:
            self.connection.execute(
                """
                INSERT INTO task_feature_profiles(
                    task_id, profile_version, intent, domain,
                    required_capabilities_json, artifacts_json, tools_json,
                    environment_json, evidence_json, profile_hash, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    profile.task_id,
                    PROFILE_VERSION,
                    profile.intent,
                    profile.domain,
                    json.dumps(profile.required_capabilities, separators=(",", ":")),
                    json.dumps(profile.artifacts, separators=(",", ":")),
                    json.dumps(profile.tools, separators=(",", ":")),
                    json.dumps(profile.environment, separators=(",", ":")),
                    json.dumps(profile.evidence, separators=(",", ":")),
                    profile.profile_hash,
                    _now(),
                ),
            )
            self.connection.commit()
        except sqlite3.IntegrityError as exc:
            self.connection.rollback()
            raise TaskSimilarityError(
                f"task profile is immutable or invalid: {profile.task_id}"
            ) from exc
        return self.profile(profile.task_id)

    def profile(self, task_id: str) -> TaskFeatureProfile:
        row = self.connection.execute(
            "SELECT * FROM task_feature_profiles WHERE task_id=?", (task_id,)
        ).fetchone()
        if row is None:
            raise TaskSimilarityError(f"task has no structured profile: {task_id}")
        return self._profile_from_row(row)

    def similar(
        self,
        task_id: str,
        *,
        limit: int = 10,
        minimum_score_micros: int = 1,
    ) -> TaskSimilarityResult:
        if type(limit) is not int or not 1 <= limit <= 50:
            raise TaskSimilarityError("limit must be 1..50")
        if (
            type(minimum_score_micros) is not int
            or not 0 <= minimum_score_micros <= MICRO
        ):
            raise TaskSimilarityError(
                "minimum_score_micros must be an integer from 0 to 1000000"
            )
        target = self.profile(task_id)
        task = self.connection.execute(
            "SELECT scope FROM tasks WHERE id=?", (task_id,)
        ).fetchone()
        if task is None:
            raise TaskSimilarityError(f"unknown task: {task_id}")
        scope = str(task["scope"])
        rows = self.connection.execute(
            """
            SELECT p.*, t.status, t.critic_score, t.completed_at
            FROM task_feature_profiles AS p
            JOIN tasks AS t ON t.id=p.task_id
            WHERE p.task_id<>?
              AND t.scope=?
              AND t.status IN ('succeeded', 'failed')
              AND t.completed_at IS NOT NULL
            ORDER BY t.completed_at DESC, p.task_id
            LIMIT ?
            """,
            (task_id, scope, MAX_CANDIDATES + 1),
        ).fetchall()
        if len(rows) > MAX_CANDIDATES:
            raise TaskSimilarityError(
                "structured similarity candidate bound exceeded"
            )
        analogies: list[TaskAnalogy] = []
        for row in rows:
            candidate = self._profile_from_row(row)
            breakdown = self._breakdown(target, candidate)
            score = sum(
                breakdown[field] * weight // MICRO
                for field, weight in FEATURE_WEIGHTS.items()
            )
            if score < minimum_score_micros:
                continue
            analogies.append(
                TaskAnalogy(
                    task_id=str(row["task_id"]),
                    status=str(row["status"]),
                    critic_score=(
                        None
                        if row["critic_score"] is None
                        else float(row["critic_score"])
                    ),
                    completed_at=str(row["completed_at"]),
                    score_micros=score,
                    breakdown_micros=breakdown,
                    profile=candidate,
                )
            )
        # The query is already newest-first; Python's stable sort preserves that
        # order for equal similarity scores.
        analogies.sort(key=lambda item: -item.score_micros)
        return TaskSimilarityResult(
            target_task_id=task_id,
            scope=scope,
            candidates_considered=len(rows),
            minimum_score_micros=minimum_score_micros,
            analogies=tuple(analogies[:limit]),
        )

    @staticmethod
    def _breakdown(
        target: TaskFeatureProfile, candidate: TaskFeatureProfile
    ) -> dict[str, int]:
        return {
            "intent": MICRO if target.intent == candidate.intent else 0,
            "domain": MICRO if target.domain == candidate.domain else 0,
            "required_capabilities": _jaccard(
                target.required_capabilities, candidate.required_capabilities
            ),
            "artifacts": _jaccard(target.artifacts, candidate.artifacts),
            "tools": _jaccard(target.tools, candidate.tools),
            "environment": _jaccard(target.environment, candidate.environment),
        }

    @staticmethod
    def _profile_from_row(row: sqlite3.Row) -> TaskFeatureProfile:
        return TaskFeatureProfile(
            task_id=str(row["task_id"]),
            intent=str(row["intent"]),
            domain=str(row["domain"]),
            required_capabilities=tuple(
                json.loads(row["required_capabilities_json"])
            ),
            artifacts=tuple(json.loads(row["artifacts_json"])),
            tools=tuple(json.loads(row["tools_json"])),
            environment=tuple(json.loads(row["environment_json"])),
            evidence=tuple(json.loads(row["evidence_json"])),
        )
