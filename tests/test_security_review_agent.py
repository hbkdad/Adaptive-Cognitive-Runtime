from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from acr_runtime.agent_spec import AgentSpec
from acr_runtime.security_review import (
    SECURITY_CATEGORIES,
    SecurityReviewReport,
    main,
)


ROOT = Path(__file__).parents[1]


def _finding(
    *,
    finding_id: str = "shell.exec",
    severity: str = "high",
    evidence_status: str = "verified",
) -> dict[str, object]:
    return {
        "id": finding_id,
        "category": "shell_execution",
        "severity": severity,
        "evidence_status": evidence_status,
        "title": "Untrusted argument reaches a shell",
        "affected_component": "acr_runtime/example.py:42",
        "evidence": ["The command is constructed from request field `name`."],
        "attack_path": [
            "An unauthenticated caller controls the request field `name`.",
            "The field reaches a shell command without argument separation.",
            "Shell metacharacters can run an attacker-selected command.",
        ],
        "recommendation": "Use an argument array with shell disabled.",
    }


def _report(findings: list[dict[str, object]]) -> dict[str, object]:
    return {
        "schema_version": 1,
        "change_ref": "git:0123456789abcdef",
        "reviewed_categories": list(SECURITY_CATEGORIES),
        "findings": findings,
        "limitations": [],
    }


class SecurityReviewAgentTests(unittest.TestCase):
    def test_clean_report_requires_complete_category_coverage(self) -> None:
        report = SecurityReviewReport.from_dict(_report([]))
        self.assertEqual(report.verdict, "pass")
        self.assertEqual(report.blocking_finding_ids, ())

        incomplete = _report([])
        incomplete["reviewed_categories"] = list(SECURITY_CATEGORIES[:-1])
        with self.assertRaisesRegex(ValueError, "every required category"):
            SecurityReviewReport.from_dict(incomplete)

    def test_only_verified_high_or_critical_findings_block(self) -> None:
        verified = SecurityReviewReport.from_dict(_report([_finding()]))
        self.assertEqual(verified.verdict, "block")
        self.assertEqual(verified.blocking_finding_ids, ("shell.exec",))

        speculative = SecurityReviewReport.from_dict(
            _report([_finding(evidence_status="speculative", severity="critical")])
        )
        self.assertEqual(speculative.verdict, "pass_with_findings")
        self.assertEqual(speculative.blocking_finding_ids, ())

        supported = SecurityReviewReport.from_dict(
            _report([_finding(evidence_status="supported", severity="critical")])
        )
        self.assertEqual(supported.verdict, "pass_with_findings")

    def test_findings_require_evidence_attack_path_and_closed_fields(self) -> None:
        for field in ("evidence", "attack_path"):
            payload = _report([_finding()])
            payload["findings"][0][field] = []
            with self.subTest(field=field):
                with self.assertRaises(ValueError):
                    SecurityReviewReport.from_dict(payload)

        payload = _report([_finding()])
        payload["findings"][0]["blocking"] = False
        with self.assertRaisesRegex(ValueError, "invalid shape"):
            SecurityReviewReport.from_dict(payload)

    def test_report_rejects_secret_material_without_echoing_it(self) -> None:
        secret = "sk-proj-" + ("a" * 40)
        payload = _report([_finding()])
        payload["findings"][0]["evidence"] = [f"Observed token {secret}"]
        with self.assertRaises(ValueError) as caught:
            SecurityReviewReport.from_dict(payload)
        self.assertNotIn(secret, str(caught.exception))

    def test_cli_has_machine_readable_exit_contract(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            report_path = Path(directory) / "review.json"
            for findings, expected_code, expected_verdict in (
                ([], 0, "pass"),
                ([_finding()], 1, "block"),
            ):
                report_path.write_text(
                    json.dumps(_report(findings)), encoding="utf-8"
                )
                output = io.StringIO()
                with contextlib.redirect_stdout(output):
                    code = main(["validate", str(report_path)])
                payload = json.loads(output.getvalue())
                self.assertEqual(code, expected_code)
                self.assertTrue(payload["valid"])
                self.assertEqual(payload["verdict"], expected_verdict)

            report_path.write_text("{}", encoding="utf-8")
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                code = main(["validate", str(report_path)])
            self.assertEqual(code, 2)
            self.assertFalse(json.loads(output.getvalue())["valid"])

    def test_role_template_is_valid_and_least_privilege(self) -> None:
        payload = json.loads(
            (
                ROOT / "examples" / "agent-spec" / "security-review-worker.json"
            ).read_text(encoding="utf-8")
        )
        spec = AgentSpec.from_dict(payload)
        self.assertEqual(spec.task_scope, ("security-review",))
        self.assertEqual(spec.tools, ())
        self.assertEqual(spec.permissions, ())
        self.assertEqual(spec.communication.mode, "none")
        self.assertTrue(spec.model_policy.local_only)
        self.assertFalse(spec.model_policy.allow_fallback)
        self.assertEqual(spec.money_budget, 0)

    def test_workflow_covers_categories_and_non_speculative_blocking(self) -> None:
        source = (ROOT / "docs" / "agents" / "security-review.md").read_text(
            encoding="utf-8"
        )
        for category in SECURITY_CATEGORIES:
            self.assertIn(category, source)
        self.assertIn(
            "Only a `verified` finding with `high` or `critical` severity is "
            "blocking.",
            source,
        )
        self.assertIn("multi-step attack path", source)
        self.assertIn("not an executable worker", source)


if __name__ == "__main__":
    unittest.main()
