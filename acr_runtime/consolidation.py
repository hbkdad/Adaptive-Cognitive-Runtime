from __future__ import annotations

import json
import math
import sqlite3
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Iterable

from .memory import (
    LifecycleState,
    MemoryPatch,
    MemoryRecord,
    MemoryStatus,
    MemoryStore,
    MemoryType,
    parse_timestamp,
    utc_now,
)


class ConsolidationKind(str, Enum):
    MERGE = "merge"
    ARCHIVE = "archive"
    SUPERSESSION = "supersession"
    PROMOTION = "promotion"
    CONFLICT = "conflict"
    DECAY = "decay"


@dataclass(frozen=True)
class ConsolidationConfig:
    stale_after_days: int = 180
    candidate_archive_days: int = 90
    low_utility_threshold: float = 0.15
    low_importance_threshold: float = 0.40
    promotion_min_uses: int = 3
    promotion_min_utility: float = 0.80
    promotion_min_confidence: float = 0.80
    decay_after_days: int = 90
    decay_half_life_days: int = 180
    decay_floor: float = 0.05
    scan_limit: int = 10_000

    def __post_init__(self) -> None:
        day_values = (
            self.stale_after_days,
            self.candidate_archive_days,
            self.decay_after_days,
            self.decay_half_life_days,
        )
        if any(value < 1 for value in day_values):
            raise ValueError("Consolidation day thresholds must be positive")
        if self.promotion_min_uses < 1:
            raise ValueError("promotion_min_uses must be positive")
        score_values = (
            self.low_utility_threshold,
            self.low_importance_threshold,
            self.promotion_min_utility,
            self.promotion_min_confidence,
            self.decay_floor,
        )
        if any(not 0 <= value <= 1 for value in score_values):
            raise ValueError("Consolidation score thresholds must be 0..1")
        if not 1 <= self.scan_limit <= 10_000:
            raise ValueError("scan_limit must be between 1 and 10000")


@dataclass(frozen=True)
class ConsolidationAction:
    id: str
    kind: ConsolidationKind
    target_ids: tuple[str, ...]
    expected_versions: dict[str, dict[str, str]]
    payload: dict[str, object]
    reason: str
    status: str = "proposed"
    error_type: str | None = None


@dataclass(frozen=True)
class ConsolidationPlan:
    id: str
    scope: str | None
    status: str
    actions: tuple[ConsolidationAction, ...]
    created_at: str
    applied_at: str | None = None

    def grouped(self) -> dict[str, list[ConsolidationAction]]:
        groups = {
            "MERGES": [],
            "ARCHIVES": [],
            "SUPERSESSIONS": [],
            "PROMOTIONS": [],
            "CONFLICTS": [],
            "DECAYS": [],
        }
        names = {
            ConsolidationKind.MERGE: "MERGES",
            ConsolidationKind.ARCHIVE: "ARCHIVES",
            ConsolidationKind.SUPERSESSION: "SUPERSESSIONS",
            ConsolidationKind.PROMOTION: "PROMOTIONS",
            ConsolidationKind.CONFLICT: "CONFLICTS",
            ConsolidationKind.DECAY: "DECAYS",
        }
        for action in self.actions:
            groups[names[action.kind]].append(action)
        return groups


