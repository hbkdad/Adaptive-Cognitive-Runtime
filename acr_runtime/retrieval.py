from __future__ import annotations

import math
import re
import sqlite3
from dataclasses import dataclass, field, replace
from datetime import timedelta, timezone
from time import perf_counter
from typing import Callable, Mapping, Protocol, Sequence

from .cache import RETRIEVAL_CACHE_VERSION, CacheEntry, SafeCache
from .memory import (
    MemoryQuery,
    MemoryReader,
    MemoryRecord,
    MemoryStatus,
    MemoryType,
    LifecycleState,
    Sensitivity,
    parse_timestamp,
)
from .scoring import lexical_relevance
from .performance_profiler import profile_operation
from .knowledge_decay import KnowledgeDecayPolicy

SPACE_RE = re.compile(r"\s+")


class SemanticSimilarityProvider(Protocol):
    """Optional semantic scorer implemented by an embedding or model adapter."""

    def score(
        self, query: str, memories: Sequence[MemoryRecord]
    ) -> Mapping[str, float]: ...


@dataclass(frozen=True)
class RetrievalWeights:
    keyword: float = 0.24
    semantic: float = 0.18
    scope: float = 0.08
    recency: float = 0.08
    temporal: float = 0.04
    confidence: float = 0.12
    historical_utility: float = 0.10
    importance: float = 0.08
    task_similarity: float = 0.05
    source_reliability: float = 0.03

    def __post_init__(self) -> None:
        values = self.as_dict().values()
        if any(value < 0 for value in values):
            raise ValueError("Retrieval weights cannot be negative")
        if sum(values) <= 0:
            raise ValueError("At least one retrieval weight must be positive")

    def as_dict(self) -> dict[str, float]:
        return {
            name: value
            for name, value in self.__dict__.items()
        }


@dataclass(frozen=True)
class RetrievalConfig:
    weights: RetrievalWeights = field(default_factory=RetrievalWeights)
    candidate_multiplier: int = 8
    maximum_candidates: int = 200
    minimum_score: float = 0.0
    recency_half_life_days: float = 90.0
    default_source_reliability: float = 0.5
    source_reliability: Mapping[str, float] = field(
        default_factory=lambda: {
            "user": 0.95,
            "test": 0.95,
            "file": 0.85,
            "runtime": 0.80,
            "model": 0.55,
            "legacy": 0.50,
        }
    )

    def __post_init__(self) -> None:
        if self.candidate_multiplier < 2:
            raise ValueError("candidate_multiplier must be at least 2")
        if not 1 <= self.maximum_candidates <= 200:
            raise ValueError("maximum_candidates must be between 1 and 200")
        if not 0 <= self.minimum_score <= 1:
            raise ValueError("minimum_score must be between 0 and 1")
        if self.recency_half_life_days <= 0:
            raise ValueError("recency_half_life_days must be positive")
        reliabilities = [
            self.default_source_reliability,
            *self.source_reliability.values(),
        ]
        if any(not 0 <= value <= 1 for value in reliabilities):
            raise ValueError("Source reliability must be between 0 and 1")


@dataclass(frozen=True)
class RetrievalRequest:
    task: str
    query: str
    scope: str
    token_budget: int
    types: tuple[MemoryType, ...] = ()
    valid_at: str | None = None
    minimum_confidence: float = 0.0
    target_memories: int = 12
    sensitivities: tuple[Sensitivity, ...] = ()
    include_global: bool = True
    cache_max_age_seconds: int | None = None

    def __post_init__(self) -> None:
        if not self.scope.strip():
            raise ValueError("Retrieval scope cannot be empty")
        if self.token_budget < 0:
            raise ValueError("Retrieval token budget cannot be negative")
        if not 1 <= self.target_memories <= 100:
            raise ValueError("target_memories must be between 1 and 100")
        if not 0 <= self.minimum_confidence <= 1:
            raise ValueError("minimum_confidence must be between 0 and 1")
        if (
            self.cache_max_age_seconds is not None
            and (
                type(self.cache_max_age_seconds) is not int
                or not 1 <= self.cache_max_age_seconds <= 86_400
            )
        ):
            raise ValueError(
                "cache_max_age_seconds must be null or between 1 and 86400"
            )


@dataclass(frozen=True)
class ScoreBreakdown:
    keyword: float
    semantic: float | None
    scope: float
    recency: float
    temporal: float
    confidence: float
    historical_utility: float
    importance: float
    task_similarity: float
    source_reliability: float

    def as_dict(self) -> dict[str, float | None]:
        return dict(self.__dict__)


