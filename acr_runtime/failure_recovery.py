from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from dataclasses import dataclass
from enum import Enum
from typing import Protocol, Sequence

from .memory import utc_now
from .secret_management import assert_secret_free
from .tool_registry import TOOL_ID

MAX_INPUT_BYTES = 64_000
MAX_ATTEMPTS = 10


class ActionClass(str, Enum):
    IDEMPOTENT = "idempotent"
    RETRYABLE = "retryable"
    NON_RETRYABLE = "non-retryable"
    HUMAN_REVIEW_REQUIRED = "human-review-required"


class RecoveryConflict(RuntimeError):
    pass


@dataclass(frozen=True)
class RecoveryStep:
    sequence: int
    operation: str
    input_json: str
    action_class: ActionClass
    idempotency_key: str
    destructive: bool = False
    max_attempts: int = 3

    def __post_init__(self) -> None:
        if (
            isinstance(self.sequence, bool)
            or not isinstance(self.sequence, int)
            or self.sequence < 1
        ):
            raise ValueError("Recovery step sequence must be positive")
        if not isinstance(self.operation, str) or not TOOL_ID.fullmatch(
            self.operation
        ):
            raise ValueError("Recovery step operation is invalid")
        if (
            not isinstance(self.idempotency_key, str)
            or not TOOL_ID.fullmatch(self.idempotency_key)
            or len(self.idempotency_key) > 128
        ):
            raise ValueError("Recovery step idempotency key is invalid")
        if not isinstance(self.input_json, str):
            raise ValueError("Recovery step input must be JSON text")
        if len(self.input_json.encode("utf-8")) > MAX_INPUT_BYTES:
            raise ValueError("Recovery step input exceeds 64 KB")
        try:
            payload = json.loads(self.input_json)
        except json.JSONDecodeError as exc:
            raise ValueError("Recovery step input must be JSON") from exc
        if not isinstance(payload, dict):
            raise ValueError("Recovery step input must be a JSON object")
        assert_secret_free(self.input_json, "recovery step input")
        if type(self.destructive) is not bool:
            raise ValueError("destructive must be a boolean")
        if not isinstance(self.action_class, ActionClass):
            raise ValueError("Unknown recovery action class")
        if self.destructive and self.action_class not in (
            ActionClass.NON_RETRYABLE,
            ActionClass.HUMAN_REVIEW_REQUIRED,
        ):
            raise ValueError(
                "Destructive steps must be non-retryable or human-review-required"
            )
        if (
            isinstance(self.max_attempts, bool)
            or not isinstance(self.max_attempts, int)
            or not 1 <= self.max_attempts <= MAX_ATTEMPTS
        ):
            raise ValueError(
                f"Recovery max_attempts must be 1..{MAX_ATTEMPTS}"
            )
        if (
            self.action_class in (
                ActionClass.NON_RETRYABLE,
                ActionClass.HUMAN_REVIEW_REQUIRED,
            )
            and self.max_attempts != 1
        ):
            raise ValueError(
                "Non-retryable and human-review steps allow one automatic attempt"
            )

    @property
    def input_hash(self) -> str:
        return hashlib.sha256(self.input_json.encode("utf-8")).hexdigest()

    def as_dict(self) -> dict[str, object]:
        return {
            "sequence": self.sequence,
            "operation": self.operation,
            "input": json.loads(self.input_json),
            "action_class": self.action_class.value,
            "idempotency_key": self.idempotency_key,
            "destructive": self.destructive,
            "max_attempts": self.max_attempts,
        }

    @classmethod
    def from_dict(cls, payload: object) -> "RecoveryStep":
        required = {
            "sequence",
            "operation",
            "input",
            "action_class",
            "idempotency_key",
            "destructive",
            "max_attempts",
        }
        if not isinstance(payload, dict) or set(payload) != required:
            raise ValueError(
                f"Recovery step must contain {sorted(required)} only"
            )
        if not isinstance(payload["input"], dict):
            raise ValueError("Recovery step input must be an object")
        if not isinstance(payload["destructive"], bool):
            raise ValueError("destructive must be a boolean")
        try:
            action_class = ActionClass(str(payload["action_class"]))
        except ValueError as exc:
            raise ValueError("Unknown recovery action class") from exc
        return cls(
            sequence=int(payload["sequence"]),
            operation=str(payload["operation"]),
            input_json=json.dumps(
                payload["input"], sort_keys=True, separators=(",", ":")
            ),
            action_class=action_class,
            idempotency_key=str(payload["idempotency_key"]),
            destructive=payload["destructive"],
            max_attempts=int(payload["max_attempts"]),
        )


