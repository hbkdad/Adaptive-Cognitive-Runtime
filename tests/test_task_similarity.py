from __future__ import annotations

import io
import json
import sqlite3
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from acr_runtime.cli import main
from acr_runtime.safe_mode import SafeModeViolation
from acr_runtime.service import AdaptiveRuntime
from acr_runtime.task_similarity import (
    TaskFeatureProfile,
    TaskSimilarityError,
)


class TaskSimilarityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.runtime = AdaptiveRuntime(self.root / "acr.db")

    def tearDown(self) -> None:
        self.runtime.close()
        self.temp.cleanup()

    def task(
        self,
        objective: str,
        *,
        scope: str = "project:runtime",
        completed: bool = False,
        success: bool = True,
    ) -> str:
        task_id = self.runtime.db.create_task(
            objective=objective, scope=scope, token_budget=100
        )
        if completed:
            self.runtime.db.complete_task(
                task_id,
                success=success,
                critic_score=0.9 if success else 0.2,
                duration_ms=25,
                attributions=(),
                task_class="repository-verification",
            )
        return task_id

    @staticmethod
    def profile(task_id: str, **changes: object) -> TaskFeatureProfile:
        payload: dict[str, object] = {
            "schema_version": 1,
            "task_id": task_id,
            "intent": "diagnose",
            "domain": "sqlite-runtime",
            "required_capabilities": ["database.read", "filesystem.read"],
            "artifacts": ["sqlite-database", "python-module"],
            "tools": ["python", "sqlite"],
            "environment": ["os:windows", "shell:powershell", "python:3.11"],
            "evidence": ["operator:test-fixture"],
        }
        payload.update(changes)
        return TaskFeatureProfile.from_dict(payload)

    def test_structured_features_rank_analogs_without_objective_embeddings(self):
        target_id = self.task("Words unique to the current request")
        exact_id = self.task(
            "Completely unrelated prose that is never compared", completed=True
        )
        partial_id = self.task("Another unrelated objective", completed=True)
        other_scope_id = self.task(
            "Same structure but private elsewhere",
            scope="project:other",
            completed=True,
        )
        planned_id = self.task("Same structure but not historical")

        self.runtime.add_task_profile(self.profile(target_id))
        self.runtime.add_task_profile(self.profile(exact_id))
        self.runtime.add_task_profile(
            self.profile(
                partial_id,
                domain="postgres-runtime",
                required_capabilities=["database.read"],
                artifacts=["sql-database"],
                tools=["python"],
                environment=["os:windows", "shell:bash"],
            )
        )
        self.runtime.add_task_profile(self.profile(other_scope_id))
        self.runtime.add_task_profile(self.profile(planned_id))

        result = self.runtime.similar_tasks(target_id)
        self.assertFalse(result.as_dict()["embedding_used"])
        self.assertEqual(result.candidates_considered, 2)
        self.assertEqual([item.task_id for item in result.analogies], [exact_id, partial_id])
        self.assertEqual(result.analogies[0].score_micros, 1_000_000)
        self.assertLess(result.analogies[1].score_micros, 1_000_000)
        self.assertTrue(result.analogies[0].as_dict()["analogy_only"])
        self.assertFalse(result.analogies[0].as_dict()["execution_authority"])

    def test_empty_sets_do_not_inflate_similarity_and_threshold_is_enforced(self):
        target_id = self.task("Target")
        candidate_id = self.task("Candidate", completed=True, success=False)
        empty = {
            "required_capabilities": [],
            "artifacts": [],
            "tools": [],
            "environment": [],
        }
        self.runtime.add_task_profile(self.profile(target_id, **empty))
        self.runtime.add_task_profile(
            self.profile(
                candidate_id,
                domain="other-domain",
                **empty,
            )
        )
        result = self.runtime.similar_tasks(
            target_id, minimum_score_micros=250_000
        )
        self.assertEqual(len(result.analogies), 1)
        analogy = result.analogies[0]
        self.assertEqual(analogy.score_micros, 250_000)
        self.assertEqual(analogy.breakdown_micros["tools"], 0)
        self.assertEqual(analogy.status, "failed")
        self.assertEqual(
            self.runtime.similar_tasks(
                target_id, minimum_score_micros=250_001
            ).analogies,
            (),
        )

    def test_profiles_are_strict_immutable_and_safe_mode_guarded(self):
        task_id = self.task("Profile target")
        profile = self.profile(task_id)
        retained = self.runtime.add_task_profile(profile)
        self.assertEqual(retained.profile_hash, profile.profile_hash)
        with self.assertRaises(TaskSimilarityError):
            self.runtime.add_task_profile(profile)
        with self.assertRaises(sqlite3.IntegrityError):
            self.runtime.db.connection.execute(
                "UPDATE task_feature_profiles SET intent='change' WHERE task_id=?",
                (task_id,),
            )
        with self.assertRaises(TaskSimilarityError):
            TaskFeatureProfile.from_dict(
                {
                    **profile.as_dict(),
                    "profile_version": "structured-v1",
                }
            )
        with self.assertRaises(TaskSimilarityError):
            self.profile(task_id, required_capabilities=["root.admin"])

        blocked_task = self.task("Blocked profile")
        self.runtime.safe_mode.enable(actor_id="test", reason="containment")
        with self.assertRaises(SafeModeViolation):
            self.runtime.task_similarity.add_profile(self.profile(blocked_task))

    def test_candidate_bound_fails_closed(self):
        target_id = self.task("Target")
        first_id = self.task("First", completed=True)
        second_id = self.task("Second", completed=True)
        for task_id in (target_id, first_id, second_id):
            self.runtime.add_task_profile(self.profile(task_id))
        with patch("acr_runtime.task_similarity.MAX_CANDIDATES", 1):
            with self.assertRaisesRegex(
                TaskSimilarityError, "candidate bound exceeded"
            ):
                self.runtime.similar_tasks(target_id)

    def test_cli_add_show_and_similar_are_machine_readable(self):
        target_id = self.task("CLI target")
        candidate_id = self.task("CLI candidate", completed=True)
        self.runtime.add_task_profile(self.profile(candidate_id))
        profile_file = self.root / "profile.json"
        request_payload = self.profile(target_id).as_dict()
        request_payload.pop("profile_version")
        profile_file.write_text(
            json.dumps(request_payload), encoding="utf-8"
        )
        database = str(self.root / "acr.db")

        def invoke(*arguments: str) -> dict[str, object]:
            output = io.StringIO()
            with redirect_stdout(output):
                result = main(["--db", database, "task", *arguments])
            self.assertEqual(result, 0)
            return json.loads(output.getvalue())

        self.runtime.close()
        added = invoke("profile-add", str(profile_file))
        shown = invoke("profile-show", target_id)
        similar = invoke("similar", target_id, "--minimum-score-micros", "1")
        self.assertEqual(added["task_id"], target_id)
        self.assertEqual(shown["profile_version"], "structured-v1")
        self.assertEqual(similar["analogies"][0]["task_id"], candidate_id)
        self.runtime = AdaptiveRuntime(database)


if __name__ == "__main__":
    unittest.main()
