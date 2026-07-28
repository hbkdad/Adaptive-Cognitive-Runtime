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
from acr_runtime.permissions import (
    CapabilityGrantRequest,
    PermissionController,
)
from acr_runtime.utility_governance import UtilityGovernor
from datetime import datetime, timedelta, timezone


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
        self.permissions = PermissionController(self.db.connection)
        self.router = ToolRouter(
            self.db.connection, self.registry, self.permissions
        )

    def tearDown(self):
        self.db.close()
        self.temp.cleanup()

    def add(self, name, description, *, permissions=(), side="READ_ONLY",
            network=False, filesystem="NONE", cost=0, latency=10):
        self.registry.register(ToolDefinition(
            name, description, schema("input"), schema("output"),
            tuple(permissions), cost, latency, side, network, filesystem, (),
        ))

    def request(self, task, *, permissions=None, **changes):
        if permissions is None:
            permissions = (
                "network.read", "filesystem.read", "filesystem.write",
                "database.read",
            )
        scope = changes.get("resource_scope", "resource:unit")
        subject_id = changes.get("subject_id", "task-route")
        for capability in permissions:
            self.permissions.grant(CapabilityGrantRequest(
                "task", subject_id, capability, scope,
                (
                    datetime.now(timezone.utc) + timedelta(hours=1)
                ).isoformat(),
                False, "trusted_workflow", "operator-test",
                "Tool router test requirement", ("test:tool-router",),
            ))
        values = {
            "task": task, "task_class": "unit",
            "subject_type": "task", "subject_id": subject_id,
            "resource_scope": scope,
            "network_allowed": True, "filesystem_access": "READ",
            "available_credentials": (),
        }
        values.update(changes)
        return ToolRouteRequest(**values)

    def test_calculation_routes_to_deterministic_calculator(self):
        self.add("calculator.evaluate", "deterministic arithmetic calculation",
                 permissions=())
        self.add("web.search", "retrieve current web facts",
                 permissions=("network.read",), network=True, latency=200)
        route = self.router.route(self.request("calculate 17 times 23"))
        self.assertEqual(route["selected_tools"], ["calculator.evaluate"])
        self.assertTrue(route["deterministic_tool_required"])

    def test_permission_gate_is_non_bypassable_and_retains_rejection(self):
        self.add("web.search", "retrieve current web facts",
                 permissions=("network.read",), network=True)
        route = self.router.route(self.request(
            "find the latest current news", network_allowed=False,
            permissions=(),
        ))
        self.assertEqual(route["selected_tools"], [])
        self.assertTrue(route["deterministic_tool_required"])
        self.assertEqual(set(route["candidates"][0]["rejection_reasons"]), {
            "missing_permissions", "network_not_allowed",
        })

    def test_cost_latency_and_side_effect_risk_affect_selection(self):
        self.add("database.query", "focused deterministic database query",
                 permissions=("database.read", "filesystem.read"),
                 filesystem="READ", latency=10)
        self.add("database.rebuild", "database query by destructive rebuild",
                 permissions=("database.read", "filesystem.write"),
                 side="DESTRUCTIVE",
                 filesystem="WRITE", latency=1000, cost=2)
        route = self.router.route(self.request(
            "query the database table", approval_reference="approved",
            filesystem_access="WRITE",
        ))
        self.assertEqual(route["selected_tools"], ["database.query"])

    def test_evidenced_outcomes_update_task_class_reliability(self):
        self.add("filesystem.search", "deterministic filesystem file search",
                 permissions=("filesystem.read",), filesystem="READ")
        route = self.router.route(self.request("search files in the folder"))
        outcome_id = self.router.record_outcome(ToolOutcome(
            route["id"], "filesystem.search", True, 5, 0,
            ("test:verified-result",),
        ))
        self.assertTrue(outcome_id)
        utility = UtilityGovernor(self.db.connection).snapshot(
            "tool", "filesystem.search"
        )
        self.assertEqual(utility.positive_count, 1)
        self.assertEqual(utility.observed_uses, 1)
        updated = self.router.route(self.request("search files in the folder"))
        candidate = updated["candidates"][0]
        self.assertEqual(candidate["historical_uses"], 1)
        self.assertGreater(candidate["historical_reliability"], 0.5)
        with self.assertRaises(ValueError):
            self.router.record_outcome(ToolOutcome(
                route["id"], "filesystem.search", True, 5, 0,
                ("test:duplicate",),
            ))

    def test_governed_route_uses_exact_stored_grant_not_asserted_permissions(self):
        self.add(
            "database.query", "focused deterministic database query",
            permissions=("database.read",), filesystem="NONE",
        )
        expires = (
            datetime.now(timezone.utc) + timedelta(hours=1)
        ).isoformat()
        self.permissions.grant(CapabilityGrantRequest(
            "task", "task-36", "database.read", "database:demo",
            expires, False, "trusted_workflow", "operator-36",
            "Unit-test minimum access", ("test:prompt36",),
        ))
        route = self.router.route(ToolRouteRequest(
            task="query the database table", task_class="unit",
            network_allowed=False, filesystem_access="NONE",
            available_credentials=(), subject_type="task",
            subject_id="task-36", resource_scope="database:demo",
        ))
        self.assertEqual(route["selected_tools"], ["database.query"])
        self.assertEqual(
            route["request"]["authorization_mode"], "capability_grants"
        )
        self.assertTrue(
            route["candidates"][0]["capability_decisions"]
        )
        denied = self.router.route(ToolRouteRequest(
            task="query the database table", task_class="unit",
            network_allowed=False, filesystem_access="NONE",
            available_credentials=(), subject_type="task",
            subject_id="task-36", resource_scope="database:other",
        ))
        self.assertEqual(denied["selected_tools"], [])
        self.assertIn(
            "missing_permissions",
            denied["candidates"][0]["rejection_reasons"],
        )

    def test_json_routes_require_governed_identity_and_reject_asserted_grants(self):
        base = {
            "task": "query database", "task_class": "unit",
            "network_allowed": False, "filesystem_access": "NONE",
            "available_credentials": [], "subject_type": "task",
            "subject_id": "task-36", "resource_scope": "database:demo",
        }
        request = ToolRouteRequest.from_dict(base)
        self.assertEqual(request.subject_id, "task-36")
        with self.assertRaises(ValueError):
            ToolRouteRequest.from_dict({
                **base, "granted_permissions": ["database.read"],
            })


if __name__ == "__main__":
    unittest.main()
