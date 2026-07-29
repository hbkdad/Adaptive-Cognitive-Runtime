from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from dataclasses import dataclass

from .memory import utc_now
from .secret_management import assert_secret_free


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _hash(value: object) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _text(value: object, field: str, maximum: int = 512) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be text")
    text = value.strip()
    if not text or len(text) > maximum:
        raise ValueError(f"{field} must be 1..{maximum} characters")
    return text


@dataclass(frozen=True)
class EvidenceGraphRequest:
    research_run_id: str
    task_id: str
    decision_memory_id: str
    skill_id: str
    assertion_evidence: tuple[str, ...]

    def __post_init__(self) -> None:
        for field in (
            "research_run_id", "task_id", "decision_memory_id", "skill_id"
        ):
            _text(getattr(self, field), field)
        if not 1 <= len(self.assertion_evidence) <= 32:
            raise ValueError("assertion_evidence requires 1..32 items")
        for item in self.assertion_evidence:
            text = _text(item, "assertion evidence", 2_000)
            assert_secret_free(text, "evidence graph assertion")

    @classmethod
    def from_dict(cls, payload: object) -> "EvidenceGraphRequest":
        fields = {
            "research_run_id", "task_id", "decision_memory_id",
            "skill_id", "assertion_evidence",
        }
        if not isinstance(payload, dict) or set(payload) != fields:
            raise ValueError(f"evidence graph request requires {sorted(fields)}")
        if not isinstance(payload["assertion_evidence"], list):
            raise ValueError("assertion_evidence must be a list")
        return cls(
            research_run_id=payload["research_run_id"],
            task_id=payload["task_id"],
            decision_memory_id=payload["decision_memory_id"],
            skill_id=payload["skill_id"],
            assertion_evidence=tuple(payload["assertion_evidence"]),
        )


