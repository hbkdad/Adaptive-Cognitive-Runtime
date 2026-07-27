from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Mapping

from .memory import (
    MemoryCreate,
    MemoryRecord,
    MemoryStatus,
    MemoryStore,
    MemoryType,
    Sensitivity,
    normalize_timestamp,
)
from .retrieval import HybridMemoryRetriever, RetrievalRequest
from .secret_management import assert_secret_free

DECISION_SCHEMA = "acr.decision.v1"


def _text(value: object, field: str, *, maximum: int = 20_000) -> str:
    text = str(value).strip()
    if not text:
        raise ValueError(f"{field} cannot be empty")
    if len(text) > maximum:
        raise ValueError(f"{field} exceeds {maximum} characters")
    assert_secret_free(text, f"decision {field}")
    return text


@dataclass(frozen=True)
class DecisionAssumption:
    name: str
    value: str

    def __post_init__(self) -> None:
        _text(self.name, "assumption name", maximum=255)
        _text(self.value, "assumption value", maximum=2_000)

    @classmethod
    def from_dict(cls, payload: object) -> "DecisionAssumption":
        if not isinstance(payload, dict) or set(payload) != {"name", "value"}:
            raise ValueError("Decision assumption requires name and value")
        if not isinstance(payload["name"], str) or not isinstance(
            payload["value"], str
        ):
            raise ValueError("Decision assumption name and value must be strings")
        return cls(payload["name"], payload["value"])

    def as_dict(self) -> dict[str, str]:
        return {"name": self.name, "value": self.value}


@dataclass(frozen=True)
class DecisionCreate:
    topic: str
    decision: str
    context: str
    alternatives: tuple[str, ...]
    reason: str
    consequences: tuple[str, ...]
    decided_at: str
    scope: str
    evidence: tuple[str, ...]
    assumptions: tuple[DecisionAssumption, ...] = ()
    supersedes: str | None = None
    confidence: float = 0.9
    importance: float = 0.9
    sensitivity: Sensitivity = Sensitivity.INTERNAL

    def __post_init__(self) -> None:
        _text(self.topic, "topic", maximum=255)
        _text(self.decision, "decision")
        _text(self.context, "context")
        _text(self.reason, "reason")
        _text(self.scope, "scope", maximum=255)
        normalize_timestamp(self.decided_at)
        if not self.alternatives:
            raise ValueError("Decision alternatives cannot be empty")
        if not self.consequences:
            raise ValueError("Decision consequences cannot be empty")
        if not self.evidence:
            raise ValueError("Decision evidence cannot be empty")
        for field, values in (
            ("alternative", self.alternatives),
            ("consequence", self.consequences),
            ("evidence", self.evidence),
        ):
            for value in values:
                _text(value, field, maximum=4_000)
        names = [item.name.casefold() for item in self.assumptions]
        if len(names) != len(set(names)):
            raise ValueError("Decision assumption names must be unique")
        for field, value in (
            ("confidence", self.confidence),
            ("importance", self.importance),
        ):
            if not 0 <= value <= 1:
                raise ValueError(f"{field} must be between 0 and 1")

    @classmethod
    def from_dict(cls, payload: object) -> "DecisionCreate":
        if not isinstance(payload, dict):
            raise ValueError("Decision create request must be an object")
        required = {
            "topic",
            "decision",
            "context",
            "alternatives",
            "reason",
            "consequences",
            "decided_at",
            "scope",
            "evidence",
        }
        optional = {
            "assumptions",
            "supersedes",
            "confidence",
            "importance",
            "sensitivity",
        }
        if not required <= set(payload) or set(payload) - required - optional:
            raise ValueError(f"Decision request requires {sorted(required)}")
        for field in ("alternatives", "consequences", "evidence"):
            if not isinstance(payload[field], list):
                raise ValueError(f"{field} must be a list")
            if any(not isinstance(item, str) for item in payload[field]):
                raise ValueError(f"{field} items must be strings")
        assumptions = payload.get("assumptions", [])
        if not isinstance(assumptions, list):
            raise ValueError("assumptions must be a list")
        return cls(
            topic=str(payload["topic"]),
            decision=str(payload["decision"]),
            context=str(payload["context"]),
            alternatives=tuple(str(item) for item in payload["alternatives"]),
            reason=str(payload["reason"]),
            consequences=tuple(str(item) for item in payload["consequences"]),
            decided_at=str(payload["decided_at"]),
            scope=str(payload["scope"]),
            evidence=tuple(str(item) for item in payload["evidence"]),
            assumptions=tuple(
                DecisionAssumption.from_dict(item) for item in assumptions
            ),
            supersedes=(
                str(payload["supersedes"])
                if payload.get("supersedes") is not None
                else None
            ),
            confidence=float(payload.get("confidence", 0.9)),
            importance=float(payload.get("importance", 0.9)),
            sensitivity=Sensitivity(payload.get("sensitivity", "internal")),
        )

    def payload(self) -> dict[str, object]:
        return {
            "schema": DECISION_SCHEMA,
            "topic": self.topic,
            "decision": self.decision,
            "context": self.context,
            "alternatives": list(self.alternatives),
            "reason": self.reason,
            "consequences": list(self.consequences),
            "decided_at": normalize_timestamp(self.decided_at),
            "scope": self.scope,
            "evidence": list(self.evidence),
            "assumptions": [item.as_dict() for item in self.assumptions],
        }


