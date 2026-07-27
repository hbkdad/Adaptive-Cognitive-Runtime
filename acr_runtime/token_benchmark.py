from __future__ import annotations

import json
import tempfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from .compiler import ContextRequest
from .models import ContextCandidate
from .scoring import estimate_tokens, lexical_relevance
from .secret_management import assert_secret_free
from .service import AdaptiveRuntime

ARMS = (
    "full_context",
    "semantic_retrieval",
    "hybrid_retrieval",
    "acr_context_compiler",
)
REQUIRED_CATEGORIES = {
    "distractor_history",
    "exact_command",
    "exact_error",
    "code_expression",
    "dependency_expansion",
}


def _strict(payload: object, fields: set[str], name: str) -> dict:
    if not isinstance(payload, dict) or set(payload) != fields:
        raise ValueError(f"{name} must contain exactly {sorted(fields)}")
    return dict(payload)


@dataclass(frozen=True)
class TokenBenchmarkEntry:
    label: str
    content: str
    semantic_score: float
    confidence: float
    expected_utility: float
    required: bool
    dependencies: tuple[str, ...]
    content_kind: str
    exact_required: bool

    def __post_init__(self) -> None:
        if (
            not isinstance(self.label, str) or not self.label.strip()
            or len(self.label) > 128
            or not isinstance(self.content, str) or not self.content.strip()
            or len(self.content) > 32_000
            or isinstance(self.semantic_score, bool)
            or not isinstance(self.semantic_score, (int, float))
            or not 0 <= self.semantic_score <= 1
            or not 0 <= self.confidence <= 1
            or not 0 <= self.expected_utility <= 1
            or type(self.required) is not bool
            or type(self.exact_required) is not bool
            or not isinstance(self.dependencies, tuple)
            or any(
                not isinstance(item, str) or not item.strip()
                for item in self.dependencies
            )
        ):
            raise ValueError("Token benchmark entry is invalid")
        assert_secret_free(self.content, "token benchmark context")

    @classmethod
    def from_dict(cls, payload: object) -> "TokenBenchmarkEntry":
        data = _strict(payload, {
            "label", "content", "semantic_score", "confidence",
            "expected_utility", "required", "dependencies", "content_kind",
            "exact_required",
        }, "Token benchmark entry")
        if not isinstance(data["dependencies"], list):
            raise ValueError("Token benchmark dependencies must be a list")
        data["dependencies"] = tuple(data["dependencies"])
        try:
            return cls(**data)
        except (TypeError, ValueError) as error:
            raise ValueError("Invalid token benchmark entry") from error

    def candidate(self) -> ContextCandidate:
        return ContextCandidate(
            source_type="file",
            source_id=self.label,
            label=self.label,
            content=self.content,
            confidence=self.confidence,
            expected_utility=self.expected_utility,
            required=self.required,
            dependencies=self.dependencies,
            content_kind=self.content_kind,
            exact_required=self.exact_required,
            provenance=(f"benchmark:{self.label}",),
        )


@dataclass(frozen=True)
class TokenBenchmarkCase:
    id: str
    category: str
    task: str
    token_budget: int
    target_items: int
    required_labels: tuple[str, ...]
    harmful_labels: tuple[str, ...]
    entries: tuple[TokenBenchmarkEntry, ...]
    noise_count: int

    def __post_init__(self) -> None:
        if (
            not isinstance(self.id, str) or not self.id.strip()
            or self.category not in REQUIRED_CATEGORIES
            or not isinstance(self.task, str) or not self.task.strip()
            or type(self.token_budget) is not int
            or not 64 <= self.token_budget <= 1_000_000
            or type(self.target_items) is not int
            or not 1 <= self.target_items <= 100
            or type(self.noise_count) is not int
            or not 0 <= self.noise_count <= 10_000
            or not self.required_labels
        ):
            raise ValueError("Token benchmark case is invalid")
        labels = {item.label for item in self.entries}
        if not set(self.required_labels) <= labels:
            raise ValueError("Required labels must reference entries")
        if not set(self.harmful_labels) <= labels:
            raise ValueError("Harmful labels must reference entries")
        if len(labels) != len(self.entries):
            raise ValueError("Token benchmark labels must be unique")
        if any(
            not set(item.dependencies) <= labels for item in self.entries
        ):
            raise ValueError("Token benchmark dependency is unknown")

    @classmethod
    def from_dict(cls, payload: object) -> "TokenBenchmarkCase":
        data = _strict(payload, {
            "record_type", "id", "category", "task", "token_budget",
            "target_items", "required_labels", "harmful_labels", "entries",
            "noise_count",
        }, "Token benchmark case")
        if data.pop("record_type") != "case":
            raise ValueError("Token benchmark records must be cases")
        if not all(isinstance(data[key], list) for key in (
            "required_labels", "harmful_labels", "entries"
        )):
            raise ValueError("Token benchmark arrays are invalid")
        data["required_labels"] = tuple(data["required_labels"])
        data["harmful_labels"] = tuple(data["harmful_labels"])
        data["entries"] = tuple(
            TokenBenchmarkEntry.from_dict(item) for item in data["entries"]
        )
        return cls(**data)

    def expanded_entries(self) -> tuple[TokenBenchmarkEntry, ...]:
        noise = tuple(
            TokenBenchmarkEntry(
                label=f"noise-{index:05d}",
                content=(
                    f"Unrelated inventory archive item {index} covers shelving, "
                    "paint, and office supplies."
                ),
                semantic_score=0.01,
                confidence=0.8,
                expected_utility=0.1,
                required=False,
                dependencies=(),
                content_kind="text",
                exact_required=False,
            )
            for index in range(self.noise_count)
        )
        return (*noise, *self.entries)