class EvidenceGraph:
    """Typed relational provenance over canonical ACR records."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def _node(
        self, node_type: str, native_kind: str, native_id: str, content_hash: str
    ) -> str:
        row = self.connection.execute(
            """
            SELECT id, content_hash FROM evidence_graph_nodes
            WHERE node_type=? AND native_kind=? AND native_id=?
            """,
            (node_type, native_kind, native_id),
        ).fetchone()
        if row is not None:
            if row["content_hash"] != content_hash:
                raise ValueError("canonical graph node changed after retention")
            return row["id"]
        node_id = str(uuid.uuid4())
        self.connection.execute(
            """
            INSERT INTO evidence_graph_nodes(
                id, node_type, native_kind, native_id, content_hash, created_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                node_id, node_type, native_kind, native_id,
                content_hash, utc_now(),
            ),
        )
        return node_id

    def _edge(
        self,
        source: str,
        target: str,
        relation: str,
        assertion_hash: str,
    ) -> str:
        row = self.connection.execute(
            """
            SELECT id FROM evidence_graph_edges
            WHERE from_node_id=? AND to_node_id=? AND relation=?
              AND assertion_hash=?
            """,
            (source, target, relation, assertion_hash),
        ).fetchone()
        if row is not None:
            return row["id"]
        edge_id = str(uuid.uuid4())
        self.connection.execute(
            """
            INSERT INTO evidence_graph_edges(
                id, from_node_id, to_node_id, relation,
                assertion_provenance, assertion_hash, created_at
            ) VALUES (?, ?, ?, ?, 'caller_asserted_unverified', ?, ?)
            """,
            (edge_id, source, target, relation, assertion_hash, utc_now()),
        )
        return edge_id

    def create(self, request: EvidenceGraphRequest) -> dict[str, object]:
        assertion_hash = _hash(sorted(set(request.assertion_evidence)))
        existing = self.connection.execute(
            """
            SELECT id, assertion_hash FROM evidence_graph_bundles
            WHERE research_run_id=? AND task_id=?
              AND decision_memory_id=? AND skill_id=?
            """,
            (
                request.research_run_id, request.task_id,
                request.decision_memory_id, request.skill_id,
            ),
        ).fetchone()
        if existing is not None:
            if existing["assertion_hash"] != assertion_hash:
                raise ValueError(
                    "canonical graph linkage already has different assertion evidence"
                )
            return self.get(existing["id"])
        run = self.connection.execute(
            "SELECT * FROM research_runs WHERE id=?",
            (request.research_run_id,),
        ).fetchone()
        if run is None or run["status"] != "completed":
            raise ValueError("research run must exist and be completed")
        task = self.connection.execute(
            "SELECT * FROM tasks WHERE id=?", (request.task_id,)
        ).fetchone()
        if task is None or task["status"] != "succeeded":
            raise ValueError("task must exist and be succeeded")
        decision = self.connection.execute(
            """
            SELECT * FROM memories
            WHERE id=? AND type='decision' AND status='confirmed'
              AND lifecycle_state<>'deleted'
            """,
            (request.decision_memory_id,),
        ).fetchone()
        if decision is None:
            raise ValueError("decision must be a live confirmed decision memory")
        skill = self.connection.execute(
            """
            SELECT * FROM skills
            WHERE id=? AND lifecycle_status='active'
            """,
            (request.skill_id,),
        ).fetchone()
        if skill is None:
            raise ValueError("skill must be active")
        if (
            not isinstance(skill["content_hash"], str)
            or len(skill["content_hash"]) != 64
        ):
            raise ValueError("active skill lacks a canonical content hash")
        findings = self.connection.execute(
            """
            SELECT * FROM research_findings
            WHERE run_id=? ORDER BY rank, id
            """,
            (request.research_run_id,),
        ).fetchall()
        if not findings:
            raise ValueError("research run has no retained findings")
        task_node = decision_node = skill_node = ""
        node_ids: set[str] = set()
        edge_ids: set[str] = set()
        with self.connection:
            task_node = self._node(
                "task", "task", task["id"],
                _hash({
                    "objective": task["objective"], "scope": task["scope"],
                    "status": task["status"],
                }),
            )
            decision_node = self._node(
                "decision", "decision_memory", decision["id"],
                _hash({
                    "content": decision["content"],
                    "payload": decision["structured_payload_json"],
                    "updated_at": decision["updated_at"],
                }),
            )
            skill_node = self._node(
                "skill", "skill", skill["id"], skill["content_hash"]
            )
            node_ids.update((task_node, decision_node, skill_node))
            edge_ids.add(self._edge(
                task_node, decision_node, "informed", assertion_hash
            ))
            edge_ids.add(self._edge(
                decision_node, skill_node, "applied", assertion_hash
            ))
            for finding in findings:
                claim_node = self._node(
                    "claim", "research_finding", finding["id"],
                    finding["claim_hash"],
                )
                node_ids.add(claim_node)
                reference_ids = json.loads(
                    finding["evidence_reference_ids_json"]
                )
                for reference_id in reference_ids:
                    reference = self.connection.execute(
                        "SELECT * FROM research_references WHERE id=?",
                        (reference_id,),
                    ).fetchone()
                    if reference is None:
                        raise ValueError("finding has a missing source reference")
                    citation_id = _hash({
                        "finding_id": finding["id"],
                        "reference_id": reference_id,
                    })
                    evidence_node = self._node(
                        "evidence", "research_citation", citation_id, citation_id
                    )
                    source_node = self._node(
                        "source", "research_reference", reference_id,
                        reference["content_hash"],
                    )
                    node_ids.update((evidence_node, source_node))
                    edge_ids.update((
                        self._edge(
                            claim_node, evidence_node, "supported_by",
                            assertion_hash,
                        ),
                        self._edge(
                            evidence_node, source_node, "derived_from",
                            assertion_hash,
                        ),
                        self._edge(
                            source_node, task_node, "used_by", assertion_hash
                        ),
                    ))
            bundle_id = str(uuid.uuid4())
            self.connection.execute(
                """
                INSERT INTO evidence_graph_bundles(
                    id, research_run_id, task_id, decision_memory_id, skill_id,
                    assertion_hash, provenance, node_count, edge_count, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, 'caller_asserted_unverified',
                          ?, ?, ?)
                """,
                (
                    bundle_id, request.research_run_id, request.task_id,
                    request.decision_memory_id, request.skill_id,
                    assertion_hash, len(node_ids), len(edge_ids), utc_now(),
                ),
            )
            self.connection.executemany(
                """
                INSERT INTO evidence_graph_bundle_nodes(bundle_id, node_id)
                VALUES (?, ?)
                """,
                ((bundle_id, item) for item in sorted(node_ids)),
            )
            self.connection.executemany(
                """
                INSERT INTO evidence_graph_bundle_edges(bundle_id, edge_id)
                VALUES (?, ?)
                """,
                ((bundle_id, item) for item in sorted(edge_ids)),
            )
        return self.get(bundle_id)

    def get(self, bundle_id: str) -> dict[str, object]:
        row = self.connection.execute(
            "SELECT * FROM evidence_graph_bundles WHERE id=?", (bundle_id,)
        ).fetchone()
        if row is None:
            raise KeyError(bundle_id)
        nodes = self.connection.execute(
            """
            SELECT n.* FROM evidence_graph_nodes n
            JOIN evidence_graph_bundle_nodes m ON m.node_id=n.id
            WHERE m.bundle_id=? ORDER BY n.node_type, n.native_id
            """,
            (bundle_id,),
        ).fetchall()
        edges = self.connection.execute(
            """
            SELECT e.* FROM evidence_graph_edges e
            JOIN evidence_graph_bundle_edges m ON m.edge_id=e.id
            WHERE m.bundle_id=? ORDER BY e.relation, e.id
            """,
            (bundle_id,),
        ).fetchall()
        result = dict(row)
        result["nodes"] = [dict(item) for item in nodes]
        result["edges"] = [dict(item) for item in edges]
        return result

    def traverse(
        self,
        bundle_id: str,
        node_id: str,
        *,
        direction: str = "forward",
        max_depth: int = 5,
        limit: int = 100,
    ) -> dict[str, object]:
        if direction not in {"forward", "backward"}:
            raise ValueError("direction must be forward or backward")
        if not 0 <= max_depth <= 5 or not 1 <= limit <= 100:
            raise ValueError("traversal bounds are max_depth 0..5 and limit 1..100")
        member = self.connection.execute(
            """
            SELECT 1 FROM evidence_graph_bundle_nodes
            WHERE bundle_id=? AND node_id=?
            """,
            (bundle_id, node_id),
        ).fetchone()
        if member is None:
            raise ValueError("start node is outside the evidence bundle")
        from_col, to_col = (
            ("from_node_id", "to_node_id")
            if direction == "forward"
            else ("to_node_id", "from_node_id")
        )
        rows = self.connection.execute(
            f"""
            WITH RECURSIVE walk(node_id, depth, path) AS (
                SELECT ?, 0, ',' || ? || ','
                UNION ALL
                SELECT e.{to_col}, walk.depth + 1,
                       walk.path || e.{to_col} || ','
                FROM walk
                JOIN evidence_graph_edges e ON e.{from_col}=walk.node_id
                JOIN evidence_graph_bundle_edges m
                  ON m.edge_id=e.id AND m.bundle_id=?
                WHERE walk.depth < ?
                  AND instr(walk.path, ',' || e.{to_col} || ',')=0
            )
            SELECT MIN(w.depth) AS depth, n.*
            FROM walk w JOIN evidence_graph_nodes n ON n.id=w.node_id
            GROUP BY n.id
            ORDER BY depth, n.node_type, n.native_id LIMIT ?
            """,
            (node_id, node_id, bundle_id, max_depth, limit),
        ).fetchall()
        return {
            "bundle_id": bundle_id,
            "start_node_id": node_id,
            "direction": direction,
            "max_depth": max_depth,
            "nodes": [dict(item) for item in rows],
        }
