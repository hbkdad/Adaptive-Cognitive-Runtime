from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from .db import RuntimeDB
from .execution import TaskEvent, TaskRun
from .providers.base import ModelCallRecord
from .secret_management import (
    redact_secret_text,
    redact_secret_value,
    sanitize_secret_json,
)


def redact_text(value: str) -> str:
    return redact_secret_text(value)


def _redact_value(value: Any) -> Any:
    return redact_secret_value(value)


def sanitize_payload_json(payload_json: str) -> str:
    return sanitize_secret_json(payload_json)


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
            commit=False,
        )
        try:
            self._record_model_cost(record)
        except BaseException:
            self.database.connection.rollback()
            raise

    def _record_model_cost(self, record: ModelCallRecord) -> None:
        from .cost_accounting import CostAccounting

        accounting_task_id = record.task_id
        if (
            accounting_task_id is not None
            and self.database.connection.execute(
                "SELECT 1 FROM tasks WHERE id=?", (accounting_task_id,)
            ).fetchone() is None
        ):
            accounting_task_id = None
        accounting_skill_ids = (
            record.loaded_skill_ids
            if accounting_task_id is not None else ()
        )
        accounting = CostAccounting(self.database.connection)
        if record.local:
            accounting.record_local(
                attempt_id=record.attempt_id,
                provider=record.provider,
                model=record.model,
                duration_ms=record.latency_ms,
                task_id=accounting_task_id,
                call_status=record.status,
                skill_ids=accounting_skill_ids,
            )
            return
        accounting.record_model_from_adapter(
            attempt_id=record.attempt_id,
            provider=record.provider,
            model=record.model,
            operation=record.operation,
            input_tokens=record.input_tokens,
            output_tokens=record.output_tokens,
            cache_read_tokens=record.cached_tokens,
            cache_write_tokens=record.cache_write_tokens,
            task_id=accounting_task_id,
            call_status=record.status,
            usage_quality=(
                "estimated" if record.usage_estimated
                else "provider_reported"
            ),
            skill_ids=accounting_skill_ids,
        )
