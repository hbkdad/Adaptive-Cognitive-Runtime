from __future__ import annotations

import math
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable

from .autonomous_improvement import digest


ASSET_KINDS = frozenset(
    {
        "memory",
        "skill",
        "model",
        "tool",
        "agent_topology",
        "context_strategy",
    }
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


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
class UtilitySnapshot:
    asset_id: str
    asset_kind: str
    external_id_hash: str
    revision_hash: str
    observed_uses: int
    evidenced_uses: int
    positive_count: int
    ignored_count: int
    misled_count: int
    failed_count: int
    utility: float
    signed_utility: float
    confidence: float
    assessment: str
    recommendation: str
    last_observed_at: str | None
    evidence_revision: str

    def as_dict(self) -> dict[str, object]:
        return dict(self.__dict__)


class UtilityGovernor:
    """Append-only, outcome-grounded utility across governed asset revisions."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def _identity(
        self, kind: str, external_id: str
    ) -> tuple[str, str]:
        if kind not in ASSET_KINDS:
            raise ValueError("unknown utility asset kind")
        if kind == "memory":
            row = self.connection.execute(
                """
                SELECT id, scope, content, structured_payload_json, status,
                       lifecycle_state, valid_from, valid_until, superseded_by,
                       sensitivity
                FROM memories WHERE id = ?
                """,
                (external_id,),
            ).fetchone()
            if row is None:
                raise LookupError(external_id)
            return digest(dict(row)), str(row["scope"])
        if kind == "skill":
            row = self.connection.execute(
                "SELECT content_hash FROM skills WHERE id = ?", (external_id,)
            ).fetchone()
            if row is None:
                raise LookupError(external_id)
            return str(row["content_hash"] or digest({"legacy": external_id})), "global"
        if kind == "model":
            row = self.connection.execute(
                """
                SELECT provider, model, context_capacity, supports_tools,
                       input_cost_per_million, output_cost_per_million,
                       local, tier
                FROM model_profiles WHERE id = ?
                """,
                (external_id,),
            ).fetchone()
            if row is None:
                raise LookupError(external_id)
            return digest(dict(row)), "global"
        if kind == "tool":
            row = self.connection.execute(
                """
                SELECT definition_hash FROM tool_definitions WHERE name = ?
                """,
                (external_id,),
            ).fetchone()
            if row is None:
                raise LookupError(external_id)
            return str(row["definition_hash"]), "global"
        if kind == "agent_topology":
            row = self.connection.execute(
                """
                SELECT structure_hash, task_class
                FROM agent_topology_outcomes
                WHERE structure_hash = ?
                ORDER BY created_at DESC LIMIT 1
                """,
                (external_id,),
            ).fetchone()
            if row is None:
                raise LookupError(external_id)
            return str(row["structure_hash"]), str(row["task_class"])
        row = self.connection.execute(
            """
            SELECT config_hash FROM meta_context_strategies
            WHERE id = ? OR config_hash = ?
            ORDER BY created_at DESC LIMIT 1
            """,
            (external_id, external_id),
        ).fetchone()
        if row is not None:
            return str(row["config_hash"]), "global"
        if len(external_id) == 64 and not set(external_id) - set("0123456789abcdef"):
            return external_id, "global"
        raise LookupError(external_id)

    def register(self, kind: str, external_id: str) -> sqlite3.Row:
        revision, scope = self._identity(kind, external_id)
        external_hash = digest({"external_id": external_id})
        row = self.connection.execute(
            """
            SELECT * FROM utility_assets
            WHERE asset_kind = ? AND external_id_hash = ?
              AND revision_hash = ?
            """,
            (kind, external_hash, revision),
        ).fetchone()
        if row is None:
            self.connection.execute(
                """
                INSERT INTO utility_assets (
                    id, asset_kind, external_id_hash, revision_hash,
                    scope_hash, registered_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    str(uuid.uuid4()),
                    kind,
                    external_hash,
                    revision,
                    digest({"scope": scope}),
                    _now(),
                ),
            )
            row = self.connection.execute(
                """
                SELECT * FROM utility_assets
                WHERE asset_kind = ? AND external_id_hash = ?
                  AND revision_hash = ?
                """,
                (kind, external_hash, revision),
            ).fetchone()
        return row

    def bind_context(
        self,
        task_id: str,
        sources: Iterable[tuple[str, str]],
    ) -> None:
        timestamp = _now()
        for kind, external_id in sources:
            if kind not in {"memory", "skill"}:
                continue
            asset = self.register(kind, external_id)
            self.connection.execute(
                """
                INSERT OR IGNORE INTO utility_context_selections (
                    task_id, source_type, source_id_hash, asset_id, selected_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    task_id,
                    kind,
                    digest({"external_id": external_id}),
                    asset["id"],
                    timestamp,
                ),
            )

    def bind_context_strategy(
        self, task_id: str, strategy: dict[str, object]
    ) -> None:
        config_hash = digest(strategy)
        asset = self.register("context_strategy", config_hash)
        self.connection.execute(
            """
            INSERT OR IGNORE INTO context_strategy_uses (
                task_id, asset_id, config_hash, status, selected_at
            ) VALUES (?, ?, ?, 'selected', ?)
            """,
            (task_id, asset["id"], config_hash, _now()),
        )

    def _append(
        self,
        *,
        asset_id: str,
        root_kind: str,
        root_id: str,
        role: str,
        outcome: str,
        evidenced: bool,
        benefit_micros: int,
        harmful: bool,
        tokens: int = 0,
        latency_ms: int = 0,
        measured_cost_micros: int = 0,
        evidence: dict[str, object],
    ) -> None:
        if type(benefit_micros) is not int or not -1_000_000 <= benefit_micros <= 1_000_000:
            raise ValueError("benefit_micros must be a bounded integer")
        values = (tokens, latency_ms, measured_cost_micros)
        if any(type(value) is not int or value < 0 for value in values):
            raise ValueError("resource metrics must be non-negative integers")
        if not evidenced:
            outcome = "uncertain"
            benefit_micros = 0
            harmful = False
        self.connection.execute(
            """
            INSERT OR IGNORE INTO utility_observations (
                id, asset_id, root_kind, root_id_hash, role, outcome,
                evidenced, benefit_micros, harmful, tokens, latency_ms,
                measured_cost_micros, evidence_hash, observed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(uuid.uuid4()),
                asset_id,
                root_kind,
                digest({"root_id": root_id}),
                role,
                outcome,
                int(evidenced),
                benefit_micros,
                int(harmful),
                tokens,
                latency_ms,
                measured_cost_micros,
                digest(evidence),
                _now(),
            ),
        )

    def observe_context_task(self, task_id: str) -> None:
        task = self.connection.execute(
            """
            SELECT status, critic_score, duration_ms
            FROM tasks WHERE id = ?
            """,
            (task_id,),
        ).fetchone()
        execution = self.connection.execute(
            """
            SELECT state, duration_ms, verification_score
            FROM execution_runs WHERE task_id = ?
            ORDER BY completed_at DESC LIMIT 1
            """,
            (task_id,),
        ).fetchone()
        evaluation = self.connection.execute(
            """
            SELECT passed, score FROM evaluation_runs WHERE task_id = ?
            ORDER BY created_at DESC LIMIT 1
            """,
            (task_id,),
        ).fetchone()
        terminal_execution = bool(
            execution
            and execution["state"] in {"completed", "failed", "cancelled"}
        )
        if task is None or not (
            task["status"] in {"succeeded", "failed"} or terminal_execution
        ):
            raise ValueError("utility requires a terminal task")
        verified = bool(
            execution
            and execution["verification_score"] is not None
            and evaluation
        )
        task_succeeded = bool(
            task["status"] == "succeeded"
            or (
                execution
                and execution["state"] == "completed"
                and evaluation
                and evaluation["passed"]
            )
        )
        duration_ms = int(
            task["duration_ms"]
            or (execution["duration_ms"] if execution else 0)
        )
        # SQLite cannot hash u.source_id; resolve the small task-local set in Python.
        selections = self.connection.execute(
            """
            SELECT s.source_type, s.source_id_hash, s.asset_id,
                   u.source_id, u.tokens, a.outcome, a.execution_score,
                   a.evaluator_score
            FROM utility_context_selections AS s
            JOIN context_uses AS u
              ON u.task_id = s.task_id AND u.source_type = s.source_type
            LEFT JOIN context_attributions AS a
              ON a.task_id = s.task_id
             AND a.source_type = s.source_type
             AND a.source_id = u.source_id
            WHERE s.task_id = ?
            """,
            (task_id,),
        ).fetchall()
        for row in selections:
            if row["source_id_hash"] != digest(
                {"external_id": row["source_id"]}
            ):
                continue
            direct = bool(
                verified
                and row["execution_score"] is not None
                and row["outcome"] != "uncertain"
            )
            outcome = str(row["outcome"] or "uncertain")
            if outcome == "contributed" and not task_succeeded:
                outcome = "failed"
            quality = row["evaluator_score"]
            if quality is None:
                quality = task["critic_score"] or 0
            benefit = (
                round(float(quality) * 1_000_000)
                if direct and outcome == "contributed"
                else (-1_000_000 if direct and outcome == "misled" else 0)
            )
            self._append(
                asset_id=str(row["asset_id"]),
                root_kind="task",
                root_id=task_id,
                role=(
                    "context_memory"
                    if row["source_type"] == "memory"
                    else "context_skill"
                ),
                outcome=outcome,
                evidenced=direct,
                benefit_micros=benefit,
                harmful=outcome == "misled",
                tokens=int(row["tokens"]),
                latency_ms=duration_ms,
                evidence={
                    "task": digest({"task_id": task_id}),
                    "outcome": outcome,
                    "verified": direct,
                },
            )
        strategy = self.connection.execute(
            """
            SELECT * FROM context_strategy_uses WHERE task_id = ?
            """,
            (task_id,),
        ).fetchone()
        if strategy is not None:
            self._append(
                asset_id=str(strategy["asset_id"]),
                root_kind="task",
                root_id=task_id,
                role="context_strategy",
                outcome="uncertain",
                evidenced=False,
                benefit_micros=0,
                harmful=False,
                latency_ms=duration_ms,
                evidence={
                    "task": digest({"task_id": task_id}),
                    "config_hash": strategy["config_hash"],
                },
            )
            if strategy["status"] == "selected":
                self.connection.execute(
                    """
                    UPDATE context_strategy_uses
                    SET status = 'resolved', resolved_at = ?
                    WHERE task_id = ? AND status = 'selected'
                    """,
                    (_now(), task_id),
                )

    def observe_model_attempt(self, attempt_id: str) -> None:
        row = self.connection.execute(
            """
            SELECT a.*, r.task_class, o.success AS utility_success
            FROM model_route_attempts AS a
            JOIN model_routes AS r ON r.id = a.route_id
            JOIN model_outcomes AS o ON o.id = a.outcome_id
            WHERE a.id = ?
            """,
            (attempt_id,),
        ).fetchone()
        if row is None:
            raise LookupError(attempt_id)
        asset = self.register("model", str(row["model_id"]))
        passed = bool(row["utility_success"])
        self._append(
            asset_id=str(asset["id"]),
            root_kind="model_route",
            root_id=str(row["route_id"]),
            role="model_attempt",
            outcome="contributed" if passed else "failed",
            evidenced=True,
            benefit_micros=round(float(row["quality"]) * 1_000_000) if passed else 0,
            harmful=False,
            tokens=int(row["input_tokens"]) + int(row["output_tokens"]),
            latency_ms=int(row["latency_ms"]),
            measured_cost_micros=round(
                (float(row["input_cost"]) + float(row["output_cost"])) * 1_000_000
            ),
            evidence={"attempt_id": attempt_id, "verification_passed": passed},
        )

    def observe_tool_outcome(self, outcome_id: str) -> None:
        row = self.connection.execute(
            "SELECT * FROM tool_outcomes WHERE id = ?", (outcome_id,)
        ).fetchone()
        if row is None:
            raise LookupError(outcome_id)
        asset = self.register("tool", str(row["tool_name"]))
        passed = bool(row["success"])
        self._append(
            asset_id=str(asset["id"]),
            root_kind="tool_route",
            root_id=str(row["route_id"]),
            role="tool_invocation",
            outcome="contributed" if passed else "failed",
            evidenced=True,
            benefit_micros=1_000_000 if passed else 0,
            harmful=False,
            latency_ms=int(row["latency_ms"]),
            measured_cost_micros=round(float(row["cost"]) * 1_000_000),
            evidence={"outcome_id": outcome_id, "success": passed},
        )

    def observe_topology_outcome(self, outcome_id: str) -> None:
        row = self.connection.execute(
            "SELECT * FROM agent_topology_outcomes WHERE id = ?", (outcome_id,)
        ).fetchone()
        if row is None:
            raise LookupError(outcome_id)
        asset = self.register("agent_topology", str(row["structure_hash"]))
        verified = bool(row["verification_passed"])
        passed = verified and bool(row["success"])
        self._append(
            asset_id=str(asset["id"]),
            root_kind="agent_plan",
            root_id=str(row["plan_id"]),
            role="agent_topology",
            outcome=("contributed" if passed else ("failed" if verified else "uncertain")),
            evidenced=verified,
            benefit_micros=round(float(row["quality"]) * 1_000_000) if passed else 0,
            harmful=False,
            tokens=int(row["tokens"]),
            latency_ms=int(row["latency_ms"]),
            evidence={"outcome_id": outcome_id, "verified": verified},
        )

    def snapshot(self, kind: str, external_id: str) -> UtilitySnapshot:
        asset = self.register(kind, external_id)
        observations = self.connection.execute(
            """
            SELECT outcome, evidenced, benefit_micros, harmful, observed_at,
                   evidence_hash
            FROM utility_observations
            WHERE asset_id = ? ORDER BY observed_at, id
            """,
            (asset["id"],),
        ).fetchall()
        evidenced = [row for row in observations if row["evidenced"]]
        positive = sum(row["outcome"] == "contributed" for row in evidenced)
        ignored = sum(row["outcome"] == "ignored" for row in evidenced)
        misled = sum(row["outcome"] == "misled" for row in evidenced)
        failed = sum(row["outcome"] == "failed" for row in evidenced)
        lower = _wilson_lower(positive, len(evidenced))
        signed = (
            sum(int(row["benefit_micros"]) for row in evidenced)
            / max(1, len(evidenced))
            / 1_000_000
        )
        confidence = min(1.0, len(evidenced) / 10)
        if not evidenced:
            assessment, recommendation = "unassessed", "collect_evidence"
        elif misled:
            assessment, recommendation = "degrading", "lifecycle_review"
        elif len(evidenced) < 3:
            assessment, recommendation = "probation", "review"
        elif lower < 0.20:
            assessment, recommendation = "degrading", "lifecycle_review"
        else:
            assessment, recommendation = "productive", "retain"
        revision = digest(
            {
                "asset_id": asset["id"],
                "observations": [row["evidence_hash"] for row in observations],
                "estimator": "wilson95-v1",
            }
        )
        snapshot = UtilitySnapshot(
            asset_id=str(asset["id"]),
            asset_kind=kind,
            external_id_hash=str(asset["external_id_hash"]),
            revision_hash=str(asset["revision_hash"]),
            observed_uses=len(observations),
            evidenced_uses=len(evidenced),
            positive_count=positive,
            ignored_count=ignored,
            misled_count=misled,
            failed_count=failed,
            utility=lower,
            signed_utility=max(-1.0, min(1.0, signed)),
            confidence=confidence,
            assessment=assessment,
            recommendation=recommendation,
            last_observed_at=(
                str(observations[-1]["observed_at"]) if observations else None
            ),
            evidence_revision=revision,
        )
        self.connection.execute(
            """
            INSERT OR IGNORE INTO utility_snapshots (
                id, asset_id, evidence_revision, observed_uses,
                evidenced_uses, positive_count, ignored_count, misled_count,
                failed_count, utility_micros, signed_utility_micros,
                confidence_micros, assessment, recommendation,
                last_observed_at, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(uuid.uuid4()),
                snapshot.asset_id,
                revision,
                snapshot.observed_uses,
                snapshot.evidenced_uses,
                positive,
                ignored,
                misled,
                failed,
                round(lower * 1_000_000),
                round(snapshot.signed_utility * 1_000_000),
                round(confidence * 1_000_000),
                assessment,
                recommendation,
                snapshot.last_observed_at,
                _now(),
            ),
        )
        return snapshot

    def inventory(self) -> list[dict[str, object]]:
        sources = (
            ("memory", "SELECT id FROM memories"),
            ("skill", "SELECT id FROM skills"),
            ("model", "SELECT id FROM model_profiles"),
            ("tool", "SELECT name AS id FROM tool_definitions"),
            (
                "agent_topology",
                "SELECT DISTINCT structure_hash AS id FROM agent_topology_outcomes",
            ),
            (
                "context_strategy",
                "SELECT id FROM meta_context_strategies",
            ),
        )
        result: list[dict[str, object]] = []
        for kind, query in sources:
            for row in self.connection.execute(query):
                result.append(self.snapshot(kind, str(row["id"])).as_dict())
        return result