@dataclass(frozen=True)
class DecisionCheck:
    task: str
    query: str
    scope: str
    assumptions: Mapping[str, str]
    token_budget: int = 1_000
    limit: int = 8

    def __post_init__(self) -> None:
        _text(self.task, "check task")
        _text(self.query, "check query")
        _text(self.scope, "check scope", maximum=255)
        if not 1 <= self.token_budget <= 20_000:
            raise ValueError("Decision token budget must be 1..20000")
        if not 1 <= self.limit <= 25:
            raise ValueError("Decision check limit must be 1..25")
        for name, value in self.assumptions.items():
            _text(name, "current assumption name", maximum=255)
            _text(value, "current assumption value", maximum=2_000)


class DecisionMemory:
    def __init__(
        self, memories: MemoryStore, retriever: HybridMemoryRetriever
    ) -> None:
        self.memories = memories
        self.retriever = retriever

    def record(self, request: DecisionCreate) -> MemoryRecord:
        payload = request.payload()
        return self.memories.create(
            MemoryCreate(
                type=MemoryType.DECISION,
                content=request.decision,
                scope=request.scope,
                subject=request.topic,
                structured_payload_json=json.dumps(payload, sort_keys=True),
                confidence=request.confidence,
                importance=request.importance,
                source_type="decision_record",
                source_id=normalize_timestamp(request.decided_at),
                evidence=request.evidence,
                retention_reasons=("architecture_or_operational_decision",),
                status=MemoryStatus.CONFIRMED,
                valid_from=request.decided_at,
                supersedes=request.supersedes,
                sensitivity=request.sensitivity,
            )
        )

    @staticmethod
    def _evaluation(
        record: MemoryRecord, current: Mapping[str, str]
    ) -> dict[str, object]:
        try:
            payload = json.loads(record.structured_payload_json)
        except (TypeError, json.JSONDecodeError):
            payload = {}
        if not isinstance(payload, dict) or payload.get("schema") != DECISION_SCHEMA:
            return {
                "memory_id": record.id,
                "topic": record.subject,
                "decision": record.content,
                "scope": record.scope,
                "status": "unstructured_legacy",
                "changed_assumptions": [],
                "unverified_assumptions": [],
                "reason": "legacy_decision_requires_manual_validation",
            }
        required = {
            "topic",
            "decision",
            "context",
            "alternatives",
            "reason",
            "consequences",
            "decided_at",
            "scope",
            "evidence",
            "assumptions",
        }
        valid_lists = all(
            isinstance(payload.get(field), list)
            for field in ("alternatives", "consequences", "evidence", "assumptions")
        )
        valid_text = all(
            isinstance(payload.get(field), str)
            for field in (
                "topic", "decision", "context", "reason", "decided_at", "scope"
            )
        )
        valid_sequence_items = valid_lists and all(
            all(isinstance(item, str) for item in payload.get(field, []))
            for field in ("alternatives", "consequences", "evidence")
        )
        valid_assumptions = valid_lists and all(
            isinstance(item, dict)
            and set(item) == {"name", "value"}
            and isinstance(item["name"], str)
            and isinstance(item["value"], str)
            for item in payload.get("assumptions", [])
        )
        if (
            not required <= set(payload)
            or not valid_text
            or not valid_sequence_items
            or not valid_assumptions
        ):
            return {
                "memory_id": record.id,
                "topic": record.subject,
                "decision": record.content,
                "scope": record.scope,
                "status": "invalid_structured_decision",
                "changed_assumptions": [],
                "unverified_assumptions": [],
                "reason": "malformed_v1_payload_requires_manual_validation",
            }
        stored = {
            str(item["name"]).casefold(): (str(item["name"]), str(item["value"]))
            for item in payload.get("assumptions", [])
            if isinstance(item, dict) and set(item) == {"name", "value"}
        }
        supplied = {str(key).casefold(): str(value) for key, value in current.items()}
        changed = [
            name
            for key, (name, value) in stored.items()
            if key in supplied and supplied[key].casefold() != value.casefold()
        ]
        unverified = [
            name for key, (name, _) in stored.items() if key not in supplied
        ]
        if changed:
            status = "stale_assumptions"
            reason = "stored_assumptions_changed"
        elif unverified:
            status = "needs_validation"
            reason = "stored_assumptions_not_checked"
        else:
            status = "applicable"
            reason = "all_stored_assumptions_match"
        return {
            "memory_id": record.id,
            "topic": payload["topic"],
            "decision": payload["decision"],
            "context": payload["context"],
            "alternatives": payload["alternatives"],
            "reason": payload["reason"],
            "consequences": payload["consequences"],
            "decided_at": payload["decided_at"],
            "scope": record.scope,
            "evidence": payload["evidence"],
            "assumptions": payload["assumptions"],
            "status": status,
            "changed_assumptions": changed,
            "unverified_assumptions": unverified,
            "applicability_reason": reason,
        }

    def check(self, request: DecisionCheck) -> dict[str, object]:
        result = self.retriever.retrieve(
            RetrievalRequest(
                task=request.task,
                query=request.query,
                scope=request.scope,
                token_budget=request.token_budget,
                types=(MemoryType.DECISION,),
                target_memories=request.limit,
            )
        )
        decisions = []
        for ranked in result.selected:
            item = self._evaluation(ranked.memory, request.assumptions)
            item["score"] = ranked.score
            item["retrieval_explanation"] = ranked.explanation
            decisions.append(item)
        return {
            "scope": request.scope,
            "candidate_count": result.candidate_count,
            "selected_tokens": result.selected_tokens,
            "decisions": decisions,
            "requires_reconsideration": any(
                item["status"] != "applicable" for item in decisions
            ),
        }

    def inspect(self, memory_id: str) -> dict[str, object]:
        record = self.memories.get(memory_id)
        if record is None or record.type is not MemoryType.DECISION:
            raise LookupError(f"Unknown decision memory: {memory_id}")
        return self._evaluation(record, {})