@dataclass(frozen=True)
class TokenBenchmarkDataset:
    name: str
    version: int
    cost_per_million_input_tokens: float
    cases: tuple[TokenBenchmarkCase, ...]

    @classmethod
    def load(cls, path: str | Path) -> "TokenBenchmarkDataset":
        source = Path(path)
        if source.stat().st_size > 5_000_000:
            raise ValueError("Token benchmark exceeds 5 MB")
        lines = [
            line for line in source.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        if not lines:
            raise ValueError("Token benchmark is empty")
        header = _strict(json.loads(lines[0]), {
            "record_type", "name", "version", "description",
            "cost_per_million_input_tokens",
        }, "Token benchmark header")
        if header["record_type"] != "token_dataset" or header["version"] != 1:
            raise ValueError("Unsupported token benchmark header")
        rate = header["cost_per_million_input_tokens"]
        if isinstance(rate, bool) or not isinstance(rate, (int, float)) or rate < 0:
            raise ValueError("Token benchmark pricing is invalid")
        cases = tuple(
            TokenBenchmarkCase.from_dict(json.loads(line))
            for line in lines[1:]
        )
        if (
            not cases
            or len({case.id for case in cases}) != len(cases)
            or {case.category for case in cases} != REQUIRED_CATEGORIES
        ):
            raise ValueError("Token benchmark requires exact category coverage")
        return cls(
            name=header["name"], version=1,
            cost_per_million_input_tokens=float(rate), cases=cases,
        )


@dataclass(frozen=True)
class TokenArmResult:
    case_id: str
    category: str
    arm: str
    quality: float
    input_tokens: int
    latency_ms: float
    cost: float
    selected_labels: tuple[str, ...]
    harmful_selected: int
    exact_preserved: bool


@dataclass(frozen=True)
class TokenBenchmarkReport:
    dataset: str
    results: tuple[TokenArmResult, ...]

    @property
    def summary(self) -> dict[str, dict[str, float | int | bool]]:
        output = {}
        full = [x for x in self.results if x.arm == "full_context"]
        full_by_case = {item.case_id: item for item in full}
        full_quality = sum(x.quality for x in full) / len(full)
        full_tokens = sum(x.input_tokens for x in full)
        for arm in ARMS:
            rows = [x for x in self.results if x.arm == arm]
            quality = sum(x.quality for x in rows) / len(rows)
            tokens = sum(x.input_tokens for x in rows)
            quality_non_regression = all(
                item.quality >= full_by_case[item.case_id].quality
                for item in rows
            )
            output[arm] = {
                "cases": len(rows),
                "average_quality": quality,
                "input_tokens": tokens,
                "token_reduction_vs_full": (
                    0.0 if not full_tokens else 1 - tokens / full_tokens
                ),
                "latency_ms": sum(x.latency_ms for x in rows),
                "cost": sum(x.cost for x in rows),
                "quality_non_regression": quality_non_regression,
                "primary_goal_met": (
                    arm != "full_context"
                    and quality >= full_quality
                    and quality_non_regression
                    and tokens < full_tokens
                ),
            }
        return output

    def to_dict(self) -> dict[str, object]:
        return {
            "dataset": self.dataset,
            "summary": self.summary,
            "results": [asdict(item) for item in self.results],
            "interpretation": (
                "offline_context_selection_quality_and_estimated_input_cost"
            ),
        }


class TokenBenchmarkRunner:
    @staticmethod
    def _expand_dependencies(
        selected: list[TokenBenchmarkEntry],
        entries: tuple[TokenBenchmarkEntry, ...],
    ) -> list[TokenBenchmarkEntry]:
        by_label = {item.label: item for item in entries}
        output = list(selected)
        seen = {item.label for item in output}
        index = 0
        while index < len(output):
            for dependency in output[index].dependencies:
                if dependency not in seen:
                    output.append(by_label[dependency])
                    seen.add(dependency)
            index += 1
        return output

    @staticmethod
    def _ranked(
        case: TokenBenchmarkCase,
        entries: tuple[TokenBenchmarkEntry, ...],
        arm: str,
    ) -> tuple[TokenBenchmarkEntry, ...]:
        scored = []
        for index, entry in enumerate(entries):
            lexical = lexical_relevance(
                case.task, f"{entry.label} {entry.content}"
            )
            score = (
                entry.semantic_score if arm == "semantic_retrieval"
                else (entry.semantic_score + lexical) / 2
            )
            scored.append((score, -index, entry))
        scored.sort(reverse=True, key=lambda item: (item[0], item[1]))
        selected: list[TokenBenchmarkEntry] = []
        tokens = 0
        for score, _, entry in scored:
            if score <= 0 or len(selected) >= case.target_items:
                continue
            cost = estimate_tokens(entry.content)
            if tokens + cost <= case.token_budget:
                selected.append(entry)
                tokens += cost
        expanded = TokenBenchmarkRunner._expand_dependencies(selected, entries)
        return tuple(expanded)

    @staticmethod
    def _score(
        dataset: TokenBenchmarkDataset,
        case: TokenBenchmarkCase,
        arm: str,
        selected: tuple[TokenBenchmarkEntry, ...],
        latency_ms: float,
        *,
        measured_tokens: int | None = None,
        exact_preserved: bool = True,
    ) -> TokenArmResult:
        labels = {item.label for item in selected}
        required = set(case.required_labels)
        harmful = set(case.harmful_labels)
        quality = float(
            required <= labels and not labels & harmful and exact_preserved
        )
        tokens = (
            measured_tokens if measured_tokens is not None
            else sum(estimate_tokens(item.content) for item in selected)
        )
        return TokenArmResult(
            case_id=case.id, category=case.category, arm=arm,
            quality=quality, input_tokens=tokens, latency_ms=latency_ms,
            cost=tokens * dataset.cost_per_million_input_tokens / 1_000_000,
            selected_labels=tuple(item.label for item in selected[:100]),
            harmful_selected=len(labels & harmful),
            exact_preserved=exact_preserved,
        )

    def run(self, dataset: TokenBenchmarkDataset) -> TokenBenchmarkReport:
        results: list[TokenArmResult] = []
        with tempfile.TemporaryDirectory() as directory:
            for case in dataset.cases:
                entries = case.expanded_entries()
                start = time.perf_counter_ns()
                results.append(self._score(
                    dataset, case, "full_context", entries,
                    (time.perf_counter_ns() - start) / 1_000_000,
                ))
                for arm in ("semantic_retrieval", "hybrid_retrieval"):
                    start = time.perf_counter_ns()
                    selected = self._ranked(case, entries, arm)
                    latency = (time.perf_counter_ns() - start) / 1_000_000
                    results.append(self._score(
                        dataset, case, arm, selected, latency
                    ))
                database = Path(directory) / f"{case.id}.db"
                runtime = AdaptiveRuntime(database)
                try:
                    start = time.perf_counter_ns()
                    bundle = runtime.compiler.compile_request(ContextRequest(
                        task=case.task,
                        scope=f"benchmark:{case.id}",
                        token_budget=case.token_budget,
                        relevant_files=tuple(
                            item.candidate() for item in entries
                        ),
                        task_class="token-optimization",
                    ))
                    latency = (time.perf_counter_ns() - start) / 1_000_000
                    by_label = {item.label: item for item in entries}
                    selected = tuple(
                        by_label[block.source_id] for block in bundle.blocks
                        if block.source_id in by_label
                    )
                    exact_labels = {
                        item.label for item in entries if item.exact_required
                    }
                    blocks = {block.source_id: block for block in bundle.blocks}
                    exact_preserved = all(
                        label in blocks and blocks[label].exact_preserved
                        for label in exact_labels
                    )
                    results.append(self._score(
                        dataset, case, "acr_context_compiler", selected,
                        latency, measured_tokens=bundle.selected_tokens,
                        exact_preserved=exact_preserved,
                    ))
                finally:
                    runtime.close()
        return TokenBenchmarkReport(dataset.name, tuple(results))
