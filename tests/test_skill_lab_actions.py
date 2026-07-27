from __future__ import annotations

import json
import shutil
import tempfile
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from acr_runtime.config import Settings
from acr_runtime.permissions import CapabilityGrantRequest
from acr_runtime.service import AdaptiveRuntime
from acr_runtime.skill_lab import SkillLabReader
from acr_runtime.skill_lab_actions import SkillLabActions, SkillLabConflict
from acr_runtime.skill_benchmark import SkillBenchmarkRequest, SkillTrial
from acr_runtime.skill_validator import STAGES


class SkillLabActionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.runtime = AdaptiveRuntime(settings=Settings.from_env(
            database=Path(self.temp.name) / "acr.db"
        ))
        self.skill_id = self.runtime.register_skill(
            "operator-skill",
            "Inspect exact evidence.",
            version="1.0.0",
            description="Operator action fixture",
        )
        self.actions = SkillLabActions(
            self.runtime, operator_id="operator-ui"
        )

    def tearDown(self):
        self.runtime.close()
        self.temp.cleanup()

    def grant(
        self,
        skill_id: str,
        *,
        capability: str = "skill.activate",
        prefix: str = "skill",
    ):
        self.runtime.permissions.grant(CapabilityGrantRequest(
            subject_type="agent",
            subject_id="operator-ui",
            capability=capability,
            resource_scope=f"{prefix}:{skill_id}",
            expires_at=(
                datetime.now(timezone.utc) + timedelta(hours=1)
            ).isoformat(),
            delegable=False,
            grantor_type="trusted_workflow",
            grantor_id="trusted-tests",
            reason="Exercise exact Skill Lab authority",
            evidence=("test:skill-lab-actions",),
        ))

    def test_transition_is_default_deny_cas_and_idempotent(self):
        detail = SkillLabReader(self.runtime).detail(self.skill_id)
        with self.assertRaises(PermissionError):
            self.actions.transition(
                self.skill_id,
                action="retire",
                expected_revision=detail["revision"],
                idempotency_key="retire-key-0001",
                reason="Fixture is obsolete",
                confirmation=detail["reference"],
            )
        self.grant(self.skill_id)
        result = self.actions.transition(
            self.skill_id,
            action="retire",
            expected_revision=detail["revision"],
            idempotency_key="retire-key-0001",
            reason="Fixture is obsolete",
            confirmation=detail["reference"],
        )
        replay = self.actions.transition(
            self.skill_id,
            action="retire",
            expected_revision=detail["revision"],
            idempotency_key="retire-key-0001",
            reason="Fixture is obsolete",
            confirmation=detail["reference"],
        )
        self.assertEqual(replay, result)
        self.assertEqual(
            self.runtime.inspect_skill(self.skill_id)["lifecycle_status"],
            "retired",
        )
        count = self.runtime.db.connection.execute(
            "SELECT COUNT(*) FROM skill_lab_actions"
        ).fetchone()[0]
        self.assertEqual(count, 1)
        with self.assertRaises(SkillLabConflict):
            self.actions.transition(
                self.skill_id,
                action="retire",
                expected_revision=detail["revision"],
                idempotency_key="retire-key-0001",
                reason="A different request",
                confirmation=detail["reference"],
            )

    def test_wrong_exact_grant_and_stale_revision_fail_closed(self):
        detail = SkillLabReader(self.runtime).detail(self.skill_id)
        other = self.runtime.register_skill(
            "other-skill", "Do other work.", version="1.0.0"
        )
        self.grant(other)
        with self.assertRaises(PermissionError):
            self.actions.transition(
                self.skill_id,
                action="quarantine",
                expected_revision=detail["revision"],
                idempotency_key="quarantine-0001",
                reason="Hold for review",
            )
        self.grant(self.skill_id)
        self.runtime.skill_registry.test(self.skill_id)
        with self.assertRaises(SkillLabConflict):
            self.actions.transition(
                self.skill_id,
                action="quarantine",
                expected_revision=detail["revision"],
                idempotency_key="quarantine-0002",
                reason="Hold for review",
            )

    def test_retirement_requires_typed_exact_reference(self):
        detail = SkillLabReader(self.runtime).detail(self.skill_id)
        self.grant(self.skill_id)
        with self.assertRaisesRegex(ValueError, "exact skill version"):
            self.actions.transition(
                self.skill_id,
                action="retire",
                expected_revision=detail["revision"],
                idempotency_key="retire-key-0002",
                reason="Fixture is obsolete",
                confirmation="wrong@1.0.0",
            )

    def test_benchmark_is_idempotent_advisory_and_never_changes_lifecycle(self):
        candidate_id = self.runtime.register_skill(
            "operator-skill",
            "Inspect exact evidence with FTS.",
            version="2.0.0",
            description="Candidate fixture",
        )
        existing = SkillLabReader(self.runtime).detail(self.skill_id)
        candidate = SkillLabReader(self.runtime).detail(candidate_id)
        trials = []
        for index in range(5):
            for arm, quality, tokens in (
                ("without_skill", 0.70, 100),
                ("existing_skill", 0.82, 120),
                ("candidate_skill", 0.84, 115),
            ):
                trials.append(SkillTrial(
                    case_id=f"case-{index}",
                    task_class="database",
                    arm=arm,
                    quality=quality,
                    tokens=tokens,
                    latency_ms=tokens,
                    cost=tokens / 1_000_000,
                    failed=False,
                    evidence=(f"evaluation:case-{index}:{arm}",),
                ))
        request = SkillBenchmarkRequest(
            skill_name=str(existing["manifest_id"]),
            existing_ref=existing["reference"],
            candidate_ref=candidate["reference"],
            trials=tuple(trials),
        )
        self.grant(
            str(existing["manifest_id"]),
            capability="database.write",
            prefix="skill-benchmark",
        )
        before = (
            self.runtime.inspect_skill(self.skill_id)["lifecycle_status"],
            self.runtime.inspect_skill(candidate_id)["lifecycle_status"],
        )
        result = self.actions.benchmark(
            request, idempotency_key="benchmark-key-0001"
        )
        replay = self.actions.benchmark(
            request, idempotency_key="benchmark-key-0001"
        )
        self.assertEqual(replay, result)
        self.assertFalse(result["lifecycle_changed"])
        self.assertEqual(before, (
            self.runtime.inspect_skill(self.skill_id)["lifecycle_status"],
            self.runtime.inspect_skill(candidate_id)["lifecycle_status"],
        ))
        self.assertEqual(
            self.runtime.db.connection.execute(
                "SELECT COUNT(*) FROM skill_benchmark_runs"
            ).fetchone()[0],
            1,
        )

    def _passed_validation(self, skill_id: str, package_hash: str) -> str:
        run_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        with self.runtime.db.connection:
            self.runtime.db.connection.execute(
                """
                INSERT INTO skill_validation_runs(
                    id, skill_id, package_hash, status, incumbent_skill_id,
                    policy_json, created_at, completed_at
                ) VALUES (?, ?, ?, 'passed', NULL, '{}', ?, ?)
                """,
                (run_id, skill_id, package_hash, now, now),
            )
            for order, stage in enumerate(STAGES, start=1):
                self.runtime.db.connection.execute(
                    """
                    INSERT INTO skill_validation_results(
                        run_id, stage_order, stage, outcome, score,
                        token_cost, estimated_cost, latency_ms,
                        details_json, created_at
                    ) VALUES (?, ?, ?, 'passed', 1, 0, 0, 0, '{}', ?)
                    """,
                    (run_id, order, stage, now),
                )
        return run_id

    def test_activation_requires_all_ten_fresh_validation_stages(self):
        fixture = (
            Path(__file__).parents[1]
            / "examples"
            / "skill-v1"
            / "sqlite-diagnostics"
        )
        package_path = Path(self.temp.name) / "activation"
        shutil.copytree(fixture, package_path)
        admitted = self.runtime.admit_skill_package(package_path)
        self.runtime.test_skill(str(admitted["id"]))
        self.grant(str(admitted["id"]))
        run_id = self._passed_validation(
            str(admitted["id"]), str(admitted["content_hash"])
        )
        self.runtime.db.connection.execute(
            """
            DELETE FROM skill_validation_results
            WHERE run_id=? AND stage_order=10
            """,
            (run_id,),
        )
        self.runtime.db.connection.commit()
        detail = SkillLabReader(self.runtime).detail(str(admitted["id"]))
        with self.assertRaisesRegex(ValueError, "all mandatory"):
            self.actions.transition(
                str(admitted["id"]),
                action="activate",
                expected_revision=detail["revision"],
                idempotency_key="activation-key-0001",
                reason="Ready after validation",
            )
        now = datetime.now(timezone.utc).isoformat()
        self.runtime.db.connection.execute(
            """
            INSERT INTO skill_validation_results(
                run_id, stage_order, stage, outcome, score,
                token_cost, estimated_cost, latency_ms,
                details_json, created_at
            ) VALUES (?, 10, ?, 'passed', 1, 0, 0, 0, '{}', ?)
            """,
            (run_id, STAGES[9], now),
        )
        self.runtime.db.connection.commit()
        result = self.actions.transition(
            str(admitted["id"]),
            action="activate",
            expected_revision=detail["revision"],
            idempotency_key="activation-key-0002",
            reason="Ready after validation",
        )
        self.assertEqual(result["to_status"], "active")

    def test_rollback_requires_both_grants_and_changes_both_versions_atomically(self):
        fixture = (
            Path(__file__).parents[1]
            / "examples"
            / "skill-v1"
            / "sqlite-diagnostics"
        )
        source_path = Path(self.temp.name) / "source"
        candidate_path = Path(self.temp.name) / "candidate"
        shutil.copytree(fixture, source_path)
        shutil.copytree(fixture, candidate_path)
        manifest_path = candidate_path / "SKILL.yaml"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["version"] = "2.0.0"
        manifest["updated_at"] = "2026-07-27T01:00:00+00:00"
        manifest_path.write_text(
            json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
        )
        (candidate_path / "instructions.md").write_text(
            "Check SQLite schema, integrity, FTS, and journal mode.\n",
            encoding="utf-8",
        )
        (candidate_path / "history.jsonl").write_text(
            json.dumps({
                "event": "candidate_mutation",
                "version": "2.0.0",
                "timestamp": "2026-07-27T01:00:00+00:00",
            }) + "\n",
            encoding="utf-8",
        )
        source = self.runtime.admit_skill_package(source_path)
        candidate = self.runtime.admit_skill_package(candidate_path)
        self.runtime.test_skill(str(source["id"]))
        self.runtime.test_skill(str(candidate["id"]))
        self._passed_validation(
            str(source["id"]), str(source["content_hash"])
        )
        with self.runtime.db.connection:
            self.runtime.db.connection.execute(
                """
                UPDATE skills SET lifecycle_status='quarantined',
                    status='quarantine' WHERE id=?
                """,
                (source["id"],),
            )
            self.runtime.db.connection.execute(
                """
                UPDATE skills SET lifecycle_status='active',
                    status='active' WHERE id=?
                """,
                (candidate["id"],),
            )
            run_id = str(uuid.uuid4())
            self.runtime.db.connection.execute(
                """
                INSERT INTO skill_evolution_runs(
                    id, source_skill_id, candidate_skill_id, source_version,
                    candidate_version, status, mutation_json, source_hash,
                    candidate_hash, winner, created_at, promoted_at
                ) VALUES (?, ?, ?, '1.0.0', '2.0.0', 'promoted', '{}',
                          ?, ?, 'candidate', ?, ?)
                """,
                (
                    run_id,
                    source["id"],
                    candidate["id"],
                    source["content_hash"],
                    candidate["content_hash"],
                    datetime.now(timezone.utc).isoformat(),
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
        self.grant(str(source["id"]))
        with self.assertRaises(PermissionError):
            self.actions.rollback(
                run_id,
                expected_source_revision=SkillLabReader._revision(
                    self.runtime.inspect_skill(str(source["id"]))
                ),
                expected_candidate_revision=SkillLabReader._revision(
                    self.runtime.inspect_skill(str(candidate["id"]))
                ),
                idempotency_key="rollback-key-0001",
                reason="Candidate regressed",
            )
        self.grant(str(candidate["id"]))
        result = self.actions.rollback(
            run_id,
            expected_source_revision=SkillLabReader._revision(
                self.runtime.inspect_skill(str(source["id"]))
            ),
            expected_candidate_revision=SkillLabReader._revision(
                self.runtime.inspect_skill(str(candidate["id"]))
            ),
            idempotency_key="rollback-key-0001",
            reason="Candidate regressed",
        )
        self.assertEqual(result["to_skill_id"], source["id"])
        self.assertEqual(
            self.runtime.inspect_skill(str(source["id"]))["lifecycle_status"],
            "active",
        )
        self.assertEqual(
            self.runtime.inspect_skill(str(candidate["id"]))["lifecycle_status"],
            "quarantined",
        )
        self.assertEqual(
            self.runtime.db.connection.execute(
                "SELECT status FROM skill_evolution_runs WHERE id=?",
                (run_id,),
            ).fetchone()[0],
            "rolled_back",
        )


if __name__ == "__main__":
    unittest.main()