class SQLiteConsolidationAudit:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def save(
        self,
        plan: ConsolidationPlan,
        config: ConsolidationConfig,
    ) -> None:
        summary = {
            name: len(actions) for name, actions in plan.grouped().items()
        }
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO memory_consolidation_runs (
                    id, status, scope, config_json, summary_json,
                    created_at, applied_at
                ) VALUES (?, ?, ?, ?, ?, ?, NULL)
                """,
                (
                    plan.id,
                    plan.status,
                    plan.scope,
                    json.dumps(asdict(config)),
                    json.dumps(summary),
                    plan.created_at,
                ),
            )
            self.connection.executemany(
                """
                INSERT INTO memory_consolidation_actions (
                    id, run_id, kind, target_ids_json,
                    expected_versions_json, payload_json, reason,
                    status, error_type, created_at, applied_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, NULL)
                """,
                (
                    (
                        action.id,
                        plan.id,
                        action.kind.value,
                        json.dumps(action.target_ids),
                        json.dumps(action.expected_versions),
                        json.dumps(action.payload),
                        action.reason,
                        action.status,
                        plan.created_at,
                    )
                    for action in plan.actions
                ),
            )

    def load(self, run_id: str) -> ConsolidationPlan:
        run = self.connection.execute(
            "SELECT * FROM memory_consolidation_runs WHERE id = ?", (run_id,)
        ).fetchone()
        if run is None:
            raise KeyError(run_id)
        rows = self.connection.execute(
            """
            SELECT * FROM memory_consolidation_actions
            WHERE run_id = ?
            ORDER BY CASE kind
                WHEN 'merge' THEN 1
                WHEN 'supersession' THEN 2
                WHEN 'promotion' THEN 3
                WHEN 'archive' THEN 4
                WHEN 'decay' THEN 5
                ELSE 6
            END, created_at, id
            """,
            (run_id,),
        ).fetchall()
        actions = tuple(
            ConsolidationAction(
                id=row["id"],
                kind=ConsolidationKind(row["kind"]),
                target_ids=tuple(json.loads(row["target_ids_json"])),
                expected_versions=json.loads(row["expected_versions_json"]),
                payload=json.loads(row["payload_json"]),
                reason=row["reason"],
                status=row["status"],
                error_type=row["error_type"],
            )
            for row in rows
        )
        return ConsolidationPlan(
            id=run["id"],
            scope=run["scope"],
            status=run["status"],
            actions=actions,
            created_at=run["created_at"],
            applied_at=run["applied_at"],
        )

    def mark_action(
        self,
        action_id: str,
        status: str,
        *,
        error_type: str | None = None,
    ) -> None:
        self.connection.execute(
            """
            UPDATE memory_consolidation_actions
            SET status = ?, error_type = ?, applied_at = ?
            WHERE id = ?
            """,
            (status, error_type, utc_now(), action_id),
        )
        self.connection.commit()

    def mark_run(self, run_id: str, status: str) -> None:
        self.connection.execute(
            """
            UPDATE memory_consolidation_runs
            SET status = ?, applied_at = ?
            WHERE id = ?
            """,
            (status, utc_now(), run_id),
        )
        self.connection.commit()


class MemoryConsolidator:
    def __init__(
        self,
        store: MemoryStore,
        audit: SQLiteConsolidationAudit,
        *,
        config: ConsolidationConfig | None = None,
    ) -> None:
        self.store = store
        self.audit = audit
        self.config = config or ConsolidationConfig()

    @staticmethod
    def _normalized(text: str) -> str:
        return " ".join(text.casefold().split())

    @staticmethod
    def _versions(
        records: Iterable[MemoryRecord],
    ) -> dict[str, dict[str, str]]:
        return {
            record.id: {
                "updated_at": record.updated_at,
                "status": record.status.value,
                "lifecycle_state": record.lifecycle_state.value,
            }
            for record in records
        }

    @staticmethod
    def _age_days(record: MemoryRecord, now: datetime) -> float:
        reference = record.last_accessed or record.created_at
        return max(
            0.0,
            (now - parse_timestamp(reference)).total_seconds() / 86_400,
        )

    @staticmethod
    def _overlap(left: MemoryRecord, right: MemoryRecord) -> bool:
        left_start = parse_timestamp(left.valid_from)
        right_start = parse_timestamp(right.valid_from)
        left_end = (
            parse_timestamp(left.valid_until)
            if left.valid_until
            else datetime.max.replace(tzinfo=timezone.utc)
        )
        right_end = (
            parse_timestamp(right.valid_until)
            if right.valid_until
            else datetime.max.replace(tzinfo=timezone.utc)
        )
        return max(left_start, right_start) < min(left_end, right_end)

    @staticmethod
    def _linked(left: MemoryRecord, right: MemoryRecord) -> bool:
        return (
            left.supersedes == right.id
            or left.superseded_by == right.id
            or right.supersedes == left.id
            or right.superseded_by == left.id
        )

    @staticmethod
    def _survivor(records: list[MemoryRecord]) -> MemoryRecord:
        return max(
            records,
            key=lambda record: (
                record.status is MemoryStatus.CONFIRMED,
                record.confidence,
                record.importance,
                record.utility_score,
                record.successful_uses,
                -len(record.content),
                record.id,
            ),
        )

    def dry_run(self, *, scope: str | None = None) -> ConsolidationPlan:
        records = list(
            self.store.scan(
                scope=scope,
                statuses=(
                    MemoryStatus.CANDIDATE,
                    MemoryStatus.CONFIRMED,
                    MemoryStatus.SUPERSEDED,
                ),
                lifecycle_states=(
                    LifecycleState.ACTIVE,
                    LifecycleState.COLD,
                ),
                limit=self.config.scan_limit,
            )
        )
        now = datetime.now(timezone.utc)
        actions: list[ConsolidationAction] = []
        occupied: set[str] = set()

        duplicate_groups: dict[
            tuple[str, MemoryType, str | None, str], list[MemoryRecord]
        ] = {}
        for record in records:
            if record.status is MemoryStatus.SUPERSEDED:
                continue
            key = (
                record.scope,
                record.type,
                record.subject.casefold() if record.subject else None,
                self._normalized(record.content),
            )
            duplicate_groups.setdefault(key, []).append(record)
        for group in duplicate_groups.values():
            if len(group) < 2:
                continue
            survivor = self._survivor(group)
            duplicates = [record for record in group if record.id != survivor.id]
            merged_evidence = tuple(
                dict.fromkeys(
                    evidence for record in group for evidence in record.evidence
                )
            )
            merged_reasons = tuple(
                dict.fromkeys(
                    reason
                    for record in group
                    for reason in record.retention_reasons
                )
            )
            actions.append(
                ConsolidationAction(
                    id=str(uuid.uuid4()),
                    kind=ConsolidationKind.MERGE,
                    target_ids=(survivor.id, *(item.id for item in duplicates)),
                    expected_versions=self._versions(group),
                    payload={
                        "survivor_id": survivor.id,
                        "evidence": merged_evidence,
                        "retention_reasons": (
                            *merged_reasons,
                            "consolidated_exact_duplicates",
                        ),
                    },
                    reason="exact_normalized_duplicate",
                )
            )
            occupied.update(record.id for record in group)

        by_subject: dict[tuple[str, str], list[MemoryRecord]] = {}
        for record in records:
            if (
                record.id in occupied
                or record.subject is None
                or record.status not in {
                    MemoryStatus.CONFIRMED,
                    MemoryStatus.SUPERSEDED,
                }
            ):
                continue
            by_subject.setdefault(
                (record.scope, record.subject.casefold()), []
            ).append(record)
        for group in by_subject.values():
            group.sort(key=lambda record: parse_timestamp(record.valid_from))
            for index, left in enumerate(group):
                for right in group[index + 1 :]:
                    if left.id in occupied or right.id in occupied:
                        continue
                    if self._normalized(left.content) == self._normalized(
                        right.content
                    ) or self._linked(left, right):
                        continue
                    if self._overlap(left, right):
                        actions.append(
                            ConsolidationAction(
                                id=str(uuid.uuid4()),
                                kind=ConsolidationKind.CONFLICT,
                                target_ids=(left.id, right.id),
                                expected_versions=self._versions((left, right)),
                                payload={},
                                reason="overlapping_unlinked_subject_claims",
                            )
                        )
                    elif (
                        left.valid_until is not None
                        and parse_timestamp(left.valid_until)
                        == parse_timestamp(right.valid_from)
                        and left.superseded_by is None
                        and right.supersedes is None
                    ):
                        actions.append(
                            ConsolidationAction(
                                id=str(uuid.uuid4()),
                                kind=ConsolidationKind.SUPERSESSION,
                                target_ids=(left.id, right.id),
                                expected_versions=self._versions((left, right)),
                                payload={"effective_at": right.valid_from},
                                reason="adjacent_subject_truth_without_links",
                            )
                        )
                        occupied.update((left.id, right.id))

        for record in records:
            if record.id in occupied:
                continue
            age_days = self._age_days(record, now)
            if (
                record.status is MemoryStatus.CANDIDATE
                and record.access_count >= self.config.promotion_min_uses
                and record.utility_score >= self.config.promotion_min_utility
                and record.confidence >= self.config.promotion_min_confidence
                and bool(record.evidence)
            ):
                actions.append(
                    ConsolidationAction(
                        id=str(uuid.uuid4()),
                        kind=ConsolidationKind.PROMOTION,
                        target_ids=(record.id,),
                        expected_versions=self._versions((record,)),
                        payload={},
                        reason="candidate_has_repeated_high_utility",
                    )
                )
                occupied.add(record.id)
                continue
            expired_temporary = (
                record.type is MemoryType.TEMPORARY
                and record.valid_until is not None
                and parse_timestamp(record.valid_until) <= now
            )
            stale_candidate = (
                record.status is MemoryStatus.CANDIDATE
                and age_days >= self.config.candidate_archive_days
                and record.access_count == 0
            )
            low_value_stale = (
                age_days >= self.config.stale_after_days
                and record.utility_score <= self.config.low_utility_threshold
                and record.importance <= self.config.low_importance_threshold
            )
            if expired_temporary or stale_candidate or low_value_stale:
                reason = (
                    "expired_temporary"
                    if expired_temporary
                    else (
                        "stale_unused_candidate"
                        if stale_candidate
                        else "stale_low_utility"
                    )
                )
                actions.append(
                    ConsolidationAction(
                        id=str(uuid.uuid4()),
                        kind=ConsolidationKind.ARCHIVE,
                        target_ids=(record.id,),
                        expected_versions=self._versions((record,)),
                        payload={},
                        reason=reason,
                    )
                )
                occupied.add(record.id)
                continue
            if (
                age_days >= self.config.decay_after_days
                and record.utility_score > self.config.decay_floor
                and record.importance < 0.8
            ):
                decayed = max(
                    self.config.decay_floor,
                    record.utility_score
                    * math.pow(
                        0.5, age_days / self.config.decay_half_life_days
                    ),
                )
                if decayed < record.utility_score:
                    actions.append(
                        ConsolidationAction(
                            id=str(uuid.uuid4()),
                            kind=ConsolidationKind.DECAY,
                            target_ids=(record.id,),
                            expected_versions=self._versions((record,)),
                            payload={
                                "from": record.utility_score,
                                "to": round(decayed, 6),
                            },
                            reason="stale_utility_decay",
                        )
                    )

        plan = ConsolidationPlan(
            id=str(uuid.uuid4()),
            scope=scope,
            status="planned",
            actions=tuple(actions),
            created_at=utc_now(),
        )
        self.audit.save(plan, self.config)
        return plan

    def _is_fresh(self, action: ConsolidationAction) -> bool:
        for memory_id, expected in action.expected_versions.items():
            record = self.store.get(memory_id)
            if (
                record is None
                or record.updated_at != expected["updated_at"]
                or record.status.value != expected["status"]
                or record.lifecycle_state.value
                != expected.get("lifecycle_state", record.lifecycle_state.value)
            ):
                return False
        return True

    def approve(self, run_id: str) -> ConsolidationPlan:
        plan = self.audit.load(run_id)
        if plan.status != "planned":
            raise ValueError("Only a planned consolidation run can be approved")
        failures = 0
        for action in plan.actions:
            if action.kind is ConsolidationKind.CONFLICT:
                self.audit.mark_action(action.id, "review_required")
                continue
            if not self._is_fresh(action):
                self.audit.mark_action(action.id, "skipped")
                failures += 1
                continue
            try:
                if action.kind is ConsolidationKind.MERGE:
                    survivor_id = str(action.payload["survivor_id"])
                    survivor = self.store.get(survivor_id)
                    if survivor is None:
                        raise KeyError(survivor_id)
                    self.store.update(
                        survivor_id,
                        MemoryPatch(
                            evidence=tuple(action.payload["evidence"]),
                            retention_reasons=tuple(
                                action.payload["retention_reasons"]
                            ),
                            expected_updated_at=survivor.updated_at,
                        ),
                    )
                    for duplicate_id in action.target_ids[1:]:
                        self.store.set_lifecycle(
                            duplicate_id, LifecycleState.ARCHIVED
                        )
                elif action.kind is ConsolidationKind.SUPERSESSION:
                    self.store.supersede(
                        action.target_ids[0],
                        action.target_ids[1],
                        effective_at=str(action.payload["effective_at"]),
                    )
                elif action.kind is ConsolidationKind.PROMOTION:
                    record = self.store.get(action.target_ids[0])
                    if record is None:
                        raise KeyError(action.target_ids[0])
                    self.store.update(
                        record.id,
                        MemoryPatch(
                            retention_reasons=tuple(
                                dict.fromkeys(
                                    (
                                        *record.retention_reasons,
                                        "consolidated_high_utility_promotion",
                                    )
                                )
                            ),
                            expected_updated_at=record.updated_at,
                        ),
                    )
                    self.store.set_status(
                        action.target_ids[0], MemoryStatus.CONFIRMED
                    )
                elif action.kind is ConsolidationKind.ARCHIVE:
                    self.store.set_lifecycle(
                        action.target_ids[0], LifecycleState.ARCHIVED
                    )
                elif action.kind is ConsolidationKind.DECAY:
                    self.store.update(
                        action.target_ids[0],
                        MemoryPatch(utility_score=float(action.payload["to"])),
                    )
            except Exception as error:
                self.audit.mark_action(
                    action.id, "error", error_type=type(error).__name__
                )
                failures += 1
            else:
                self.audit.mark_action(action.id, "applied")
        self.audit.mark_run(
            run_id, "partially_applied" if failures else "applied"
        )
        return self.audit.load(run_id)
