import tempfile
import unittest
from pathlib import Path

from acr_runtime.db import RuntimeDB
from acr_runtime.tool_registry import (
    ToolAccessRequest,
    ToolDefinition,
    ToolRegistry,
)


def schema(name):
    return {
        "type": "object",
        "properties": {name: {"type": "string"}},
        "required": [name],
        "additionalProperties": False,
    }


class ToolRegistryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = RuntimeDB(Path(self.temp.name) / "acr.db")
        self.registry = ToolRegistry(self.db.connection)

    def tearDown(self):
        self.db.close()
        self.temp.cleanup()

    def definition(self, **changes):
        values = {
            "name": "filesystem.delete", "description": "Delete one file",
            "input_schema": schema("path"), "output_schema": schema("status"),
            "permissions": ("filesystem.write",), "cost": 0,
            "latency_estimate_ms": 20, "side_effect": "DESTRUCTIVE",
            "network_access": False, "filesystem_access": "WRITE",
            "credential_requirements": (),
        }
        values.update(changes)
        return ToolDefinition(**values)

    def test_definition_round_trips_and_is_immutable(self):
        first = self.registry.register(self.definition())
        self.assertEqual(first["side_effect"], "DESTRUCTIVE")
        self.assertTrue(first["requires_approval"])
        self.assertEqual(
            self.registry.register(self.definition())["definition_hash"],
            first["definition_hash"],
        )
        with self.assertRaises(ValueError):
            self.registry.register(self.definition(cost=1))

    def test_authorization_explains_every_missing_boundary(self):
        self.registry.register(self.definition(
            network_access=True,
            credential_requirements=("token:storage",),
            permissions=(
                "filesystem.write", "network.write", "credential.use"
            ),
        ))
        denied = self.registry.authorize(ToolAccessRequest(
            "filesystem.delete", (), False, "READ", (), None
        ))
        self.assertFalse(denied["allowed"])
        self.assertEqual(set(denied["rejection_reasons"]), {
            "missing_permissions", "network_not_allowed",
            "insufficient_filesystem_access", "missing_credentials",
            "destructive_action_requires_approval",
        })
        allowed = self.registry.authorize(ToolAccessRequest(
            "filesystem.delete",
            ("filesystem.write", "network.write", "credential.use"),
            True, "WRITE",
            ("token:storage",), "approval:123",
        ))
        self.assertTrue(allowed["allowed"])

    def test_strict_schemas_and_read_only_write_conflict_fail_closed(self):
        with self.assertRaises(ValueError):
            self.registry.register(self.definition(
                input_schema={"type": "object", "properties": {}},
            ))
        with self.assertRaises(ValueError):
            self.registry.register(self.definition(
                side_effect="READ_ONLY", filesystem_access="WRITE",
            ))
        with self.assertRaises(ValueError):
            ToolDefinition.from_dict({
                **self.definition().as_dict(), "unknown": True,
            })
        with self.assertRaises(ValueError):
            self.definition(permissions=("filesystem:delete",))


if __name__ == "__main__":
    unittest.main()
