from __future__ import annotations

import json
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal

from .db import RuntimeDB
from .memory import MemoryCreate, MemoryStatus, MemoryType, parse_timestamp
from .retrieval import HybridMemoryRetriever, RetrievalRequest
from .scoring import estimate_tokens, lexical_relevance
from .secret_management import assert_secret_free

SUPPORTED_MEMORY_BENCHMARK_VERSION = 1
MEMORY_CASE_CATEGORIES = {
    "durable_fact",
    "irrelevant_fact",
    "temporal_change",
    "contradiction",
    "cross_project_isolation",
    "failure_recall",
    "memory_poisoning",
    "large_history",
}
ARMS = ("no_memory", "raw_conversation", "simple_rag", "acr_memory")
ExpectedKind = Literal["answer", "conflict"]


def _strict(payload: object, fields: set[str], name: str) -> dict:
    if not isinstance(payload, dict) or set(payload) != fields:
        raise ValueError(f"{name} must contain exactly {sorted(fields)}")
    return dict(payload)


@dataclass(frozen=True)
class MemoryBenchmarkEntry:
    label: str
    type: MemoryType
    scope: str
    subject: str
    content: str
    status: MemoryStatus
    confidence: float
    importance: float
    valid_from: str
    valid_until: str | None
    supersedes: str | None

    def __post_init__(self) -> None:
        if (
            not isinstance(self.label, str)
            or not isinstance(self.scope, str)
            or not isinstance(self.subject, str)
            or not isinstance(self.content, str)
            or not isinstance(self.type, MemoryType)
            or not isinstance(self.status, MemoryStatus)
            or not isinstance(self.valid_from, str)
            or self.valid_until is not None
            and not isinstance(self.valid_until, str)
            or self.supersedes is not None
            and not isinstance(self.supersedes, str)
            or isinstance(self.confidence, bool)
            or not isinstance(self.confidence, (int, float))
            or isinstance(self.importance, bool)
            or not isinstance(self.importance, (int, float))
            or not 0 <= self.confidence <= 1
            or not 0 <= self.importance <= 1
            or not self.label.strip() or len(self.label) > 128
            or not self.scope.strip() or len(self.scope) > 128
            or not self.subject.strip() or len(self.subject) > 256
            or not self.content.strip() or len(self.content) > 8_000
        ):
            raise ValueError("Memory benchmark entry fields must be bounded")
        parse_timestamp(self.valid_from)
        if self.valid_until is not None:
            if parse_timestamp(self.valid_until) <= parse_timestamp(self.valid_from):
                raise ValueError("Memory benchmark validity interval is reversed")
        assert_secret_free(self.content, "memory benchmark entry")

    @classmethod
    def from_dict(cls, payload: object) -> "MemoryBenchmarkEntry":
        fields = {
            "label", "type", "scope", "subject", "content", "status",
            "confidence", "importance", "valid_from", "valid_until",
            "supersedes",
        }
        data = _strict(payload, fields, "Memory benchmark entry")
        try:
            return cls(
                **{
                    **data,
                    "type": MemoryType(data["type"]),
                    "status": MemoryStatus(data["status"]),
                }
            )
        except (TypeError, ValueError) as error:
            raise ValueError("Invalid memory benchmark entry") from error


