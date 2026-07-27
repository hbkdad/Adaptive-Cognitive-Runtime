from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from enum import Enum
from typing import Iterable

from .models import ContextBlock, ContextBundle, ContextSourceType

SourceRef = tuple[ContextSourceType, str]


class AttributionOutcome(str, Enum):
    CONTRIBUTED = "contributed"
    IGNORED = "ignored"
    MISLED = "misled"
    UNCERTAIN = "uncertain"


@dataclass(frozen=True)
class EvaluatorJudgment:
    source_type: ContextSourceType
    source_id: str
    score: float

    def __post_init__(self) -> None:
        if not -1 <= self.score <= 1:
            raise ValueError("Evaluator attribution score must be -1..1")


@dataclass(frozen=True)
class AttributionSignals:
    model_sources: tuple[SourceRef, ...] = ()
    execution_sources: tuple[SourceRef, ...] = ()
    tool_dependencies: tuple[SourceRef, ...] = ()
    ignored_sources: tuple[SourceRef, ...] = ()
    misled_sources: tuple[SourceRef, ...] = ()
    evaluator_judgments: tuple[EvaluatorJudgment, ...] = ()


@dataclass(frozen=True)
class ContextAttribution:
    id: str
    task_id: str
    source_type: ContextSourceType
    source_id: str
    role: str
    outcome: AttributionOutcome
    impact_score: float
    confidence: float
    approximate_roi: float
    model_score: float | None
    execution_score: float | None
    dependency_score: float | None
    evaluator_score: float | None
    evidence_json: str


class ContextAttributor:
    """Conservative evidence fusion; missing evidence remains uncertain."""

    WEIGHTS = {
        "model": 0.20,
        "execution": 0.30,
        "dependency": 0.25,
        "evaluator": 0.25,
    }

    @staticmethod
    def _role(block: ContextBlock) -> str:
        return {
            "memory": "memory_affected_answer",
            "skill": "skill_used",
            "file": "document_contributed",
            "tool": "tool_used",
        }.get(block.source_type, "context_contributed")

    def attribute(
        self,
        bundle: ContextBundle,
        *,
        signals: AttributionSignals,
        success: bool,
        critic_score: float,
    ) -> tuple[ContextAttribution, ...]:
        if not 0 <= critic_score <= 1:
            raise ValueError("critic_score must be 0..1")
        selected = {(block.source_type, block.source_id) for block in bundle.blocks}
        supplied = {
            *signals.model_sources,
            *signals.execution_sources,
            *signals.tool_dependencies,
            *signals.ignored_sources,
            *signals.misled_sources,
            *((item.source_type, item.source_id) for item in signals.evaluator_judgments),
        }
        unknown = supplied - selected
        if unknown:
            raise ValueError(f"Attribution references unselected context: {sorted(unknown)}")

        model = set(signals.model_sources)
        execution = set(signals.execution_sources)
        dependencies = set(signals.tool_dependencies)
        ignored = set(signals.ignored_sources)
        misled = set(signals.misled_sources)
        evaluator = {
            (item.source_type, item.source_id): item.score
            for item in signals.evaluator_judgments
        }
        if len(evaluator) != len(signals.evaluator_judgments):
            raise ValueError("Duplicate evaluator judgment for context source")
        records: list[ContextAttribution] = []
        for block in bundle.blocks:
            ref = (block.source_type, block.source_id)
            channel_scores: dict[str, float | None] = {
                "model": 1.0 if ref in model else None,
                "execution": 1.0 if ref in execution else None,
                "dependency": 1.0 if ref in dependencies else None,
                "evaluator": evaluator.get(ref),
            }
            positive = sum(
                self.WEIGHTS[name] * max(0.0, score)
                for name, score in channel_scores.items()
                if score is not None
            )
            coverage = sum(
                self.WEIGHTS[name]
                for name, score in channel_scores.items()
                if score is not None
            )
            evaluator_score = channel_scores["evaluator"]
            if ref in misled or (
                evaluator_score is not None and evaluator_score < 0
            ):
                outcome = AttributionOutcome.MISLED
                impact = min(-0.5, evaluator_score or -1.0)
                confidence = max(coverage, 0.75 if ref in misled else 0.25)
            elif positive > 0:
                outcome = AttributionOutcome.CONTRIBUTED
                impact = min(1.0, positive / max(coverage, 0.01))
                confidence = coverage
            elif ref in ignored:
                outcome = AttributionOutcome.IGNORED
                impact = 0.0
                confidence = 0.75
            else:
                outcome = AttributionOutcome.UNCERTAIN
                impact = 0.0
                confidence = coverage
            quality = critic_score * (1.0 if success else 0.5)
            evidence = [
                name for name, score in channel_scores.items() if score is not None
            ]
            if ref in ignored:
                evidence.append("explicit_ignored")
            if ref in misled:
                evidence.append("explicit_misled")
            records.append(
                ContextAttribution(
                    id=str(uuid.uuid4()),
                    task_id=bundle.task_id,
                    source_type=block.source_type,
                    source_id=block.source_id,
                    role=self._role(block),
                    outcome=outcome,
                    impact_score=impact,
                    confidence=min(1.0, confidence),
                    approximate_roi=(impact * quality / max(1, block.tokens)),
                    model_score=channel_scores["model"],
                    execution_score=channel_scores["execution"],
                    dependency_score=channel_scores["dependency"],
                    evaluator_score=evaluator_score,
                    evidence_json=json.dumps(sorted(set(evidence))),
                )
            )
        return tuple(records)


def refs(
    source_type: ContextSourceType, source_ids: Iterable[str]
) -> tuple[SourceRef, ...]:
    return tuple((source_type, source_id) for source_id in source_ids)
