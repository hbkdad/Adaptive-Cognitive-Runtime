from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime, timezone
from time import perf_counter_ns
from typing import Callable, Iterator


PROFILE_CATEGORIES = (
    "database_queries",
    "retrieval_latency",
    "embedding_latency",
    "model_wait",
    "tool_latency",
    "context_compilation",
    "serialization",
)
_CATEGORY_SET = frozenset(PROFILE_CATEGORIES)
_OPERATION = re.compile(r"^[a-z][a-z0-9_.:-]{1,127}$")
_ERROR_TYPE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]{0,127}$")
_SQL_OPERATIONS = frozenset(
    {
        "alter",
        "analyze",
        "attach",
        "begin",
        "commit",
        "create",
        "delete",
        "detach",
        "drop",
        "explain",
        "insert",
        "pragma",
        "reindex",
        "release",
        "replace",
        "rollback",
        "savepoint",
        "select",
        "update",
        "vacuum",
        "values",
        "with",
    }
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _validate_operation(value: str) -> str:
    if not isinstance(value, str) or not _OPERATION.fullmatch(value):
        raise ValueError("Profiler operation must be a bounded low-cardinality ID")
    return value


@dataclass(frozen=True)
class ProfileMeasurement:
    sequence: int
    category: str
    operation: str
    duration_ns: int
    status: str
    error_type: str | None
    created_at: str


@dataclass
class _Capture:
    measurements: list[ProfileMeasurement] = field(default_factory=list)

    def record(
        self,
        category: str,
        operation: str,
        duration_ns: int,
        *,
        status: str,
        error_type: str | None,
    ) -> None:
        if len(self.measurements) >= 100_000:
            raise RuntimeError("Profiler measurement limit reached")
        if category not in _CATEGORY_SET:
            raise ValueError("Unknown profiler category")
        _validate_operation(operation)
        if (
            isinstance(duration_ns, bool)
            or not isinstance(duration_ns, int)
            or not 0 <= duration_ns <= 9_223_372_036_854_775_807
        ):
            raise ValueError("Profiler duration must be non-negative nanoseconds")
        if status not in {"succeeded", "failed"}:
            raise ValueError("Profiler status is invalid")
        if (
            status == "succeeded"
            and error_type is not None
            or status == "failed"
            and (
                not isinstance(error_type, str)
                or not _ERROR_TYPE.fullmatch(error_type)
            )
        ):
            raise ValueError("Profiler error type does not match status")
        self.measurements.append(
            ProfileMeasurement(
                sequence=len(self.measurements) + 1,
                category=category,
                operation=operation,
                duration_ns=duration_ns,
                status=status,
                error_type=error_type,
                created_at=_now(),
            )
        )


_ACTIVE_CAPTURE: ContextVar[_Capture | None] = ContextVar(
    "acr_active_performance_capture", default=None
)


@contextmanager
def profile_operation(category: str, operation: str) -> Iterator[None]:
    """Measure a runtime boundary only while an explicit capture is active."""

    capture = _ACTIVE_CAPTURE.get()
    if capture is None:
        yield
        return
    if category not in _CATEGORY_SET:
        raise ValueError("Unknown profiler category")
    _validate_operation(operation)
    started = perf_counter_ns()
    try:
        yield
    except BaseException as error:
        capture.record(
            category,
            operation,
            max(0, perf_counter_ns() - started),
            status="failed",
            error_type=type(error).__name__,
        )
        raise
    else:
        capture.record(
            category,
            operation,
            max(0, perf_counter_ns() - started),
            status="succeeded",
            error_type=None,
        )


def observe_duration(
    category: str,
    operation: str,
    duration_ns: int,
    *,
    status: str = "succeeded",
    error_type: str | None = None,
) -> None:
    """Record an adapter-reported duration in the active capture, if any."""

    capture = _ACTIVE_CAPTURE.get()
    if capture is None:
        return
    if status not in {"succeeded", "failed"}:
        raise ValueError("Profiler status is invalid")
    capture.record(
        category,
        operation,
        duration_ns,
        status=status,
        error_type=error_type,
    )


def _database_operation(sql: object) -> str:
    if not isinstance(sql, str):
        return "sqlite.unknown"
    keyword = next(
        (
            word.lower()
            for word in re.findall(r"[A-Za-z]+", sql[:1_000])
            if word.lower() in _SQL_OPERATIONS
        ),
        "unknown",
    )
    return f"sqlite.{keyword}"


class ProfiledConnection(sqlite3.Connection):
    """SQLite connection that times calls without retaining SQL or parameters."""

    def _timed(self, operation: str, call: Callable[[], object]) -> object:
        capture = _ACTIVE_CAPTURE.get()
        if capture is None:
            return call()
        started = perf_counter_ns()
        try:
            result = call()
        except BaseException as error:
            capture.record(
                "database_queries",
                operation,
                max(0, perf_counter_ns() - started),
                status="failed",
                error_type=type(error).__name__,
            )
            raise
        capture.record(
            "database_queries",
            operation,
            max(0, perf_counter_ns() - started),
            status="succeeded",
            error_type=None,
        )
        return result

    def execute(self, sql: str, parameters: object = (), /) -> sqlite3.Cursor:
        return self._timed(  # type: ignore[return-value]
            _database_operation(sql),
            lambda: super(ProfiledConnection, self).execute(sql, parameters),
        )

    def executemany(
        self, sql: str, parameters: object, /
    ) -> sqlite3.Cursor:
        return self._timed(  # type: ignore[return-value]
            f"{_database_operation(sql)}.many",
            lambda: super(ProfiledConnection, self).executemany(sql, parameters),
        )

    def executescript(self, sql_script: str, /) -> sqlite3.Cursor:
        return self._timed(  # type: ignore[return-value]
            "sqlite.script",
            lambda: super(ProfiledConnection, self).executescript(sql_script),
        )


class ProfileSession:
    def __init__(self, capture: _Capture) -> None:
        self._capture = capture
        self.run_id: str | None = None

    def measure(self, category: str, operation: str):
        return profile_operation(category, operation)

    def observe(
        self,
        category: str,
        operation: str,
        duration_ns: int,
        *,
        status: str = "succeeded",
        error_type: str | None = None,
    ) -> None:
        self._capture.record(
            category,
            operation,
            duration_ns,
            status=status,
            error_type=error_type,
        )

    def serialize(self, value: object, *, operation: str = "json.dumps") -> str:
        with self.measure("serialization", operation):
            return json.dumps(
                value,
                sort_keys=True,
                ensure_ascii=False,
                allow_nan=False,
            )


class PerformanceProfiler:
    """Opt-in, local-only profiler with immutable content-minimized results."""

    def __init__(
        self,
        connection: sqlite3.Connection,
        *,
        minimum_bottleneck_samples: int = 5,
        minimum_bottleneck_mean_ns: int = 10_000_000,
    ) -> None:
        if (
            isinstance(minimum_bottleneck_samples, bool)
            or not isinstance(minimum_bottleneck_samples, int)
            or not 2 <= minimum_bottleneck_samples <= 100
        ):
            raise ValueError("minimum_bottleneck_samples must be 2..100")
        if (
            isinstance(minimum_bottleneck_mean_ns, bool)
            or not isinstance(minimum_bottleneck_mean_ns, int)
            or not 1_000_000 <= minimum_bottleneck_mean_ns <= 60_000_000_000
        ):
            raise ValueError(
                "minimum_bottleneck_mean_ns must be 1ms..60s"
            )
        self.connection = connection
        self.minimum_bottleneck_samples = minimum_bottleneck_samples
        self.minimum_bottleneck_mean_ns = minimum_bottleneck_mean_ns

    @contextmanager
    def capture(self, label: str, *, scope: str = "global") -> Iterator[ProfileSession]:
        if not isinstance(label, str) or not 1 <= len(label.strip()) <= 512:
            raise ValueError("Profiler label must be bounded non-empty text")
        if not isinstance(scope, str) or not 1 <= len(scope.strip()) <= 512:
            raise ValueError("Profiler scope must be bounded non-empty text")
        if _ACTIVE_CAPTURE.get() is not None:
            raise RuntimeError("Nested performance captures are not supported")
        capture = _Capture()
        session = ProfileSession(capture)
        started_at = _now()
        token = _ACTIVE_CAPTURE.set(capture)
        status = "completed"
        try:
            yield session
        except BaseException:
            status = "failed"
            raise
        finally:
            _ACTIVE_CAPTURE.reset(token)
            session.run_id = self._persist(
                label_hash=_hash(label.strip()),
                scope_hash=_hash(scope.strip()),
                status=status,
                started_at=started_at,
                completed_at=_now(),
                measurements=capture.measurements,
            )

    def _persist(
        self,
        *,
        label_hash: str,
        scope_hash: str,
        status: str,
        started_at: str,
        completed_at: str,
        measurements: list[ProfileMeasurement],
    ) -> str:
        run_id = str(uuid.uuid4())
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO performance_profile_runs(
                    id, label_hash, scope_hash, status, measurement_count,
                    clock, started_at, completed_at
                ) VALUES (?, ?, ?, ?, ?, 'perf_counter_ns', ?, ?)
                """,
                (
                    run_id,
                    label_hash,
                    scope_hash,
                    status,
                    len(measurements),
                    started_at,
                    completed_at,
                ),
            )
            self.connection.executemany(
                """
                INSERT INTO performance_measurements(
                    id, run_id, sequence, category, operation, duration_ns,
                    status, error_type, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    (
                        str(uuid.uuid4()),
                        run_id,
                        item.sequence,
                        item.category,
                        item.operation,
                        item.duration_ns,
                        item.status,
                        item.error_type,
                        item.created_at,
                    )
                    for item in measurements
                ),
            )
        return run_id

    @staticmethod
    def _percentile(values: list[int], percentile: float) -> int:
        ordered = sorted(values)
        index = max(0, math.ceil(percentile * len(ordered)) - 1)
        return ordered[index]

    def report(self, run_id: str) -> dict[str, object]:
        if not isinstance(run_id, str) or not 1 <= len(run_id) <= 128:
            raise ValueError("Profiler run ID is invalid")
        run = self.connection.execute(
            "SELECT * FROM performance_profile_runs WHERE id=?", (run_id,)
        ).fetchone()
        if run is None:
            raise LookupError(f"Unknown performance profile: {run_id}")
        rows = self.connection.execute(
            """
            SELECT category, operation, duration_ns, status, error_type
            FROM performance_measurements
            WHERE run_id=?
            ORDER BY sequence
            """,
            (run_id,),
        ).fetchall()
        category_values: dict[str, list[int]] = {
            category: [] for category in PROFILE_CATEGORIES
        }
        failures: dict[str, int] = {
            category: 0 for category in PROFILE_CATEGORIES
        }
        for row in rows:
            category_values[row["category"]].append(int(row["duration_ns"]))
            failures[row["category"]] += int(row["status"] == "failed")
        categories: dict[str, dict[str, object]] = {}
        for category in PROFILE_CATEGORIES:
            values = category_values[category]
            categories[category] = {
                "measured": bool(values),
                "sample_count": len(values),
                "failed_samples": failures[category],
                "total_ns": sum(values),
                "mean_ns": (
                    round(sum(values) / len(values)) if values else None
                ),
                "p50_ns": self._percentile(values, 0.50) if values else None,
                "p95_ns": self._percentile(values, 0.95) if values else None,
                "max_ns": max(values) if values else None,
            }
        eligible = [
            category
            for category, values in category_values.items()
            if len(values) >= self.minimum_bottleneck_samples
            and sum(values) / len(values)
            >= self.minimum_bottleneck_mean_ns
        ]
        bottlenecks: list[dict[str, object]] = []
        if eligible:
            maximum = max(sum(category_values[item]) for item in eligible)
            for category in eligible:
                total = sum(category_values[category])
                if total == maximum and maximum > 0:
                    bottlenecks.append(
                        {
                            "category": category,
                            "sample_count": len(category_values[category]),
                            "total_ns": total,
                            "basis": "largest repeated measured category total",
                        }
                    )
        return {
            "id": run["id"],
            "label_hash": run["label_hash"],
            "scope_hash": run["scope_hash"],
            "status": run["status"],
            "measurement_count": int(run["measurement_count"]),
            "clock": run["clock"],
            "started_at": run["started_at"],
            "completed_at": run["completed_at"],
            "categories": categories,
            "bottlenecks": bottlenecks,
            "optimization_allowed": bool(bottlenecks),
            "bottleneck_thresholds": {
                "minimum_samples": self.minimum_bottleneck_samples,
                "minimum_mean_ns": self.minimum_bottleneck_mean_ns,
            },
            "overlapping_spans": True,
            "distributed_tracing_enabled": False,
        }

    def list(self, *, limit: int = 50) -> list[dict[str, object]]:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 200:
            raise ValueError("Profiler list limit must be 1..200")
        rows = self.connection.execute(
            """
            SELECT id, label_hash, scope_hash, status, measurement_count,
                   clock, started_at, completed_at
            FROM performance_profile_runs
            ORDER BY completed_at DESC, id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [dict(row) for row in rows]
