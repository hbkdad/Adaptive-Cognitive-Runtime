from __future__ import annotations

import json
import math
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .autonomous_improvement import digest
from .skill_format import SkillPackageLoader


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalized(value: str) -> str:
    return " ".join(value.casefold().split())


def _wilson_lower(successes: int, total: int, z: float = 1.96) -> float:
    if total <= 0:
        return 0.0
    rate = successes / total
    denominator = 1 + z * z / total
    centre = rate + z * z / (2 * total)
    margin = z * math.sqrt(
        rate * (1 - rate) / total + z * z / (4 * total * total)
    )
    return max(0.0, (centre - margin) / denominator)


@dataclass(frozen=True)
class SkillTrust:
    skill_id: str
    assessment: str
    support_total: int
    support_valid: int
    independent_roots: int
    execution_successes: int
    execution_failures: int
    reliability: float
    evidence_revision: str
    activation_eligible: bool
    reasons: tuple[str, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            **self.__dict__,
            "reasons": list(self.reasons),
        }


class MemorySkillCoevolution:
    """Managed skill lineage, invalidation, and execution-grounded trust."""

    def __init__(
        self,
        connection: sqlite3.Connection,
        *,
        minimum_roots: int = 3,
        minimum_active_reliability: float = 0.20,
    ) -> None:
        self.connection = connection
        self.minimum_roots = minimum_roots
        self.minimum_active_reliability = minimum_active_reliability

    def link_generated_candidate(
        self, generation_candidate_id: str, skill_id: str
    ) -> SkillTrust:
        candidate = self.connection.execute(
            """
            SELECT procedure, scope, task_class, trace_ids_json, skill_id
            FROM skill_generation_candidates WHERE id = ?
            """,
            (generation_candidate_id,),
        ).fetchone()
        if candidate is None or candidate["skill_id"] != skill_id:
            raise ValueError("generation candidate is not bound to this skill")
        skill = self.connection.execute(
            "SELECT content_hash FROM skills WHERE id = ?", (skill_id,)
        ).fetchone()
        if skill is None or not skill["content_hash"]:
            raise ValueError("generated skill lacks an immutable package hash")
        trace_ids = json.loads(candidate["trace_ids_json"])
        linked = 0
        for trace_id in dict.fromkeys(trace_ids):
            rows = self.connection.execute(
                """
                SELECT t.id AS trace_id, t.scope, t.task_class, t.outcome,
                       d.id AS distillation_id, d.status AS distillation_status,
                       i.id AS item_id, i.content, i.status AS item_status,
                       i.memory_id
                FROM experience_traces AS t
                JOIN experience_distillations AS d ON d.trace_id = t.id
                JOIN experience_distilled_items AS i ON i.run_id = d.id
                WHERE t.id = ? AND i.kind = 'successful_procedure'
                  AND i.memory_id IS NOT NULL
                ORDER BY d.created_at DESC, i.created_at DESC
                """,
                (trace_id,),
            ).fetchall()
            matching = next(
                (
                    row
                    for row in rows
                    if row["outcome"] == "succeeded"
                    and row["distillation_status"]
                    in {"applied", "partially_applied"}
                    and row["item_status"] == "applied"
                    and row["scope"] == candidate["scope"]
                    and row["task_class"] == candidate["task_class"]
                    and _normalized(row["content"])
                    == _normalized(candidate["procedure"])
                ),
                None,
            )
            if matching is None:
                continue
            support_payload = {
                "skill_id": skill_id,
                "candidate_id": generation_candidate_id,
                "trace_id": matching["trace_id"],
                "distillation_id": matching["distillation_id"],
                "item_id": matching["item_id"],
                "memory_id": matching["memory_id"],
                "package_hash": skill["content_hash"],
            }
            with self.connection:
                inserted = self.connection.execute(
                    """
                    INSERT OR IGNORE INTO skill_support_links (
                        id, skill_id, generation_candidate_id, root_trace_id,
                        distillation_id, distilled_item_id, memory_id,
                        scope_hash, task_class_hash, support_hash, package_hash,
                        created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(uuid.uuid4()),
                        skill_id,
                        generation_candidate_id,
                        matching["trace_id"],
                        matching["distillation_id"],
                        matching["item_id"],
                        matching["memory_id"],
                        digest({"scope": candidate["scope"]}),
                        digest({"task_class": candidate["task_class"]}),
                        digest(support_payload),
                        skill["content_hash"],
                        _now(),
                    ),
                ).rowcount
                if inserted:
                    linked += 1
                    self._event(
                        skill_id,
                        "lineage_linked",
                        digest(support_payload),
                    )
        trust = self.refresh(skill_id)
        if linked == 0 and trust.support_total == 0:
            return trust
        return trust

    def trust(self, skill_id: str) -> SkillTrust:
        skill = self.connection.execute(
            """
            SELECT id, content_hash, package_path, lifecycle_status, reliability
            FROM skills WHERE id = ?
            """,
            (skill_id,),
        ).fetchone()
        if skill is None:
            raise LookupError(f"Unknown skill: {skill_id}")
        package_current = False
        if skill["package_path"]:
            try:
                package_current = (
                    SkillPackageLoader()
                    .load(Path(str(skill["package_path"])))
                    .content_hash
                    == skill["content_hash"]
                )
            except (OSError, ValueError):
                package_current = False
        supports = self.connection.execute(
            """
            SELECT l.*, t.task_id, t.outcome AS trace_outcome,
                   t.scope AS trace_scope, t.task_class AS trace_task_class,
                   c.scope AS candidate_scope,
                   c.task_class AS candidate_task_class,
                   c.procedure AS candidate_procedure,
                   d.status AS distillation_status,
                   i.kind, i.content AS item_content,
                   i.status AS item_status, i.memory_id AS item_memory_id,
                   m.type AS memory_type, m.status AS memory_status,
                   m.lifecycle_state, m.superseded_by,
                   x.id AS invalidation_id
            FROM skill_support_links AS l
            JOIN skill_generation_candidates AS c
                ON c.id = l.generation_candidate_id
            JOIN experience_traces AS t ON t.id = l.root_trace_id
            JOIN experience_distillations AS d ON d.id = l.distillation_id
            JOIN experience_distilled_items AS i ON i.id = l.distilled_item_id
            JOIN memories AS m ON m.id = l.memory_id
            LEFT JOIN skill_support_invalidations AS x
                ON x.support_link_id = l.id
            WHERE l.skill_id = ?
            ORDER BY l.id
            """,
            (skill_id,),
        ).fetchall()
        valid = [
            row
            for row in supports
            if row["invalidation_id"] is None
            and row["trace_outcome"] == "succeeded"
            and row["distillation_status"] in {"applied", "partially_applied"}
            and row["kind"] == "successful_procedure"
            and row["item_status"] == "applied"
            and row["item_memory_id"] == row["memory_id"]
            and row["trace_scope"] == row["candidate_scope"]
            and row["trace_task_class"] == row["candidate_task_class"]
            and _normalized(row["candidate_procedure"])
            == _normalized(row["item_content"])
            and row["scope_hash"]
            == digest({"scope": row["candidate_scope"]})
            and row["task_class_hash"]
            == digest({"task_class": row["candidate_task_class"]})
            and row["support_hash"]
            == digest(
                {
                    "skill_id": skill_id,
                    "candidate_id": row["generation_candidate_id"],
                    "trace_id": row["root_trace_id"],
                    "distillation_id": row["distillation_id"],
                    "item_id": row["distilled_item_id"],
                    "memory_id": row["memory_id"],
                    "package_hash": row["package_hash"],
                }
            )
            and row["memory_type"] == "procedural"
            and row["memory_status"] == "confirmed"
            and row["lifecycle_state"] == "active"
            and row["superseded_by"] is None
            and row["package_hash"] == skill["content_hash"]
            and package_current
        ]
        independent_roots = len(
            {
                row["task_id"]
                for row in valid
                if row["task_id"]
                and self._verified_root(str(row["task_id"]))
            }
        )
        executions = self.connection.execute(
            """
            SELECT a.task_id, a.outcome, t.status
            FROM context_attributions AS a
            JOIN tasks AS t ON t.id = a.task_id
            WHERE a.source_type = 'skill' AND a.source_id = ?
              AND a.outcome != 'uncertain'
              AND a.execution_score IS NOT NULL
              AND EXISTS (
                  SELECT 1 FROM execution_runs AS e
                  WHERE e.task_id = a.task_id
                    AND e.verification_score IS NOT NULL
              )
              AND EXISTS (
                  SELECT 1 FROM evaluation_runs AS v
                  WHERE v.task_id = a.task_id
              )
            GROUP BY a.task_id
            """,
            (skill_id,),
        ).fetchall()
        successes = sum(
            row["outcome"] == "contributed" and row["status"] == "succeeded"
            for row in executions
        )
        failures = len(executions) - successes
        lower = _wilson_lower(successes, successes + failures)
        support_ratio = len(valid) / len(supports) if supports else 0.0
        reliability = lower * support_ratio if executions else 0.0
        reasons: list[str] = []
        if not supports:
            assessment = "unassessed"
            reasons.append("no_managed_lineage")
        elif len(valid) != len(supports):
            assessment = "invalidated"
            reasons.append("invalid_support")
        elif independent_roots < self.minimum_roots:
            assessment = "probation"
            reasons.append(
                f"insufficient_independent_roots:{independent_roots}/"
                f"{self.minimum_roots}"
            )
        elif not executions:
            assessment = "grounded"
            reasons.append("awaiting_independent_execution")
        elif reliability < self.minimum_active_reliability:
            assessment = "probation"
            reasons.append("execution_reliability_below_threshold")
        else:
            assessment = "grounded"
        revision_payload = {
            "skill_id": skill_id,
            "package_hash": skill["content_hash"],
            "supports": [
                {
                    "id": row["id"],
                    "valid": row in valid,
                    "support_hash": row["support_hash"],
                }
                for row in supports
            ],
            "execution_tasks": [
                {
                    "task_id_hash": digest({"task_id": row["task_id"]}),
                    "outcome": row["outcome"],
                    "status": row["status"],
                }
                for row in executions
            ],
        }
        return SkillTrust(
            skill_id=skill_id,
            assessment=assessment,
            support_total=len(supports),
            support_valid=len(valid),
            independent_roots=independent_roots,
            execution_successes=successes,
            execution_failures=failures,
            reliability=reliability,
            evidence_revision=digest(revision_payload),
            activation_eligible=(
                len(valid) >= self.minimum_roots
                and independent_roots >= self.minimum_roots
                and len(valid) == len(supports)
            ),
            reasons=tuple(reasons),
        )

    def refresh(self, skill_id: str) -> SkillTrust:
        trust = self.trust(skill_id)
        snapshot = self.connection.execute(
            """
            SELECT 1 FROM skill_reliability_snapshots
            WHERE skill_id = ? AND evidence_revision = ?
            """,
            (skill_id, trust.evidence_revision),
        ).fetchone()
        if snapshot is None:
            total = trust.execution_successes + trust.execution_failures
            lower = _wilson_lower(trust.execution_successes, total)
            with self.connection:
                self.connection.execute(
                    """
                    INSERT INTO skill_reliability_snapshots (
                        id, skill_id, evidence_revision, support_total,
                        support_valid, execution_successes, execution_failures,
                        wilson_lower_micros, reliability_micros, assessment,
                        created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(uuid.uuid4()),
                        skill_id,
                        trust.evidence_revision,
                        trust.support_total,
                        trust.support_valid,
                        trust.execution_successes,
                        trust.execution_failures,
                        round(lower * 1_000_000),
                        round(trust.reliability * 1_000_000),
                        trust.assessment,
                        _now(),
                    ),
                )
                if trust.support_total:
                    self.connection.execute(
                        "UPDATE skills SET reliability = ? WHERE id = ?",
                        (trust.reliability, skill_id),
                    )
                self._event(
                    skill_id,
                    "reliability_updated",
                    trust.evidence_revision,
                )
                lifecycle = self.connection.execute(
                    """
                    SELECT lifecycle_status FROM skills WHERE id = ?
                    """,
                    (skill_id,),
                ).fetchone()["lifecycle_status"]
                if (
                    trust.support_total
                    and lifecycle == "active"
                    and not trust.activation_eligible
                ):
                    self.connection.execute(
                        """
                        UPDATE skills
                        SET lifecycle_status = 'quarantined',
                            status = 'quarantine'
                        WHERE id = ? AND lifecycle_status = 'active'
                        """,
                        (skill_id,),
                    )
                    self.connection.execute(
                        """
                        INSERT INTO skill_registry_history (
                            id, skill_id, event, from_status, to_status,
                            details_json, created_at
                        ) VALUES (?, ?, 'evidence_invalidated', 'active',
                                  'quarantined', ?, ?)
                        """,
                        (
                            str(uuid.uuid4()),
                            skill_id,
                            '{"reason":"managed_support_invalid"}',
                            _now(),
                        ),
                    )
                    self._event(
                        skill_id,
                        "auto_quarantined",
                        trust.evidence_revision,
                    )
        return trust

    def invalidate(
        self,
        support_link_id: str,
        *,
        reason: str,
        actor: str,
        actor_type: str = "operator",
    ) -> SkillTrust:
        allowed_reasons = {
            "memory_missing",
            "memory_not_current",
            "memory_untrusted",
            "trace_not_succeeded",
            "distillation_not_applied",
            "item_not_applied",
            "package_changed",
            "support_hash_changed",
            "operator_rejected",
        }
        if reason not in allowed_reasons:
            raise ValueError("invalidation reason must be a closed reason code")
        if actor_type not in {"reconciler", "operator"}:
            raise ValueError("invalid invalidation actor type")
        if not actor.strip():
            raise ValueError("invalidation reason and actor are required")
        row = self.connection.execute(
            "SELECT skill_id FROM skill_support_links WHERE id = ?",
            (support_link_id,),
        ).fetchone()
        if row is None:
            raise LookupError(f"Unknown support link: {support_link_id}")
        skill_id = str(row["skill_id"])
        timestamp = _now()
        with self.connection:
            inserted = self.connection.execute(
                """
                INSERT OR IGNORE INTO skill_support_invalidations (
                    id, support_link_id, reason, reason_hash, actor_type,
                    actor_hash, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(uuid.uuid4()),
                    support_link_id,
                    reason,
                    digest(reason),
                    actor_type,
                    digest(actor.strip()),
                    timestamp,
                ),
            ).rowcount
            if inserted:
                self._event(
                    skill_id,
                    "support_invalidated",
                    digest(
                        {
                            "support_link_id": support_link_id,
                            "reason_hash": digest(reason),
                        }
                    ),
                )
        trust = self.refresh(skill_id)
        lifecycle = self.connection.execute(
            "SELECT lifecycle_status FROM skills WHERE id = ?", (skill_id,)
        ).fetchone()["lifecycle_status"]
        if lifecycle == "active" and not trust.activation_eligible:
            with self.connection:
                changed = self.connection.execute(
                    """
                    UPDATE skills SET lifecycle_status = 'quarantined',
                                      status = 'quarantine'
                    WHERE id = ? AND lifecycle_status = 'active'
                    """,
                    (skill_id,),
                ).rowcount
                if changed:
                    self.connection.execute(
                        """
                        INSERT INTO skill_registry_history (
                            id, skill_id, event, from_status, to_status,
                            details_json, created_at
                        ) VALUES (?, ?, 'evidence_invalidated', 'active',
                                  'quarantined', ?, ?)
                        """,
                        (
                            str(uuid.uuid4()),
                            skill_id,
                            '{"reason":"managed_support_invalid"}',
                            _now(),
                        ),
                    )
                    self._event(
                        skill_id,
                        "auto_quarantined",
                        trust.evidence_revision,
                    )
        return self.trust(skill_id)

    def report(self, skill_id: str) -> dict[str, object]:
        trust = self.trust(skill_id)
        return {
            **trust.as_dict(),
            "supports": [
                dict(row)
                for row in self.connection.execute(
                    """
                    SELECT l.id, l.root_trace_id, l.distillation_id,
                           l.distilled_item_id, l.memory_id, l.support_hash,
                           l.package_hash, l.created_at,
                           (x.id IS NULL) AS currently_valid
                    FROM skill_support_links AS l
                    LEFT JOIN skill_support_invalidations AS x
                        ON x.support_link_id = l.id
                    WHERE l.skill_id = ? ORDER BY l.created_at, l.id
                    """,
                    (skill_id,),
                )
            ],
        }

    def _verified_root(self, task_id: str) -> bool:
        row = self.connection.execute(
            """
            SELECT t.status,
                   EXISTS (
                       SELECT 1 FROM execution_runs e
                       WHERE e.task_id = t.id
                         AND e.verification_score IS NOT NULL
                   ) AS execution_verified,
                   EXISTS (
                       SELECT 1 FROM evaluation_runs v
                       WHERE v.task_id = t.id AND v.passed = 1
                   ) AS evaluation_passed
            FROM tasks t WHERE t.id = ?
            """,
            (task_id,),
        ).fetchone()
        return bool(
            row
            and row["status"] == "succeeded"
            and row["execution_verified"]
            and row["evaluation_passed"]
        )

    def _event(
        self, skill_id: str, event_type: str, evidence_hash: str
    ) -> None:
        self.connection.execute(
            """
            INSERT OR IGNORE INTO skill_coevolution_events (
                id, skill_id, event_type, evidence_hash, created_at
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                str(uuid.uuid4()),
                skill_id,
                event_type,
                evidence_hash,
                _now(),
            ),
        )
