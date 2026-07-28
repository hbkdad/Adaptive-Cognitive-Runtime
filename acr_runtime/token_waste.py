from __future__ import annotations

import hashlib
import json
import sqlite3
import unicodedata
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Mapping

from .scoring import estimate_tokens
from .secret_management import assert_secret_free


ANALYZER_VERSION = "acr-token-waste-v1.0.0"
CATEGORIES = (
    "large_retrieved_blocks_never_used",
    "repeated_instructions",
    "duplicate_memories",
    "unnecessary_skill_text",
    "oversized_tool_descriptions",
    "full_files_when_symbols_sufficient",
    "excessive_reflection",
    "too_many_agents",
    "unnecessary_model_escalation",
)
VERDICTS = frozenset(
    {
        "observed_overhead",
        "candidate_waste",
        "counterfactually_avoidable",
        "protected",
        "confounded",
        "insufficient_evidence",
    }
)
MAX_SCOPE_LENGTH = 255
LARGE_CONTEXT_TOKENS = 128
OVERSIZED_TOOL_TOKENS = 256
MAX_SOURCE_ROWS = 10_000

RECOMMENDATIONS = {
    "large_retrieved_blocks_never_used": "run_paired_context_ablation",
    "repeated_instructions": "review_exact_repetition",
    "duplicate_memories": "review_reference_or_supersede",
    "unnecessary_skill_text": "run_paired_skill_ablation",
    "oversized_tool_descriptions": "benchmark_compact_projection",
    "full_files_when_symbols_sufficient": "prefer_hash_verified_symbol_slice",
    "excessive_reflection": "benchmark_single_reflection_pass",
    "too_many_agents": "benchmark_single_agent_baseline",
    "unnecessary_model_escalation": "benchmark_verified_model_cascade",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _digest(value: object) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _text_hash(value: str) -> str:
    normalized = unicodedata.normalize(
        "NFC", value.replace("\r\n", "\n").replace("\r", "\n")
    )
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _scope(value: str) -> str:
    if (
        not isinstance(value, str)
        or not value.strip()
        or len(value) > MAX_SCOPE_LENGTH
    ):
        raise ValueError("scope must be a non-empty bounded identifier")
    normalized = unicodedata.normalize("NFC", value)
    assert_secret_free(normalized, "scope")
    return normalized


@dataclass(frozen=True)
class TokenWasteFinding:
    sequence: int
    category: str
    verdict: str
    subject_count: int
    observed_tokens: int
    token_quality: str
    evidence_method: str
    savings_low: int | None
    savings_base: int | None
    savings_high: int | None
    evidence: dict[str, object]
    recommendation: str
    automatic_action_allowed: bool = False

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class TokenWasteRun:
    id: str
    scope_hash: str
    analyzer_version: str
    evidence_revision: str
    findings: tuple[TokenWasteFinding, ...]
    created_at: str

    def as_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "scope_hash": self.scope_hash,
            "analyzer_version": self.analyzer_version,
            "evidence_revision": self.evidence_revision,
            "findings": [item.as_dict() for item in self.findings],
            "summary": {
                verdict: sum(
                    item.verdict == verdict for item in self.findings
                )
                for verdict in sorted(VERDICTS)
            },
            "created_at": self.created_at,
        }


