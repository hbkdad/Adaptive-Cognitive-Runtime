from __future__ import annotations

import json
import random
import re
import string
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Protocol, Sequence

from .providers import ChatMessage, ChatRequest, ModelProvider

SUPPORTED_DATASET_VERSION = 1
VALID_CATEGORIES = frozenset(
    {
        "fact_retrieval",
        "memory_recall",
        "temporal_recall",
        "coding",
        "debugging",
        "research",
        "planning",
        "tool_use",
        "multi_step",
        "skill_reuse",
        "classification",
        "summarization",
        "memory_extraction",
        "simple_planning",
        "code_analysis",
    }
)


@dataclass(frozen=True)
class BenchmarkCase:
    id: str
    category: str
    prompt: str
    expected: str
    max_output_tokens: int = 64

    def __post_init__(self) -> None:
        if not self.id.strip():
            raise ValueError("Benchmark case id cannot be empty")
        if self.category not in VALID_CATEGORIES:
            raise ValueError(f"Unsupported benchmark category: {self.category}")
        if not self.prompt.strip() or not self.expected.strip():
            raise ValueError("Benchmark prompt and expected output cannot be empty")
        if self.max_output_tokens < 1:
            raise ValueError("max_output_tokens must be positive")


@dataclass(frozen=True)
class BenchmarkDataset:
    name: str
    version: int
    cases: tuple[BenchmarkCase, ...]
    source_path: str

    @classmethod
    def load(cls, path: str | Path) -> "BenchmarkDataset":
        source = Path(path)
        lines = [
            line
            for line in source.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        if not lines:
            raise ValueError("Benchmark dataset is empty")
        header = json.loads(lines[0])
        if header.get("record_type") != "dataset":
            raise ValueError("First JSONL record must be a dataset header")
        version = int(header.get("version", 0))
        if version != SUPPORTED_DATASET_VERSION:
            raise ValueError(
                f"Unsupported dataset version {version}; "
                f"expected {SUPPORTED_DATASET_VERSION}"
            )
        cases = tuple(
            BenchmarkCase(
                id=record["id"],
                category=record["category"],
                prompt=record["prompt"],
                expected=record["expected"],
                max_output_tokens=int(record.get("max_output_tokens", 64)),
            )
            for record in (json.loads(line) for line in lines[1:])
            if record.get("record_type") == "case"
        )
        if not cases:
            raise ValueError("Benchmark dataset contains no cases")
        if len({case.id for case in cases}) != len(cases):
            raise ValueError("Benchmark case ids must be unique")
        return cls(
            name=str(header["name"]),
            version=version,
            cases=cases,
            source_path=str(source),
        )


class QualityScorer(Protocol):
    @property
    def name(self) -> str: ...

    def score(self, expected: str, actual: str) -> float: ...


class NormalizedExactScorer:
    name = "normalized_exact"

    @staticmethod
    def _normalize(value: str) -> str:
        collapsed = " ".join(value.strip().lower().split())
        return collapsed.strip(string.punctuation + " ")

    def score(self, expected: str, actual: str) -> float:
        return float(self._normalize(expected) == self._normalize(actual))


@dataclass(frozen=True)
class BenchmarkCaseResult:
    case_id: str
    category: str
    quality: float
    input_tokens: int
    output_tokens: int
    cached_tokens: int
    estimated_cost: float
    latency_ms: int
    tool_calls: int
    retrieval_precision: float | None
    retrieval_recall: float | None
    skill_effectiveness: float | None
    failed: bool
    error_kind: str | None


@dataclass(frozen=True)
class BenchmarkReport:
    dataset: str
    dataset_version: int
    provider: str
    model: str
    scorer: str
    seed: int
    cases: tuple[BenchmarkCaseResult, ...]

    @property
    def summary(self) -> dict[str, float | int]:
        count = len(self.cases)
        successes = sum(not case.failed for case in self.cases)
        return {
            "cases": count,
            "average_quality": (
                sum(case.quality for case in self.cases) / count if count else 0.0
            ),
            "input_tokens": sum(case.input_tokens for case in self.cases),
            "output_tokens": sum(case.output_tokens for case in self.cases),
            "estimated_cost": sum(case.estimated_cost for case in self.cases),
            "average_latency_ms": (
                sum(case.latency_ms for case in self.cases) / count if count else 0.0
            ),
            "failure_rate": 1 - (successes / count) if count else 0.0,
            "tool_calls": sum(case.tool_calls for case in self.cases),
        }

    def to_dict(self) -> dict[str, object]:
        return {
            "dataset": self.dataset,
            "dataset_version": self.dataset_version,
            "provider": self.provider,
            "model": self.model,
            "scorer": self.scorer,
            "seed": self.seed,
            "summary": self.summary,
            "cases": [asdict(case) for case in self.cases],
        }


class BenchmarkRunner:
    def __init__(
        self,
        provider: ModelProvider,
        *,
        model: str,
        scorer: QualityScorer | None = None,
    ) -> None:
        capabilities = provider.capabilities(model)
        if not capabilities.chat:
            raise ValueError(f"{provider.name}/{model} is not chat-capable")
        self.provider = provider
        self.model = model
        self.scorer = scorer or NormalizedExactScorer()

    def run(
        self, dataset: BenchmarkDataset, *, seed: int = 0
    ) -> BenchmarkReport:
        cases = list(dataset.cases)
        random.Random(seed).shuffle(cases)
        results: list[BenchmarkCaseResult] = []
        for case in cases:
            request = ChatRequest(
                model=self.model,
                messages=(
                    ChatMessage(
                        role="system",
                        content=(
                            "Answer the benchmark case directly. Follow its output "
                            "format exactly and do not add explanation."
                        ),
                    ),
                    ChatMessage(role="user", content=case.prompt),
                ),
                max_output_tokens=case.max_output_tokens,
                temperature=0.0,
                task_id=f"benchmark:{dataset.name}:{case.id}",
            )
            try:
                response = self.provider.chat(request)
            except Exception as error:
                results.append(
                    BenchmarkCaseResult(
                        case_id=case.id,
                        category=case.category,
                        quality=0.0,
                        input_tokens=0,
                        output_tokens=0,
                        cached_tokens=0,
                        estimated_cost=0.0,
                        latency_ms=0,
                        tool_calls=0,
                        retrieval_precision=None,
                        retrieval_recall=None,
                        skill_effectiveness=None,
                        failed=True,
                        error_kind=type(error).__name__,
                    )
                )
                continue
            tool_calls = 0
            if response.tool_calls_json:
                try:
                    tool_calls = len(json.loads(response.tool_calls_json))
                except (TypeError, json.JSONDecodeError):
                    tool_calls = 1
            results.append(
                BenchmarkCaseResult(
                    case_id=case.id,
                    category=case.category,
                    quality=self.scorer.score(case.expected, response.content),
                    input_tokens=response.usage.input_tokens,
                    output_tokens=response.usage.output_tokens,
                    cached_tokens=response.usage.cached_tokens,
                    estimated_cost=0.0,
                    latency_ms=response.latency_ms,
                    tool_calls=tool_calls,
                    retrieval_precision=None,
                    retrieval_recall=None,
                    skill_effectiveness=None,
                    failed=False,
                    error_kind=None,
                )
            )
        return BenchmarkReport(
            dataset=dataset.name,
            dataset_version=dataset.version,
            provider=self.provider.name,
            model=self.model,
            scorer=self.scorer.name,
            seed=seed,
            cases=tuple(results),
        )
