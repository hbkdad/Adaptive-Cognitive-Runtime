from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from acr_runtime.cli import main
from acr_runtime.decision_memory import DecisionCreate
from acr_runtime.evidence_graph import EvidenceGraphRequest
from acr_runtime.parallel_research import (
    ParallelResearchRequest,
    ResearchFinding,
    ResearchQuestion,
    ResearchReferenceCreate,
)
from acr_runtime.service import AdaptiveRuntime


class GraphResearchAdapter:
    adapter_id = "test.graph-research-v1"

    def research(self, assignment, resolve_reference):
        reference = resolve_reference(assignment.reference_ids[0])
        return (
            ResearchFinding(
                claim=f"Supported claim {assignment.question_id}",
                evidence_reference_ids=(reference.id,),
                confidence=0.8,
            ),
        )

    def synthesize(self, objective, findings):
        return objective + ": " + "; ".join(item.claim for item in findings)


class EvidenceGraphTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "acr.db"
        self.runtime = AdaptiveRuntime(self.path)
        now = datetime.now(timezone.utc).isoformat()
        self.task_id = "task-evidence-graph"
        self.skill_id = "skill-evidence-graph"
        with self.runtime.db.connection:
            self.runtime.db.connection.execute(
                """
                INSERT INTO tasks(
                    id, objective, scope, token_budget, status,
                    critic_score, duration_ms, created_at, completed_at
                ) VALUES (?, 'Build evidence lineage', 'global', 1000,
                          'succeeded', 0.95, 10, ?, ?)
                """,
                (self.task_id, now, now),
            )
            self.runtime.db.connection.execute(
                """
                INSERT INTO skills(
                    id, name, version, description, instructions,
                    token_cost, created_at, manifest_id, content_hash,
                    status, lifecycle_status, verification_status
                ) VALUES (?, 'Evidence skill', '1.0.0', 'Test skill',
                          'Use cited evidence.', 5, ?, 'evidence-skill',
                          ?, 'active', 'active', 'static_passed')
                """,
                (self.skill_id, now, "b" * 64),
            )
        decision = self.runtime.decisions.record(DecisionCreate(
            topic="Evidence graph",
            decision="Apply the evidence skill.",
            context="Completed research and task.",
            alternatives=("Do nothing",),
            reason="Evidence supports the decision.",
            consequences=("Retain explicit lineage",),
            decided_at=now,
            scope="global",
            evidence=("completed research receipt",),
        ))
        self.decision_id = decision.id
        reference = self.runtime.parallel_research.add_reference(
            ResearchReferenceCreate(
                locator="https://example.test/provenance",
                title="Primary provenance source",
                source_kind="primary",
                authority=0.9,
                content="A bounded primary source supports both claims.",
            )
        )
        plan = self.runtime.parallel_research.plan(ParallelResearchRequest(
            objective="Research two independent provenance questions",
            questions=(
                ResearchQuestion(
                    "q1", "Question one", True, (reference.id,)
                ),
                ResearchQuestion(
                    "q2", "Question two", True, (reference.id,)
                ),
            ),
            max_workers=2,
        ))
        run = self.runtime.parallel_research.execute(
            plan["id"], GraphResearchAdapter()
        )
        self.run_id = run["id"]
        self.request = EvidenceGraphRequest(
            research_run_id=self.run_id,
            task_id=self.task_id,
            decision_memory_id=self.decision_id,
            skill_id=self.skill_id,
            assertion_evidence=("operator-linked completed task records",),
        )

    def tearDown(self):
        self.runtime.close()
        self.temp.cleanup()

    def test_complete_typed_chain_is_relational_minimized_and_idempotent(self):
        graph = self.runtime.evidence_graph.create(self.request)
        replay = self.runtime.evidence_graph.create(self.request)
        self.assertEqual(graph["id"], replay["id"])
        with self.assertRaisesRegex(ValueError, "different assertion evidence"):
            self.runtime.evidence_graph.create(EvidenceGraphRequest(
                research_run_id=self.run_id,
                task_id=self.task_id,
                decision_memory_id=self.decision_id,
                skill_id=self.skill_id,
                assertion_evidence=("different caller assertion",),
            ))
        self.assertEqual(graph["provenance"], "caller_asserted_unverified")
        self.assertEqual(graph["node_count"], 8)
        self.assertEqual(graph["edge_count"], 7)
        self.assertEqual(
            {item["node_type"] for item in graph["nodes"]},
            {"claim", "evidence", "source", "task", "decision", "skill"},
        )
        serialized = json.dumps(graph)
        self.assertNotIn("A bounded primary source supports", serialized)
        self.assertNotIn("Supported claim q1", serialized)
        self.assertEqual(
            {item["relation"] for item in graph["edges"]},
            {"supported_by", "derived_from", "used_by", "informed", "applied"},
        )

    def test_forward_and_backward_traversal_are_bundle_scoped_and_bounded(self):
        graph = self.runtime.evidence_graph.create(self.request)
        claim = next(
            item for item in graph["nodes"] if item["node_type"] == "claim"
        )
        forward = self.runtime.evidence_graph.traverse(
            graph["id"], claim["id"], max_depth=5
        )
        self.assertEqual(
            {item["node_type"] for item in forward["nodes"]},
            {"claim", "evidence", "source", "task", "decision", "skill"},
        )
        skill = next(
            item for item in graph["nodes"] if item["node_type"] == "skill"
        )
        backward = self.runtime.evidence_graph.traverse(
            graph["id"], skill["id"], direction="backward", max_depth=5
        )
        self.assertIn("claim", {item["node_type"] for item in backward["nodes"]})
        with self.assertRaises(ValueError):
            self.runtime.evidence_graph.traverse(
                graph["id"], "outside-node", max_depth=5
            )
        with self.assertRaises(ValueError):
            self.runtime.evidence_graph.traverse(
                graph["id"], claim["id"], max_depth=6
            )

    def test_invalid_native_states_and_wrong_edge_types_fail_closed(self):
        with self.runtime.db.connection:
            self.runtime.db.connection.execute(
                "UPDATE tasks SET status='failed' WHERE id=?", (self.task_id,)
            )
        with self.assertRaisesRegex(ValueError, "succeeded"):
            self.runtime.evidence_graph.create(self.request)
        with self.runtime.db.connection:
            self.runtime.db.connection.execute(
                "UPDATE tasks SET status='succeeded' WHERE id=?", (self.task_id,)
            )
        graph = self.runtime.evidence_graph.create(self.request)
        claim = next(
            item for item in graph["nodes"] if item["node_type"] == "claim"
        )
        skill = next(
            item for item in graph["nodes"] if item["node_type"] == "skill"
        )
        with self.assertRaises(sqlite3.IntegrityError):
            with self.runtime.db.connection:
                self.runtime.db.connection.execute(
                    """
                    INSERT INTO evidence_graph_edges(
                        id, from_node_id, to_node_id, relation,
                        assertion_provenance, assertion_hash, created_at
                    ) VALUES ('forged-edge', ?, ?, 'applied',
                              'caller_asserted_unverified', ?, ?)
                    """,
                    (claim["id"], skill["id"], "a" * 64, datetime.now(
                        timezone.utc
                    ).isoformat()),
                )

    def test_cli_create_inspect_and_traverse(self):
        request_file = Path(self.temp.name) / "graph.json"
        request_file.write_text(json.dumps({
            "research_run_id": self.run_id,
            "task_id": self.task_id,
            "decision_memory_id": self.decision_id,
            "skill_id": self.skill_id,
            "assertion_evidence": ["operator-linked completed task records"],
        }), encoding="utf-8")
        self.assertEqual(main([
            "--db", str(self.path), "evidence-graph", "create",
            str(request_file),
        ]), 0)
        bundle_id = self.runtime.db.connection.execute(
            "SELECT id FROM evidence_graph_bundles"
        ).fetchone()["id"]
        self.assertEqual(main([
            "--db", str(self.path), "evidence-graph", "inspect", bundle_id,
        ]), 0)


if __name__ == "__main__":
    unittest.main()
