from __future__ import annotations

import json
import math
import statistics
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, Sequence


@dataclass(frozen=True)
class QualityCase:
    id: str
    prompt: str
    criteria: tuple[str, ...]
    minimum_score: float

    def __post_init__(self) -> None:
        if not self.id or len(self.id) > 128:
            raise ValueError("Quality case ID is invalid")
        if not self.prompt.strip() or len(self.prompt) > 20_000:
            raise ValueError("Quality prompt is invalid")
        if (
            not self.criteria
            or len(self.criteria) > 20
            or any(
                not item.strip() or len(item) > 512
                for item in self.criteria
            )
        ):
            raise ValueError("Quality criteria are invalid")
        if (
            isinstance(self.minimum_score, bool)
            or not isinstance(self.minimum_score, (int, float))
            or not math.isfinite(self.minimum_score)
            or not 0 <= self.minimum_score <= 1
        ):
            raise ValueError("Quality minimum score must be 0..1")

    @classmethod
    def from_dict(cls, payload: dict[str, object]) -> "QualityCase":
        if set(payload) != {
            "id",
            "prompt",
            "criteria",
            "minimum_score",
        }:
            raise ValueError("Quality case fields are invalid")
        if not isinstance(payload["id"], str) or not isinstance(
            payload["prompt"], str
        ):
            raise ValueError("Quality case text fields are invalid")
        criteria = payload["criteria"]
        if not isinstance(criteria, list) or not all(
            isinstance(item, str) for item in criteria
        ):
            raise ValueError("Quality criteria must be a string list")
        return cls(
            id=payload["id"],
            prompt=payload["prompt"],
            criteria=tuple(criteria),
            minimum_score=payload["minimum_score"],  # type: ignore[arg-type]
        )


@dataclass(frozen=True)
class QualityDataset:
    name: str
    cases: tuple[QualityCase, ...]

    @classmethod
    def load(cls, path: str | Path) -> "QualityDataset":
        source = Path(path)
        if source.stat().st_size > 1_000_000:
            raise ValueError("Quality dataset exceeds 1 MB")
        lines = [
            line
            for line in source.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        if len(lines) < 2:
            raise ValueError("Quality dataset requires metadata and cases")
        metadata = json.loads(lines[0])
        if (
            not isinstance(metadata, dict)
            or set(metadata) != {"schema_version", "name"}
            or metadata["schema_version"] != 1
            or not isinstance(metadata["name"], str)
            or not metadata["name"].strip()
        ):
            raise ValueError("Quality dataset metadata is invalid")
        cases = tuple(
            QualityCase.from_dict(json.loads(line)) for line in lines[1:]
        )
        identifiers = [case.id for case in cases]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("Quality case IDs must be unique")
        return cls(name=metadata["name"], cases=cases)


class QualityProvider(Protocol):
    name: str

    def generate(self, prompt: str, *, seed: int) -> str: ...


class QualityEvaluator(Protocol):
    name: str

    def score(
        self, output: str, *, criteria: Sequence[str]
    ) -> float: ...


class MockQualityProvider:
    """Offline scripted provider for deterministic harness tests."""

    name = "mock-quality"

    def __init__(self, responses: Sequence[str]) -> None:
        if not responses:
            raise ValueError("Mock quality responses cannot be empty")
        self.responses = tuple(responses)

    def generate(self, prompt: str, *, seed: int) -> str:
        return self.responses[seed % len(self.responses)]


class KeywordQualityEvaluator:
    """Deterministic evaluator used only for harness and fixture validation."""

    name = "keyword-quality"

    def score(
        self, output: str, *, criteria: Sequence[str]
    ) -> float:
        normalized = output.casefold()
        matches = sum(
            1 for criterion in criteria if criterion.casefold() in normalized
        )
        return matches / len(criteria)


class QualityBenchmarkRunner:
    """Repeated quality measurement; deliberately outside unittest discovery."""

    def __init__(
        self,
        provider: QualityProvider,
        evaluator: QualityEvaluator,
    ) -> None:
        self.provider = provider
        self.evaluator = evaluator

    def run(
        self,
        dataset: QualityDataset,
        *,
        repetitions: int = 5,
        seed: int = 0,
    ) -> dict[str, object]:
        if (
            isinstance(repetitions, bool)
            or not isinstance(repetitions, int)
            or not 3 <= repetitions <= 100
        ):
            raise ValueError("Quality repetitions must be 3..100")
        if isinstance(seed, bool) or not isinstance(seed, int):
            raise ValueError("Quality seed must be an integer")
        case_reports: list[dict[str, object]] = []
        all_scores: list[float] = []
        for case_index, case in enumerate(dataset.cases):
            scores: list[float] = []
            for repetition in range(repetitions):
                output = self.provider.generate(
                    case.prompt,
                    seed=seed + case_index * repetitions + repetition,
                )
                score = self.evaluator.score(
                    output, criteria=case.criteria
                )
                if (
                    isinstance(score, bool)
                    or not isinstance(score, (int, float))
                    or not math.isfinite(score)
                    or not 0 <= score <= 1
                ):
                    raise ValueError("Quality evaluator returned invalid score")
                scores.append(float(score))
            all_scores.extend(scores)
            case_reports.append(
                {
                    "id": case.id,
                    "samples": len(scores),
                    "mean_score": statistics.fmean(scores),
                    "standard_deviation": (
                        statistics.stdev(scores)
                        if len(scores) > 1 else 0.0
                    ),
                    "minimum_score": min(scores),
                    "maximum_score": max(scores),
                    "threshold": case.minimum_score,
                    "passing_samples": sum(
                        score >= case.minimum_score for score in scores
                    ),
                }
            )
        return {
            "dataset": dataset.name,
            "provider": self.provider.name,
            "evaluator": self.evaluator.name,
            "repetitions": repetitions,
            "seed": seed,
            "case_count": len(dataset.cases),
            "sample_count": len(all_scores),
            "mean_score": statistics.fmean(all_scores),
            "cases": case_reports,
            "deterministic_assertion": False,
            "probabilistic_quality_benchmark": True,
        }
