from __future__ import annotations

import base64
import json
from datetime import datetime, timezone
from typing import Any, Callable

from .db import RuntimeDB


CATEGORIES = (
    "memory_promotion",
    "memory_deletion",
    "new_skill",
    "skill_mutation",
    "routing_change",
    "topology_discovery",
    "context_optimization",
)
AUTONOMY = (
    "explicit_approval",
    "proposal_only",
    "workflow_unattributed",
    "runtime_derived_advisory",
    "automatic_within_requested_run",
)
MAX_EVENTS = 100


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class LearningDashboardReader:
    """Content-minimized audit projections over retained learning records."""

    def __init__(self, source: RuntimeDB | object) -> None:
        database = source if isinstance(source, RuntimeDB) else getattr(
            source, "db", None
        )
        if not isinstance(database, RuntimeDB):
            raise TypeError(
                "LearningDashboardReader requires RuntimeDB or AdaptiveRuntime"
            )
        self.connection = database.connection

    @staticmethod
    def _cursor(
        occurred_at: str,
        event_id: str,
        category: str | None,
        autonomy: str | None,
    ) -> str:
        raw = json.dumps(
            [1, occurred_at, event_id, category, autonomy],
            separators=(",", ":"),
        ).encode()
        return base64.urlsafe_b64encode(raw).decode("ascii")

    @staticmethod
    def _decode_cursor(
        cursor: str,
        *,
        category: str | None,
        autonomy: str | None,
    ) -> tuple[str, str]:
        try:
            payload = json.loads(
                base64.urlsafe_b64decode(cursor.encode("ascii")).decode()
            )
        except Exception as error:
            raise ValueError("invalid learning dashboard cursor") from error
        if (
            not isinstance(payload, list)
            or len(payload) != 5
            or payload[0] != 1
            or not isinstance(payload[1], str)
            or not isinstance(payload[2], str)
            or payload[3] != category
            or payload[4] != autonomy
        ):
            raise ValueError("invalid learning dashboard cursor")
        return payload[1], payload[2]

    @staticmethod
    def _event(
        *,
        event_id: str,
        category: str,
        action: str,
        status: str,
        autonomy: str,
        actor: str,
        actor_attribution: str,
        summary: str,
        occurred_at: str,
        evidence: dict[str, Any],
        source_record: str,
        reversible: bool,
        audit_gap: str | None = None,
    ) -> dict[str, Any]:
        return {
            "id": event_id,
            "category": category,
            "action": action,
            "status": status,
            "autonomy": autonomy,
            "actor": actor,
            "actor_attribution": actor_attribution,
            "summary": summary,
            "occurred_at": occurred_at,
            "evidence": evidence,
            "source_record": source_record,
            "content_minimized": True,
            "reversible": reversible,
            "audit_gap": audit_gap,
        }

    def _query(
        self,
        sql: str,
        *,
        prefix: str,
        limit: int,
        cursor: tuple[str, str] | None,
        params: tuple[object, ...] = (),
    ) -> list[Any]:
        cursor_time, cursor_id = cursor or ("\uffff", "\uffff")
        return self.connection.execute(
            sql,
            (
                *params,
                cursor_time,
                cursor_time,
                prefix,
                cursor_id,
                limit + 1,
            ),
        ).fetchall()

    def _memory_promotions(
        self,
        limit: int,
        cursor: tuple[str, str] | None,
        autonomy: str | None = None,
    ) -> list[dict[str, Any]]:
        if autonomy not in {None, "explicit_approval", "proposal_only"}:
            return []
        status_clause = (
            "AND status='applied'"
            if autonomy == "explicit_approval"
            else "AND status!='applied'"
            if autonomy == "proposal_only"
            else ""
        )
        prefix = "memory_promotion:"
        rows = self._query(
            f"""
            SELECT id, status, json_array_length(target_ids_json) AS targets,
                   COALESCE(applied_at, created_at) AS occurred_at
            FROM memory_consolidation_actions
            WHERE kind='promotion' {status_clause} AND (
                COALESCE(applied_at, created_at) < ? OR (
                    COALESCE(applied_at, created_at) = ?
                    AND ? || id < ?
                )
            )
            ORDER BY occurred_at DESC, id DESC LIMIT ?
            """,
            prefix=prefix,
            limit=limit,
            cursor=cursor,
        )
        return [
            self._event(
                event_id=prefix + row["id"],
                category="memory_promotion",
                action="promote_memory",
                status=row["status"],
                autonomy=(
                    "explicit_approval"
                    if row["status"] == "applied"
                    else "proposal_only"
                ),
                actor=(
                    "operator"
                    if row["status"] == "applied"
                    else "runtime"
                ),
                actor_attribution="workflow_semantics",
                summary=(
                    "Memory promotion applied"
                    if row["status"] == "applied"
                    else "Memory promotion proposal retained"
                ),
                occurred_at=row["occurred_at"],
                evidence={"target_count": int(row["targets"] or 0)},
                source_record="memory_consolidation_actions",
                reversible=True,
                audit_gap=(
                    "Approving operator identity was not retained"
                    if row["status"] == "applied"
                    else None
                ),
            )
            for row in rows
        ]

    def _memory_deletions(
        self, limit: int, cursor: tuple[str, str] | None
    ) -> list[dict[str, Any]]:
        prefix = "memory_deletion:"
        rows = self._query(
            """
            SELECT id, status, deletion_requirement, verification_json,
                   COALESCE(completed_at, created_at) AS occurred_at
            FROM memory_deletion_requests
            WHERE (
                COALESCE(completed_at, created_at) < ? OR (
                    COALESCE(completed_at, created_at) = ?
                    AND ? || id < ?
                )
            )
            ORDER BY occurred_at DESC, id DESC LIMIT ?
            """,
            prefix=prefix,
            limit=limit,
            cursor=cursor,
        )
        output = []
        for row in rows:
            verification = json.loads(row["verification_json"])
            output.append(self._event(
                event_id=prefix + row["id"],
                category="memory_deletion",
                action="delete_memory",
                status=row["status"],
                autonomy="explicit_approval",
                actor="operator",
                actor_attribution="retained_pseudonymous_reference",
                summary=(
                    "Verified memory erasure completed"
                    if row["status"] == "completed"
                    else f"Memory erasure {row['status']}"
                ),
                occurred_at=row["occurred_at"],
                evidence={
                    "deletion_requirement": row["deletion_requirement"],
                    "content_fields_erased": verification.get(
                        "content_fields_erased"
                    ),
                    "fts_residual_rows": verification.get(
                        "fts_residual_rows"
                    ),
                },
                source_record="memory_deletion_requests",
                reversible=False,
            ))
        return output

    def _new_skills(
        self, limit: int, cursor: tuple[str, str] | None
    ) -> list[dict[str, Any]]:
        prefix = "new_skill:"
        rows = self._query(
            """
            SELECT c.id, c.trigger_kind, c.occurrence_count,
                   c.status, c.applied_at AS occurred_at,
                   s.manifest_id, s.version
            FROM skill_generation_candidates c
            JOIN skills s ON s.id=c.skill_id
            WHERE c.status='generated' AND c.applied_at IS NOT NULL AND (
                c.applied_at < ? OR (
                    c.applied_at = ? AND ? || c.id < ?
                )
            )
            ORDER BY occurred_at DESC, c.id DESC LIMIT ?
            """,
            prefix=prefix,
            limit=limit,
            cursor=cursor,
        )
        return [
            self._event(
                event_id=prefix + row["id"],
                category="new_skill",
                action="generate_skill",
                status=row["status"],
                autonomy="explicit_approval",
                actor="operator",
                actor_attribution="workflow_semantics",
                summary=(
                    f"New quarantined skill generated: "
                    f"{row['manifest_id']}@{row['version']}"
                ),
                occurred_at=row["occurred_at"],
                evidence={
                    "trigger_kind": row["trigger_kind"],
                    "occurrence_count": int(row["occurrence_count"]),
                    "lifecycle": "quarantined",
                },
                source_record="skill_generation_candidates",
                reversible=True,
                audit_gap="Approving operator identity was not retained",
            )
            for row in rows
        ]

    def _skill_mutations(
        self, limit: int, cursor: tuple[str, str] | None
    ) -> list[dict[str, Any]]:
        prefix = "skill_mutation:"
        rows = self._query(
            """
            SELECT id, source_version, candidate_version, status,
                   json_type(mutation_json, '$.instructions') IS NOT NULL
                       AS changes_instructions,
                   COALESCE(json_array_length(
                       json_extract(mutation_json, '$.workflow')
                   ), 0) > 0 AS changes_workflow,
                   json_type(mutation_json, '$.tools') IS NOT NULL
                       AS changes_tools,
                   json_type(
                       mutation_json, '$.retrieval_strategy'
                   ) IS NOT NULL AS changes_retrieval_strategy,
                   json_type(mutation_json, '$.verification') IS NOT NULL
                       AS changes_verification,
                   json_type(mutation_json, '$.error_handling') IS NOT NULL
                       AS changes_error_handling,
                   json_type(mutation_json, '$.token_budget') IS NOT NULL
                       AS changes_token_budget,
                   COALESCE(
                       rolled_back_at, promoted_at, compared_at, created_at
                   ) AS occurred_at
            FROM skill_evolution_runs
            WHERE (
                COALESCE(
                    rolled_back_at, promoted_at, compared_at, created_at
                ) < ? OR (
                    COALESCE(
                        rolled_back_at, promoted_at, compared_at, created_at
                    ) = ? AND ? || id < ?
                )
            )
            ORDER BY occurred_at DESC, id DESC LIMIT ?
            """,
            prefix=prefix,
            limit=limit,
            cursor=cursor,
        )
        output = []
        for row in rows:
            changed_fields = [
                field
                for field in (
                    "instructions", "workflow", "tools",
                    "retrieval_strategy", "verification",
                    "error_handling", "token_budget",
                )
                if row[f"changes_{field}"]
            ]
            output.append(self._event(
                event_id=prefix + row["id"],
                category="skill_mutation",
                action="mutate_skill_version",
                status=row["status"],
                autonomy="workflow_unattributed",
                actor="runtime_workflow",
                actor_attribution="not_retained",
                summary=(
                    f"Skill mutation {row['source_version']} → "
                    f"{row['candidate_version']}"
                ),
                occurred_at=row["occurred_at"],
                evidence={
                    "source_version": row["source_version"],
                    "candidate_version": row["candidate_version"],
                    "changed_fields": changed_fields,
                },
                source_record="skill_evolution_runs",
                reversible=row["status"] == "promoted",
                audit_gap="Initiating actor identity was not retained",
            ))
        return output

    def _routing_changes(
        self, limit: int, cursor: tuple[str, str] | None
    ) -> list[dict[str, Any]]:
        prefix = "routing_change:"
        rows = self._query(
            """
            SELECT i.id, i.attribution_outcome, i.recommendation,
                   i.status, i.created_at AS occurred_at,
                   s.manifest_id, s.version
            FROM learning_routing_improvements i
            JOIN skills s ON s.id=i.skill_id
            WHERE (
                i.created_at < ? OR (
                    i.created_at = ? AND ? || i.id < ?
                )
            )
            ORDER BY occurred_at DESC, i.id DESC LIMIT ?
            """,
            prefix=prefix,
            limit=limit,
            cursor=cursor,
        )
        return [
            self._event(
                event_id=prefix + row["id"],
                category="routing_change",
                action="propose_routing_change",
                status=row["status"],
                autonomy="proposal_only",
                actor="runtime",
                actor_attribution="retained_learning_run",
                summary=(
                    f"Routing recommendation for "
                    f"{row['manifest_id']}@{row['version']}"
                ),
                occurred_at=row["occurred_at"],
                evidence={
                    "attribution_outcome": row["attribution_outcome"],
                    "recommendation": row["recommendation"],
                    "production_policy_changed": False,
                },
                source_record="learning_routing_improvements",
                reversible=False,
            )
            for row in rows
        ]

    def _topology_discoveries(
        self, limit: int, cursor: tuple[str, str] | None
    ) -> list[dict[str, Any]]:
        prefix = "topology_discovery:"
        rows = self._query(
            """
            SELECT id, task_class, topology, worker_count, parallelism,
                   created_at AS occurred_at
            FROM agent_topology_recipes
            WHERE (
                created_at < ? OR (
                    created_at = ? AND ? || id < ?
                )
            )
            ORDER BY occurred_at DESC, id DESC LIMIT ?
            """,
            prefix=prefix,
            limit=limit,
            cursor=cursor,
        )
        return [
            self._event(
                event_id=prefix + row["id"],
                category="topology_discovery",
                action="retain_verified_topology",
                status="advisory",
                autonomy="runtime_derived_advisory",
                actor="runtime",
                actor_attribution="retained_verified_outcome",
                summary=f"Verified {row['topology']} recipe retained",
                occurred_at=row["occurred_at"],
                evidence={
                    "task_class": row["task_class"],
                    "topology": row["topology"],
                    "worker_count": int(row["worker_count"]),
                    "parallelism": float(row["parallelism"]),
                    "production_topology_changed": False,
                },
                source_record="agent_topology_recipes",
                reversible=True,
            )
            for row in rows
        ]

    def _context_optimizations(
        self, limit: int, cursor: tuple[str, str] | None
    ) -> list[dict[str, Any]]:
        prefix = "context_optimization:"
        rows = self._query(
            """
            SELECT p.id, p.complexity, p.candidate_count, p.selected_count,
                   p.context_budget, p.created_at AS occurred_at,
                   COUNT(c.source_id) AS retained_blocks,
                   COALESCE(SUM(c.tokens), 0) AS selected_tokens,
                   COALESCE(SUM(COALESCE(c.original_tokens, c.tokens)), 0)
                       AS original_tokens,
                   COALESCE(SUM(
                       COALESCE(c.original_tokens, c.tokens) - c.tokens
                   ), 0) AS tokens_saved,
                   COALESCE(SUM(c.exact_preserved), 0) AS exact_blocks
            FROM token_budget_plans p
            LEFT JOIN context_uses c ON c.task_id=p.task_id
            WHERE (
                p.created_at < ? OR (
                    p.created_at = ? AND ? || p.id < ?
                )
            )
            GROUP BY p.id
            ORDER BY occurred_at DESC, p.id DESC LIMIT ?
            """,
            prefix=prefix,
            limit=limit,
            cursor=cursor,
        )
        return [
            self._event(
                event_id=prefix + row["id"],
                category="context_optimization",
                action="optimize_context_budget",
                status="applied",
                autonomy="automatic_within_requested_run",
                actor="runtime",
                actor_attribution="retained_budget_plan",
                summary="Context selection and compression budget applied",
                occurred_at=row["occurred_at"],
                evidence={
                    "complexity": row["complexity"],
                    "candidate_count": int(row["candidate_count"]),
                    "selected_count": int(row["selected_count"]),
                    "retained_blocks": int(row["retained_blocks"]),
                    "context_budget": int(row["context_budget"]),
                    "selected_tokens": int(row["selected_tokens"]),
                    "original_tokens": int(row["original_tokens"]),
                    "tokens_saved": int(row["tokens_saved"]),
                    "exact_blocks": int(row["exact_blocks"]),
                },
                source_record="token_budget_plans+context_uses",
                reversible=False,
            )
            for row in rows
        ]

    def events(
        self,
        *,
        limit: int = 50,
        cursor: str | None = None,
        category: str | None = None,
        autonomy: str | None = None,
    ) -> dict[str, Any]:
        if not 1 <= limit <= MAX_EVENTS:
            raise ValueError("limit must be between 1 and 100")
        if category is not None and category not in CATEGORIES:
            raise ValueError("unknown learning event category")
        if autonomy is not None and autonomy not in AUTONOMY:
            raise ValueError("unknown learning event autonomy")
        decoded = (
            self._decode_cursor(
                cursor, category=category, autonomy=autonomy
            )
            if cursor else None
        )
        readers: dict[str, Callable[
            [int, tuple[str, str] | None], list[dict[str, Any]]
        ]] = {
            "memory_promotion": (
                lambda page_limit, page_cursor: self._memory_promotions(
                    page_limit, page_cursor, autonomy
                )
            ),
            "memory_deletion": self._memory_deletions,
            "new_skill": self._new_skills,
            "skill_mutation": self._skill_mutations,
            "routing_change": self._routing_changes,
            "topology_discovery": self._topology_discoveries,
            "context_optimization": self._context_optimizations,
        }
        possible_autonomy = {
            "memory_promotion": {
                "explicit_approval", "proposal_only",
            },
            "memory_deletion": {"explicit_approval"},
            "new_skill": {"explicit_approval"},
            "skill_mutation": {"workflow_unattributed"},
            "routing_change": {"proposal_only"},
            "topology_discovery": {"runtime_derived_advisory"},
            "context_optimization": {
                "automatic_within_requested_run",
            },
        }
        selected = tuple(
            item_category
            for item_category in ((category,) if category else CATEGORIES)
            if (
                autonomy is None
                or autonomy in possible_autonomy[item_category]
            )
        )
        items = [
            event
            for item_category in selected
            for event in readers[item_category](limit, decoded)
            if autonomy is None or event["autonomy"] == autonomy
        ]
        items.sort(
            key=lambda item: (item["occurred_at"], item["id"]),
            reverse=True,
        )
        truncated = len(items) > limit
        visible = items[:limit]
        return {
            "status": "available" if visible else "empty",
            "items": visible,
            "count": len(visible),
            "next_cursor": (
                self._cursor(
                    str(visible[-1]["occurred_at"]),
                    str(visible[-1]["id"]),
                    category,
                    autonomy,
                )
                if truncated and visible else None
            ),
            "truncated": truncated,
            "reason": None if visible else "no_retained_learning_events",
            "as_of": _now(),
            "categories": list(CATEGORIES),
            "autonomy_states": list(AUTONOMY),
            "truth_notice": (
                "No self-initiated autonomous improvement loop is enabled. "
                "Proposals are not production changes, and runtime-derived "
                "records remain advisory or bounded to a requested run."
            ),
        }
