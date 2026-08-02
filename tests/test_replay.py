from __future__ import annotations

import hashlib
import io
import json
import sqlite3
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from acr_runtime.cli import main
from acr_runtime.replay import (
    ReplayCaseCreate,
    ReplayError,
    ReplayObservation,
    ReplayRequest,
)
from acr_runtime.safe_mode import SafeModeViolation
from acr_runtime.service import AdaptiveRuntime


class DeterministicReplayAdapter:
    def __init__(self, *, quality: int = 800_000) -> None:
        self.quality = quality
        self.calls = []

    def identity(self):
        return {
            "available": True,
            "isolation": "offline",
            "external_network": "forbidden",
            "side_effects": "none",
            "deployment": "forbidden",
            "adapter": "deterministic-test-v1",
        }

    def run(self, context):
        self.calls.append(context)
        return ReplayObservation(
            success=True,
            quality_micros=self.quality,
            input_tokens=10,
            output_tokens=4,
            latency_ms=20,
            cost_micros=5,
            output_hash=hashlib.sha256(
                f"{context.target_ref}:{context.seed}".encode()
            ).hexdigest(),
            evidence=("test:deterministic-replay",),
        )


class ReplayTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.database = self.root / "acr.db"
        self.runtime = AdaptiveRuntime(self.database)

    def tearDown(self) -> None:
        self.runtime.close()
        self.temp.cleanup()

    def task(self, *, completed: bool = True) -> str:
        task_id = self.runtime.db.create_task(
            objective="Replay fixture source",
            scope="project:runtime",
            token_budget=100,
        )
        if completed:
            self.runtime.db.complete_task(
                task_id,
                success=True,
                critic_score=0.9,
                duration_ms=10,
                attributions=(),
                task_class="repository-verification",
            )
        return task_id

    @staticmethod
    def case_request(task_id: str, **changes: object) -> ReplayCaseCreate:
        payload: dict[str, object] = {
            "schema_version": 1,
            "source_task_id": task_id,
            "input": {"question": "Return the deterministic fixture result."},
            "evaluation_spec": {
                "kind": "exact_match",
                "expected_hash": hashlib.sha256(b"expected").hexdigest(),
            },
            "privacy_class": "internal",
            "privacy_permission_ref": None,
            "evidence": ["test:replay-case"],
        }
        payload.update(changes)
        return ReplayCaseCreate.from_dict(payload)

    @staticmethod
    def replay_request(case_id: str, **changes: object) -> ReplayRequest:
        payload: dict[str, object] = {
            "schema_version": 1,
            "case_id": case_id,
            "target_kind": "model",
            "target_ref": "local:model-a",
            "target_version_hash": "a" * 64,
            "evaluator_ref": "evaluator:exact-match-v1",
            "seed": 7,
            "evidence": ["test:replay-request"],
        }
        payload.update(changes)
        return ReplayRequest.from_dict(payload)

    def test_replays_models_skills_routers_and_context_with_paired_comparison(self):
        case = self.runtime.add_replay_case(self.case_request(self.task()))
        baseline_adapter = DeterministicReplayAdapter(quality=700_000)
        candidate_adapter = DeterministicReplayAdapter(quality=900_000)
        baseline = self.runtime.replay_task(
            self.replay_request(case.id), baseline_adapter
        )
        candidate = self.runtime.replay_task(
            self.replay_request(
                case.id,
                target_kind="context_algorithm",
                target_ref="context:bounded-v2",
                target_version_hash="b" * 64,
            ),
            candidate_adapter,
        )
        skill = self.runtime.replay_task(
            self.replay_request(
                case.id,
                target_kind="skill",
                target_ref="skill:sqlite-v2",
                target_version_hash="c" * 64,
            ),
            DeterministicReplayAdapter(),
        )
        router = self.runtime.replay_task(
            self.replay_request(
                case.id,
                target_kind="router",
                target_ref="router:bounded-v2",
                target_version_hash="d" * 64,
            ),
            DeterministicReplayAdapter(),
        )
        comparison = self.runtime.replay.compare(baseline.id, candidate.id)
        self.assertEqual(comparison["delta"]["quality_micros"], 200_000)
        self.assertFalse(comparison["causal_claim"])
        self.assertFalse(comparison["promotion_authority"])
        self.assertFalse(candidate.as_dict()["deployment_authority"])
        self.assertFalse(candidate_adapter.calls[0].external_network_allowed)
        self.assertNotIn("input", candidate.as_dict())
        self.assertEqual(
            {
                baseline.target_kind,
                skill.target_kind,
                router.target_kind,
                candidate.target_kind,
            },
            {"model", "skill", "router", "context_algorithm"},
        )

    def test_case_requires_terminal_task_sanitized_privacy_and_secret_free_input(self):
        with self.assertRaisesRegex(ReplayError, "completed source task"):
            self.runtime.add_replay_case(self.case_request(self.task(completed=False)))
        task_id = self.task()
        with self.assertRaisesRegex(ReplayError, "public or internal"):
            self.case_request(
                task_id,
                privacy_class="confidential",
                privacy_permission_ref=None,
            )
        token = "sk-" + "A1b2C3d4E5f6G7h8I9j0K1"
        with self.assertRaisesRegex(ReplayError, "secret material"):
            self.case_request(task_id, input={"credential": token})

    def test_adapter_contract_and_observation_schema_fail_closed(self):
        case = self.runtime.add_replay_case(self.case_request(self.task()))

        class UnsafeAdapter(DeterministicReplayAdapter):
            def identity(self):
                return {
                    **super().identity(),
                    "external_network": "allowed",
                }

        with self.assertRaisesRegex(ReplayError, "isolation contract"):
            self.runtime.replay_task(
                self.replay_request(case.id), UnsafeAdapter()
            )

        class InvalidAdapter(DeterministicReplayAdapter):
            def run(self, context):
                return {"quality": 1}

        with self.assertRaisesRegex(ReplayError, "invalid observation"):
            self.runtime.replay_task(
                self.replay_request(case.id), InvalidAdapter()
            )

    def test_cases_runs_are_immutable_idempotent_and_safe_mode_guarded(self):
        case_request = self.case_request(self.task())
        case = self.runtime.add_replay_case(case_request)
        self.assertEqual(self.runtime.add_replay_case(case_request).id, case.id)
        with self.assertRaisesRegex(ReplayError, "different provenance"):
            self.runtime.add_replay_case(
                ReplayCaseCreate(
                    source_task_id=case_request.source_task_id,
                    input_payload=case_request.input_payload,
                    evaluation_spec=case_request.evaluation_spec,
                    privacy_class=case_request.privacy_class,
                    privacy_permission_ref=case_request.privacy_permission_ref,
                    evidence=("test:different-provenance",),
                )
            )
        adapter = DeterministicReplayAdapter()
        request = self.replay_request(case.id)
        first = self.runtime.replay_task(request, adapter)
        replay = self.runtime.replay_task(request, adapter)
        self.assertEqual(first.id, replay.id)
        self.assertEqual(len(adapter.calls), 1)
        with self.assertRaises(sqlite3.IntegrityError):
            self.runtime.db.connection.execute(
                "UPDATE replay_runs SET quality_micros=0 WHERE id=?",
                (first.id,),
            )

        blocked_task = self.task()
        self.runtime.safe_mode.enable(actor_id="test", reason="containment")
        with self.assertRaises(SafeModeViolation):
            self.runtime.replay.add_case(self.case_request(blocked_task))
        with self.assertRaises(SafeModeViolation):
            self.runtime.replay.run(
                self.replay_request(case.id, seed=8), adapter
            )

    def test_cli_reports_hashes_and_comparison_without_raw_inputs(self):
        case = self.runtime.add_replay_case(self.case_request(self.task()))
        baseline = self.runtime.replay_task(
            self.replay_request(case.id), DeterministicReplayAdapter(quality=600_000)
        )
        candidate = self.runtime.replay_task(
            self.replay_request(
                case.id,
                target_kind="router",
                target_ref="router:v2",
                target_version_hash="c" * 64,
            ),
            DeterministicReplayAdapter(quality=800_000),
        )
        database = str(self.database)

        def invoke(*arguments: str) -> dict[str, object]:
            output = io.StringIO()
            with redirect_stdout(output):
                result = main(["--db", database, "replay", *arguments])
            self.assertEqual(result, 0)
            return json.loads(output.getvalue())

        self.runtime.close()
        report = invoke("case-report", case.id)
        run = invoke("run-report", baseline.id)
        comparison = invoke("compare", baseline.id, candidate.id)
        self.assertNotIn("input", report)
        self.assertEqual(run["id"], baseline.id)
        self.assertEqual(comparison["delta"]["quality_micros"], 200_000)
        self.runtime = AdaptiveRuntime(self.database)


if __name__ == "__main__":
    unittest.main()
