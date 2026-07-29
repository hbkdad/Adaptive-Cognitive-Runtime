from __future__ import annotations

import json
import sqlite3
from typing import Any


class RuntimeExplainability:
    """Read-only explanations assembled only from retained decision evidence."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    @staticmethod
    def _result(
        question: str,
        status: str,
        facts: dict[str, object],
        sources: list[dict[str, str]],
        limitations: list[str] | None = None,
    ) -> dict[str, object]:
        return {
            "question": question,
            "status": status,
            "facts": facts,
            "sources": sources,
            "limitations": limitations or [],
            "narrative_generated": False,
        }

    def model(self, route_id: str) -> dict[str, object]:
        row = self.connection.execute(
            "SELECT * FROM model_routes WHERE id=?", (route_id,)
        ).fetchone()
        if row is None:
            raise KeyError(route_id)
        candidates = json.loads(row["candidates_json"])
        selected_id = row["selected_model_id"]
        selected = next(
            (item for item in candidates if item["model_id"] == selected_id),
            None,
        )
        eligible = [item for item in candidates if item["eligible"]]
        cheapest = min(
            eligible, key=lambda item: (
                float(item["expected_cost"]),
                -float(item["quality_lower_bound"]),
                float(item["average_latency_ms"] or float("inf")),
                str(item["model_id"]),
            ),
            default=None,
        )
        limitations: list[str] = []
        status = "supported"
        if selected is None:
            status = "unavailable"
            limitations.append("no_model_was_selected")
        elif cheapest is not None and cheapest["model_id"] != selected_id:
            status = "partial"
            limitations.append(
                "external_allowed_or_preferred_model_constraints_were_not_retained"
            )
        return self._result(
            "Why was this model chosen?",
            status,
            {
                "route_id": route_id,
                "state": row["state"],
                "selected_model_id": selected_id,
                "selected_candidate": selected,
                "eligible_candidate_count": len(eligible),
                "stored_cheapest_eligible_model_id": (
                    None if cheapest is None else cheapest["model_id"]
                ),
                "rejected_candidates": [
                    {
                        "model_id": item["model_id"],
                        "rejection_reasons": item["rejection_reasons"],
                    }
                    for item in candidates if not item["eligible"]
                ],
            },
            [{"table": "model_routes", "id": route_id}],
            limitations,
        )

    def skill(self, task_id: str, skill_id: str) -> dict[str, object]:
        row = self.connection.execute(
            """
            SELECT c.*, r.task_class, r.token_budget, r.semantic_available
            FROM skill_routing_candidates c
            JOIN skill_routing_runs r ON r.id=c.run_id
            WHERE r.task_id=? AND c.skill_id=?
            """,
            (task_id, skill_id),
        ).fetchone()
        if row is None:
            return self._result(
                "Why was this skill loaded?", "unavailable",
                {"task_id": task_id, "skill_id": skill_id},
                [], ["no_skill_routing_candidate_was_retained"],
            )
        facts = dict(row)
        facts["router_selected"] = bool(facts["router_selected"])
        facts["compiler_selected"] = bool(facts["compiler_selected"])
        return self._result(
            "Why was this skill loaded?",
            "supported" if facts["compiler_selected"] else "not_loaded",
            facts,
            [
                {"table": "skill_routing_runs", "id": row["run_id"]},
                {"table": "skill_routing_candidates", "id": skill_id},
            ],
        )

    def memory(self, task_id: str, memory_id: str) -> dict[str, object]:
        use = self.connection.execute(
            """
            SELECT * FROM context_uses
            WHERE task_id=? AND source_type='memory' AND source_id=?
            """,
            (task_id, memory_id),
        ).fetchone()
        if use is None:
            return self._result(
                "Why was this memory retrieved?", "unavailable",
                {"task_id": task_id, "memory_id": memory_id},
                [], ["no_compiled_memory_use_was_retained"],
            )
        attribution = self.connection.execute(
            """
            SELECT role, outcome, impact_score, confidence, approximate_roi,
                   evidence_json
            FROM context_attributions
            WHERE task_id=? AND source_type='memory' AND source_id=?
            """,
            (task_id, memory_id),
        ).fetchone()
        facts = dict(use)
        if attribution is not None:
            facts["attribution"] = {
                **dict(attribution),
                "evidence": json.loads(attribution["evidence_json"]),
            }
            facts["attribution"].pop("evidence_json")
        return self._result(
            "Why was this memory retrieved?",
            "supported",
            facts,
            [{"table": "context_uses", "id": f"{task_id}:memory:{memory_id}"}]
            + (
                [{"table": "context_attributions", "id": f"{task_id}:memory:{memory_id}"}]
                if attribution is not None else []
            ),
        )

    def agent(self, plan_id: str, worker_id: str | None = None) -> dict[str, object]:
        plan = self.connection.execute(
            "SELECT * FROM agent_factory_plans WHERE id=?", (plan_id,)
        ).fetchone()
        if plan is None:
            raise KeyError(plan_id)
        params: tuple[Any, ...] = (plan_id,)
        clause = ""
        if worker_id is not None:
            clause = " AND id=?"
            params = (plan_id, worker_id)
        workers = self.connection.execute(
            f"""
            SELECT id, sequence, responsibility, spec_json,
                   context_scope_json, status
            FROM agent_factory_workers WHERE plan_id=?{clause}
            ORDER BY sequence
            """,
            params,
        ).fetchall()
        if worker_id is not None and not workers:
            raise KeyError(worker_id)
        worker_facts = []
        for item in workers:
            spec = json.loads(item["spec_json"])
            worker_facts.append(
                {
                    "id": item["id"],
                    "sequence": item["sequence"],
                    "responsibility": item["responsibility"],
                    "status": item["status"],
                    "role": spec["role"],
                    "task_scope": spec["task_scope"],
                    "tools": spec["tools"],
                    "skills": spec["skills"],
                    "model_policy": spec["model_policy"],
                    "token_budget": spec["token_budget"],
                    "money_budget": spec["money_budget"],
                    "time_budget": spec["time_budget"],
                    "permissions": spec["permissions"],
                    "verification_requirement_count": len(
                        spec["verification_requirements"]
                    ),
                    "communication": spec["communication"],
                    "context_scope": json.loads(item["context_scope_json"]),
                }
            )
        return self._result(
            "Why was this agent spawned?",
            "not_executed",
            {
                "plan_id": plan_id,
                "selected_topology": plan["selected_topology"],
                "selected_estimate": json.loads(plan["selected_estimate_json"]),
                "plan_status": plan["status"],
                "workers": worker_facts,
            },
            [{"table": "agent_factory_plans", "id": plan_id}],
            [
                "agent_factory_status_is_proposed",
                "no_agent_spawn_or_execution_receipt_exists",
            ],
        )

    def context(self, task_id: str) -> dict[str, object]:
        task = self.connection.execute(
            """
            SELECT id, token_budget, selected_tokens, status
            FROM tasks WHERE id=?
            """,
            (task_id,),
        ).fetchone()
        if task is None:
            raise KeyError(task_id)
        rows = self.connection.execute(
            """
            SELECT source_type, source_id, tokens, utility, roi, useful,
                   compression_strategy, original_tokens, exact_preserved
            FROM context_uses WHERE task_id=?
            ORDER BY tokens DESC, source_type, source_id
            """,
            (task_id,),
        ).fetchall()
        measured = sum(int(item["tokens"]) for item in rows)
        plan = self.connection.execute(
            "SELECT * FROM token_budget_plans WHERE task_id=?", (task_id,)
        ).fetchone()
        return self._result(
            "Why did context consume these tokens?",
            "supported" if rows else "unavailable",
            {
                "task_id": task_id,
                "task_selected_tokens": task["selected_tokens"],
                "summed_context_use_tokens": measured,
                "totals_match": measured == int(task["selected_tokens"]),
                "task_token_budget": task["token_budget"],
                "budget_plan": None if plan is None else dict(plan),
                "sources": [dict(item) for item in rows],
            },
            [{"table": "tasks", "id": task_id}]
            + [{"table": "context_uses", "id": task_id}]
            + (
                [{"table": "token_budget_plans", "id": plan["id"]}]
                if plan is not None else []
            ),
            [] if rows else ["no_context_use_rows_were_retained"],
        )

    def forgotten(self, memory_id: str) -> dict[str, object]:
        memory = self.connection.execute(
            """
            SELECT id, status, lifecycle_state, superseded_by, retention_until,
                   retention_reason_json, lifecycle_updated_at, archived_at,
                   deleted_at
            FROM memories WHERE id=?
            """,
            (memory_id,),
        ).fetchone()
        if memory is None:
            raise KeyError(memory_id)
        gc = self.connection.execute(
            """
            SELECT run_id, from_state, to_state, score_json, reason, status,
                   error_type, created_at, applied_at
            FROM memory_gc_actions WHERE memory_id=?
            ORDER BY created_at
            """,
            (memory_id,),
        ).fetchall()
        deletion = self.connection.execute(
            """
            SELECT id, classification, deletion_requirement, requested_by,
                   reason, status, verification_json, created_at, completed_at
            FROM memory_deletion_requests WHERE memory_id=?
            ORDER BY created_at
            """,
            (memory_id,),
        ).fetchall()
        facts = dict(memory)
        facts["retention_reasons"] = json.loads(
            facts.pop("retention_reason_json")
        )
        facts["gc_actions"] = [
            {**dict(item), "score": json.loads(item["score_json"])}
            for item in gc
        ]
        for item in facts["gc_actions"]:
            item.pop("score_json")
        facts["deletion_requests"] = [
            {**dict(item), "verification": json.loads(item["verification_json"])}
            for item in deletion
        ]
        for item in facts["deletion_requests"]:
            item.pop("verification_json")
        forgotten = (
            memory["lifecycle_state"] in {"archived", "deleted"}
            or memory["status"] in {"archived", "deleted", "superseded"}
        )
        return self._result(
            "Why was this memory forgotten?",
            "supported" if forgotten and (gc or deletion or memory["superseded_by"])
            else ("partial" if forgotten else "not_forgotten"),
            facts,
            [{"table": "memories", "id": memory_id}]
            + [{"table": "memory_gc_actions", "id": item["run_id"]} for item in gc]
            + [{"table": "memory_deletion_requests", "id": item["id"]} for item in deletion],
            (
                ["terminal_state_has_no_retained_causal_action"]
                if forgotten and not (gc or deletion or memory["superseded_by"])
                else []
            ),
        )
