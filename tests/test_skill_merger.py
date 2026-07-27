from __future__ import annotations

import json
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path

from acr_runtime import AdaptiveRuntime, Settings, SkillMerger
from acr_runtime.scoring import estimate_tokens


class FixedSemanticSimilarity:
    def __init__(self, score: float) -> None:
        self.score = score

    def similarity(self, left: str, right: str) -> float:
        return self.score


class SkillMergerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        root = Path(self.directory.name)
        self.runtime = AdaptiveRuntime(
            settings=Settings(
                database=root / "acr.db",
                state_dir=root / "state",
                skills_dir=root / "skills",
                provider=None,
                ollama_url="http://127.0.0.1:11434",
            )
        )
        self.root = root

    def tearDown(self) -> None:
        self.runtime.close()
        self.directory.cleanup()

    def add_skill(
        self,
        identifier: str,
        instructions: str,
        *,
        task_classes: tuple[str, ...] = ("database-diagnostics",),
        dependencies: tuple[str, ...] = (),
        version: str = "1.0.0",
        manifest_id: str | None = None,
        name: str | None = None,
    ) -> str:
        source = (
            Path(__file__).parents[1]
            / "examples"
            / "skill-v1"
            / "sqlite-diagnostics"
        )
        target = self.root / identifier
        shutil.copytree(source, target)
        manifest_path = target / "SKILL.yaml"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["id"] = manifest_id or identifier
        manifest["name"] = name or identifier.replace("-", " ").title()
        manifest["version"] = version
        manifest["description"] = f"Focused procedure for {identifier}."
        manifest["task_classes"] = list(task_classes)
        manifest["dependencies"] = list(dependencies)
        manifest["applicability"] = list(task_classes)
        manifest["token_estimate"] = estimate_tokens(instructions)
        manifest_path.write_text(
            json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
        )
        (target / "instructions.md").write_text(
            instructions + "\n", encoding="utf-8"
        )
        return str(self.runtime.admit_skill_package(target)["id"])

    def use_semantic_score(self, score: float) -> None:
        self.runtime.skill_merger = SkillMerger(
            self.runtime.db.connection,
            self.runtime.skill_registry,
            semantic_similarity=FixedSemanticSimilarity(score),
        )

    def test_missing_semantic_adapter_is_explicit_and_keeps_separate(self):
        left = self.add_skill(
            "sqlite-check-a", "Inspect schema. Run integrity check. Report."
        )
        right = self.add_skill(
            "sqlite-check-b", "Inspect schema. Run integrity check. Report."
        )

        report = self.runtime.analyze_skill_merges()
        pair = report.pairs[0]

        self.assertEqual(pair.recommendation, "KEEP_SEPARATE")
        self.assertFalse(
            pair.evidence["semantic_overlap"]["available"]
        )
        self.assertFalse(
            pair.evidence["semantic_overlap"][
                "lexical_proxy_used_for_semantic_decision"
            ]
        )
        self.assertFalse(pair.automatic_action_allowed)
        self.assertEqual(
            self.runtime.inspect_skill(left)["lifecycle_status"],
            "quarantined",
        )
        self.assertEqual(
            self.runtime.inspect_skill(right)["lifecycle_status"],
            "quarantined",
        )

    def test_high_overlap_recommends_merge_without_mutation(self):
        instructions = (
            "Inspect schema. Run focused integrity checks. Report evidence."
        )
        left = self.add_skill("sqlite-integrity-a", instructions)
        right = self.add_skill("sqlite-integrity-b", instructions)
        self.use_semantic_score(0.96)

        pair = self.runtime.analyze_skill_merges().pairs[0]

        self.assertEqual(pair.recommendation, "MERGE")
        self.assertIsNone(pair.deprecate_skill_id)
        self.assertFalse(pair.automatic_action_allowed)
        self.assertEqual(pair.evidence["dependencies"]["score"], 1.0)
        self.assertFalse(pair.evidence["automatic_action_taken"])
        self.assertEqual(
            self.runtime.inspect_skill(left)["lifecycle_status"],
            "quarantined",
        )
        self.assertEqual(
            self.runtime.inspect_skill(right)["lifecycle_status"],
            "quarantined",
        )

    def test_performance_dominance_recommends_only_weaker_deprecation(self):
        instructions = (
            "Inspect schema. Run focused integrity checks. Report evidence."
        )
        stronger = self.add_skill("sqlite-strong", instructions)
        weaker = self.add_skill("sqlite-weak", instructions)
        self.runtime.db.connection.execute(
            """
            UPDATE skills
            SET use_count = 5, success_count = 5, failure_count = 0,
                total_tokens = 400, total_cost = 0.4,
                total_latency_ms = 400
            WHERE id = ?
            """,
            (stronger,),
        )
        self.runtime.db.connection.execute(
            """
            UPDATE skills
            SET use_count = 5, success_count = 4, failure_count = 1,
                total_tokens = 500, total_cost = 0.5,
                total_latency_ms = 500
            WHERE id = ?
            """,
            (weaker,),
        )
        self.runtime.db.connection.commit()
        self.use_semantic_score(0.98)

        pair = self.runtime.analyze_skill_merges().pairs[0]

        self.assertEqual(pair.recommendation, "DEPRECATE_ONE")
        self.assertEqual(pair.deprecate_skill_id, weaker)
        self.assertEqual(
            pair.evidence["performance_history"]["dominance"][
                "dominant_skill_id"
            ],
            stronger,
        )
        self.assertEqual(
            self.runtime.inspect_skill(weaker)["lifecycle_status"],
            "quarantined",
        )

    def test_related_but_distinct_procedures_recommend_composition(self):
        self.add_skill(
            "schema-reader",
            "Read table definitions and identify the requested schema fields.",
        )
        self.add_skill(
            "backup-verifier",
            "Compare backup checksums, timestamps, and recovery checkpoints.",
            dependencies=("checksum-tool@1.0.0",),
        )
        self.use_semantic_score(0.70)

        pair = self.runtime.analyze_skill_merges().pairs[0]

        self.assertEqual(pair.recommendation, "COMPOSE")
        self.assertLess(
            pair.evidence["procedure_similarity"]["score"], 0.70
        )
        self.assertLess(pair.evidence["dependencies"]["score"], 0.80)

    def test_active_skill_is_flagged_and_never_changed(self):
        active = self.add_skill(
            "active-reader", "Inspect schema and report focused evidence."
        )
        self.add_skill(
            "similar-reader", "Inspect schema and report focused evidence."
        )
        self.runtime.db.connection.execute(
            """
            UPDATE skills SET lifecycle_status = 'active', status = 'active'
            WHERE id = ?
            """,
            (active,),
        )
        self.runtime.db.connection.commit()
        self.use_semantic_score(0.99)

        report = self.runtime.analyze_skill_merges(reference=active)
        loaded = self.runtime.skill_merge_analysis(report.id)
        pair = loaded.pairs[0]

        self.assertTrue(pair.active_involved)
        self.assertTrue(
            pair.evidence["active_skill_protected_from_automatic_merge"]
        )
        self.assertFalse(pair.automatic_action_allowed)
        self.assertEqual(
            self.runtime.inspect_skill(active)["lifecycle_status"], "active"
        )

    def test_analysis_is_bounded_and_database_forbids_automatic_action(self):
        self.add_skill("bounded-a", "Inspect schema.")
        self.add_skill("bounded-b", "Report schema.")
        with self.assertRaises(ValueError):
            self.runtime.analyze_skill_merges(limit=101)

        report = self.runtime.analyze_skill_merges(limit=1)
        self.assertEqual(report.skill_count, 1)
        self.assertEqual(report.pair_count, 0)
        with self.assertRaises(sqlite3.IntegrityError):
            self.runtime.db.connection.execute(
                """
                INSERT INTO skill_merge_analysis_pairs(
                    id, run_id, left_skill_id, right_skill_id,
                    recommendation, active_involved,
                    automatic_action_allowed, evidence_json, created_at
                ) VALUES (
                    'forbidden', ?, ?, ?, 'KEEP_SEPARATE', 0, 1, '{}', 'now'
                )
                """,
                (report.id, self.runtime.skills()[0]["id"],
                 self.runtime.skills()[1]["id"]),
            )


if __name__ == "__main__":
    unittest.main()
