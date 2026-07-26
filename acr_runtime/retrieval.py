from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Mapping, Protocol, Sequence

from .memory import MemoryQuery, MemoryReader, MemoryRecord, MemoryType
from .scoring import lexical_relevance, recency_score

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

    def __post_init__(self) -> None:
        if not self.scope.strip():
            raise ValueError("Retrieval scope cannot be empty")
        if self.token_budget < 0:
            raise ValueError("Retrieval token budget cannot be negative")
        if not 1 <= self.target_memories <= 100:
            raise ValueError("target_memories must be between 1 and 100")
        if not 0 <= self.minimum_confidence <= 1:
            raise ValueError("minimum_confidence must be between 0 and 1")


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


class HybridMemoryRetriever:
    def __init__(
        self,
        reader: MemoryReader,
        *,
        semantic: SemanticSimilarityProvider | None = None,
        config: RetrievalConfig | None = None,
    ) -> None:
        self.reader = reader
        self.semantic = semantic
        self.config = config or RetrievalConfig()

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
            "types": request.types,
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
            recency=recency_score(
                record.updated_at, self.config.recency_half_life_days
            ),
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
        candidates = self._candidates(request)
        semantic_scores: Mapping[str, float] = {}
        semantic_available = False
        semantic_status = "not_configured"
        if self.semantic is not None and candidates:
            try:
                semantic_scores = self.semantic.score(request.query, candidates)
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
        return RetrievalResult(
            ranked=tuple(ranked),
            selected=tuple(selected),
            rejected=tuple(rejected),
            candidate_count=len(candidates),
            selected_tokens=selected_tokens,
            semantic_available=semantic_available,
            semantic_status=semantic_status,
        )
