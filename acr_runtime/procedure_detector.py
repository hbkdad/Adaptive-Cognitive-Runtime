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


_NAME = re.compile(r"^[a-z][a-z0-9_.:-]{1,127}$")
_PARAMETER = re.compile(r"^[a-z][a-z0-9_.:-]{0,63}$")
_UUID = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-"
    r"[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)


class ProcedureDetectionError(ValueError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _text(value: object, field: str, maximum: int) -> str:
    if not isinstance(value, str):
        raise ProcedureDetectionError(f"{field} must be text")
    normalized = value.strip()
    if len(normalized) < 2 or len(normalized) > maximum:
        raise ProcedureDetectionError(f"{field} must be 2..{maximum} characters")
    try:
        assert_secret_free(normalized, f"procedure detection {field}")
    except SecretBoundaryError as exc:
        raise ProcedureDetectionError(f"{field} contains secret material") from exc
    return normalized


def _timestamp(value: object) -> str:
    text = _text(value, "observed_before", 64)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ProcedureDetectionError(
            "observed_before must be an ISO-8601 timestamp"
        ) from exc
    if parsed.tzinfo is None:
        raise ProcedureDetectionError("observed_before must include a timezone")
    return parsed.astimezone(timezone.utc).isoformat()


@dataclass(frozen=True)
class ProcedureDetectionRequest:
    scope: str
    task_classes: tuple[str, ...]
    observed_before: str
    minimum_successes: int = 3
    minimum_distinct_tasks: int = 3
    maximum_non_success_rate: float = 0.0
    minimum_significance: float = 0.6
    maximum_traces: int = 500

    @classmethod
    def from_dict(cls, payload: object) -> "ProcedureDetectionRequest":
        fields = {
            "schema_version",
            "scope",
            "task_classes",
            "observed_before",
            "minimum_successes",
            "minimum_distinct_tasks",
            "maximum_non_success_rate",
            "minimum_significance",
            "maximum_traces",
        }
        if not isinstance(payload, Mapping) or set(payload) != fields:
            raise ProcedureDetectionError(
                "procedure detection request requires the closed version 1 schema"
            )
        if payload.get("schema_version") != 1:
            raise ProcedureDetectionError("schema_version must be 1")
        classes = payload["task_classes"]
        if not isinstance(classes, list) or not 1 <= len(classes) <= 16:
            raise ProcedureDetectionError("task_classes must contain 1..16 values")
        normalized_classes = tuple(
            _text(value, "task_class", 160) for value in classes
        )
        if len(set(normalized_classes)) != len(normalized_classes):
            raise ProcedureDetectionError("task_classes must be unique")
        minimum_successes = payload["minimum_successes"]
        minimum_tasks = payload["minimum_distinct_tasks"]
        maximum_rate = payload["maximum_non_success_rate"]
        minimum_significance = payload["minimum_significance"]
        maximum_traces = payload["maximum_traces"]
        if (
            not isinstance(minimum_successes, int)
            or isinstance(minimum_successes, bool)
            or not 3 <= minimum_successes <= 20
        ):
            raise ProcedureDetectionError("minimum_successes must be 3..20")
        if (
            not isinstance(minimum_tasks, int)
            or isinstance(minimum_tasks, bool)
            or not 3 <= minimum_tasks <= minimum_successes
        ):
            raise ProcedureDetectionError(
                "minimum_distinct_tasks must be 3..minimum_successes"
            )
        if (
            not isinstance(maximum_rate, (int, float))
            or isinstance(maximum_rate, bool)
            or not 0 <= float(maximum_rate) <= 0.25
        ):
            raise ProcedureDetectionError(
                "maximum_non_success_rate must be 0..0.25"
            )
        if (
            not isinstance(minimum_significance, (int, float))
            or isinstance(minimum_significance, bool)
            or not 0.6 <= float(minimum_significance) <= 1
        ):
            raise ProcedureDetectionError("minimum_significance must be 0.6..1")
        if (
            not isinstance(maximum_traces, int)
            or isinstance(maximum_traces, bool)
            or not 10 <= maximum_traces <= 500
        ):
            raise ProcedureDetectionError("maximum_traces must be 10..500")
        return cls(
            scope=_text(payload["scope"], "scope", 160),
            task_classes=normalized_classes,
            observed_before=_timestamp(payload["observed_before"]),
            minimum_successes=minimum_successes,
            minimum_distinct_tasks=minimum_tasks,
            maximum_non_success_rate=float(maximum_rate),
            minimum_significance=float(minimum_significance),
            maximum_traces=maximum_traces,
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "scope": self.scope,
            "task_classes": list(self.task_classes),
            "observed_before": self.observed_before,
            "minimum_successes": self.minimum_successes,
            "minimum_distinct_tasks": self.minimum_distinct_tasks,
            "maximum_non_success_rate": self.maximum_non_success_rate,
            "minimum_significance": self.minimum_significance,
            "maximum_traces": self.maximum_traces,
        }


@dataclass(frozen=True)
class ProcedureCandidate:
    id: str
    task_class: str
    signature_hash: str
    operations: tuple[str, ...]
    variability: tuple[dict[str, object], ...]
    success_count: int
    non_success_count: int
    distinct_task_count: int
    average_significance: float
    support_trace_ids: tuple[str, ...]
    status: str = "suggested"

    def as_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "task_class": self.task_class,
            "signature_hash": self.signature_hash,
            "operations": list(self.operations),
            "variability": list(self.variability),
            "success_count": self.success_count,
            "non_success_count": self.non_success_count,
            "distinct_task_count": self.distinct_task_count,
            "average_significance": self.average_significance,
            "support_trace_ids": list(self.support_trace_ids),
            "status": self.status,
        }


