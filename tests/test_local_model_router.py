import json
import tempfile
import unittest
from pathlib import Path

from acr_runtime.benchmark import BenchmarkDataset
from acr_runtime.db import RuntimeDB
from acr_runtime.local_model_router import LocalModelRouter, LocalRouteRequest
from acr_runtime.model_router import (
    ModelOutcome,
    ModelProfile,
    ModelRouter,
    RouteAttempt,
    RouteRequest,
)
from acr_runtime.providers import (
    MockProvider,
    ModelCapabilities,
    ModelMetadata,
)


class InspectableLocalProvider(MockProvider):
    name = "ollama"

    def __init__(self):
        super().__init__(lambda request: request.messages[-1].content)
        self._models = (
            ModelMetadata(
                provider="ollama", model="local-test",
                capabilities=ModelCapabilities(
                    chat=True, structured_output=True, tool_calling=True,
                    streaming=True, token_accounting=True, context_window=16_384,
                ),
                local=True, input_cost_per_million=0,
                output_cost_per_million=0,
            ),
        )

    def inspect_model(self, model):
        self.capabilities(model)
        return self._models[0]


class LocalModelRouterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = RuntimeDB(Path(self.temp.name) / "acr.db")
        self.generic = ModelRouter(self.db.connection)
        self.local = LocalModelRouter(self.db.connection, self.generic)

    def tearDown(self):
        self.db.close()
        self.temp.cleanup()

    def add_profile(self, model, *, local, cost, quality):
        profile = ModelProfile(
            "test", model, 16_384, True, cost, cost, local=local
        )
        self.generic.register(profile)
        for index in range(3):
            self.generic.record_outcome(ModelOutcome(
                profile.id, "classification", True, quality, 10, 100, 20,
                1, 1, (f"eval:{model}:{index}",),
            ))
        return profile.id

    @staticmethod
    def request(*, sensitive=False, configured=True, permission=None):
        return LocalRouteRequest(
            route=RouteRequest(
                "classification", 0.7, 0.7, 100, 20, 1000,
                True, 0.7, 3, 0.0, 0.7,
            ),
            risk_level="low",
            contains_sensitive_context=sensitive,
            cloud_escalation_configured=configured,
            external_permission_reference=permission,
        )

    def test_local_is_preferred_and_configured_cloud_can_be_escalation(self):
        local_id = self.add_profile(
            "local", local=True, cost=1.0, quality=0.8
        )
        cloud_id = self.add_profile(
            "cloud", local=False, cost=0.0, quality=0.95
        )
        route = self.local.route(self.request())
        self.assertEqual(route.selected_model_id, local_id)
        route = self.generic.record_attempt(route.id, RouteAttempt(
            local_id, False, 0.2, 0.4, 10, 100, 20, 1, 0,
            ("verification:local-failed",),
        ))
        self.assertEqual(route.escalation_model_id, cloud_id)
        policy = self.local.policy(route.id)
        self.assertEqual(policy["cloud_candidates_allowed"], 1)

    def test_sensitive_context_blocks_cloud_without_policy_permission(self):
        local_id = self.add_profile(
            "local", local=True, cost=0, quality=0.8
        )
        self.add_profile("cloud", local=False, cost=0, quality=0.95)
        route = self.local.route(self.request(sensitive=True))
        self.assertEqual(route.selected_model_id, local_id)
        self.assertEqual({item["model_id"] for item in route.candidates}, {local_id})
        policy = self.local.policy(route.id)
        self.assertEqual(policy["cloud_candidates_allowed"], 0)
        self.assertIsNone(policy["external_permission_reference_hash"])

        permitted = self.local.route(self.request(
            sensitive=True, permission="policy-ticket-123"
        ))
        policy = self.local.policy(permitted.id)
        self.assertEqual(policy["cloud_candidates_allowed"], 1)
        self.assertNotIn("policy-ticket-123", json.dumps(policy))

    def test_discovery_registers_authoritative_local_capabilities(self):
        discovery = self.local.discover(InspectableLocalProvider())
        self.assertEqual(discovery["status"], "completed")
        profile = self.db.connection.execute(
            "SELECT * FROM model_profiles WHERE id='ollama:local-test'"
        ).fetchone()
        self.assertEqual(profile["context_capacity"], 16_384)
        self.assertEqual(profile["supports_tools"], 1)
        self.assertEqual(profile["local"], 1)

    def test_five_class_benchmark_becomes_verified_routing_history(self):
        provider = InspectableLocalProvider()
        discovery = self.local.discover(provider)
        path = Path(self.temp.name) / "local.jsonl"
        records = [{"record_type": "dataset", "name": "local", "version": 1}]
        for category in (
            "classification", "summarization", "memory_extraction",
            "simple_planning", "code_analysis",
        ):
            records.append({
                "record_type": "case", "id": category, "category": category,
                "prompt": category, "expected": category,
            })
        path.write_text(
            "\n".join(json.dumps(record) for record in records),
            encoding="utf-8",
        )
        result = self.local.benchmark(
            provider, BenchmarkDataset.load(path), model="local-test",
            discovery_id=discovery["id"],
        )
        self.assertEqual(len(result["outcome_ids"]), 5)
        classes = {
            row[0] for row in self.db.connection.execute(
                "SELECT task_class FROM model_outcomes"
            )
        }
        self.assertEqual(classes, {
            "classification", "summarization", "memory_extraction",
            "simple_planning", "code_analysis",
        })


if __name__ == "__main__":
    unittest.main()
