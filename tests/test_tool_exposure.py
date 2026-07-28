from __future__ import annotations

import hashlib
import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from acr_runtime.agent_spec import AgentSpec
from acr_runtime.permissions import CapabilityGrantRequest
from acr_runtime.service import AdaptiveRuntime
from acr_runtime.tool_exposure import (
    ToolExposureBenchmarkSpec,
    ToolExposureTrial,
)
from acr_runtime.tool_registry import ToolDefinition
from acr_runtime.tool_router import ToolRouteRequest


def schema(name: str) -> dict[str, object]:
    return {
        "type": "object",
        "properties": {name: {"type": "string"}},
        "required": [name],
        "additionalProperties": False,
    }


def digest(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


class ToolExposureTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.database = Path(self.temporary.name) / "acr.db"
        self.runtime = AdaptiveRuntime(self.database)

    def tearDown(self) -> None:
        self.runtime.close()
        self.temporary.cleanup()

    def add_tool(
        self,
        name: str,
        description: str,
        *,
        permissions: tuple[str, ...] = (),
        side_effect: str = "READ_ONLY",
        credential_requirements: tuple[str, ...] = (),
    ) -> None:
        self.runtime.tools.register(ToolDefinition(
            name=name,
            description=description,
            input_schema=schema("input"),
            output_schema=schema("output"),
            permissions=permissions,
            cost=0,
            latency_estimate_ms=5,
            side_effect=side_effect,
            network_access=False,
            filesystem_access="NONE",
            credential_requirements=credential_requirements,
        ))

    @staticmethod
    def agent_payload(
        *,
        tools: list[str],
        permissions: list[str],
        agent_id: str = "database-worker",
    ) -> dict[str, object]:
        return {
            "id": agent_id,
            "role": "Focused database worker",
            "objective": "Run only assigned database diagnostics.",
            "task_scope": ["database-diagnostics"],
            "tools": tools,
            "skills": [],
            "memory_scope": ["project-alpha"],
            "model_policy": {
                "allowed_models": ["local-test"],
                "preferred_model": "local-test",
                "local_only": True,
                "allow_fallback": False,
            },
            "token_budget": 4_000,
            "money_budget": 0,
            "time_budget": 300,
            "permissions": permissions,
            "communication": {
                "mode": "none",
                "allowed_peers": [],
                "max_messages": 0,
            },
            "termination_conditions": [
                "objective_met",
                "verification_failed",
                "budget_exhausted",
                "time_exhausted",
                "cancelled",
            ],
            "verification_requirements": ["Return bounded evidence."],
        }

    def grant(self, capability: str) -> str:
        result = self.runtime.permissions.grant(CapabilityGrantRequest(
            subject_type="agent",
            subject_id="database-worker",
            capability=capability,
            resource_scope="database:demo",
            expires_at=(
                datetime.now(timezone.utc) + timedelta(hours=1)
            ).isoformat(),
            delegable=False,
            grantor_type="trusted_workflow",
            grantor_id="operator-test",
            reason="Prompt 73 test grant",
            evidence=("test:tool-exposure",),
        ))
        return str(result["id"])

    def request(self, **overrides: object) -> ToolRouteRequest:
        values: dict[str, object] = {
            "task": "query the sqlite database",
            "task_class": "database-diagnostics",
            "subject_type": "agent",
            "subject_id": "database-worker",
            "resource_scope": "database:demo",
            "network_allowed": False,
            "filesystem_access": "NONE",
            "available_credentials": (),
            "max_tools": 3,
        }
        values.update(overrides)
        return ToolRouteRequest(**values)

    def prepared_projection(self) -> dict[str, object]:
        self.add_tool("calculator.evaluate", "deterministic arithmetic calculation")
        self.add_tool(
            "database.query",
            "focused sqlite database query",
            permissions=("database.read",),
        )
        self.runtime.agent_specs.define(AgentSpec.from_dict(self.agent_payload(
            tools=["calculator.evaluate", "database.query"],
            permissions=["database.read"],
        )))
        self.grant("database.read")
        route = self.runtime.tool_exposure.route_for_agent(
            self.request(), "database-worker"
        )
        return self.runtime.tool_exposure.create_projection(
            str(route["id"]), "database-worker"
        )

    def test_projection_intersects_agent_scope_and_preserves_canonical_payload(self):
        projection = self.prepared_projection()
        self.assertEqual(projection["status"], "available")
        self.assertEqual(
            projection["baseline_tools"],
            ["calculator.evaluate", "database.query"],
        )
        self.assertEqual(projection["exposed_tools"], ["database.query"])

        rendered = self.runtime.tool_exposure.render(str(projection["id"]))
        self.assertEqual(rendered["tool_count"], 1)
        payload = rendered["tools"][0]
        self.assertEqual(
            set(payload["function"]),
            {"name", "description", "parameters"},
        )
        self.assertEqual(payload["function"]["name"], "database.query")
        self.assertNotIn("output_schema", json.dumps(rendered))
        self.assertEqual(rendered["estimate"]["quality"], "estimated")

    def test_revocation_after_projection_fails_closed(self):
        projection = self.prepared_projection()
        grant_id = self.runtime.db.connection.execute(
            """
            SELECT id FROM capability_grants
            WHERE subject_type='agent' AND subject_id='database-worker'
            """
        ).fetchone()["id"]
        self.runtime.permissions.revoke(grant_id, reason="test revocation")
        with self.assertRaises(PermissionError):
            self.runtime.tool_exposure.render(str(projection["id"]))

    def test_router_does_not_disclose_tool_outside_agent(self):
        self.add_tool("database.query", "focused sqlite database query")
        self.add_tool("database.admin", "sqlite database query administrator")
        self.runtime.agent_specs.define(AgentSpec.from_dict(self.agent_payload(
            tools=["database.query"], permissions=[]
        )))
        route = self.runtime.tool_exposure.route_for_agent(
            self.request(), "database-worker"
        )
        self.assertEqual(route["selected_tools"], ["database.query"])
        self.assertEqual(
            [item["tool_name"] for item in route["candidates"]],
            ["database.query"],
        )

    def test_mismatched_route_and_credentialed_or_destructive_tools_deny(self):
        self.add_tool("database.safe", "focused sqlite database query")
        self.add_tool(
            "database.credentialed",
            "focused sqlite database query with credential",
            permissions=("credential.use",),
            credential_requirements=("database-token",),
        )
        self.add_tool(
            "database.destroy",
            "destructive sqlite database query",
            side_effect="DESTRUCTIVE",
        )
        self.runtime.agent_specs.define(AgentSpec.from_dict(self.agent_payload(
            tools=[
                "database.safe", "database.credentialed", "database.destroy",
            ],
            permissions=["credential.use"],
        )))
        self.grant("credential.use")
        route = self.runtime.tool_exposure.route_for_agent(
            self.request(
                available_credentials=("database-token",),
                approval_reference="approved-once",
            ),
            "database-worker",
        )
        projection = self.runtime.tool_exposure.create_projection(
            str(route["id"]), "database-worker"
        )
        self.assertEqual(projection["status"], "unavailable")
        self.assertIn(
            "selected_tool_not_currently_authorized", projection["reasons"]
        )

        other = ToolRouteRequest(
            **{
                **self.request().__dict__,
                "subject_id": "different-agent",
            }
        )
        with self.assertRaises(ValueError):
            self.runtime.tool_exposure.route_for_agent(
                other, "database-worker"
            )

    def benchmark_spec(
        self, projection: dict[str, object]
    ) -> ToolExposureBenchmarkSpec:
        cases = tuple(digest({"case": index}) for index in range(5))
        return ToolExposureBenchmarkSpec(
            task_class="database-diagnostics",
            agent_spec_hash=str(projection["agent_spec_hash"]),
            catalog_hash=str(projection["catalog_hash"]),
            selector_hash=str(projection["selector_hash"]),
            dataset_hash=digest(list(cases)),
            model_hash=digest("local-test"),
            settings_hash=digest({"temperature": 0}),
            evaluator_hash=digest("deterministic-evaluator-v1"),
            case_hashes=cases,
            seed=7,
            expected_cases=5,
            quality_margin_micros=10_000,
        )

    def record_pairs(
        self,
        run_id: str,
        projection_id: str,
        cases: tuple[str, ...],
        *,
        token_quality: str = "provider_reported",
        dynamic_success: bool = True,
        dynamic_invalid: int = 0,
    ) -> None:
        sequence = 0
        for case_index, case_hash in enumerate(cases):
            for arm in ("full_authorized", "dynamic"):
                sequence += 1
                self.runtime.tool_exposure.record_trial(ToolExposureTrial(
                    run_id=run_id,
                    sequence=sequence,
                    case_hash=case_hash,
                    projection_id=projection_id,
                    arm=arm,
                    attempt_id=(
                        f"attempt-{run_id[:8]}-{case_index}-{arm}"
                    ),
                    success=(dynamic_success if arm == "dynamic" else True),
                    quality_micros=900_000,
                    required_tool_recall_micros=1_000_000,
                    hard_violation=False,
                    unauthorized_exposure_count=0,
                    invalid_call_count=(
                        dynamic_invalid if arm == "dynamic" else 0
                    ),
                    input_tokens=50 if arm == "dynamic" else 100,
                    output_tokens=10,
                    cached_tokens=0,
                    token_quality=token_quality,
                    latency_ms=10,
                    evidence_hash=digest(
                        {"case": case_hash, "arm": arm}
                    ),
                ))

    def test_paired_import_stays_unverified_even_when_candidate_preserves_quality(self):
        projection = self.prepared_projection()
        spec = self.benchmark_spec(projection)
        run = self.runtime.tool_exposure.start_benchmark(spec)
        self.record_pairs(
            str(run["id"]), str(projection["id"]), spec.case_hashes
        )
        sealed = self.runtime.tool_exposure.seal_benchmark(str(run["id"]))
        self.assertEqual(sealed["status"], "insufficient_evidence")
        self.assertEqual(
            sealed["recommendation"], "collect_verified_receipts"
        )
        self.assertTrue(sealed["summary"]["candidate_quality_preserved"])
        self.assertEqual(
            sealed["summary"]["receipt_provenance"],
            "caller_supplied_unverified",
        )
        self.assertFalse(sealed["summary"]["automatic_activation"])
        self.assertEqual(sealed["summary"]["input_token_delta"], -250)
        with self.assertRaises(sqlite3.IntegrityError):
            self.runtime.db.connection.execute(
                """
                UPDATE tool_exposure_benchmark_trials
                SET input_tokens=1 WHERE run_id=?
                """,
                (run["id"],),
            )

    def test_estimated_or_failed_benchmark_cannot_support(self):
        projection = self.prepared_projection()
        first_spec = self.benchmark_spec(projection)
        first = self.runtime.tool_exposure.start_benchmark(first_spec)
        self.record_pairs(
            str(first["id"]), str(projection["id"]),
            first_spec.case_hashes, token_quality="estimated",
        )
        sealed = self.runtime.tool_exposure.seal_benchmark(str(first["id"]))
        self.assertEqual(sealed["status"], "insufficient_evidence")

        second_spec = ToolExposureBenchmarkSpec(
            **{
                **first_spec.__dict__,
                "seed": 8,
            }
        )
        second = self.runtime.tool_exposure.start_benchmark(second_spec)
        self.record_pairs(
            str(second["id"]), str(projection["id"]),
            second_spec.case_hashes, dynamic_success=False,
        )
        rejected = self.runtime.tool_exposure.seal_benchmark(str(second["id"]))
        self.assertEqual(rejected["status"], "rejected")

    def test_schema_rejects_forged_projection_and_unsealed_case(self):
        projection = self.prepared_projection()
        with self.assertRaises(sqlite3.IntegrityError):
            self.runtime.db.connection.execute(
                """
                INSERT INTO tool_exposure_projections(
                    id, route_id, agent_spec_id, task_class, agent_spec_hash,
                    catalog_hash, selector_hash, mode, baseline_tools_json,
                    exposed_tools_json, definition_hashes_json,
                    baseline_tool_count, exposed_tool_count,
                    baseline_estimated_tokens, exposed_estimated_tokens,
                    estimate_version, status, reasons_json, created_at
                ) VALUES (
                    'forged', ?, 'database-worker', 'wrong-task', ?, ?, ?,
                    'direct_filtered', '["ghost"]', '["ghost"]', '{}',
                    1, 1, 1, 1, 'test', 'available', '[]',
                    '2026-07-28T00:00:00Z'
                )
                """,
                (
                    projection["route_id"], "a" * 64, "b" * 64, "c" * 64,
                ),
            )

        broken_hashes = dict(projection["definition_hashes"])
        removed = next(iter(broken_hashes))
        broken_hashes.pop(removed)
        broken_hashes["bogus"] = "a" * 64
        with self.assertRaises(sqlite3.IntegrityError):
            self.runtime.db.connection.execute(
                """
                INSERT INTO tool_exposure_projections(
                    id, route_id, agent_spec_id, task_class, agent_spec_hash,
                    catalog_hash, selector_hash, mode, baseline_tools_json,
                    exposed_tools_json, definition_hashes_json,
                    baseline_tool_count, exposed_tool_count,
                    baseline_estimated_tokens, exposed_estimated_tokens,
                    estimate_version, status, reasons_json, created_at
                ) VALUES (
                    'bad-definition-map', ?, 'database-worker',
                    'database-diagnostics', ?, ?, ?, 'direct_filtered',
                    ?, ?, ?, ?, ?, 100, 50,
                    'acr-json-char-estimate-v1.0.0', 'available', '[]',
                    '2026-07-28T00:00:00Z'
                )
                """,
                (
                    projection["route_id"], projection["agent_spec_hash"],
                    projection["catalog_hash"], projection["selector_hash"],
                    json.dumps(projection["baseline_tools"]),
                    json.dumps(projection["exposed_tools"]),
                    json.dumps(broken_hashes),
                    projection["baseline_tool_count"],
                    projection["exposed_tool_count"],
                ),
            )

        spec = self.benchmark_spec(projection)
        run = self.runtime.tool_exposure.start_benchmark(spec)
        with self.assertRaises(sqlite3.IntegrityError):
            self.runtime.tool_exposure.record_trial(ToolExposureTrial(
                run_id=str(run["id"]),
                sequence=1,
                case_hash=digest("not-in-dataset"),
                projection_id=str(projection["id"]),
                arm="dynamic",
                attempt_id="attempt-outside-dataset",
                success=True,
                quality_micros=900_000,
                required_tool_recall_micros=1_000_000,
                hard_violation=False,
                unauthorized_exposure_count=0,
                invalid_call_count=0,
                input_tokens=1,
                output_tokens=1,
                cached_tokens=0,
                token_quality="provider_reported",
                latency_ms=1,
                evidence_hash=digest("outside"),
            ))

    def test_unfiltered_route_and_stale_projection_cannot_benchmark(self):
        projection = self.prepared_projection()
        ordinary_route = self.runtime.tool_router.route(self.request())
        ordinary = self.runtime.tool_exposure.create_projection(
            str(ordinary_route["id"]), "database-worker"
        )
        self.assertEqual(ordinary["status"], "unavailable")
        self.assertIn("route_not_agent_filtered", ordinary["reasons"])

        spec = self.benchmark_spec(projection)
        run = self.runtime.tool_exposure.start_benchmark(spec)
        grant_id = self.runtime.db.connection.execute(
            """
            SELECT id FROM capability_grants
            WHERE subject_type='agent' AND subject_id='database-worker'
            """
        ).fetchone()["id"]
        self.runtime.permissions.revoke(grant_id, reason="benchmark stale test")
        with self.assertRaises(PermissionError):
            self.runtime.tool_exposure.record_trial(ToolExposureTrial(
                run_id=str(run["id"]),
                sequence=1,
                case_hash=spec.case_hashes[0],
                projection_id=str(projection["id"]),
                arm="dynamic",
                attempt_id="attempt-stale-projection",
                success=True,
                quality_micros=900_000,
                required_tool_recall_micros=1_000_000,
                hard_violation=False,
                unauthorized_exposure_count=0,
                invalid_call_count=0,
                input_tokens=50,
                output_tokens=10,
                cached_tokens=0,
                token_quality="provider_reported",
                latency_ms=10,
                evidence_hash=digest("stale"),
            ))


if __name__ == "__main__":
    unittest.main()