class TokenWasteAnalyzer:
    """Content-minimized, evidence-tiered, advisory token-waste analysis."""

    POLICY = {
        "analyzer_version": ANALYZER_VERSION,
        "large_context_tokens": LARGE_CONTEXT_TOKENS,
        "oversized_tool_tokens": OVERSIZED_TOOL_TOKENS,
        "categories": CATEGORIES,
        "automatic_actions": False,
        "raw_content_persisted": False,
        "unknown_is_waste": False,
        "counterfactual_required_for_savings": True,
        "provider_tokens_preferred": True,
        "maximum_rows_per_source": MAX_SOURCE_ROWS,
    }

    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def _source_snapshot(self, scope: str) -> dict[str, object]:
        context = [
            dict(row)
            for row in self.connection.execute(
                """
                SELECT u.task_id, u.source_type, u.source_id, u.tokens,
                       u.useful, u.compression_strategy, u.original_tokens,
                       u.exact_preserved, u.content_origin,
                       u.security_authority, s.content_hash,
                       a.outcome, a.confidence, a.execution_score,
                       a.evaluator_score
                FROM context_uses AS u
                JOIN tasks AS t ON t.id=u.task_id
                LEFT JOIN context_attributions AS a
                  ON a.task_id=u.task_id
                 AND a.source_type=u.source_type
                 AND a.source_id=u.source_id
                LEFT JOIN content_security_assessments AS s
                  ON s.id=u.security_assessment_id
                WHERE t.scope=?
                ORDER BY u.task_id, u.source_type, u.source_id
                LIMIT 10001
                """,
                (scope,),
            ).fetchall()
        ]
        dedup_memory = [
            dict(row)
            for row in self.connection.execute(
                """
                SELECT li.content_hash AS duplicate_group_hash,
                       COUNT(DISTINCT li.id || ':' || ri.id) AS pair_count
                FROM deduplication_runs AS r
                JOIN deduplication_matches AS m ON m.run_id=r.id
                JOIN deduplication_items AS li
                  ON li.run_id=r.id AND li.id=m.left_item_id
                JOIN deduplication_items AS ri
                  ON ri.run_id=r.id AND ri.id=m.right_item_id
                WHERE r.scope_hash=? AND r.sealed=1
                  AND m.relation='exact_duplicate'
                  AND li.kind='memory' AND ri.kind='memory'
                  AND li.content_hash=ri.content_hash
                GROUP BY li.content_hash
                ORDER BY li.content_hash
                LIMIT 10001
                """,
                (_digest({"scope": scope}),),
            ).fetchall()
        ]
        tools = [
            dict(row)
            for row in self.connection.execute(
                """
                SELECT name, description, input_schema_json,
                       output_schema_json, definition_hash
                FROM tool_definitions ORDER BY name LIMIT 10001
                """
            ).fetchall()
        ] if scope == "global" else []
        reflections = [
            dict(row)
            for row in self.connection.execute(
                """
                SELECT r.task_id, COUNT(*) AS run_count,
                       SUM(r.estimated_output_tokens) AS tokens
                FROM reflection_runs AS r
                JOIN tasks AS t ON t.id=r.task_id
                WHERE t.scope=?
                GROUP BY r.task_id HAVING COUNT(*) > 1
                ORDER BY r.task_id LIMIT 10001
                """,
                (scope,),
            ).fetchall()
        ]
        topologies = [
            dict(row)
            for row in self.connection.execute(
                """
                SELECT o.id, o.task_class, o.worker_count, o.tokens,
                       o.quality, o.verification_passed
                FROM agent_topology_outcomes AS o
                JOIN agent_factory_plans AS p ON p.id=o.plan_id
                WHERE o.worker_count > 1 AND o.verification_passed=1
                ORDER BY o.id LIMIT 10001
                """
            ).fetchall()
        ] if scope == "global" else []
        escalations = [
            dict(row)
            for row in self.connection.execute(
                """
                SELECT r.id, r.task_class, r.escalation_improved,
                       a.input_tokens + a.output_tokens AS tokens,
                       a.verification_passed, a.quality
                FROM model_routes AS r
                JOIN model_route_attempts AS a
                  ON a.route_id=r.id AND a.sequence=2
                WHERE r.escalation_improved=0
                ORDER BY r.id LIMIT 10001
                """
            ).fetchall()
        ] if scope == "global" else []
        sources = {
            "context": context,
            "dedup_memory": dedup_memory,
            "tools": tools,
            "reflections": reflections,
            "topologies": topologies,
            "escalations": escalations,
        }
        coverage: list[dict[str, object]] = []
        for name, values in sources.items():
            coverage.append(
                {
                    "source": name,
                    "available_rows": min(len(values), MAX_SOURCE_ROWS),
                    "truncated": len(values) > MAX_SOURCE_ROWS,
                }
            )
            sources[name] = values[:MAX_SOURCE_ROWS]
        sources["coverage"] = coverage
        return sources

    @staticmethod
    def _revision(snapshot: Mapping[str, object]) -> str:
        minimized: dict[str, object] = {}
        for key, rows in snapshot.items():
            minimized[key] = [
                {
                    column: (
                        _text_hash(value)
                        if isinstance(value, str)
                        and column
                        in {
                            "source_id",
                            "content",
                            "instructions",
                            "description",
                            "input_schema_json",
                            "output_schema_json",
                        }
                        else value
                    )
                    for column, value in row.items()
                }
                for row in rows  # type: ignore[union-attr]
            ]
        return _digest(
            {"analyzer_version": ANALYZER_VERSION, "sources": minimized}
        )

    def _findings(
        self, snapshot: dict[str, list[dict[str, object]]]
    ) -> tuple[TokenWasteFinding, ...]:
        context = snapshot["context"]
        coverage = {
            str(item["source"]): {
                "available_rows": int(item["available_rows"]),
                "truncated": bool(item["truncated"]),
            }
            for item in snapshot["coverage"]
        }
        results: list[TokenWasteFinding] = []

        def add(
            category: str,
            verdict: str,
            subjects: int,
            observed_tokens: int,
            *,
            token_quality: str = "estimated",
            method: str = "associated",
            evidence: Mapping[str, object] | None = None,
        ) -> None:
            savings: tuple[int | None, int | None, int | None] = (
                None,
                None,
                None,
            )
            results.append(
                TokenWasteFinding(
                    sequence=len(results) + 1,
                    category=category,
                    verdict=verdict,
                    subject_count=subjects,
                    observed_tokens=observed_tokens,
                    token_quality=token_quality,
                    evidence_method=method,
                    savings_low=savings[0],
                    savings_base=savings[1],
                    savings_high=savings[2],
                    evidence={
                        **dict(evidence or {}),
                        "coverage": coverage,
                    },
                    recommendation=RECOMMENDATIONS[category],
                )
            )

        large = [
            row
            for row in context
            if int(row["tokens"]) >= LARGE_CONTEXT_TOKENS
            and row["source_type"] in {"memory", "file", "tool", "observation"}
            and row["useful"] == 0
            and row["outcome"] == "ignored"
            and float(row["confidence"] or 0) >= 0.75
            and row["security_authority"] in {None, "none"}
        ]
        add(
            CATEGORIES[0],
            "candidate_waste" if large else "insufficient_evidence",
            len(large),
            sum(int(row["tokens"]) for row in large),
            evidence={
                "signal": "caller_attributed_ignored",
                "independent_counterfactual": False,
                "minimum_tokens": LARGE_CONTEXT_TOKENS,
            },
        )

        instruction_groups: dict[
            tuple[object, object, object, object], list[int]
        ] = {}
        for row in context:
            if (
                row["content_hash"] is not None
                and row["content_origin"]
                in {
                    "system_policy",
                    "developer_instruction",
                    "user_instruction",
                    "skill_instruction",
                }
            ):
                key = (
                    row["task_id"],
                    row["content_hash"],
                    row["content_origin"],
                    row["security_authority"],
                )
                instruction_groups.setdefault(key, []).append(int(row["tokens"]))
        repeated = [
            values for values in instruction_groups.values() if len(values) > 1
        ]
        add(
            CATEGORIES[1],
            "candidate_waste" if repeated else "insufficient_evidence",
            len(repeated),
            sum(sum(values) - max(values) for values in repeated),
            evidence={
                "signal": "same_task_same_authority_content_hash_repetition",
                "authority_preserved": True,
                "content_retained": False,
            },
        )

        duplicates = snapshot["dedup_memory"]
        add(
            CATEGORIES[2],
            "candidate_waste" if duplicates else "insufficient_evidence",
            len(duplicates),
            0,
            token_quality="unknown",
            evidence={
                "signal": "sealed_scope_partitioned_exact_dedup_match",
                "semantic_equivalence_proven": False,
                "tokens_unavailable_from_content_minimized_dedup": True,
            },
        )

        skill_rows: dict[str, list[dict[str, object]]] = {}
        for row in context:
            if row["source_type"] == "skill":
                skill_rows.setdefault(_text_hash(str(row["source_id"])), []).append(
                    row
                )
        ignored_skills = [
            rows
            for rows in skill_rows.values()
            if sum(
                row["outcome"] == "ignored"
                and float(row["confidence"] or 0) >= 0.75
                for row in rows
            )
            >= 2
            and not any(row["outcome"] == "contributed" for row in rows)
        ]
        add(
            CATEGORIES[3],
            "candidate_waste" if ignored_skills else "insufficient_evidence",
            len(ignored_skills),
            sum(sum(int(row["tokens"]) for row in rows) for rows in ignored_skills),
            evidence={
                "signal": "repeated_caller_attributed_ignored_skill",
                "specific_text_inferred": False,
            },
        )

        oversized: list[int] = []
        for row in snapshot["tools"]:
            tokens = estimate_tokens(
                "\n".join(
                    str(row[key])
                    for key in (
                        "description",
                        "input_schema_json",
                        "output_schema_json",
                    )
                )
            )
            if tokens > OVERSIZED_TOOL_TOKENS:
                oversized.append(tokens)
        add(
            CATEGORIES[4],
            "insufficient_evidence",
            len(oversized),
            0,
            token_quality="unknown",
            evidence={
                "signal": "registry_size_only",
                "schemas_counted": True,
                "canonical_rewrite_allowed": False,
                "delivery_telemetry_available": False,
                "estimated_registry_tokens": sum(oversized),
            },
        )

        file_rows = [
            row
            for row in context
            if row["source_type"] == "file"
            and row["compression_strategy"] == "none"
        ]
        add(
            CATEGORIES[5],
            "insufficient_evidence",
            len(file_rows),
            sum(int(row["tokens"]) for row in file_rows),
            evidence={
                "signal": "uncompressed_file_context",
                "symbol_sufficiency_proven": False,
                "requires_hash_verified_slice_comparison": True,
            },
        )

        reflections = snapshot["reflections"]
        add(
            CATEGORIES[6],
            "confounded" if reflections else "insufficient_evidence",
            len(reflections),
            sum(int(row["tokens"]) for row in reflections),
            evidence={
                "signal": "multiple_bounded_reflections_same_task",
                "task_difficulty_controlled": False,
            },
        )

        topologies = snapshot["topologies"]
        add(
            CATEGORIES[7],
            "confounded" if topologies else "insufficient_evidence",
            len(topologies),
            sum(int(row["tokens"]) for row in topologies),
            evidence={
                "signal": "verified_multi_worker_outcome",
                "single_agent_counterfactual_available": False,
                "plans_not_counted_as_execution": True,
                "scope_binding": (
                    "unscoped_global_inventory"
                    if topologies
                    else "no_bound_evidence"
                ),
            },
        )

        escalations = snapshot["escalations"]
        add(
            CATEGORIES[8],
            "confounded" if escalations else "insufficient_evidence",
            len(escalations),
            sum(int(row["tokens"]) for row in escalations),
            evidence={
                "signal": "second_route_attempt",
                "failed_first_attempt_cost_required": True,
                "difficulty_controlled": False,
                "scope_binding": (
                    "unscoped_global_inventory"
                    if escalations
                    else "no_bound_evidence"
                ),
            },
        )
        return tuple(results)

    def scan(self, *, scope: str = "global") -> TokenWasteRun:
        scope = _scope(scope)
        scope_hash = _text_hash(scope)
        if self.connection.in_transaction:
            raise RuntimeError("token-waste scans require their own transaction")
        try:
            self.connection.execute("BEGIN IMMEDIATE")
            snapshot = self._source_snapshot(scope)
            revision = self._revision(snapshot)
            existing = self.connection.execute(
                """
                SELECT id FROM token_waste_runs
                WHERE analyzer_version=? AND scope_hash=?
                  AND evidence_revision=? AND status='completed'
                """,
                (ANALYZER_VERSION, scope_hash, revision),
            ).fetchone()
            if existing is not None:
                run_id = existing["id"]
                self.connection.commit()
                return self.load(run_id, scope=scope)

            findings = self._findings(snapshot)
            if len(findings) != len(CATEGORIES):
                raise RuntimeError(
                    "token-waste scan must produce all nine categories"
                )
            run_id = str(uuid.uuid4())
            created_at = _now()
            self.connection.execute(
                """
                INSERT INTO token_waste_runs(
                    id, scope_hash, analyzer_version, policy_json,
                    evidence_revision, expected_findings, status, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, 'running', ?)
                """,
                (
                    run_id,
                    scope_hash,
                    ANALYZER_VERSION,
                    _json(self.POLICY),
                    revision,
                    len(CATEGORIES),
                    created_at,
                ),
            )
            for item in findings:
                self.connection.execute(
                    """
                    INSERT INTO token_waste_findings(
                        id, run_id, sequence, category, verdict,
                        subject_count, observed_tokens, token_quality,
                        evidence_method, savings_low, savings_base,
                        savings_high, evidence_json, recommendation,
                        automatic_action_allowed, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?)
                    """,
                    (
                        str(uuid.uuid4()),
                        run_id,
                        item.sequence,
                        item.category,
                        item.verdict,
                        item.subject_count,
                        item.observed_tokens,
                        item.token_quality,
                        item.evidence_method,
                        item.savings_low,
                        item.savings_base,
                        item.savings_high,
                        _json(item.evidence),
                        item.recommendation,
                        created_at,
                    ),
                )
            self.connection.execute(
                """
                UPDATE token_waste_runs
                SET status='completed', completed_at=?
                WHERE id=? AND status='running'
                """,
                (_now(), run_id),
            )
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise
        return self.load(run_id, scope=scope)

    def load(self, run_id: str, *, scope: str) -> TokenWasteRun:
        scope_hash = _text_hash(_scope(scope))
        row = self.connection.execute(
            """
            SELECT * FROM token_waste_runs
            WHERE id=? AND scope_hash=? AND status='completed'
            """,
            (run_id, scope_hash),
        ).fetchone()
        if row is None:
            raise KeyError(run_id)
        finding_rows = self.connection.execute(
            """
            SELECT * FROM token_waste_findings
            WHERE run_id=? ORDER BY sequence
            """,
            (run_id,),
        ).fetchall()
        if len(finding_rows) != int(row["expected_findings"]):
            raise RuntimeError("token-waste report is incomplete")
        findings = tuple(
            TokenWasteFinding(
                sequence=item["sequence"],
                category=item["category"],
                verdict=item["verdict"],
                subject_count=item["subject_count"],
                observed_tokens=item["observed_tokens"],
                token_quality=item["token_quality"],
                evidence_method=item["evidence_method"],
                savings_low=item["savings_low"],
                savings_base=item["savings_base"],
                savings_high=item["savings_high"],
                evidence=json.loads(item["evidence_json"]),
                recommendation=item["recommendation"],
                automatic_action_allowed=bool(
                    item["automatic_action_allowed"]
                ),
            )
            for item in finding_rows
        )
        return TokenWasteRun(
            id=row["id"],
            scope_hash=row["scope_hash"],
            analyzer_version=row["analyzer_version"],
            evidence_revision=row["evidence_revision"],
            findings=findings,
            created_at=row["created_at"],
        )