@dataclass(frozen=True)
class MemoryBenchmarkCase:
    id: str
    category: str
    task: str
    query: str
    scope: str
    token_budget: int
    target_memories: int
    valid_at: str | None
    expected_kind: ExpectedKind
    required_labels: tuple[str, ...]
    harmful_labels: tuple[str, ...]
    entries: tuple[MemoryBenchmarkEntry, ...]
    noise_count: int = 0

    def __post_init__(self) -> None:
        if (
            not isinstance(self.id, str)
            or not isinstance(self.category, str)
            or not isinstance(self.task, str)
            or not isinstance(self.query, str)
            or not isinstance(self.scope, str)
            or type(self.token_budget) is not int
            or type(self.target_memories) is not int
            or type(self.noise_count) is not int
            or not isinstance(self.required_labels, tuple)
            or not isinstance(self.harmful_labels, tuple)
            or not isinstance(self.entries, tuple)
            or any(not isinstance(item, str) for item in (
                *self.required_labels, *self.harmful_labels
            ))
            or not self.id.strip() or len(self.id) > 128
            or self.category not in MEMORY_CASE_CATEGORIES
            or not self.task.strip() or not self.query.strip()
            or not self.scope.strip()
            or not 1 <= self.token_budget <= 1_000_000
            or not 1 <= self.target_memories <= 100
            or self.expected_kind not in ("answer", "conflict")
            or not 1 <= len(self.required_labels) <= 8
            or not 0 <= self.noise_count <= 10_000
        ):
            raise ValueError("Invalid memory benchmark case")
        if self.valid_at is not None:
            if not isinstance(self.valid_at, str):
                raise ValueError("valid_at must be an ISO timestamp")
            parse_timestamp(self.valid_at)
        labels = [entry.label for entry in self.entries]
        if len(labels) != len(set(labels)):
            raise ValueError("Memory benchmark entry labels must be unique")
        known = set(labels)
        if not set(self.required_labels) <= known:
            raise ValueError("Required labels must reference entries")
        if not set(self.harmful_labels) <= known:
            raise ValueError("Harmful labels must reference entries")
        if set(self.required_labels) & set(self.harmful_labels):
            raise ValueError("Required and harmful labels cannot overlap")
        if any(
            entry.supersedes is not None and entry.supersedes not in known
            for entry in self.entries
        ):
            raise ValueError("Supersedes labels must reference entries")

    @classmethod
    def from_dict(cls, payload: object) -> "MemoryBenchmarkCase":
        fields = {
            "record_type", "id", "category", "task", "query", "scope",
            "token_budget", "target_memories", "valid_at", "expected_kind",
            "required_labels", "harmful_labels", "entries", "noise_count",
        }
        data = _strict(payload, fields, "Memory benchmark case")
        if data.pop("record_type") != "case":
            raise ValueError("Memory benchmark records must be cases")
        if not all(
            isinstance(data[field], list)
            for field in ("required_labels", "harmful_labels", "entries")
        ):
            raise ValueError("Memory benchmark label and entry fields must be lists")
        return cls(
            **{
                **data,
                "required_labels": tuple(data["required_labels"]),
                "harmful_labels": tuple(data["harmful_labels"]),
                "entries": tuple(
                    MemoryBenchmarkEntry.from_dict(item)
                    for item in data["entries"]
                ),
            }
        )

    def expanded_entries(self) -> tuple[MemoryBenchmarkEntry, ...]:
        noise = tuple(
            MemoryBenchmarkEntry(
                label=f"noise-{index:05d}",
                type=MemoryType.SEMANTIC,
                scope=self.scope,
                subject=f"unrelated ledger {index}",
                content=(
                    f"Unrelated historical ledger item {index} concerns "
                    "inventory shelving and has no bearing on the query."
                ),
                status=MemoryStatus.CONFIRMED,
                confidence=0.8,
                importance=0.2,
                valid_from="2025-01-01T00:00:00Z",
                valid_until=None,
                supersedes=None,
            )
            for index in range(self.noise_count)
        )
        return (*noise, *self.entries)


