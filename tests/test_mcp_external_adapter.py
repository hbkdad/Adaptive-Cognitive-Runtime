from __future__ import annotations

import asyncio
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from acr_runtime.content_security import ContentSecurityController
from acr_runtime.db import RuntimeDB
from acr_runtime.mcp_bridge import (
    ExternalMcpTool,
    ExternalMcpToolAdapter,
)
from acr_runtime.permissions import (
    CapabilityGrantRequest,
    PermissionController,
)


class FakeMcpClient:
    def __init__(self, result: object = None) -> None:
        self.result = {"answer": "safe"} if result is None else result
        self.calls: list[tuple[str, dict[str, object]]] = []

    async def list_tools(self) -> tuple[ExternalMcpTool, ...]:
        return (
            ExternalMcpTool(
                name="lookup",
                description="Remote-provided description",
                input_schema={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "limit": {"type": "integer"},
                    },
                    "required": ["query"],
                    "additionalProperties": False,
                },
            ),
        )

    async def call_tool(
        self, name: str, arguments: dict[str, object]
    ) -> object:
        self.calls.append((name, arguments))
        return self.result


class ExternalMcpAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.db = RuntimeDB(Path(self.temporary.name) / "acr.db")
        self.security = ContentSecurityController(self.db.connection)
        self.permissions = PermissionController(
            self.db.connection, self.security
        )

    def tearDown(self) -> None:
        self.db.close()
        self.temporary.cleanup()

    def adapter(self, client: FakeMcpClient) -> ExternalMcpToolAdapter:
        return ExternalMcpToolAdapter(
            client,
            namespace="reviewed",
            permissions=("network.read",),
            network_access=True,
            filesystem_access="NONE",
            security=self.security,
            permission_controller=self.permissions,
            subject_type="agent",
            subject_id="external-mcp-test",
            permission_scopes={"network.read": "mcp-server:reviewed"},
        )

    def grant(self) -> None:
        self.permissions.grant(
            CapabilityGrantRequest(
                subject_type="agent",
                subject_id="external-mcp-test",
                capability="network.read",
                resource_scope="mcp-server:reviewed",
                expires_at=(
                    datetime.now(timezone.utc) + timedelta(hours=1)
                ).isoformat(),
                delegable=False,
                grantor_type="trusted_workflow",
                grantor_id="mcp-adapter-operator",
                reason="Test reviewed external MCP server",
                evidence=("test:mcp-external",),
            )
        )

    def test_optional_remote_schema_is_wrapped_and_versioned(self) -> None:
        client = FakeMcpClient()
        adapter = self.adapter(client)
        definitions = asyncio.run(adapter.discover())
        self.assertEqual(len(definitions), 1)
        definition = definitions[0]
        self.assertRegex(
            definition.name, r"^mcp\.reviewed\.lookup\.[0-9a-f]{12}$"
        )
        self.assertEqual(
            definition.input_schema["required"], ["arguments"]
        )
        with self.assertRaises(PermissionError):
            asyncio.run(
                adapter.invoke(
                    definition.name, {"arguments": {"query": "safe"}}
                )
            )
        self.grant()
        result = asyncio.run(
            adapter.invoke(
                definition.name, {"arguments": {"query": "safe"}}
            )
        )
        self.assertIn('<untrusted_data origin="tool_output"', result["result"])
        self.assertEqual(client.calls, [("lookup", {"query": "safe"})])

    def test_secret_result_and_undiscovered_tool_fail_closed(self) -> None:
        secret_label = "pass" + "word"
        secret_value = "fixture" + "value123"
        client = FakeMcpClient({secret_label: secret_value})
        adapter = self.adapter(client)
        definition = asyncio.run(adapter.discover())[0]
        self.grant()
        with self.assertRaisesRegex(ValueError, "secret policy"):
            asyncio.run(
                adapter.invoke(
                    definition.name, {"arguments": {"query": "safe"}}
                )
            )
        with self.assertRaises(LookupError):
            asyncio.run(
                adapter.invoke(
                    "mcp.reviewed.unknown.deadbeef0000",
                    {"arguments": {}},
                )
            )

    def test_remote_reference_schema_is_rejected(self) -> None:
        class RefClient(FakeMcpClient):
            async def list_tools(self) -> tuple[ExternalMcpTool, ...]:
                return (
                    ExternalMcpTool(
                        "bad",
                        "bad",
                        {"type": "object", "$ref": "https://remote/schema"},
                    ),
                )

        with self.assertRaisesRegex(ValueError, "unsupported"):
            asyncio.run(self.adapter(RefClient()).discover())

    def test_reviewed_schema_is_enforced_before_remote_call(self) -> None:
        client = FakeMcpClient()
        adapter = self.adapter(client)
        definition = asyncio.run(adapter.discover())[0]
        self.grant()
        with self.assertRaisesRegex(ValueError, "reviewed schema"):
            asyncio.run(
                adapter.invoke(
                    definition.name, {"arguments": {"limit": 3}}
                )
            )
        self.assertEqual(client.calls, [])


if __name__ == "__main__":
    unittest.main()
