import tempfile
import unittest
from pathlib import Path

from acr_runtime.db import RuntimeDB
from acr_runtime.tool_registry import ToolDefinition, ToolRegistry
from acr_runtime.tool_router import (
    ToolOutcome,
    ToolRouteRequest,
    ToolRouter,
)


def schema(name):
    return {
        "type": "object", "properties": {name: {"type": "string"}},
        "required": [name], "additionalProperties": False,
    }


class ToolRouterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = RuntimeDB(Path(self.temp.name) / "acr.db")
        self.registry = ToolRegistry(self.db.connection)
        self.router = ToolRouter(self.db.connection, self.registry)

    def tearDown(self):
        self.db.close()
        self.temp.cleanup()

    def add(self, name, description, *, permission, side="READ_ONLY",
            network=False, filesystem="NONE", cost=0, latency=10):
        self.registry.register(ToolDefinition(
            name, description, schema("input"), schema("output"),
            (permission,), cost, latency, side, network, filesystem, (),
        ))

    @staticmethod
    def request(task, **changes):
        values = {
            "task": task, "task_class": "unit",
            "granted_permissions": ("compute:read", "network:read",
                                    "filesystem:read", "database:read"),
            "network_allowed": True, "filesystem_access": "READ",
            "available_credentials": (),
        }
        values.update(changes)
        return ToolRouteRequest(**values)

    def test_calculation_routes_to_deterministic_calculator(self):
        self.add("calculator.evaluate", "deterministic arithmetic calculation",
                 permission="compute:read")
        self.add("web.search", "retrieve current web facts",
                 permission="network:read", network=True, latency=200)
        route = self.router.route(self.request("calculate 17 times 23"))
        self.assertEqual(route["selected_tools"], ["calculator.evaluate"])
        self.assertTrue(route["deterministic_tool_required"])

    def test_permission_gate_is_non_bypassable_and_retains_rejection(self):
        self.add("web.search", "retrieve current web facts",
                 permission="network:read", network=True)
        route = self.router.route(self.request(
            "find the latest current news", network_allowed=False,
            granted_permissions=(),
        ))
        self.assertEqual(route["selected_tools"], [])
        self.assertTrue(route["deterministic_tool_required"])
        self.assertEqual(set(route["candidates"][0]["rejection_reasons"]), {
            "missing_permissions", "network_not_allowed",
        })

    def test_cost_latency_and_side_effect_risk_affect_selection(self):
        self.add("database.query", "focused deterministic database query",
                 permission="database:read", filesystem="READ", latency=10)
        self.add("database.rebuild", "database query by destructive rebuild",
                 permission="database:read", side="DESTRUCTIVE",
                 filesystem="WRITE", latency=1000, cost=2)
        route = self.router.route(self.request(
            "query the database table", approval_reference="approved",
            filesystem_access="WRITE",
        ))
        self.assertEqual(route["selected_tools"], ["database.query"])

    def test_evidenced_outcomes_update_task_class_reliability(self):
        self.add("filesystem.search", "deterministic filesystem file search",
                 permission="filesystem:read", filesystem="READ")
        route = self.router.route(self.request("search files in the folder"))
        outcome_id = self.router.record_outcome(ToolOutcome(
            route["id"], "filesystem.search", True, 5, 0,
            ("test:verified-result",),
        ))
        self.assertTrue(outcome_id)
        updated = self.router.route(self.request("search files in the folder"))
        candidate = updated["candidates"][0]
        self.assertEqual(candidate["historical_uses"], 1)
        self.assertGreater(candidate["historical_reliability"], 0.5)
        with self.assertRaises(ValueError):
            self.router.record_outcome(ToolOutcome(
                route["id"], "filesystem.search", True, 5, 0,
                ("test:duplicate",),
            ))


if __name__ == "__main__":
    unittest.main()
