from __future__ import annotations

import hashlib
import io
import json
import sqlite3
import tempfile
import unittest
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stdout
from pathlib import Path

from acr_runtime.service import AdaptiveRuntime
from acr_runtime.cli import _execute
from acr_runtime.memory import MemoryCreate, MemoryType


def digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class TokenWasteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.database = Path(self.temporary.name) / "acr.db"
        self.runtime = AdaptiveRuntime(self.database)
        self.connection = self.runtime.db.connection

    def tearDown(self) -> None:
        self.runtime.close()
        self.temporary.cleanup()

    def add_task(self, task_id: str, *, scope: str = "global") -> None:
        self.connection.execute(
            """
            INSERT INTO tasks(
                id, objective, scope, token_budget, selected_tokens,
                status, created_at, completed_at
            ) VALUES (?, 'bounded objective', ?, 1000, 0,
                      'succeeded', '2026-07-28T00:00:00Z',
                      '2026-07-28T00:01:00Z')
            """,
            (task_id, scope),
        )
        self.connection.commit()

    def test_empty_scan_is_complete_content_minimized_and_idempotent(self):
        first = self.runtime.token_waste.scan(scope="global")
        second = self.runtime.token_waste.scan(scope="global")

        self.assertEqual(first.id, second.id)
        self.assertEqual(len(first.findings), 9)
        self.assertEqual(
            {finding.category for finding in first.findings},
            {
                "large_retrieved_blocks_never_used",
                "repeated_instructions",
                "duplicate_memories",
                "unnecessary_skill_text",
                "oversized_tool_descriptions",
                "full_files_when_symbols_sufficient",
                "excessive_reflection",
                "too_many_agents",
                "unnecessary_model_escalation",
            },
        )
        self.assertTrue(
            all(
                finding.verdict == "insufficient_evidence"
                for finding in first.findings
            )
        )
        self.assertTrue(
            all(not finding.automatic_action_allowed for finding in first.findings)
        )

    def test_ignored_large_block_is_candidate_not_quantified_waste(self):
        self.add_task("task-large")
        canary = "private-customer-context-should-not-persist"
        self.connection.execute(
            """
            INSERT INTO context_uses(
                task_id, source_type, source_id, tokens, utility, roi, useful
            ) VALUES ('task-large', 'memory', ?, 900, 0.2, 0.001, 0)
            """,
            (canary,),
        )
        self.connection.execute(
            """
            INSERT INTO context_attributions(
                id, task_id, source_type, source_id, role, outcome,
                impact_score, confidence, approximate_roi, evidence_json,
                created_at
            ) VALUES (
                'attr-large', 'task-large', 'memory', ?, 'background',
                'ignored', 0, 0.75, 0, '["caller_signal"]',
                '2026-07-28T00:01:00Z'
            )
            """,
            (canary,),
        )
        self.connection.commit()

        report = self.runtime.token_waste.scan(scope="global")
        finding = report.findings[0]
        self.assertEqual(finding.verdict, "candidate_waste")
        self.assertEqual(finding.observed_tokens, 900)
        self.assertIsNone(finding.savings_low)
        self.assertFalse(finding.evidence["independent_counterfactual"])

        retained = "\n".join(
            str(tuple(row))
            for table in ("token_waste_runs", "token_waste_findings")
            for row in self.connection.execute(f"SELECT * FROM {table}")
        )
        self.assertNotIn(canary, retained)
        self.assertNotIn(canary, json.dumps(report.as_dict()))

    def test_protected_and_low_confidence_ignored_blocks_are_not_candidates(self):
        self.add_task("task-protected")
        for source_type, source_id, confidence in (
            ("system_rule", "mandatory-policy", 1.0),
            ("memory", "uncertain-memory", 0.2),
        ):
            self.connection.execute(
                """
                INSERT INTO context_uses(
                    task_id, source_type, source_id, tokens, utility, roi,
                    useful
                ) VALUES (
                    'task-protected', ?, ?, 900, 0.1, 0.001, 0
                )
                """,
                (source_type, source_id),
            )
            self.connection.execute(
                """
                INSERT INTO context_attributions(
                    id, task_id, source_type, source_id, role, outcome,
                    impact_score, confidence, approximate_roi, evidence_json,
                    created_at
                ) VALUES (
                    ?, 'task-protected', ?, ?, 'constraint', 'ignored',
                    0, ?, 0, '["caller_signal"]',
                    '2026-07-28T00:01:00Z'
                )
                """,
                (f"attr-{source_type}", source_type, source_id, confidence),
            )
        self.connection.commit()

        finding = self.runtime.token_waste.scan().findings[0]
        self.assertEqual(finding.verdict, "insufficient_evidence")
        self.assertEqual(finding.subject_count, 0)
        self.assertEqual(finding.observed_tokens, 0)

    def test_static_signals_remain_advisory_and_count_tool_schemas(self):
        self.add_task("task-repeat")
        content_hash = digest("same selected instruction")
        for sequence in (1, 2):
            self.connection.execute(
                """
                INSERT INTO content_security_assessments(
                    id, assessment_hash, origin, source_id, content_hash,
                    authority, disposition, suspicious_signals_json,
                    provenance_json, created_at
                ) VALUES (?, ?, 'skill_instruction', ?, ?,
                          'scoped_skill', 'scoped_instruction', '[]', '[]',
                          '2026-07-28T00:00:00Z')
                """,
                (
                    f"assessment-{sequence}",
                    digest(f"assessment-{sequence}"),
                    f"skill-{sequence}",
                    content_hash,
                ),
            )
            self.connection.execute(
                """
                INSERT INTO context_uses(
                    task_id, source_type, source_id, tokens, utility, roi,
                    useful, security_assessment_id, content_origin,
                    security_authority
                ) VALUES (
                    'task-repeat', 'skill', ?, 50, 0.5, 0.01, NULL, ?,
                    'skill_instruction', 'scoped_skill'
                )
                """,
                (f"skill-{sequence}", f"assessment-{sequence}"),
            )
        large_schema = json.dumps(
            {
                "type": "object",
                "properties": {
                    f"field_{index}": {
                        "type": "string",
                        "description": "required safety constrained value",
                    }
                    for index in range(90)
                },
            }
        )
        self.connection.execute(
            """
            INSERT INTO tool_definitions(
                name, description, input_schema_json, output_schema_json,
                permissions_json, cost, latency_estimate_ms, side_effect,
                network_access, filesystem_access,
                credential_requirements_json, definition_hash, created_at
            ) VALUES (
                'bounded-tool', 'Short routing description.', ?, '{}', '[]',
                0, 1, 'READ_ONLY', 0, 'NONE', '[]', ?,
                '2026-07-28T00:00:00Z'
            )
            """,
            (large_schema, digest("bounded-tool")),
        )
        self.connection.commit()

        findings = {
            item.category: item
            for item in self.runtime.token_waste.scan().findings
        }
        repeated = findings["repeated_instructions"]
        oversized = findings["oversized_tool_descriptions"]
        self.assertEqual(repeated.verdict, "candidate_waste")
        self.assertEqual(repeated.subject_count, 1)
        self.assertEqual(oversized.verdict, "insufficient_evidence")
        self.assertTrue(oversized.evidence["schemas_counted"])
        self.assertFalse(oversized.evidence["canonical_rewrite_allowed"])
        self.assertGreater(oversized.evidence["estimated_registry_tokens"], 0)
        self.assertEqual(oversized.observed_tokens, 0)
        self.assertIsNone(oversized.savings_base)

    def test_duplicate_memory_requires_sealed_prompt66_exact_match(self):
        for _ in range(2):
            self.runtime.db.memories.create(
                MemoryCreate(
                    type=MemoryType.SEMANTIC,
                    subject="same bounded subject",
                    content="The same exact scoped fact is retained twice.",
                    scope="global",
                    valid_from="2026-07-28T00:00:00Z",
                )
            )
        before = next(
            item
            for item in self.runtime.token_waste.scan().findings
            if item.category == "duplicate_memories"
        )
        self.assertEqual(before.verdict, "insufficient_evidence")

        self.runtime.scan_duplicates(
            kinds=("memory",), scope="global", limit=10
        )
        after = next(
            item
            for item in self.runtime.token_waste.scan().findings
            if item.category == "duplicate_memories"
        )
        self.assertEqual(after.verdict, "candidate_waste")
        self.assertEqual(after.subject_count, 1)
        self.assertEqual(
            after.evidence["signal"],
            "sealed_scope_partitioned_exact_dedup_match",
        )

    def test_schema52_rejects_forged_quantified_savings(self):
        self.connection.execute(
            """
            INSERT INTO token_waste_runs(
                id, scope_hash, analyzer_version, policy_json,
                evidence_revision, expected_findings, status, created_at
            ) VALUES (
                'forged-run', ?, 'forged', '{}', ?, 9, 'running',
                '2026-07-28T00:00:00Z'
            )
            """,
            (digest("scope"), digest("revision")),
        )
        with self.assertRaises(sqlite3.IntegrityError):
            self.connection.execute(
                """
                INSERT INTO token_waste_findings(
                    id, run_id, sequence, category, verdict, subject_count,
                    observed_tokens, token_quality, evidence_method,
                    savings_low, savings_base, savings_high, evidence_json,
                    recommendation, automatic_action_allowed, created_at
                ) VALUES (
                    'forged-finding', 'forged-run', 1,
                    'full_files_when_symbols_sufficient',
                    'counterfactually_avoidable', 1, 1000,
                    'provider_reported', 'controlled', 750, 750, 750, '{}',
                    'delete_context', 0, '2026-07-28T00:00:00Z'
                )
                """
            )

    def test_schema52_rejects_direct_completed_run_without_findings(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.connection.execute(
                """
                INSERT INTO token_waste_runs(
                    id, scope_hash, analyzer_version, policy_json,
                    evidence_revision, expected_findings, status,
                    created_at, completed_at
                ) VALUES (
                    'incomplete-sealed-run', ?, 'forged', '{}', ?, 9,
                    'completed', '2026-07-28T00:00:00Z',
                    '2026-07-28T00:01:00Z'
                )
                """,
                (digest("scope-two"), digest("revision-two")),
            )

    def test_project_scan_is_not_changed_by_global_tool_catalog(self):
        before = self.runtime.token_waste.scan(scope="project:one")
        self.connection.execute(
            """
            INSERT INTO tool_definitions(
                name, description, input_schema_json, output_schema_json,
                permissions_json, cost, latency_estimate_ms, side_effect,
                network_access, filesystem_access,
                credential_requirements_json, definition_hash, created_at
            ) VALUES (
                'global-only-tool', ?, '{}', '{}', '[]',
                0, 1, 'READ_ONLY', 0, 'NONE', '[]', ?,
                '2026-07-28T00:00:00Z'
            )
            """,
            ("large global description " * 200, digest("global-only-tool")),
        )
        self.connection.commit()
        after = self.runtime.token_waste.scan(scope="project:one")

        self.assertEqual(after.id, before.id)
        self.assertEqual(after.evidence_revision, before.evidence_revision)
        tool_finding = next(
            item
            for item in after.findings
            if item.category == "oversized_tool_descriptions"
        )
        self.assertEqual(tool_finding.verdict, "insufficient_evidence")
        self.assertEqual(tool_finding.subject_count, 0)

    def test_concurrent_scans_return_the_same_completed_run(self):
        shared_database = Path(self.temporary.name) / "shared.db"
        AdaptiveRuntime(shared_database).close()

        def scan() -> str:
            runtime = AdaptiveRuntime(shared_database)
            try:
                return runtime.token_waste.scan(scope="global").id
            finally:
                runtime.close()

        with ThreadPoolExecutor(max_workers=2) as executor:
            run_ids = list(executor.map(lambda _value: scan(), range(2)))
        self.assertEqual(run_ids[0], run_ids[1])

    def test_scope_hashing_normalizes_canonically_equivalent_unicode(self):
        composed = "project:café"
        decomposed = unicodedata.normalize("NFD", composed)
        first = self.runtime.token_waste.scan(scope=composed)
        second = self.runtime.token_waste.scan(scope=decomposed)
        self.assertEqual(first.id, second.id)
        self.assertEqual(first.scope_hash, second.scope_hash)

    def test_completed_run_rejects_late_findings_and_deletion(self):
        run = self.runtime.token_waste.scan()
        with self.assertRaises(sqlite3.IntegrityError):
            self.connection.execute(
                """
                INSERT INTO token_waste_findings(
                    id, run_id, sequence, category, verdict, subject_count,
                    observed_tokens, token_quality, evidence_method,
                    evidence_json, recommendation,
                    automatic_action_allowed, created_at
                ) VALUES (
                    'late', ?, 1, 'large_retrieved_blocks_never_used',
                    'insufficient_evidence', 0, 0, 'unknown', 'none',
                    '{}', 'collect_evidence', 0,
                    '2026-07-28T00:00:00Z'
                )
                """,
                (run.id,),
            )
        with self.assertRaises(sqlite3.IntegrityError):
            self.connection.execute(
                "DELETE FROM token_waste_runs WHERE id=?", (run.id,)
            )

    def test_cli_scan_and_report_are_machine_readable(self):
        other_database = Path(self.temporary.name) / "cli.db"
        output = io.StringIO()
        with redirect_stdout(output):
            code = _execute(
                [
                    "--db",
                    str(other_database),
                    "waste",
                    "scan",
                    "--scope",
                    "global",
                ]
            )
        self.assertEqual(code, 0)
        scan = json.loads(output.getvalue())
        self.assertEqual(len(scan["findings"]), 9)

        output = io.StringIO()
        with redirect_stdout(output):
            code = _execute(
                [
                    "--db",
                    str(other_database),
                    "waste",
                    "report",
                    scan["id"],
                    "--scope",
                    "global",
                ]
            )
        self.assertEqual(code, 0)
        report = json.loads(output.getvalue())
        self.assertEqual(report["evidence_revision"], scan["evidence_revision"])


if __name__ == "__main__":
    unittest.main()