@dataclass(frozen=True)
class RankedMemory:
    memory: MemoryRecord
    score: float
    breakdown: ScoreBreakdown
    explanation: str
    conflict_ids: tuple[str, ...] = ()
    selected: bool = False
    rejection_reason: str | None = None


@dataclass(frozen=True)
class RetrievalResult:
    ranked: tuple[RankedMemory, ...]
    selected: tuple[RankedMemory, ...]
    rejected: tuple[RankedMemory, ...]
    candidate_count: int
    selected_tokens: int
    semantic_available: bool
    semantic_status: str
    cache_status: str = "bypass"


class HybridMemoryRetriever:
    def __init__(
        self,
        reader: MemoryReader,
        *,
        semantic: SemanticSimilarityProvider | None = None,
        config: RetrievalConfig | None = None,
        cache: SafeCache | None = None,
        config_provider: Callable[[str], RetrievalConfig] | None = None,
        decay_policy: KnowledgeDecayPolicy | None = None,
    ) -> None:
        self.reader = reader
        self.semantic = semantic
        self.config = config or RetrievalConfig()
        self.cache = cache
        self.config_provider = config_provider
        self.decay_policy = decay_policy or KnowledgeDecayPolicy()

    def _cache_envelope(self, request: RetrievalRequest) -> dict[str, object]:
        return {
            "algorithm_version": RETRIEVAL_CACHE_VERSION,
            "task": request.task,
            "query": request.query,
            "scope": request.scope,
            "token_budget": request.token_budget,
            "types": sorted(item.value for item in request.types),
            "valid_at": request.valid_at,
            "minimum_confidence": request.minimum_confidence,
            "target_memories": request.target_memories,
            "sensitivities": sorted(
                item.value for item in request.sensitivities
            ),
            "include_global": request.include_global,
            "cache_max_age_seconds": request.cache_max_age_seconds,
            "config": {
                "weights": self.config.weights.as_dict(),
                "candidate_multiplier": self.config.candidate_multiplier,
                "maximum_candidates": self.config.maximum_candidates,
                "minimum_score": self.config.minimum_score,
                "recency_half_life_days": self.config.recency_half_life_days,
                "default_source_reliability": (
                    self.config.default_source_reliability
                ),
                "source_reliability": dict(self.config.source_reliability),
            },
        }

    @staticmethod
    def _cache_payload(result: RetrievalResult) -> dict[str, object]:
        return {
            "ranked": [
                {
                    "memory_id": item.memory.id,
                    "memory_updated_at": item.memory.updated_at,
                    "privacy_policy_version": (
                        item.memory.privacy_policy_version
                    ),
                    "score": item.score,
                    "breakdown": item.breakdown.as_dict(),
                    "conflict_ids": list(item.conflict_ids),
                    "selected": item.selected,
                    "rejection_reason": item.rejection_reason,
                }
                for item in result.ranked
            ],
            "candidate_count": result.candidate_count,
            "selected_tokens": result.selected_tokens,
        }

    def _cached_result(
        self,
        entry: CacheEntry,
        request: RetrievalRequest,
    ) -> RetrievalResult | None:
        payload = entry.payload
        rows = payload.get("ranked")
        if (
            not isinstance(rows, list)
            or len(rows) > self.config.maximum_candidates
            or type(payload.get("candidate_count")) is not int
            or type(payload.get("selected_tokens")) is not int
        ):
            return None
        if self.cache is None:
            return None
        visible_scopes = set(self.cache.scopes.visible_scope_ids(
            request.scope, include_ancestors=request.include_global
        ))
        allowed_statuses = (
            {MemoryStatus.CONFIRMED, MemoryStatus.SUPERSEDED}
            if request.valid_at is not None
            else {MemoryStatus.CONFIRMED}
        )
        moment = (
            parse_timestamp(request.valid_at)
            if request.valid_at is not None
            else self.cache.clock()
        )
        authorization_time = self.cache.clock().astimezone(timezone.utc)
        ranked: list[RankedMemory] = []
        for row in rows:
            if not isinstance(row, dict) or set(row) != {
                "memory_id",
                "memory_updated_at",
                "privacy_policy_version",
                "score",
                "breakdown",
                "conflict_ids",
                "selected",
                "rejection_reason",
            }:
                return None
            memory_id = row["memory_id"]
            if not isinstance(memory_id, str):
                return None
            memory = self.reader.get(memory_id)
            if (
                memory is None
                or memory.updated_at != row["memory_updated_at"]
                or memory.privacy_policy_version
                != row["privacy_policy_version"]
                or memory.scope not in visible_scopes
                or memory.status not in allowed_statuses
                or memory.lifecycle_state not in {
                    LifecycleState.ACTIVE,
                    LifecycleState.COLD,
                }
                or (
                    request.types
                    and memory.type not in request.types
                )
                or (
                    request.sensitivities
                    and memory.sensitivity not in request.sensitivities
                )
                or memory.confidence < request.minimum_confidence
                or parse_timestamp(memory.valid_from) > moment
                or (
                    memory.valid_until is not None
                    and moment >= parse_timestamp(memory.valid_until)
                )
                or (
                    memory.retention_until is not None
                    and authorization_time
                    >= parse_timestamp(memory.retention_until)
                )
            ):
                return None
            breakdown = row["breakdown"]
            conflict_ids = row["conflict_ids"]
            if (
                not isinstance(breakdown, dict)
                or set(breakdown) != set(RetrievalWeights().as_dict())
                or not isinstance(conflict_ids, list)
                or any(not isinstance(item, str) for item in conflict_ids)
                or type(row["selected"]) is not bool
                or (
                    row["rejection_reason"] is not None
                    and not isinstance(row["rejection_reason"], str)
                )
                or type(row["score"]) not in (int, float)
                or not math.isfinite(float(row["score"]))
                or not 0 <= float(row["score"]) <= 1
            ):
                return None
            try:
                score_breakdown = ScoreBreakdown(**breakdown)
            except (TypeError, ValueError):
                return None
            if any(
                value is not None
                and (
                    type(value) not in (int, float)
                    or not math.isfinite(float(value))
                    or not 0 <= float(value) <= 1
                )
                for value in score_breakdown.as_dict().values()
            ):
                return None
            components = score_breakdown.as_dict()
            weights = self.config.weights.as_dict()
            active = {
                name: value
                for name, value in components.items()
                if value is not None and weights[name] > 0
            }
            strongest = sorted(
                active,
                key=lambda name: active[name] * weights[name],
                reverse=True,
            )[:3]
            explanation = ", ".join(
                f"{name}={active[name]:.2f}" for name in strongest
            )
            if score_breakdown.semantic is None:
                explanation += "; semantic=unavailable"
            if conflict_ids:
                explanation += (
                    f"; unresolved_conflicts={len(conflict_ids)}"
                )
            ranked.append(
                RankedMemory(
                    memory=memory,
                    score=float(row["score"]),
                    breakdown=score_breakdown,
                    explanation=explanation,
                    conflict_ids=tuple(conflict_ids),
                    selected=row["selected"],
                    rejection_reason=row["rejection_reason"],
                )
            )
        selected = tuple(item for item in ranked if item.selected)
        rejected = tuple(item for item in ranked if not item.selected)
        if (
            payload["candidate_count"] != len(ranked)
            or payload["selected_tokens"]
            != sum(item.memory.token_cost for item in selected)
        ):
            return None
        return RetrievalResult(
            ranked=tuple(ranked),
            selected=selected,
            rejected=rejected,
            candidate_count=payload["candidate_count"],
            selected_tokens=payload["selected_tokens"],
            semantic_available=False,
            semantic_status="not_configured",
            cache_status="hit",
        )

    @staticmethod
    def _normalized(record: MemoryRecord) -> str:
        subject = record.subject or ""
        return SPACE_RE.sub(" ", f"{subject}\n{record.content}".strip().lower())

    @staticmethod
    def _historical(record: MemoryRecord) -> float:
        if record.access_count:
            return record.successful_uses / record.access_count
        return record.utility_score if record.utility_score else 0.5

    def _candidate_limit(self, request: RetrievalRequest) -> int:
        return min(
            self.config.maximum_candidates,
            max(
                request.target_memories * self.config.candidate_multiplier,
                request.target_memories + 1,
            ),
        )

    def _candidates(self, request: RetrievalRequest) -> list[MemoryRecord]:
        limit = self._candidate_limit(request)
        common = {
            "scope": request.scope,
            "include_global": request.include_global,
            "types": request.types,
            "sensitivities": request.sensitivities,
            "statuses": (
                (MemoryStatus.CONFIRMED, MemoryStatus.SUPERSEDED)
                if request.valid_at is not None
                else (MemoryStatus.CONFIRMED,)
            ),
            "valid_at": request.valid_at,
            "minimum_confidence": request.minimum_confidence,
            "limit": limit,
        }
        keyword = self.reader.search(
            MemoryQuery(text=request.query, **common)
        ).records
        broad = self.reader.search(MemoryQuery(**common)).records
        by_id = {record.id: record for record in (*keyword, *broad)}
        return list(by_id.values())

    def _score(
        self,
        record: MemoryRecord,
        request: RetrievalRequest,
        semantic_scores: Mapping[str, float],
        semantic_available: bool,
    ) -> RankedMemory:
        searchable = f"{record.subject or ''} {record.content}"
        semantic = (
            max(0.0, min(1.0, semantic_scores.get(record.id, 0.0)))
            if semantic_available
            else None
        )
        breakdown = ScoreBreakdown(
            keyword=lexical_relevance(request.query, searchable),
            semantic=semantic,
            scope=1.0 if record.scope == request.scope else 0.8,
            recency=self.decay_policy.assess(
                record,
                assessed_at=request.valid_at,
                baseline_days=self.config.recency_half_life_days,
            ).recency_score,
            temporal=1.0,
            confidence=record.confidence,
            historical_utility=self._historical(record),
            importance=record.importance,
            task_similarity=lexical_relevance(request.task, searchable),
            source_reliability=self.config.source_reliability.get(
                record.source_type or "",
                self.config.default_source_reliability,
            ),
        )
        components = breakdown.as_dict()
        weights = self.config.weights.as_dict()
        active = {
            name: value
            for name, value in components.items()
            if value is not None and weights[name] > 0
        }
        denominator = sum(weights[name] for name in active)
        score = sum(active[name] * weights[name] for name in active) / denominator
        strongest = sorted(
            active, key=lambda name: active[name] * weights[name], reverse=True
        )[:3]
        explanation = ", ".join(
            f"{name}={active[name]:.2f}" for name in strongest
        )
        if semantic is None:
            explanation += "; semantic=unavailable"
        return RankedMemory(
            memory=record,
            score=score,
            breakdown=breakdown,
            explanation=explanation,
        )

    @staticmethod
    def _conflicts(
        ranked: Sequence[RankedMemory],
    ) -> dict[str, tuple[str, ...]]:
        subjects: dict[tuple[str, str], list[RankedMemory]] = {}
        for item in ranked:
            if item.memory.subject:
                key = (item.memory.scope, item.memory.subject.strip().lower())
                subjects.setdefault(key, []).append(item)
        conflicts: dict[str, tuple[str, ...]] = {}
        for group in subjects.values():
            contents = {
                SPACE_RE.sub(" ", item.memory.content.strip().lower())
                for item in group
            }
            if len(contents) < 2:
                continue
            for item in group:
                related = tuple(
                    other.memory.id
                    for other in group
                    if other.memory.id != item.memory.id
                    and other.memory.id not in {
                        item.memory.supersedes,
                        item.memory.superseded_by,
                    }
                )
                if related:
                    conflicts[item.memory.id] = related
        return conflicts

    def retrieve(self, request: RetrievalRequest) -> RetrievalResult:
        if self.config_provider is not None:
            resolved = self.config_provider(request.scope)
            return HybridMemoryRetriever(
                self.reader,
                semantic=self.semantic,
                config=resolved,
                cache=self.cache,
            ).retrieve(request)
        cache_key: str | None = None
        source_generation: int | None = None
        cache_status = "bypass"
        if self.cache is not None:
            if request.cache_max_age_seconds is None:
                pass
            elif self.semantic is not None:
                self.cache.event("bypass", "semantic_identity_unavailable")
            else:
                try:
                    cache_key = self.cache.retrieval_key(
                        self._cache_envelope(request),
                        scope=request.scope,
                        include_ancestors=request.include_global,
                    )
                    if cache_key is None:
                        self.cache.event("bypass", "secret_key_material")
                    else:
                        entry = self.cache.probe(cache_key)
                        if entry is not None:
                            cached = self._cached_result(entry, request)
                            if cached is not None:
                                if self.cache.confirm_hit(entry):
                                    return cached
                                cache_status = "miss"
                            else:
                                self.cache.invalidate(
                                    entry.id, reason="rehydration_failed"
                                )
                        self.cache.event("miss", "no_fresh_exact_match")
                        source_generation = self.cache.generation()
                except (sqlite3.Error, TypeError, ValueError):
                    cache_key = None
                    cache_status = "error"
        started = perf_counter()
        candidates = self._candidates(request)
        semantic_scores: Mapping[str, float] = {}
        semantic_available = False
        semantic_status = "not_configured"
        if self.semantic is not None and candidates:
            try:
                with profile_operation(
                    "embedding_latency", "semantic.score"
                ):
                    semantic_scores = self.semantic.score(
                        request.query, candidates
                    )
            except Exception as error:
                semantic_status = f"failed:{type(error).__name__}"
            else:
                semantic_available = True
                semantic_status = "used"
        scored = sorted(
            (
                self._score(
                    record, request, semantic_scores, semantic_available
                )
                for record in candidates
            ),
            key=lambda item: (
                item.score,
                item.memory.importance,
                item.memory.created_at,
                item.memory.id,
            ),
            reverse=True,
        )
        conflicts = self._conflicts(scored)
        selected: list[RankedMemory] = []
        rejected: list[RankedMemory] = []
        ranked: list[RankedMemory] = []
        seen_content: set[str] = set()
        selected_tokens = 0
        for item in scored:
            conflict_ids = conflicts.get(item.memory.id, ())
            explanation = item.explanation
            if conflict_ids:
                explanation += f"; unresolved_conflicts={len(conflict_ids)}"
            reason: str | None = None
            fingerprint = self._normalized(item.memory)
            has_relevance = (
                item.breakdown.keyword > 0
                or item.breakdown.task_similarity > 0
                or (item.breakdown.semantic or 0) > 0
            )
            if (request.query.strip() or request.task.strip()) and not has_relevance:
                reason = "no_relevance"
            elif fingerprint in seen_content:
                reason = "duplicate"
            elif item.score < self.config.minimum_score:
                reason = "below_minimum_score"
            elif len(selected) >= request.target_memories:
                reason = "target_limit"
            elif selected_tokens + item.memory.token_cost > request.token_budget:
                reason = "token_budget"
            else:
                seen_content.add(fingerprint)
                selected_tokens += item.memory.token_cost
            resolved = RankedMemory(
                memory=item.memory,
                score=item.score,
                breakdown=item.breakdown,
                explanation=explanation,
                conflict_ids=conflict_ids,
                selected=reason is None,
                rejection_reason=reason,
            )
            ranked.append(resolved)
            (selected if reason is None else rejected).append(resolved)
        result = RetrievalResult(
            ranked=tuple(ranked),
            selected=tuple(selected),
            rejected=tuple(rejected),
            candidate_count=len(candidates),
            selected_tokens=selected_tokens,
            semantic_available=semantic_available,
            semantic_status=semantic_status,
            cache_status="miss" if cache_key is not None else cache_status,
        )
        if (
            self.cache is not None
            and cache_key is not None
            and source_generation is not None
            and request.cache_max_age_seconds is not None
        ):
            try:
                if self.cache.generation() == source_generation:
                    cache_now = self.cache.clock().astimezone(timezone.utc)
                    if any(
                        item.memory.retention_until is not None
                        and parse_timestamp(
                            item.memory.retention_until
                        ) <= cache_now
                        for item in result.ranked
                    ):
                        self.cache.event(
                            "bypass", "retention_deadline_elapsed"
                        )
                        return replace(result, cache_status="bypass")
                    expires_at = (
                        cache_now
                        + timedelta(
                            seconds=request.cache_max_age_seconds
                        )
                    )
                    retention_deadlines = [
                        parse_timestamp(item.memory.retention_until)
                        for item in result.ranked
                        if item.memory.retention_until is not None
                    ]
                    if retention_deadlines:
                        expires_at = min(
                            expires_at, *retention_deadlines
                        )
                    if request.valid_at is None:
                        transition = self.cache.next_retrieval_transition(
                            scope=request.scope,
                            include_ancestors=request.include_global,
                            after=cache_now,
                        )
                        if transition is not None:
                            expires_at = min(expires_at, transition)
                    if expires_at <= cache_now:
                        self.cache.event(
                            "bypass", "temporal_validity_elapsed"
                        )
                        return replace(result, cache_status="bypass")
                    self.cache.put(
                        key_hash=cache_key,
                        scope=request.scope,
                        source_generation=source_generation,
                        payload=self._cache_payload(result),
                        compute_duration_ms=max(
                            0, int((perf_counter() - started) * 1_000)
                        ),
                        expires_at=expires_at,
                    )
                else:
                    self.cache.event(
                        "bypass", "source_changed_during_compute"
                    )
            except (sqlite3.Error, TypeError, ValueError):
                result = replace(result, cache_status="error")
        return result