@dataclass(frozen=True)
class MemoryBenchmarkDataset:
    name: str
    version: int
    cases: tuple[MemoryBenchmarkCase, ...]
    source_path: str

    @classmethod
    def load(cls, path: str | Path) -> "MemoryBenchmarkDataset":
        source = Path(path)
        if source.stat().st_size > 5_000_000:
            raise ValueError("Memory benchmark dataset exceeds 5 MB")
        lines = [
            line for line in source.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        if not lines:
            raise ValueError("Memory benchmark dataset is empty")
        header = _strict(
            json.loads(lines[0]),
            {"record_type", "name", "version", "description"},
            "Memory benchmark header",
        )
        if header["record_type"] != "memory_dataset":
            raise ValueError("First record must be a memory_dataset header")
        if header["version"] != SUPPORTED_MEMORY_BENCHMARK_VERSION:
            raise ValueError("Unsupported memory benchmark dataset version")
        cases = tuple(
            MemoryBenchmarkCase.from_dict(json.loads(line))
            for line in lines[1:]
        )
        if not cases or len({case.id for case in cases}) != len(cases):
            raise ValueError("Memory benchmark requires uniquely identified cases")
        categories = {case.category for case in cases}
        if categories != MEMORY_CASE_CATEGORIES:
            missing = sorted(MEMORY_CASE_CATEGORIES - categories)
            extra = sorted(categories - MEMORY_CASE_CATEGORIES)
            raise ValueError(
                f"Memory benchmark category coverage mismatch; "
                f"missing={missing}, extra={extra}"
            )
        return cls(
            name=header["name"],
            version=header["version"],
            cases=cases,
            source_path=str(source),
        )


@dataclass(frozen=True)
class MemoryArmResult:
    case_id: str
    category: str
    arm: str
    accuracy: float
    input_tokens: int
    selected_count: int
    retrieval_precision: float
    retrieval_recall: float
    harmful_selected: int
    conflict_detected: bool
    selected_labels: tuple[str, ...]
    selected_labels_truncated: bool


@dataclass(frozen=True)
class MemoryBenchmarkReport:
    dataset: str
    dataset_version: int
    cases: int
    results: tuple[MemoryArmResult, ...]

    @property
    def summary(self) -> dict[str, dict[str, float | int]]:
        summary: dict[str, dict[str, float | int]] = {}
        for arm in ARMS:
            rows = [row for row in self.results if row.arm == arm]
            summary[arm] = {
                "cases": len(rows),
                "average_accuracy": (
                    sum(row.accuracy for row in rows) / len(rows) if rows else 0
                ),
                "input_tokens": sum(row.input_tokens for row in rows),
                "average_input_tokens": (
                    sum(row.input_tokens for row in rows) / len(rows)
                    if rows else 0
                ),
                "average_retrieval_precision": (
                    sum(row.retrieval_precision for row in rows) / len(rows)
                    if rows else 0
                ),
                "average_retrieval_recall": (
                    sum(row.retrieval_recall for row in rows) / len(rows)
                    if rows else 0
                ),
            }
        return summary

    def to_dict(self) -> dict[str, object]:
        return {
            "dataset": self.dataset,
            "dataset_version": self.dataset_version,
            "cases": self.cases,
            "summary": self.summary,
            "results": [asdict(row) for row in self.results],
            "interpretation": (
                "offline_retrieval_accuracy_tokens_no_model_quality_claim"
            ),
        }


class MemoryBenchmarkRunner:
    """Run four deterministic context-selection arms over synthetic memories."""

    @staticmethod
    def _score(
        case: MemoryBenchmarkCase,
        arm: str,
        selected: tuple[MemoryBenchmarkEntry, ...],
        *,
        conflict_detected: bool = False,
    ) -> MemoryArmResult:
        labels = {entry.label for entry in selected}
        required = set(case.required_labels)
        harmful = set(case.harmful_labels)
        recall = len(labels & required) / len(required)
        precision = (
            len(labels & required) / len(labels) if labels else 0.0
        )
        if case.expected_kind == "conflict":
            accurate = required <= labels and conflict_detected
        else:
            accurate = required <= labels and not labels & harmful
        return MemoryArmResult(
            case_id=case.id,
            category=case.category,
            arm=arm,
            accuracy=float(accurate),
            input_tokens=sum(estimate_tokens(entry.content) for entry in selected),
            selected_count=len(selected),
            retrieval_precision=precision,
            retrieval_recall=recall,
            harmful_selected=len(labels & harmful),
            conflict_detected=conflict_detected,
            selected_labels=tuple(entry.label for entry in selected[:100]),
            selected_labels_truncated=len(selected) > 100,
        )

    @staticmethod
    def _simple_rag(
        case: MemoryBenchmarkCase,
        entries: tuple[MemoryBenchmarkEntry, ...],
    ) -> tuple[MemoryBenchmarkEntry, ...]:
        ranked = sorted(
            enumerate(entries),
            key=lambda item: (
                -lexical_relevance(
                    case.query,
                    f"{item[1].subject} {item[1].content}",
                ),
                item[0],
            ),
        )
        selected: list[MemoryBenchmarkEntry] = []
        tokens = 0
        for _, entry in ranked:
            relevance = lexical_relevance(
                case.query, f"{entry.subject} {entry.content}"
            )
            if relevance <= 0 or len(selected) >= case.target_memories:
                continue
            cost = estimate_tokens(entry.content)
            if tokens + cost > case.token_budget:
                continue
            selected.append(entry)
            tokens += cost
        return tuple(selected)

    @staticmethod
    def _acr(
        case: MemoryBenchmarkCase,
        entries: tuple[MemoryBenchmarkEntry, ...],
        directory: str,
    ) -> tuple[tuple[MemoryBenchmarkEntry, ...], bool]:
        database = RuntimeDB(Path(directory) / f"{case.id}.db")
        labels_by_id: dict[str, str] = {}
        ids_by_label: dict[str, str] = {}
        entries_by_label = {entry.label: entry for entry in entries}
        pending = list(entries)
        try:
            while pending:
                progressed = False
                for entry in pending[:]:
                    if (
                        entry.supersedes is not None
                        and entry.supersedes not in ids_by_label
                    ):
                        continue
                    record = database.memories.create(MemoryCreate(
                        type=entry.type,
                        content=entry.content,
                        scope=entry.scope,
                        subject=entry.subject,
                        confidence=entry.confidence,
                        importance=entry.importance,
                        source_type="test",
                        evidence=(f"benchmark:{case.id}:{entry.label}",),
                        status=entry.status,
                        valid_from=entry.valid_from,
                        valid_until=entry.valid_until,
                        supersedes=(
                            ids_by_label[entry.supersedes]
                            if entry.supersedes else None
                        ),
                    ))
                    labels_by_id[record.id] = entry.label
                    ids_by_label[entry.label] = record.id
                    pending.remove(entry)
                    progressed = True
                if not progressed:
                    raise ValueError("Memory benchmark supersession graph is cyclic")
            result = HybridMemoryRetriever(database.memories).retrieve(
                RetrievalRequest(
                    task=case.task,
                    query=case.query,
                    scope=case.scope,
                    token_budget=case.token_budget,
                    valid_at=case.valid_at,
                    target_memories=case.target_memories,
                )
            )
            selected = tuple(
                entries_by_label[labels_by_id[item.memory.id]]
                for item in result.selected
            )
            selected_ids = {item.memory.id for item in result.selected}
            conflict_detected = any(
                selected_ids.intersection(item.conflict_ids)
                for item in result.selected
            )
            return selected, conflict_detected
        finally:
            database.close()

    def run(self, dataset: MemoryBenchmarkDataset) -> MemoryBenchmarkReport:
        results: list[MemoryArmResult] = []
        with tempfile.TemporaryDirectory() as directory:
            for case in dataset.cases:
                entries = case.expanded_entries()
                results.append(self._score(case, "no_memory", ()))
                results.append(
                    self._score(case, "raw_conversation", entries)
                )
                results.append(self._score(
                    case, "simple_rag", self._simple_rag(case, entries)
                ))
                selected, conflict = self._acr(case, entries, directory)
                results.append(self._score(
                    case, "acr_memory", selected,
                    conflict_detected=conflict,
                ))
        return MemoryBenchmarkReport(
            dataset=dataset.name,
            dataset_version=dataset.version,
            cases=len(dataset.cases),
            results=tuple(results),
        )
