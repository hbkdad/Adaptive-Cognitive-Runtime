from __future__ import annotations

import asyncio
import importlib.util
import io
import json
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from acr_runtime.failure import FailureCreate
from acr_runtime.mcp_stdio import (
    MCP_PROTOCOL_REVISION,
    TOOL_CATALOG,
    McpStdioServer,
)
from acr_runtime.memory import (
    MemoryCreate,
    MemoryStatus,
    MemoryType,
    Sensitivity,
)
from acr_runtime.permissions import CapabilityGrantRequest
from acr_runtime.provider_tools import (
    AcrProviderTools,
    ProviderAccessContext,
    ProviderCallError,
)
from acr_runtime.service import AdaptiveRuntime


class McpProviderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.database = Path(self.temporary.name) / "acr.db"
        self.runtime = AdaptiveRuntime(self.database)
        self.access = ProviderAccessContext("agent", "mcp-test-agent")
        self.provider = AcrProviderTools(self.runtime, self.access)

    def tearDown(self) -> None:
        self.runtime.close()
        self.temporary.cleanup()

    def grant(self, capability: str, resource_scope: str) -> None:
        self.provider.permissions.grant(
            CapabilityGrantRequest(
                subject_type="agent",
                subject_id="mcp-test-agent",
                capability=capability,
                resource_scope=resource_scope,
                expires_at=(
                    datetime.now(timezone.utc) + timedelta(hours=1)
                ).isoformat(),
                delegable=False,
                grantor_type="trusted_workflow",
                grantor_id="mcp-test-operator",
                reason="Prompt 56 MCP conformance test",
                evidence=("test:mcp-provider",),
            )
        )

    def test_catalog_is_exact_stable_and_annotations_are_truthful(self) -> None:
        names = [item["name"] for item in TOOL_CATALOG]
        self.assertEqual(
            names,
            [
                "execute_skill",
                "failure_lookup",
                "find_skill",
                "retrieve_context",
                "search_memory",
                "task_history",
            ],
        )
        self.assertEqual(len(names), len(set(names)))
        for item in TOOL_CATALOG:
            schema = item["inputSchema"]
            self.assertEqual(schema["type"], "object")
            self.assertFalse(schema["additionalProperties"])
            self.assertIn("outputSchema", item)
            self.assertFalse(item["outputSchema"]["additionalProperties"])
        retrieve = next(
            item for item in TOOL_CATALOG if item["name"] == "retrieve_context"
        )
        self.assertFalse(retrieve["annotations"]["readOnlyHint"])
        execute = next(
            item for item in TOOL_CATALOG if item["name"] == "execute_skill"
        )
        self.assertTrue(execute["annotations"]["destructiveHint"])

    def test_exact_grant_and_ancestor_public_internal_projection(self) -> None:
        self.runtime.db.memories.create(
            MemoryCreate(
                type=MemoryType.SEMANTIC,
                content="The service uses SQLite FTS5 locally.",
                scope="project-a",
                status=MemoryStatus.CONFIRMED,
                sensitivity=Sensitivity.INTERNAL,
            )
        )
        self.runtime.db.memories.create(
            MemoryCreate(
                type=MemoryType.SEMANTIC,
                content="Global SQLite guidance is visible through scope ancestry.",
                scope="global",
                status=MemoryStatus.CONFIRMED,
                sensitivity=Sensitivity.INTERNAL,
            )
        )
        self.runtime.db.memories.create(
            MemoryCreate(
                type=MemoryType.SEMANTIC,
                content="Private deployment topology is restricted.",
                scope="project-a",
                status=MemoryStatus.CONFIRMED,
                sensitivity=Sensitivity.CONFIDENTIAL,
            )
        )
        request = {
            "query": "service SQLite FTS5 deployment",
            "scope": "project-a",
            "token_budget": 500,
            "limit": 10,
        }
        with self.assertRaisesRegex(ProviderCallError, "exact active grant"):
            self.provider.call("search_memory", request)
        self.grant("memory.read", "memory:project-a")
        result = self.provider.call("search_memory", request)
        self.assertEqual(len(result["memories"]), 2)
        for memory in result["memories"]:
            self.assertIn("<untrusted_data", memory["content"])
            self.assertEqual(memory["authority"], "none")
        self.assertNotIn("restricted", json.dumps(result))

    def test_execute_is_truthfully_unavailable_and_runs_nothing(self) -> None:
        with self.assertRaises(ProviderCallError) as raised:
            self.provider.call(
                "execute_skill",
                {"reference": "anything@1.0.0", "inputs": {}},
            )
        self.assertEqual(raised.exception.code, "skill_execution_unavailable")

    def test_history_and_failure_outputs_are_content_minimized(self) -> None:
        self.grant("database.write", "context:project-a")
        self.grant("memory.read", "memory:project-a")
        self.provider.call(
            "retrieve_context",
            {
                "task": "A private objective that must not appear in history",
                "scope": "project-a",
                "token_budget": 128,
            },
        )
        self.grant("database.read", "tasks:project-a")
        history = self.provider.call(
            "task_history", {"scope": "project-a", "limit": 10}
        )
        self.assertEqual(len(history["tasks"]), 1)
        self.assertNotIn("objective", history["tasks"][0])

        self.runtime.record_failure(
            FailureCreate(
                task_class="database",
                strategy_attempted="rebuild",
                symptoms=("locked",),
                failed_action="rebuild index",
                error_type="OperationalError",
                error_message="host path C:/private/database.db",
                avoidance_rule="stop writers first",
                environment_json='{"hostname":"private-host"}',
                evidence=("private-run-reference",),
                scope="project-a",
            )
        )
        failures = self.provider.call(
            "failure_lookup",
            {
                "task": "database locked",
                "task_class": "database",
                "scope": "project-a",
                "limit": 5,
            },
        )
        encoded = json.dumps(failures)
        self.assertIn("stop writers first", encoded)
        self.assertNotIn("private-host", encoded)
        self.assertNotIn("C:/private", encoded)
        self.assertNotIn("private-run-reference", encoded)

    def test_stdio_lifecycle_structured_error_and_recovery(self) -> None:
        server = McpStdioServer(self.provider)
        before = server.handle(
            {"jsonrpc": "2.0", "id": 0, "method": "tools/list", "params": {}}
        )
        self.assertEqual(before["error"]["code"], -32002)
        initialized = server.handle(
            {
                "jsonrpc": "2.0",
                "id": "init",
                "method": "initialize",
                "params": {
                    "protocolVersion": MCP_PROTOCOL_REVISION,
                    "capabilities": {},
                    "clientInfo": {"name": "test", "version": "1"},
                },
            }
        )
        self.assertEqual(
            initialized["result"]["protocolVersion"], MCP_PROTOCOL_REVISION
        )
        self.assertIsNone(
            server.handle(
                {
                    "jsonrpc": "2.0",
                    "method": "notifications/initialized",
                    "params": {},
                }
            )
        )
        unavailable = server.handle(
            {
                "jsonrpc": "2.0",
                "id": 0,
                "method": "tools/call",
                "params": {
                    "name": "execute_skill",
                    "arguments": {"reference": "x@1", "inputs": {}},
                },
            }
        )
        self.assertTrue(unavailable["result"]["isError"])
        self.assertEqual(
            unavailable["result"]["structuredContent"]["error"]["code"],
            "skill_execution_unavailable",
        )
        listed = server.handle(
            {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
        )
        self.assertEqual(len(listed["result"]["tools"]), 6)

    def test_stdio_process_stdout_contains_only_json_rpc(self) -> None:
        messages = [
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": MCP_PROTOCOL_REVISION,
                    "capabilities": {},
                    "clientInfo": {"name": "test", "version": "1"},
                },
            },
            {
                "jsonrpc": "2.0",
                "method": "notifications/initialized",
                "params": {},
            },
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        ]
        payload = "".join(
            json.dumps(item, separators=(",", ":")) + "\n" for item in messages
        )
        process = subprocess.run(
            [
                sys.executable,
                "-m",
                "acr_runtime.cli",
                "--db",
                str(Path(self.temporary.name) / "stdio.db"),
                "mcp",
                "serve",
                "--subject-type",
                "agent",
                "--subject-id",
                "mcp-process-agent",
            ],
            input=payload.encode(),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=Path(__file__).parents[1],
            timeout=20,
            check=False,
        )
        self.assertEqual(process.returncode, 0, process.stderr.decode())
        lines = process.stdout.decode("utf-8").splitlines()
        self.assertEqual(len(lines), 2)
        responses = [json.loads(line) for line in lines]
        self.assertEqual(responses[0]["id"], 1)
        self.assertEqual(responses[1]["id"], 2)
        self.assertEqual(process.stderr, b"")

    @unittest.skipUnless(
        importlib.util.find_spec("mcp"),
        "official MCP SDK is an optional compatibility check",
    )
    def test_official_sdk_initializes_lists_and_calls(self) -> None:
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        async def scenario() -> tuple[str, list[str], bool]:
            server = StdioServerParameters(
                command=sys.executable,
                args=[
                    "-m",
                    "acr_runtime.cli",
                    "--db",
                    str(Path(self.temporary.name) / "sdk.db"),
                    "mcp",
                    "serve",
                    "--subject-type",
                    "agent",
                    "--subject-id",
                    "mcp-sdk-agent",
                ],
                cwd=Path(__file__).parents[1],
            )
            async with stdio_client(server) as (reader, writer):
                async with ClientSession(reader, writer) as session:
                    initialized = await session.initialize()
                    tools = await session.list_tools()
                    result = await session.call_tool(
                        "execute_skill",
                        {"reference": "x@1", "inputs": {}},
                    )
                    return (
                        initialized.protocolVersion,
                        [tool.name for tool in tools.tools],
                        bool(result.isError),
                    )

        revision, names, is_error = asyncio.run(scenario())
        self.assertEqual(revision, MCP_PROTOCOL_REVISION)
        self.assertEqual(names, [item["name"] for item in TOOL_CATALOG])
        self.assertTrue(is_error)

    def test_run_loop_rejects_invalid_json_and_continues(self) -> None:
        server = McpStdioServer(self.provider)
        source = io.BytesIO(
            b"not-json\n"
            + json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 7,
                    "method": "initialize",
                    "params": {"protocolVersion": MCP_PROTOCOL_REVISION},
                }
            ).encode()
            + b"\n"
        )
        sink = io.BytesIO()
        self.assertEqual(server.run(source, sink), 0)
        output = [json.loads(line) for line in sink.getvalue().splitlines()]
        self.assertEqual(output[0]["error"]["code"], -32700)
        self.assertEqual(output[1]["id"], 7)


if __name__ == "__main__":
    unittest.main()
