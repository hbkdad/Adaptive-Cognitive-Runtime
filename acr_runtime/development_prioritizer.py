from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Mapping

from .secret_management import SecretBoundaryError, assert_secret_free


WORK_KINDS = (
    "bug",
    "technical_debt",
    "feature_request",
    "benchmark_failure",
    "security_finding",
    "token_waste",
)
INVENTORY_CLAIMS = ("complete", "partial")
_ID = re.compile(r"^[a-z][a-z0-9._-]{1,127}$")
_REFERENCE = re.compile(r"^[a-z][a-z0-9_.-]{0,31}:[^\s]{1,240}$")


class DevelopmentPriorityError(ValueError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _digest(value: object) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _closed(
    value: object, fields: set[str], label: str
) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or set(value) != fields:
        raise DevelopmentPriorityError(
            f"{label} requires exactly {sorted(fields)}"
        )
    return value


def _text(value: object, field: str, maximum: int) -> str:
    if not isinstance(value, str):
        raise DevelopmentPriorityError(f"{field} must be text")
    normalized = value.strip()
    if not 1 <= len(normalized) <= maximum:
        raise DevelopmentPriorityError(
            f"{field} must be 1..{maximum} characters"
        )
    try:
        assert_secret_free(normalized, f"development priority {field}")
    except SecretBoundaryError as exc:
        raise DevelopmentPriorityError(
            f"{field} contains secret material"
        ) from exc
    return normalized


def _reference(value: object, field: str) -> str:
    normalized = _text(value, field, 240)
    if not _REFERENCE.fullmatch(normalized):
        raise DevelopmentPriorityError(
            f"{field} must be a bounded type:value reference"
        )
    return normalized


def _references(
    value: object, field: str, *, minimum: int = 1, maximum: int = 16
) -> tuple[str, ...]:
    if not isinstance(value, list) or not minimum <= len(value) <= maximum:
        raise DevelopmentPriorityError(
            f"{field} must contain {minimum}..{maximum} references"
        )
    result = tuple(_reference(item, field) for item in value)
    if len(set(result)) != len(result):
        raise DevelopmentPriorityError(f"{field} must be unique")
    return result


def _integer(
    value: object, field: str, *, minimum: int, maximum: int
) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise DevelopmentPriorityError(
            f"{field} must be an integer from {minimum} to {maximum}"
        )
    return value


@dataclass(frozen=True)
class DevelopmentWorkCandidate:
    id: str
    kind: str
    title: str
    source_refs: tuple[str, ...]
    expected_value_points: int
    confidence_bps: int
    frequency_count: int
    effort_points: int
    delivery_risk_points: int
    estimate_evidence: tuple[str, ...]

    @classmethod
    def from_dict(cls, payload: object) -> "DevelopmentWorkCandidate":
        fields = {
            "id",
            "kind",
            "title",
            "source_refs",
            "expected_value_points",
            "confidence_bps",
            "frequency_count",
            "effort_points",
            "delivery_risk_points",
            "estimate_evidence",
        }
        data = _closed(payload, fields, "development work candidate")
        identifier = data["id"]
        if not isinstance(identifier, str) or not _ID.fullmatch(identifier):
            raise DevelopmentPriorityError("candidate id is invalid")
        kind = data["kind"]
        if kind not in WORK_KINDS:
            raise DevelopmentPriorityError(
                f"kind must be one of {list(WORK_KINDS)}"
            )
        return cls(
            id=identifier,
            kind=str(kind),
            title=_text(data["title"], "title", 240),
            source_refs=_references(data["source_refs"], "source_refs"),
            expected_value_points=_integer(
                data["expected_value_points"],
                "expected_value_points",
                minimum=0,
                maximum=100,
            ),
            confidence_bps=_integer(
                data["confidence_bps"],
                "confidence_bps",
                minimum=0,
                maximum=10_000,
            ),
            frequency_count=_integer(
                data["frequency_count"],
                "frequency_count",
                minimum=1,
                maximum=1_000_000,
            ),
            effort_points=_integer(
                data["effort_points"],
                "effort_points",
                minimum=1,
                maximum=100,
            ),
            delivery_risk_points=_integer(
                data["delivery_risk_points"],
                "delivery_risk_points",
                minimum=1,
                maximum=100,
            ),
            estimate_evidence=_references(
                data["estimate_evidence"], "estimate_evidence"
            ),
        )

    @property
    def priority_micros(self) -> int:
        numerator = (
            self.expected_value_points
            * self.confidence_bps
            * self.frequency_count
            * 1_000_000
        )
        denominator = self.effort_points * self.delivery_risk_points * 10_000
        return numerator // denominator

    def inputs(self) -> dict[str, int]:
        return {
            "expected_value_points": self.expected_value_points,
            "confidence_bps": self.confidence_bps,
            "frequency_count": self.frequency_count,
            "effort_points": self.effort_points,
            "delivery_risk_points": self.delivery_risk_points,
        }


@dataclass(frozen=True)
class DevelopmentPriorityRequest:
    scope: str
    inventory_ref: str
    inventory_claim: str
    candidates: tuple[DevelopmentWorkCandidate, ...]

    @classmethod
    def from_dict(cls, payload: object) -> "DevelopmentPriorityRequest":
        fields = {
            "schema_version",
            "scope",
            "inventory_ref",
            "inventory_claim",
            "candidates",
        }
        data = _closed(payload, fields, "development priority request")
        if data["schema_version"] != 1 or not isinstance(
            data["candidates"], list
        ):
            raise DevelopmentPriorityError(
                "development priority request requires version 1 candidates"
            )
        claim = data["inventory_claim"]
        if claim not in INVENTORY_CLAIMS:
            raise DevelopmentPriorityError(
                f"inventory_claim must be one of {list(INVENTORY_CLAIMS)}"
            )
        candidates = tuple(
            DevelopmentWorkCandidate.from_dict(item)
            for item in data["candidates"]
        )
        if not 1 <= len(candidates) <= 256:
            raise DevelopmentPriorityError(
                "candidates must contain 1..256 work items"
            )
        if len({item.id for item in candidates}) != len(candidates):
            raise DevelopmentPriorityError("candidate ids must be unique")
        return cls(
            scope=_text(data["scope"], "scope", 160),
            inventory_ref=_reference(data["inventory_ref"], "inventory_ref"),
            inventory_claim=str(claim),
            candidates=candidates,
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "scope": self.scope,
            "inventory_ref": self.inventory_ref,
            "inventory_claim": self.inventory_claim,
            "candidates": [
                {
                    "id": item.id,
                    "kind": item.kind,
                    "title": item.title,
                    "source_refs": list(item.source_refs),
                    **item.inputs(),
                    "estimate_evidence": list(item.estimate_evidence),
                }
                for item in self.candidates
            ],
        }


class DevelopmentPrioritizer:
    """Retain and explain an advisory fixed-point work ranking."""

    def __init__(
        self,
        connection: sqlite3.Connection,
        *,
        mutation_guard: Callable[[str], None] | None = None,
    ) -> None:
        self.connection = connection
        self.mutation_guard = mutation_guard

    def prioritize(
        self, request: DevelopmentPriorityRequest
    ) -> dict[str, object]:
        if self.mutation_guard is not None:
            self.mutation_guard("development_prioritization")
        request_hash = _digest(request.as_dict())
        existing = self.connection.execute(
            "SELECT id FROM development_priority_runs WHERE request_hash=?",
            (request_hash,),
        ).fetchone()
        if existing is not None:
            return self.report(str(existing["id"]))
        ranked = sorted(
            request.candidates,
            key=lambda item: (
                -item.priority_micros,
                item.effort_points,
                item.delivery_risk_points,
                item.id,
            ),
        )
        run_id = str(uuid.uuid4())
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO development_priority_runs(
                    id, scope, inventory_ref, inventory_claim, request_hash,
                    candidate_count, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    request.scope,
                    request.inventory_ref,
                    request.inventory_claim,
                    request_hash,
                    len(ranked),
                    _now(),
                ),
            )
            for rank, item in enumerate(ranked, start=1):
                self.connection.execute(
                    """
                    INSERT INTO development_priority_candidates(
                        run_id, candidate_id, rank, kind, title,
                        source_refs_json, expected_value_points,
                        confidence_bps, frequency_count, effort_points,
                        delivery_risk_points, estimate_evidence_json,
                        priority_micros
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        run_id,
                        item.id,
                        rank,
                        item.kind,
                        item.title,
                        _json(item.source_refs),
                        item.expected_value_points,
                        item.confidence_bps,
                        item.frequency_count,
                        item.effort_points,
                        item.delivery_risk_points,
                        _json(item.estimate_evidence),
                        item.priority_micros,
                    ),
                )
        return self.report(run_id)

    def report(self, run_id: str) -> dict[str, object]:
        run = self.connection.execute(
            "SELECT * FROM development_priority_runs WHERE id=?", (run_id,)
        ).fetchone()
        if run is None:
            raise DevelopmentPriorityError(
                f"unknown development priority run: {run_id}"
            )
        rows = self.connection.execute(
            """
            SELECT * FROM development_priority_candidates
            WHERE run_id=? ORDER BY rank
            """,
            (run_id,),
        ).fetchall()
        return {
            "id": str(run["id"]),
            "scope": str(run["scope"]),
            "inventory_ref": str(run["inventory_ref"]),
            "inventory_claim": str(run["inventory_claim"]),
            "completeness": (
                "caller_asserted_complete"
                if run["inventory_claim"] == "complete"
                else "partial_inventory"
            ),
            "request_hash": str(run["request_hash"]),
            "candidate_count": int(run["candidate_count"]),
            "ranked_work": [
                {
                    "rank": int(row["rank"]),
                    "id": str(row["candidate_id"]),
                    "kind": str(row["kind"]),
                    "title": str(row["title"]),
                    "priority_micros": int(row["priority_micros"]),
                    "inputs": {
                        "expected_value_points": int(
                            row["expected_value_points"]
                        ),
                        "confidence_bps": int(row["confidence_bps"]),
                        "frequency_count": int(row["frequency_count"]),
                        "effort_points": int(row["effort_points"]),
                        "delivery_risk_points": int(
                            row["delivery_risk_points"]
                        ),
                    },
                    "source_refs": json.loads(row["source_refs_json"]),
                    "estimate_evidence": json.loads(
                        row["estimate_evidence_json"]
                    ),
                    "reasoning": (
                        "floor(expected_value_points * confidence_bps "
                        "* frequency_count * 1000000 / "
                        "(effort_points * delivery_risk_points * 10000))"
                    ),
                }
                for row in rows
            ],
            "advisory_only": True,
            "implementation_authority": False,
            "automatic_action_performed": False,
            "created_at": str(run["created_at"]),
        }
