from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum

from .memory import MemoryRecord, MemoryStatus, MemoryType, parse_timestamp


class DecayMode(str, Enum):
    TIMED = "timed"
    SUPERSESSION_ONLY = "supersession_only"


@dataclass(frozen=True)
class KnowledgeDecayProfile:
    memory_type: MemoryType
    mode: DecayMode
    baseline_ratio: float | None
    rationale: str

    def half_life_days(self, baseline_days: float = 90.0) -> float | None:
        if baseline_days <= 0:
            raise ValueError("baseline_days must be positive")
        if self.baseline_ratio is None:
            return None
        return baseline_days * self.baseline_ratio


@dataclass(frozen=True)
class KnowledgeDecayAssessment:
    memory_id: str
    memory_type: MemoryType
    mode: DecayMode
    half_life_days: float | None
    anchor_at: str
    assessed_at: str
    age_days: float
    recency_score: float
    validity_state: str
    source_freshness_state: str
    review_due: bool
    reason: str


DEFAULT_PROFILES = {
    MemoryType.TEMPORARY: KnowledgeDecayProfile(
        MemoryType.TEMPORARY,
        DecayMode.TIMED,
        1 / 90,
        "temporary state changes rapidly",
    ),
    MemoryType.ENVIRONMENT: KnowledgeDecayProfile(
        MemoryType.ENVIRONMENT,
        DecayMode.TIMED,
        7 / 90,
        "environment and external-state facts are volatile",
    ),
    MemoryType.EPISODIC: KnowledgeDecayProfile(
        MemoryType.EPISODIC,
        DecayMode.TIMED,
        0.5,
        "episodes retain historical value but lose current relevance",
    ),
    MemoryType.SEMANTIC: KnowledgeDecayProfile(
        MemoryType.SEMANTIC,
        DecayMode.TIMED,
        1.0,
        "facts use the configured semantic baseline",
    ),
    MemoryType.FAILURE: KnowledgeDecayProfile(
        MemoryType.FAILURE,
        DecayMode.TIMED,
        2.0,
        "failure lessons decay slowly and remain scoped evidence",
    ),
    MemoryType.PROCEDURAL: KnowledgeDecayProfile(
        MemoryType.PROCEDURAL,
        DecayMode.TIMED,
        4.0,
        "verified procedures are comparatively stable",
    ),
    MemoryType.PREFERENCE: KnowledgeDecayProfile(
        MemoryType.PREFERENCE,
        DecayMode.TIMED,
        8.0,
        "durable stated preferences decay slowly",
    ),
    MemoryType.DECISION: KnowledgeDecayProfile(
        MemoryType.DECISION,
        DecayMode.SUPERSESSION_ONLY,
        None,
        "decisions persist until explicit invalidity or supersession",
    ),
}


class KnowledgeDecayPolicy:
    """Deterministic type-aware recency without claiming source freshness."""

    def __init__(
        self,
        profiles: dict[MemoryType, KnowledgeDecayProfile] | None = None,
    ) -> None:
        self.profiles = dict(profiles or DEFAULT_PROFILES)
        if set(self.profiles) != set(MemoryType):
            raise ValueError("Knowledge decay profiles must cover every memory type")
        for memory_type, profile in self.profiles.items():
            if profile.memory_type is not memory_type:
                raise ValueError("Knowledge decay profile type mismatch")
            if (
                profile.mode is DecayMode.TIMED
                and (
                    profile.baseline_ratio is None
                    or profile.baseline_ratio <= 0
                )
            ):
                raise ValueError("Timed profiles require a positive baseline ratio")
            if (
                profile.mode is DecayMode.SUPERSESSION_ONLY
                and profile.baseline_ratio is not None
            ):
                raise ValueError(
                    "Supersession-only profiles cannot define a half-life"
                )

    def profile_for(self, memory_type: MemoryType) -> KnowledgeDecayProfile:
        return self.profiles[memory_type]

    def assess(
        self,
        record: MemoryRecord,
        *,
        assessed_at: str | None = None,
        baseline_days: float = 90.0,
    ) -> KnowledgeDecayAssessment:
        now = (
            parse_timestamp(assessed_at)
            if assessed_at is not None
            else datetime.now(timezone.utc)
        )
        anchor = parse_timestamp(record.valid_from)
        age_days = max(0.0, (now - anchor).total_seconds() / 86_400)
        profile = self.profile_for(record.type)
        half_life = profile.half_life_days(baseline_days)

        validity_state = "current"
        reason = "type_profile"
        recency = 1.0
        if now < anchor:
            validity_state = "not_yet_valid"
            reason = "explicit_validity"
            recency = 0.0
        elif (
            record.valid_until is not None
            and now >= parse_timestamp(record.valid_until)
        ):
            validity_state = "expired"
            reason = "explicit_validity"
            recency = 0.0
        elif (
            (
                record.status is MemoryStatus.SUPERSEDED
                or record.superseded_by is not None
            )
            and record.valid_until is None
        ):
            validity_state = "superseded"
            reason = "explicit_supersession"
            recency = 0.0
        elif profile.mode is DecayMode.TIMED:
            assert half_life is not None
            recency = math.pow(0.5, age_days / half_life)
        review_due = bool(
            validity_state == "current"
            and half_life is not None
            and age_days >= half_life
        )
        return KnowledgeDecayAssessment(
            memory_id=record.id,
            memory_type=record.type,
            mode=profile.mode,
            half_life_days=half_life,
            anchor_at=anchor.isoformat(),
            assessed_at=now.isoformat(),
            age_days=round(age_days, 6),
            recency_score=round(max(0.0, min(1.0, recency)), 6),
            validity_state=validity_state,
            source_freshness_state="unavailable",
            review_due=review_due,
            reason=reason,
        )
