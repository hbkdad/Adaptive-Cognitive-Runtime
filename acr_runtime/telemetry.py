from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone
from typing import Any

from .db import RuntimeDB
from .execution import TaskEvent, TaskRun
from .providers.base import ModelCallRecord

SECRET_PATTERNS = (
    re.compile(r"\bsk-[A-Za-z0-9_-]{8,}\b"),
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{8,}"),
    re.compile(
        r"(?i)\b(api[_-]?key|key|access[_-]?token|secret|password)\s*[:=]\s*"
        r"[^\s,;\"']+"
    ),
)


def redact_text(value: str) -> str:
    redacted = value
    for pattern in SECRET_PATTERNS:
        redacted = pattern.sub("[REDACTED]", redacted)
    return redacted


def _redact_value(value: Any) -> Any:
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, list):
        return [_redact_value(item) for item in value]
    if isinstance(value, dict):
        return {
            key: "[REDACTED]"
            if any(marker in key.lower() for marker in ("key", "token", "secret", "password"))
            else _redact_value(item)
            for key, item in value.items()
        }
    return value


def sanitize_payload_json(payload_json: str) -> str:
    try:
        payload = json.loads(payload_json)
    except json.JSONDecodeError:
        return json.dumps({"unparsed": redact_text(payload_json)}, sort_keys=True)
    return json.dumps(_redact_value(payload), sort_keys=True)


class TelemetryRecorder:
    """Persists task events without coupling the execution engine to SQLite."""

    def __init__(self, database: RuntimeDB) -> None:
        self.database = database

    def __call__(self, event: TaskEvent) -> None:
        payload_json = sanitize_payload_json(event.payload_json)
        payload = json.loads(payload_json)
        self.database.record_telemetry_event(
            event_id=str(uuid.uuid4()),
            sequence=event.sequence,
            category="task",
            event_type=event.event_type,
            task_id=event.task_id,
            run_id=event.run_id,
            step_id=payload.get("step_id"),
            status=event.state.value,
            payload_json=payload_json,
            created_at=event.created_at,
        )

    def record_run(self, run: TaskRun) -> None:
        started = datetime.fromisoformat(run.started_at)
        completed = datetime.fromisoformat(run.completed_at)
        duration_ms = max(
            0, int((completed - started).total_seconds() * 1_000)
        )
        self.database.record_execution_run(
            run_id=run.id,
            task_id=run.task_id,
            state=run.state.value,
            event_count=len(run.events),
            step_count=len(run.steps),
            action_count=len(run.actions),
            duration_ms=duration_ms,
            verification_score=(
                run.verification.score if run.verification is not None else None
            ),
            evaluation_score=(
                run.evaluation.score if run.evaluation is not None else None
            ),
            failure_kind=run.failure.kind if run.failure is not None else None,
            started_at=run.started_at,
            completed_at=run.completed_at,
        )

    def record_model_call(self, record: ModelCallRecord) -> None:
        self.database.record_telemetry_event(
            event_id=str(uuid.uuid4()),
            sequence=None,
            category="model",
            event_type=f"model.{record.operation}",
            task_id=record.task_id,
            run_id=None,
            step_id=record.step_id,
            provider=record.provider,
            model=record.model,
            input_tokens=record.input_tokens,
            output_tokens=record.output_tokens,
            cached_tokens=record.cached_tokens,
            estimated_cost=record.estimated_cost,
            latency_ms=record.latency_ms,
            status=record.status,
            context_bundle_id=record.context_bundle_id,
            skills_json=json.dumps(record.loaded_skill_ids),
            memories_json=json.dumps(record.loaded_memory_ids),
            payload_json=json.dumps(
                {"error_kind": record.error_kind} if record.error_kind else {},
                sort_keys=True,
            ),
            created_at=datetime.now(timezone.utc).isoformat(),
        )
