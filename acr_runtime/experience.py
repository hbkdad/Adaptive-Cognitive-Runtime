from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import asdict, dataclass
from enum import Enum
from typing import Protocol

from .memory import MemoryType, utc_now
from .scoring import estimate_tokens
from .write_controller import CandidateFact, MemoryWriteController

MAX_RAW_TRACE_BYTES = 5_000_000
MAX_EVENT_CONTENT = 100_000


class ExperienceEventKind(str, Enum):
    FACT = "fact"
    DECISION = "decision"
    PROCEDURE = "procedure"
    FAILURE = "failure"
    ENVIRONMENT = "environment"
    TOOL_SEQUENCE = "tool_sequence"
    CANDIDATE_SKILL = "candidate_skill"
    OBSERVATION = "observation"


class DistilledKind(str, Enum):
    DURABLE_FACT = "durable_fact"
    DECISION = "decision"
    SUCCESSFUL_PROCEDURE = "successful_procedure"
    FAILURE_PATTERN = "failure_pattern"
    ENVIRONMENT_DISCOVERY = "environment_discovery"
    TOOL_SEQUENCE = "tool_sequence"
    CANDIDATE_SKILL = "candidate_skill"


@dataclass(frozen=True)
class ExperienceEvent:
    kind: ExperienceEventKind
    content: str
    evidence: tuple[str, ...] = ()
    confidence: float = 0.7
    importance: float = 0.5
    durable: bool = True
    metadata_json: str = "{}"

    def __post_init__(self) -> None:
        if not self.content.strip():
            raise ValueError("Experience event content cannot be empty")
        if len(self.content) > MAX_EVENT_CONTENT:
            raise ValueError("Experience event content exceeds 100 KB")
        for value in (self.confidence, self.importance):
            if not 0 <= value <= 1:
                raise ValueError("Experience scores must be between 0 and 1")
        metadata = json.loads(self.metadata_json)
        if not isinstance(metadata, dict):
            raise ValueError("Experience metadata must be a JSON object")
        if any(not item.strip() for item in self.evidence):
            raise ValueError("Experience evidence references cannot be empty")


@dataclass(frozen=True)
class ExperienceTraceCreate:
    scope: str
    task_class: str
    outcome: str
    significance_score: float
    events: tuple[ExperienceEvent, ...]
    task_id: str | None = None

    def __post_init__(self) -> None:
        if not self.scope.strip() or not self.task_class.strip():
            raise ValueError("Experience scope and task_class are required")
        if self.outcome not in {"succeeded", "failed", "partial", "cancelled"}:
            raise ValueError("Invalid experience outcome")
        if not 0 <= self.significance_score <= 1:
            raise ValueError("significance_score must be between 0 and 1")
        if not self.events:
            raise ValueError("Experience trace must contain at least one event")


@dataclass(frozen=True)
class ExperienceTrace:
    id: str
    task_id: str | None
    scope: str
    task_class: str
    outcome: str
    significance_score: float
    events: tuple[ExperienceEvent, ...]
    raw_tokens: int
    created_at: str


@dataclass(frozen=True)
class DistillationConfig:
    minimum_significance: float = 0.60
    minimum_confidence: float = 0.50
    minimum_importance: float = 0.40
    extractor: str = "structured-v1"

    def __post_init__(self) -> None:
        for value in (
            self.minimum_significance,
            self.minimum_confidence,
            self.minimum_importance,
        ):
            if not 0 <= value <= 1:
                raise ValueError("Distillation thresholds must be between 0 and 1")
        if not self.extractor.strip():
            raise ValueError("extractor cannot be empty")


@dataclass(frozen=True)
class DistilledItem:
    id: str
    kind: DistilledKind
    content: str
    evidence: tuple[str, ...]
    confidence: float
    importance: float
    source_event_indexes: tuple[int, ...]
    status: str = "proposed"
    memory_id: str | None = None
    skill_id: str | None = None
    error_type: str | None = None


@dataclass(frozen=True)
class DistillationPlan:
    id: str
    trace_id: str
    status: str
    extractor: str
    raw_tokens: int
    distilled_tokens: int
    compression_ratio: float
    items: tuple[DistilledItem, ...]
    created_at: str
    applied_at: str | None = None

    @property
    def reduction_ratio(self) -> float:
        if self.raw_tokens == 0:
            return 0.0
        return max(0.0, 1.0 - self.distilled_tokens / self.raw_tokens)

    def summary(self) -> dict[str, int]:
        return {
            kind.value: sum(item.kind is kind for item in self.items)
            for kind in DistilledKind
        }