@dataclass(frozen=True)
class RecoveryOutput:
    output_json: str
    evidence: tuple[str, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.output_json, str):
            raise ValueError("Recovery output must be JSON text")
        try:
            payload = json.loads(self.output_json)
        except json.JSONDecodeError as exc:
            raise ValueError("Recovery output must be JSON") from exc
        if not isinstance(payload, dict):
            raise ValueError("Recovery output must be a JSON object")
        if len(self.output_json.encode("utf-8")) > MAX_INPUT_BYTES:
            raise ValueError("Recovery output exceeds 64 KB")
        assert_secret_free(self.output_json, "recovery output")
        if (
            not isinstance(self.evidence, tuple)
            or not 1 <= len(self.evidence) <= 64
            or any(
                not isinstance(item, str)
                or not item.strip()
                or item != item.strip()
                or len(item) > 512
                for item in self.evidence
            )
        ):
            raise ValueError("Recovery output requires bounded evidence")
        for item in self.evidence:
            assert_secret_free(item, "recovery output evidence")


class RecoveryExecutor(Protocol):
    def execute(self, step: RecoveryStep) -> RecoveryOutput: ...


class FailureRecovery:
    """Durable step checkpoints with conservative interruption semantics."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    @staticmethod
    def _plan_hash(task_id: str, steps: Sequence[RecoveryStep]) -> str:
        payload = {
            "task_id": task_id,
            "steps": [step.as_dict() for step in steps],
        }
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode(
                "utf-8"
            )
        ).hexdigest()

    def _event(
        self,
        run_id: str,
        event: str,
        *,
        step_sequence: int | None = None,
        details: dict[str, object] | None = None,
    ) -> None:
        sequence = int(
            self.connection.execute(
                """
                SELECT COALESCE(MAX(sequence), 0) + 1
                FROM recovery_events
                WHERE run_id=?
                """,
                (run_id,),
            ).fetchone()[0]
        )
        self.connection.execute(
            """
            INSERT INTO recovery_events (
                id, run_id, sequence, step_sequence, event,
                details_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(uuid.uuid4()),
                run_id,
                sequence,
                step_sequence,
                event,
                json.dumps(details or {}, sort_keys=True),
                utc_now(),
            ),
        )

    def create(
        self, task_id: str, steps: Sequence[RecoveryStep]
    ) -> dict[str, object]:
        if (
            not isinstance(task_id, str)
            or not isinstance(steps, (tuple, list))
            or not TOOL_ID.fullmatch(task_id)
            or not 1 <= len(steps) <= 256
            or any(not isinstance(step, RecoveryStep) for step in steps)
            or [step.sequence for step in steps]
            != list(range(1, len(steps) + 1))
        ):
            raise ValueError(
                "Recovery plan requires a valid task and contiguous steps"
            )
        if len({step.idempotency_key for step in steps}) != len(steps):
            raise ValueError("Recovery idempotency keys must be unique")
        plan_hash = self._plan_hash(task_id, steps)
        existing = self.connection.execute(
            "SELECT id FROM recovery_runs WHERE plan_hash=?",
            (plan_hash,),
        ).fetchone()
        if existing is not None:
            return self.get(existing["id"])
        run_id = str(uuid.uuid4())
        now = utc_now()
        try:
            self.connection.execute(
                """
                INSERT INTO recovery_runs (
                    id, task_id, plan_hash, status, current_sequence,
                    created_at, updated_at
                ) VALUES (?, ?, ?, 'planned', 1, ?, ?)
                """,
                (run_id, task_id, plan_hash, now, now),
            )
            for step in steps:
                self.connection.execute(
                    """
                    INSERT INTO recovery_steps (
                        run_id, sequence, operation, input_json, input_hash,
                        action_class, idempotency_key, destructive,
                        max_attempts, state, attempt_count
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', 0)
                    """,
                    (
                        run_id,
                        step.sequence,
                        step.operation,
                        step.input_json,
                        step.input_hash,
                        step.action_class.value,
                        step.idempotency_key,
                        step.destructive,
                        step.max_attempts,
                    ),
                )
            self._event(
                run_id,
                "plan.created",
                details={"plan_hash": plan_hash, "step_count": len(steps)},
            )
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise
        return self.get(run_id)

    @staticmethod
    def _step_from_row(row: sqlite3.Row) -> RecoveryStep:
        return RecoveryStep(
            sequence=int(row["sequence"]),
            operation=str(row["operation"]),
            input_json=str(row["input_json"]),
            action_class=ActionClass(str(row["action_class"])),
            idempotency_key=str(row["idempotency_key"]),
            destructive=bool(row["destructive"]),
            max_attempts=int(row["max_attempts"]),
        )

    def _step_result(self, row: sqlite3.Row) -> dict[str, object]:
        return {
            "sequence": int(row["sequence"]),
            "operation": row["operation"],
            "input_hash": row["input_hash"],
            "action_class": row["action_class"],
            "idempotency_key": row["idempotency_key"],
            "destructive": bool(row["destructive"]),
            "max_attempts": int(row["max_attempts"]),
            "state": row["state"],
            "attempt_count": int(row["attempt_count"]),
            "output": (
                None
                if row["output_json"] is None
                else json.loads(row["output_json"])
            ),
            "evidence": json.loads(row["evidence_json"] or "[]"),
            "error_kind": row["error_kind"],
            "error_hash": row["error_hash"],
            "started_at": row["started_at"],
            "completed_at": row["completed_at"],
        }

    def get(self, run_id: str) -> dict[str, object]:
        row = self.connection.execute(
            "SELECT * FROM recovery_runs WHERE id=?", (run_id,)
        ).fetchone()
        if row is None:
            raise LookupError(f"Unknown recovery run: {run_id}")
        steps = self.connection.execute(
            """
            SELECT * FROM recovery_steps
            WHERE run_id=?
            ORDER BY sequence
            """,
            (run_id,),
        ).fetchall()
        events = self.connection.execute(
            """
            SELECT sequence, step_sequence, event, details_json, created_at
            FROM recovery_events
            WHERE run_id=?
            ORDER BY sequence
            """,
            (run_id,),
        ).fetchall()
        return {
            "id": row["id"],
            "task_id": row["task_id"],
            "plan_hash": row["plan_hash"],
            "status": row["status"],
            "current_sequence": int(row["current_sequence"]),
            "steps": [self._step_result(step) for step in steps],
            "events": [
                {
                    "sequence": int(event["sequence"]),
                    "step_sequence": event["step_sequence"],
                    "event": event["event"],
                    "details": json.loads(event["details_json"]),
                    "created_at": event["created_at"],
                }
                for event in events
            ],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def mark_interrupted(
        self,
        run_id: str,
        *,
        actor: str,
        reason: str,
        evidence: Sequence[str],
    ) -> dict[str, object]:
        self._review_fields(actor, reason, evidence)
        run = self.connection.execute(
            "SELECT status FROM recovery_runs WHERE id=?", (run_id,)
        ).fetchone()
        if run is None:
            raise LookupError(f"Unknown recovery run: {run_id}")
        if run["status"] != "running":
            raise RecoveryConflict(
                "Only a run checkpointed as running can be marked interrupted"
            )
        step = self.connection.execute(
            """
            SELECT * FROM recovery_steps
            WHERE run_id=? AND state='running'
            """,
            (run_id,),
        ).fetchone()
        if step is None:
            raise RecoveryConflict("Running recovery has no claimed step")
        action_class = ActionClass(step["action_class"])
        safe_replay = action_class is ActionClass.IDEMPOTENT
        state = "failed" if safe_replay else "review_required"
        status = "interrupted" if safe_replay else "blocked"
        now = utc_now()
        try:
            self.connection.execute(
                """
                UPDATE recovery_steps
                SET state=?, error_kind='Interrupted',
                    error_hash=?, completed_at=?
                WHERE run_id=? AND sequence=? AND state='running'
                """,
                (
                    state,
                    hashlib.sha256(reason.encode("utf-8")).hexdigest(),
                    now,
                    run_id,
                    step["sequence"],
                ),
            )
            self.connection.execute(
                """
                UPDATE recovery_runs
                SET status=?, current_sequence=?, updated_at=?
                WHERE id=?
                """,
                (status, step["sequence"], now, run_id),
            )
            self._event(
                run_id,
                "run.interrupted",
                step_sequence=int(step["sequence"]),
                details={
                    "actor": actor,
                    "reason_hash": hashlib.sha256(
                        reason.encode("utf-8")
                    ).hexdigest(),
                    "evidence": list(evidence),
                    "safe_replay": safe_replay,
                },
            )
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise
        return self.get(run_id)

    @staticmethod
    def _review_fields(
        actor: str, reason: str, evidence: Sequence[str]
    ) -> None:
        for name, value, limit in (
            ("actor", actor, 255),
            ("reason", reason, 2_000),
        ):
            if (
                not isinstance(value, str)
                or not value.strip()
                or value != value.strip()
                or len(value) > limit
            ):
                raise ValueError(f"Recovery {name} is invalid")
            assert_secret_free(value, f"recovery {name}")
        if (
            not isinstance(evidence, (tuple, list))
            or isinstance(evidence, (str, bytes))
            or not 1 <= len(evidence) <= 64
            or any(
                not isinstance(item, str)
                or not item.strip()
                or item != item.strip()
                or len(item) > 512
                for item in evidence
            )
        ):
            raise ValueError("Recovery review requires bounded evidence")
        for item in evidence:
            assert_secret_free(item, "recovery review evidence")

    def resolve_review(
        self,
        run_id: str,
        sequence: int,
        decision: str,
        *,
        actor: str,
        reason: str,
        evidence: Sequence[str],
    ) -> dict[str, object]:
        if decision not in ("execute", "accept_completed", "abort"):
            raise ValueError("Unknown recovery review decision")
        self._review_fields(actor, reason, evidence)
        step = self.connection.execute(
            """
            SELECT *
            FROM recovery_steps
            WHERE run_id=? AND sequence=?
            """,
            (run_id, sequence),
        ).fetchone()
        if step is None:
            raise LookupError("Unknown recovery step")
        initial_review = (
            step["state"] == "pending"
            and step["action_class"] == ActionClass.HUMAN_REVIEW_REQUIRED.value
        )
        if step["state"] != "review_required" and not initial_review:
            raise RecoveryConflict("Recovery step does not require review")
        if decision == "accept_completed" and step["error_kind"] != "Interrupted":
            raise RecoveryConflict(
                "Only an interrupted ambiguous step can be accepted as completed"
            )
        now = utc_now()
        reason_hash = hashlib.sha256(reason.encode("utf-8")).hexdigest()
        try:
            if decision == "execute":
                self.connection.execute(
                    """
                    UPDATE recovery_steps
                    SET state='pending', review_approved=1,
                        error_kind=NULL, error_hash=NULL, completed_at=NULL
                    WHERE run_id=? AND sequence=?
                    """,
                    (run_id, sequence),
                )
                run_status = "interrupted"
            elif decision == "accept_completed":
                self.connection.execute(
                    """
                    UPDATE recovery_steps
                    SET state='completed', output_json='{}',
                        evidence_json=?, completed_at=?
                    WHERE run_id=? AND sequence=?
                    """,
                    (json.dumps(tuple(evidence)), now, run_id, sequence),
                )
                run_status = "interrupted"
            else:
                self.connection.execute(
                    """
                    UPDATE recovery_steps
                    SET state='failed', completed_at=?
                    WHERE run_id=? AND sequence=?
                    """,
                    (now, run_id, sequence),
                )
                run_status = "failed"
            self.connection.execute(
                """
                UPDATE recovery_runs
                SET status=?, current_sequence=?, updated_at=?
                WHERE id=?
                """,
                (run_status, sequence, now, run_id),
            )
            self._event(
                run_id,
                "step.reviewed",
                step_sequence=sequence,
                details={
                    "decision": decision,
                    "actor": actor,
                    "reason_hash": reason_hash,
                    "evidence": list(evidence),
                },
            )
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise
        return self.get(run_id)

    def resume(
        self, run_id: str, executor: RecoveryExecutor
    ) -> dict[str, object]:
        run = self.connection.execute(
            "SELECT status FROM recovery_runs WHERE id=?", (run_id,)
        ).fetchone()
        if run is None:
            raise LookupError(f"Unknown recovery run: {run_id}")
        if run["status"] == "running":
            raise RecoveryConflict(
                "Run is already claimed; mark a confirmed dead worker interrupted"
            )
        if run["status"] in ("completed", "failed"):
            return self.get(run_id)

        while True:
            row = self.connection.execute(
                """
                SELECT *
                FROM recovery_steps
                WHERE run_id=? AND state!='completed'
                ORDER BY sequence
                LIMIT 1
                """,
                (run_id,),
            ).fetchone()
            if row is None:
                now = utc_now()
                self.connection.execute(
                    """
                    UPDATE recovery_runs
                    SET status='completed', updated_at=?
                    WHERE id=?
                    """,
                    (now, run_id),
                )
                self._event(run_id, "run.completed")
                self.connection.commit()
                return self.get(run_id)
            step = self._step_from_row(row)
            if row["state"] == "review_required":
                self.connection.execute(
                    """
                    UPDATE recovery_runs
                    SET status='blocked', current_sequence=?, updated_at=?
                    WHERE id=?
                    """,
                    (step.sequence, utc_now(), run_id),
                )
                self.connection.commit()
                return self.get(run_id)
            if (
                step.action_class is ActionClass.HUMAN_REVIEW_REQUIRED
                and not bool(row["review_approved"])
            ):
                self.connection.execute(
                    """
                    UPDATE recovery_runs
                    SET status='blocked', current_sequence=?, updated_at=?
                    WHERE id=?
                    """,
                    (step.sequence, utc_now(), run_id),
                )
                self._event(
                    run_id,
                    "step.review_required",
                    step_sequence=step.sequence,
                )
                self.connection.commit()
                return self.get(run_id)
            if row["state"] == "failed":
                safe_retry = step.action_class in (
                    ActionClass.IDEMPOTENT,
                    ActionClass.RETRYABLE,
                )
                if not safe_retry or int(row["attempt_count"]) >= step.max_attempts:
                    self.connection.execute(
                        """
                        UPDATE recovery_steps
                        SET state='review_required'
                        WHERE run_id=? AND sequence=?
                        """,
                        (run_id, step.sequence),
                    )
                    self.connection.execute(
                        """
                        UPDATE recovery_runs
                        SET status='blocked', current_sequence=?, updated_at=?
                        WHERE id=?
                        """,
                        (step.sequence, utc_now(), run_id),
                    )
                    self._event(
                        run_id,
                        "step.review_required",
                        step_sequence=step.sequence,
                        details={"reason": "automatic_retry_not_safe"},
                    )
                    self.connection.commit()
                    return self.get(run_id)

            now = utc_now()
            cursor = self.connection.execute(
                """
                UPDATE recovery_steps
                SET state='running', attempt_count=attempt_count+1,
                    started_at=?, completed_at=NULL
                WHERE run_id=? AND sequence=? AND state IN ('pending', 'failed')
                """,
                (now, run_id, step.sequence),
            )
            if cursor.rowcount != 1:
                self.connection.rollback()
                raise RecoveryConflict("Recovery step claim lost")
            self.connection.execute(
                """
                UPDATE recovery_runs
                SET status='running', current_sequence=?, updated_at=?
                WHERE id=? AND status!='running'
                """,
                (step.sequence, now, run_id),
            )
            self._event(
                run_id,
                "step.started",
                step_sequence=step.sequence,
                details={
                    "attempt": int(row["attempt_count"]) + 1,
                    "idempotency_key": step.idempotency_key,
                },
            )
            self.connection.commit()

            try:
                output = executor.execute(step)
            except Exception as exc:
                error_text = f"{type(exc).__name__}:{exc}"
                error_hash = hashlib.sha256(
                    error_text.encode("utf-8")
                ).hexdigest()
                attempts = int(row["attempt_count"]) + 1
                retry_safe = step.action_class in (
                    ActionClass.IDEMPOTENT,
                    ActionClass.RETRYABLE,
                )
                review = not retry_safe or attempts >= step.max_attempts
                state = "review_required" if review else "failed"
                status = "blocked" if review else "interrupted"
                finished = utc_now()
                self.connection.execute(
                    """
                    UPDATE recovery_steps
                    SET state=?, error_kind=?, error_hash=?, completed_at=?
                    WHERE run_id=? AND sequence=? AND state='running'
                    """,
                    (
                        state,
                        type(exc).__name__,
                        error_hash,
                        finished,
                        run_id,
                        step.sequence,
                    ),
                )
                self.connection.execute(
                    """
                    UPDATE recovery_runs
                    SET status=?, current_sequence=?, updated_at=?
                    WHERE id=?
                    """,
                    (status, step.sequence, finished, run_id),
                )
                self._event(
                    run_id,
                    "step.failed",
                    step_sequence=step.sequence,
                    details={
                        "error_kind": type(exc).__name__,
                        "error_hash": error_hash,
                        "automatic_retry_available": not review,
                    },
                )
                self.connection.commit()
                return self.get(run_id)

            finished = utc_now()
            self.connection.execute(
                """
                UPDATE recovery_steps
                SET state='completed', output_json=?, evidence_json=?,
                    error_kind=NULL, error_hash=NULL, completed_at=?
                WHERE run_id=? AND sequence=? AND state='running'
                """,
                (
                    output.output_json,
                    json.dumps(output.evidence),
                    finished,
                    run_id,
                    step.sequence,
                ),
            )
            self.connection.execute(
                """
                UPDATE recovery_runs
                SET status='interrupted', current_sequence=?,
                    updated_at=?
                WHERE id=?
                """,
                (step.sequence + 1, finished, run_id),
            )
            self._event(
                run_id,
                "step.completed",
                step_sequence=step.sequence,
                details={"evidence": list(output.evidence)},
            )
            self.connection.commit()