@dataclass(frozen=True)
class ProcedureDetectionRun:
    id: str
    scope: str
    task_classes: tuple[str, ...]
    config: dict[str, object]
    request_hash: str
    source_digest: str
    scanned_trace_count: int
    eligible_sequence_count: int
    rejected_sequence_count: int
    cluster_count: int
    suggestions: tuple[ProcedureCandidate, ...]
    status: str
    created_at: str

    def as_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "scope": self.scope,
            "task_classes": list(self.task_classes),
            "config": self.config,
            "request_hash": self.request_hash,
            "source_digest": self.source_digest,
            "scanned_trace_count": self.scanned_trace_count,
            "eligible_sequence_count": self.eligible_sequence_count,
            "rejected_sequence_count": self.rejected_sequence_count,
            "cluster_count": self.cluster_count,
            "suggestion_count": len(self.suggestions),
            "suggestions": [item.as_dict() for item in self.suggestions],
            "status": self.status,
            "created_at": self.created_at,
        }


@dataclass(frozen=True)
class _Sequence:
    operations: tuple[str, ...]
    parameters: tuple[dict[str, object], ...]


class EmergentProcedureDetector:
    """Suggests repeated procedure skeletons without creating memory or skills."""

    def __init__(
        self,
        connection: sqlite3.Connection,
        *,
        mutation_guard: Callable[[str], None] | None = None,
    ) -> None:
        self.connection = connection
        self.mutation_guard = mutation_guard

    def detect(
        self, request: ProcedureDetectionRequest
    ) -> ProcedureDetectionRun:
        if self.mutation_guard is not None:
            self.mutation_guard("procedure_detection_write")
        rows = self._source_rows(request)
        source_digest = self._source_digest(rows)
        request_json = json.dumps(
            request.as_dict(), sort_keys=True, separators=(",", ":")
        )
        request_hash = hashlib.sha256(request_json.encode("utf-8")).hexdigest()
        existing = self.connection.execute(
            """
            SELECT id FROM procedure_detection_runs
            WHERE request_hash=? AND source_digest=?
            """,
            (request_hash, source_digest),
        ).fetchone()
        if existing is not None:
            return self.load(existing["id"])

        clusters: dict[
            tuple[str, tuple[str, ...]], list[dict[str, object]]
        ] = {}
        rejected = 0
        eligible = 0
        for row in rows:
            status, sequence = self._sequence(row["raw_trace_json"])
            if status != "valid" or sequence is None:
                if status == "rejected":
                    rejected += 1
                continue
            eligible += 1
            key = (row["task_class"], sequence.operations)
            clusters.setdefault(key, []).append(
                {
                    "trace_id": row["id"],
                    "task_id": row["task_id"],
                    "outcome": row["outcome"],
                    "significance": float(row["significance_score"]),
                    "parameters": sequence.parameters,
                }
            )

        suggestions = tuple(
            candidate
            for key, occurrences in sorted(
                clusters.items(), key=lambda item: (item[0][0], item[0][1])
            )
            if (
                candidate := self._candidate(
                    key, occurrences, request
                )
            )
            is not None
        )
        run = ProcedureDetectionRun(
            id=str(uuid.uuid4()),
            scope=request.scope,
            task_classes=request.task_classes,
            config={
                "observed_before": request.observed_before,
                "minimum_successes": request.minimum_successes,
                "minimum_distinct_tasks": request.minimum_distinct_tasks,
                "maximum_non_success_rate": request.maximum_non_success_rate,
                "minimum_significance": request.minimum_significance,
                "maximum_traces": request.maximum_traces,
                "sequence_schema": "operation_sequence_v1",
                "clustering": "exact_operation_skeleton",
            },
            request_hash=request_hash,
            source_digest=source_digest,
            scanned_trace_count=len(rows),
            eligible_sequence_count=eligible,
            rejected_sequence_count=rejected,
            cluster_count=len(clusters),
            suggestions=suggestions,
            status="completed",
            created_at=_now(),
        )
        stored_id = self._save(run)
        return run if stored_id == run.id else self.load(stored_id)

    def load(self, run_id: str) -> ProcedureDetectionRun:
        if not isinstance(run_id, str) or not _UUID.fullmatch(run_id):
            raise ProcedureDetectionError("run_id must be a UUID")
        row = self.connection.execute(
            "SELECT * FROM procedure_detection_runs WHERE id=?", (run_id,)
        ).fetchone()
        if row is None:
            raise ProcedureDetectionError(f"unknown procedure detection run: {run_id}")
        candidates = self.connection.execute(
            """
            SELECT * FROM procedure_detection_candidates
            WHERE run_id=?
            ORDER BY average_significance DESC, task_class, signature_hash
            """,
            (run_id,),
        ).fetchall()
        return ProcedureDetectionRun(
            id=row["id"],
            scope=row["scope"],
            task_classes=tuple(json.loads(row["task_classes_json"])),
            config=json.loads(row["config_json"]),
            request_hash=row["request_hash"],
            source_digest=row["source_digest"],
            scanned_trace_count=row["scanned_trace_count"],
            eligible_sequence_count=row["eligible_sequence_count"],
            rejected_sequence_count=row["rejected_sequence_count"],
            cluster_count=row["cluster_count"],
            suggestions=tuple(
                ProcedureCandidate(
                    id=item["id"],
                    task_class=item["task_class"],
                    signature_hash=item["signature_hash"],
                    operations=tuple(json.loads(item["operations_json"])),
                    variability=tuple(json.loads(item["variability_json"])),
                    success_count=item["success_count"],
                    non_success_count=item["non_success_count"],
                    distinct_task_count=item["distinct_task_count"],
                    average_significance=item["average_significance"],
                    support_trace_ids=tuple(
                        json.loads(item["support_trace_ids_json"])
                    ),
                    status=item["status"],
                )
                for item in candidates
            ),
            status=row["status"],
            created_at=row["created_at"],
        )

    def _source_rows(
        self, request: ProcedureDetectionRequest
    ) -> list[sqlite3.Row]:
        placeholders = ",".join("?" for _ in request.task_classes)
        rows = self.connection.execute(
            f"""
            SELECT id, task_id, task_class, outcome, significance_score,
                   raw_trace_json, created_at
            FROM experience_traces
            WHERE scope=?
              AND task_class IN ({placeholders})
              AND created_at <= ?
            ORDER BY created_at, id
            LIMIT ?
            """,
            (
                request.scope,
                *request.task_classes,
                request.observed_before,
                request.maximum_traces + 1,
            ),
        ).fetchall()
        if len(rows) > request.maximum_traces:
            raise ProcedureDetectionError(
                "matching traces exceed maximum_traces; narrow the request"
            )
        return list(rows)

    @staticmethod
    def _source_digest(rows: list[sqlite3.Row]) -> str:
        source = [
            {
                "id": row["id"],
                "task_id": row["task_id"],
                "task_class": row["task_class"],
                "outcome": row["outcome"],
                "significance_score": row["significance_score"],
                "raw_hash": hashlib.sha256(
                    row["raw_trace_json"].encode("utf-8")
                ).hexdigest(),
                "created_at": row["created_at"],
            }
            for row in rows
        ]
        encoded = json.dumps(source, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    @classmethod
    def _sequence(cls, raw_json: str) -> tuple[str, _Sequence | None]:
        try:
            raw = json.loads(raw_json)
            events = raw["events"]
            if not isinstance(events, list):
                return "rejected", None
        except (json.JSONDecodeError, KeyError, TypeError):
            return "rejected", None
        candidates = []
        malformed = False
        for event in events:
            if not isinstance(event, dict) or event.get("kind") not in {
                "procedure",
                "tool_sequence",
            }:
                continue
            try:
                metadata = json.loads(event.get("metadata_json", "{}"))
            except (json.JSONDecodeError, TypeError):
                malformed = True
                continue
            if not isinstance(metadata, dict) or "operation_sequence_v1" not in metadata:
                continue
            try:
                candidates.append(
                    cls._validate_operations(metadata["operation_sequence_v1"])
                )
            except ProcedureDetectionError:
                malformed = True
        if malformed or len(candidates) > 1:
            return "rejected", None
        if not candidates:
            return "absent", None
        return "valid", candidates[0]

    @staticmethod
    def _validate_operations(value: object) -> _Sequence:
        if not isinstance(value, list) or not 2 <= len(value) <= 64:
            raise ProcedureDetectionError(
                "operation_sequence_v1 must contain 2..64 steps"
            )
        operations: list[str] = []
        parameters: list[dict[str, object]] = []
        for step in value:
            if not isinstance(step, dict) or set(step) != {
                "operation",
                "parameters",
            }:
                raise ProcedureDetectionError(
                    "operation steps require operation and parameters only"
                )
            operation = step["operation"]
            values = step["parameters"]
            if not isinstance(operation, str) or not _NAME.fullmatch(operation):
                raise ProcedureDetectionError("operation name is invalid")
            if not isinstance(values, dict) or len(values) > 16:
                raise ProcedureDetectionError(
                    "operation parameters must be an object with at most 16 fields"
                )
            normalized: dict[str, object] = {}
            for name, parameter in values.items():
                if not isinstance(name, str) or not _PARAMETER.fullmatch(name):
                    raise ProcedureDetectionError("parameter name is invalid")
                if not (
                    parameter is None
                    or isinstance(parameter, (bool, int, float))
                    or isinstance(parameter, str)
                ):
                    raise ProcedureDetectionError("parameter values must be scalar")
                if isinstance(parameter, str) and len(parameter) > 256:
                    raise ProcedureDetectionError(
                        "string parameter exceeds 256 characters"
                    )
                normalized[name] = parameter
            operations.append(operation)
            parameters.append(normalized)
        return _Sequence(tuple(operations), tuple(parameters))

    @classmethod
    def _candidate(
        cls,
        key: tuple[str, tuple[str, ...]],
        occurrences: list[dict[str, object]],
        request: ProcedureDetectionRequest,
    ) -> ProcedureCandidate | None:
        successes = [
            item
            for item in occurrences
            if item["outcome"] == "succeeded"
            and item["significance"] >= request.minimum_significance
        ]
        non_successes = [
            item for item in occurrences if item["outcome"] != "succeeded"
        ]
        distinct_tasks = {
            item["task_id"] for item in successes if item["task_id"] is not None
        }
        total = len(successes) + len(non_successes)
        non_success_rate = len(non_successes) / max(1, total)
        if (
            len(successes) < request.minimum_successes
            or len(distinct_tasks) < request.minimum_distinct_tasks
            or non_success_rate > request.maximum_non_success_rate
        ):
            return None
        task_class, operations = key
        signature_source = json.dumps(
            {"task_class": task_class, "operations": operations},
            sort_keys=True,
            separators=(",", ":"),
        )
        return ProcedureCandidate(
            id=str(uuid.uuid4()),
            task_class=task_class,
            signature_hash=hashlib.sha256(
                signature_source.encode("utf-8")
            ).hexdigest(),
            operations=operations,
            variability=cls._variability(successes, operations),
            success_count=len(successes),
            non_success_count=len(non_successes),
            distinct_task_count=len(distinct_tasks),
            average_significance=round(
                sum(float(item["significance"]) for item in successes)
                / len(successes),
                6,
            ),
            support_trace_ids=tuple(
                sorted(str(item["trace_id"]) for item in successes)
            ),
        )

    @staticmethod
    def _value_type(value: object) -> str:
        if value is None:
            return "null"
        if isinstance(value, bool):
            return "boolean"
        if isinstance(value, int):
            return "integer"
        if isinstance(value, float):
            return "number"
        return "string"

    @classmethod
    def _variability(
        cls,
        successes: list[dict[str, object]],
        operations: tuple[str, ...],
    ) -> tuple[dict[str, object], ...]:
        steps = []
        occurrence_count = len(successes)
        for index, operation in enumerate(operations):
            parameter_sets = [
                item["parameters"][index] for item in successes
            ]
            names = sorted(
                {name for parameters in parameter_sets for name in parameters}
            )
            boundaries = []
            for name in names:
                present = [
                    parameters[name]
                    for parameters in parameter_sets
                    if name in parameters
                ]
                hashes = {
                    hashlib.sha256(
                        json.dumps(
                            value,
                            sort_keys=True,
                            separators=(",", ":"),
                        ).encode("utf-8")
                    ).hexdigest()
                    for value in present
                }
                classification = (
                    "optional"
                    if len(present) != occurrence_count
                    else "invariant"
                    if len(hashes) == 1
                    else "variable"
                )
                boundaries.append(
                    {
                        "name": name,
                        "classification": classification,
                        "observed_types": sorted(
                            {cls._value_type(value) for value in present}
                        ),
                        "distinct_value_count": len(hashes),
                        "present_count": len(present),
                        "occurrence_count": occurrence_count,
                    }
                )
            steps.append(
                {
                    "step": index + 1,
                    "operation": operation,
                    "parameters": boundaries,
                }
            )
        return tuple(steps)

    def _save(self, run: ProcedureDetectionRun) -> str:
        try:
            self.connection.execute("BEGIN IMMEDIATE")
            self.connection.execute(
                """
                INSERT INTO procedure_detection_runs(
                    id, scope, task_classes_json, config_json, request_hash,
                    source_digest, scanned_trace_count, eligible_sequence_count,
                    rejected_sequence_count, cluster_count, suggestion_count,
                    status, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'completed', ?)
                """,
                (
                    run.id,
                    run.scope,
                    json.dumps(run.task_classes, separators=(",", ":")),
                    json.dumps(run.config, sort_keys=True, separators=(",", ":")),
                    run.request_hash,
                    run.source_digest,
                    run.scanned_trace_count,
                    run.eligible_sequence_count,
                    run.rejected_sequence_count,
                    run.cluster_count,
                    len(run.suggestions),
                    run.created_at,
                ),
            )
            self.connection.executemany(
                """
                INSERT INTO procedure_detection_candidates(
                    id, run_id, signature_hash, task_class, operations_json,
                    variability_json, success_count, non_success_count,
                    distinct_task_count, average_significance,
                    support_trace_ids_json, status, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'suggested', ?)
                """,
                (
                    (
                        item.id,
                        run.id,
                        item.signature_hash,
                        item.task_class,
                        json.dumps(item.operations, separators=(",", ":")),
                        json.dumps(
                            item.variability,
                            sort_keys=True,
                            separators=(",", ":"),
                        ),
                        item.success_count,
                        item.non_success_count,
                        item.distinct_task_count,
                        item.average_significance,
                        json.dumps(
                            item.support_trace_ids, separators=(",", ":")
                        ),
                        run.created_at,
                    )
                    for item in run.suggestions
                ),
            )
            self.connection.commit()
            return run.id
        except sqlite3.IntegrityError:
            self.connection.rollback()
            existing = self.connection.execute(
                """
                SELECT id FROM procedure_detection_runs
                WHERE request_hash=? AND source_digest=?
                """,
                (run.request_hash, run.source_digest),
            ).fetchone()
            if existing is None:
                raise
            return existing["id"]
        except Exception:
            self.connection.rollback()
            raise