class SkillRegistry(Protocol):
    def add_skill(
        self,
        *,
        name: str,
        version: str,
        description: str,
        instructions: str,
        tags: tuple[str, ...],
        status: str,
    ) -> str: ...


class ExperienceDistiller:
    """Preserves raw traces and produces governed compact representations."""

    def __init__(
        self,
        connection: sqlite3.Connection,
        writer: MemoryWriteController,
        skills: SkillRegistry,
        *,
        config: DistillationConfig | None = None,
    ) -> None:
        self.connection = connection
        self.writer = writer
        self.skills = skills
        self.config = config or DistillationConfig()

    @staticmethod
    def _event_dict(event: ExperienceEvent) -> dict[str, object]:
        return {
            **asdict(event),
            "kind": event.kind.value,
            "evidence": list(event.evidence),
        }

    @staticmethod
    def _event(value: dict[str, object]) -> ExperienceEvent:
        return ExperienceEvent(
            kind=ExperienceEventKind(str(value["kind"])),
            content=str(value["content"]),
            evidence=tuple(str(item) for item in value.get("evidence", [])),
            confidence=float(value.get("confidence", 0.7)),
            importance=float(value.get("importance", 0.5)),
            durable=bool(value.get("durable", True)),
            metadata_json=str(value.get("metadata_json", "{}")),
        )

    def capture(self, candidate: ExperienceTraceCreate) -> ExperienceTrace:
        raw = json.dumps(
            {
                "task_id": candidate.task_id,
                "scope": candidate.scope,
                "task_class": candidate.task_class,
                "outcome": candidate.outcome,
                "significance_score": candidate.significance_score,
                "events": [self._event_dict(event) for event in candidate.events],
            },
            sort_keys=True,
            ensure_ascii=False,
        )
        if len(raw.encode("utf-8")) > MAX_RAW_TRACE_BYTES:
            raise ValueError("Raw experience trace exceeds the 5 MB limit")
        trace_id = str(uuid.uuid4())
        created_at = utc_now()
        raw_tokens = estimate_tokens(raw)
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO experience_traces(
                    id, task_id, scope, task_class, outcome,
                    significance_score, raw_trace_json, raw_tokens,
                    event_count, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    trace_id,
                    candidate.task_id,
                    candidate.scope,
                    candidate.task_class,
                    candidate.outcome,
                    candidate.significance_score,
                    raw,
                    raw_tokens,
                    len(candidate.events),
                    created_at,
                ),
            )
        return ExperienceTrace(
            id=trace_id,
            task_id=candidate.task_id,
            scope=candidate.scope,
            task_class=candidate.task_class,
            outcome=candidate.outcome,
            significance_score=candidate.significance_score,
            events=candidate.events,
            raw_tokens=raw_tokens,
            created_at=created_at,
        )

    def get_trace(self, trace_id: str) -> ExperienceTrace | None:
        row = self.connection.execute(
            "SELECT * FROM experience_traces WHERE id = ?", (trace_id,)
        ).fetchone()
        if row is None:
            return None
        raw = json.loads(row["raw_trace_json"])
        return ExperienceTrace(
            id=row["id"],
            task_id=row["task_id"],
            scope=row["scope"],
            task_class=row["task_class"],
            outcome=row["outcome"],
            significance_score=row["significance_score"],
            events=tuple(self._event(item) for item in raw["events"]),
            raw_tokens=row["raw_tokens"],
            created_at=row["created_at"],
        )

    @staticmethod
    def _distilled_kind(
        event: ExperienceEvent, outcome: str
    ) -> DistilledKind | None:
        mapping = {
            ExperienceEventKind.FACT: DistilledKind.DURABLE_FACT,
            ExperienceEventKind.DECISION: DistilledKind.DECISION,
            ExperienceEventKind.FAILURE: DistilledKind.FAILURE_PATTERN,
            ExperienceEventKind.ENVIRONMENT: DistilledKind.ENVIRONMENT_DISCOVERY,
            ExperienceEventKind.TOOL_SEQUENCE: DistilledKind.TOOL_SEQUENCE,
            ExperienceEventKind.CANDIDATE_SKILL: DistilledKind.CANDIDATE_SKILL,
        }
        if event.kind is ExperienceEventKind.PROCEDURE:
            return (
                DistilledKind.SUCCESSFUL_PROCEDURE
                if outcome == "succeeded"
                else None
            )
        return mapping.get(event.kind)

    def plan(
        self, trace_id: str, *, manage_transaction: bool = True
    ) -> DistillationPlan:
        trace = self.get_trace(trace_id)
        if trace is None:
            raise KeyError(trace_id)
        if trace.significance_score < self.config.minimum_significance:
            raise ValueError("Trace does not meet the significance threshold")
        grouped: dict[tuple[DistilledKind, str], dict[str, object]] = {}
        for index, event in enumerate(trace.events):
            kind = self._distilled_kind(event, trace.outcome)
            if (
                kind is None
                or not event.durable
                or event.confidence < self.config.minimum_confidence
                or event.importance < self.config.minimum_importance
            ):
                continue
            key = (kind, " ".join(event.content.casefold().split()))
            entry = grouped.setdefault(
                key,
                {
                    "content": event.content.strip(),
                    "evidence": [],
                    "confidence": event.confidence,
                    "importance": event.importance,
                    "indexes": [],
                },
            )
            entry["evidence"] = list(
                dict.fromkeys((*entry["evidence"], *event.evidence))
            )
            entry["confidence"] = max(float(entry["confidence"]), event.confidence)
            entry["importance"] = max(float(entry["importance"]), event.importance)
            entry["indexes"].append(index)
        if not grouped:
            raise ValueError("Trace contains no durable distillation candidates")
        items = tuple(
            DistilledItem(
                id=str(uuid.uuid4()),
                kind=kind,
                content=str(entry["content"]),
                evidence=tuple(entry["evidence"])
                or (f"trace:{trace.id}:event:{entry['indexes'][0]}",),
                confidence=float(entry["confidence"]),
                importance=float(entry["importance"]),
                source_event_indexes=tuple(entry["indexes"]),
            )
            for (kind, _), entry in grouped.items()
        )
        distilled_tokens = sum(estimate_tokens(item.content) for item in items)
        compression_ratio = trace.raw_tokens / max(1, distilled_tokens)
        plan = DistillationPlan(
            id=str(uuid.uuid4()),
            trace_id=trace.id,
            status="planned",
            extractor=self.config.extractor,
            raw_tokens=trace.raw_tokens,
            distilled_tokens=distilled_tokens,
            compression_ratio=round(compression_ratio, 6),
            items=items,
            created_at=utc_now(),
        )
        self._save_plan(plan, manage_transaction=manage_transaction)
        return plan

    def _save_plan(
        self, plan: DistillationPlan, *, manage_transaction: bool = True
    ) -> None:
        try:
            self.connection.execute(
                """
                INSERT INTO experience_distillations(
                    id, trace_id, status, extractor, raw_tokens,
                    distilled_tokens, compression_ratio, summary_json,
                    created_at, applied_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)
                """,
                (
                    plan.id,
                    plan.trace_id,
                    plan.status,
                    plan.extractor,
                    plan.raw_tokens,
                    plan.distilled_tokens,
                    plan.compression_ratio,
                    json.dumps(plan.summary()),
                    plan.created_at,
                ),
            )
            self.connection.executemany(
                """
                INSERT INTO experience_distilled_items(
                    id, run_id, kind, content, evidence_json, confidence,
                    importance, source_event_indexes_json, status,
                    memory_id, skill_id, error_type, created_at, applied_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'proposed',
                          NULL, NULL, NULL, ?, NULL)
                """,
                (
                    (
                        item.id,
                        plan.id,
                        item.kind.value,
                        item.content,
                        json.dumps(item.evidence),
                        item.confidence,
                        item.importance,
                        json.dumps(item.source_event_indexes),
                        plan.created_at,
                    )
                    for item in plan.items
                ),
            )
            if manage_transaction:
                self.connection.commit()
        except Exception:
            if manage_transaction:
                self.connection.rollback()
            raise

    def load_plan(self, run_id: str) -> DistillationPlan:
        run = self.connection.execute(
            "SELECT * FROM experience_distillations WHERE id = ?", (run_id,)
        ).fetchone()
        if run is None:
            raise KeyError(run_id)
        rows = self.connection.execute(
            """
            SELECT * FROM experience_distilled_items
            WHERE run_id = ? ORDER BY created_at, id
            """,
            (run_id,),
        ).fetchall()
        return DistillationPlan(
            id=run["id"],
            trace_id=run["trace_id"],
            status=run["status"],
            extractor=run["extractor"],
            raw_tokens=run["raw_tokens"],
            distilled_tokens=run["distilled_tokens"],
            compression_ratio=run["compression_ratio"],
            items=tuple(
                DistilledItem(
                    id=row["id"],
                    kind=DistilledKind(row["kind"]),
                    content=row["content"],
                    evidence=tuple(json.loads(row["evidence_json"])),
                    confidence=row["confidence"],
                    importance=row["importance"],
                    source_event_indexes=tuple(
                        json.loads(row["source_event_indexes_json"])
                    ),
                    status=row["status"],
                    memory_id=row["memory_id"],
                    skill_id=row["skill_id"],
                    error_type=row["error_type"],
                )
                for row in rows
            ),
            created_at=run["created_at"],
            applied_at=run["applied_at"],
        )

    @staticmethod
    def _memory_type(kind: DistilledKind) -> MemoryType:
        return {
            DistilledKind.DURABLE_FACT: MemoryType.SEMANTIC,
            DistilledKind.DECISION: MemoryType.DECISION,
            DistilledKind.SUCCESSFUL_PROCEDURE: MemoryType.PROCEDURAL,
            DistilledKind.FAILURE_PATTERN: MemoryType.FAILURE,
            DistilledKind.ENVIRONMENT_DISCOVERY: MemoryType.ENVIRONMENT,
            DistilledKind.TOOL_SEQUENCE: MemoryType.PROCEDURAL,
        }[kind]

    def _mark_item(
        self,
        item_id: str,
        status: str,
        *,
        memory_id: str | None = None,
        skill_id: str | None = None,
        error_type: str | None = None,
    ) -> None:
        with self.connection:
            self.connection.execute(
                """
                UPDATE experience_distilled_items
                SET status = ?, memory_id = ?, skill_id = ?,
                    error_type = ?, applied_at = ?
                WHERE id = ?
                """,
                (status, memory_id, skill_id, error_type, utc_now(), item_id),
            )

    def approve(self, run_id: str) -> DistillationPlan:
        plan = self.load_plan(run_id)
        if plan.status != "planned":
            raise ValueError("Only a planned distillation can be approved")
        trace = self.get_trace(plan.trace_id)
        if trace is None:
            raise RuntimeError("Raw trace is missing")
        failures = 0
        for item in plan.items:
            try:
                if item.kind is DistilledKind.CANDIDATE_SKILL:
                    metadata = json.loads(
                        trace.events[item.source_event_indexes[0]].metadata_json
                    )
                    name = str(
                        metadata.get(
                            "name",
                            f"distilled-{trace.task_class}-{item.id[:8]}",
                        )
                    )
                    skill_id = self.skills.add_skill(
                        name=name,
                        version="0.1.0",
                        description=f"Candidate distilled from trace {trace.id}",
                        instructions=item.content,
                        tags=("distilled", trace.task_class),
                        status="quarantine",
                    )
                    self._mark_item(item.id, "applied", skill_id=skill_id)
                else:
                    decision = self.writer.consider(
                        CandidateFact(
                            type=self._memory_type(item.kind),
                            content=item.content,
                            scope=trace.scope,
                            confidence=item.confidence,
                            importance=item.importance,
                            usefulness=max(0.5, item.importance),
                            stability=0.8,
                            evidence=item.evidence,
                            source_type="experience-distillation",
                            source_id=plan.id,
                            structured_payload_json=json.dumps(
                                {
                                    "trace_id": trace.id,
                                    "distillation_id": plan.id,
                                    "distilled_kind": item.kind.value,
                                },
                                sort_keys=True,
                            ),
                        )
                    )
                    if decision.memory is None:
                        self._mark_item(item.id, "skipped")
                    else:
                        self._mark_item(
                            item.id, "applied", memory_id=decision.memory.id
                        )
            except Exception as error:
                self._mark_item(
                    item.id, "error", error_type=type(error).__name__
                )
                failures += 1
        with self.connection:
            self.connection.execute(
                """
                UPDATE experience_distillations
                SET status = ?, applied_at = ? WHERE id = ?
                """,
                (
                    "partially_applied" if failures else "applied",
                    utc_now(),
                    run_id,
                ),
            )
        return self.load_plan(run_id)
