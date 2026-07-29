from __future__ import annotations

import io
import json
import sqlite3
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path

from acr_runtime.db import RuntimeDB
from acr_runtime.cli import main
from acr_runtime.permissions import (
    CapabilityGrantRequest,
    PermissionController,
)
from acr_runtime.plugin_system import (
    PluginManifest,
    PluginManifestError,
    PluginRegistry,
)
from acr_runtime.tool_registry import ToolDefinition, ToolRegistry
from acr_runtime.tool_router import ToolRouteRequest, ToolRouter


def schema(field: str) -> dict[str, object]:
    return {
        "type": "object",
        "properties": {field: {"type": "string"}},
        "required": [field],
        "additionalProperties": False,
    }


class PluginSystemTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "acr.db"
        self.db = RuntimeDB(self.path)
        self.tools = ToolRegistry(self.db.connection)
        self.permissions = PermissionController(self.db.connection)
        self.router = ToolRouter(
            self.db.connection, self.tools, self.permissions
        )
        self.plugins = PluginRegistry(
            self.db.connection, self.tools, self.router
        )

    def tearDown(self) -> None:
        self.db.close()
        self.temp.cleanup()

    def add_tool(
        self,
        name: str = "web.search",
        permissions: tuple[str, ...] = ("network.read",),
    ) -> None:
        self.tools.register(
            ToolDefinition(
                name=name,
                description="web search latest current sources",
                input_schema=schema("query"),
                output_schema=schema("result"),
                permissions=permissions,
                cost=0.01,
                latency_estimate_ms=100,
                side_effect="READ_ONLY",
                network_access="network.read" in permissions,
                filesystem_access="NONE",
                credential_requirements=(),
            )
        )

    @staticmethod
    def manifest(
        *,
        name: str = "research",
        version: str = "1.0.0",
        tool: str = "web.search",
        permissions: tuple[str, ...] = ("network.read",),
        dependencies: tuple[str, ...] = (),
    ) -> PluginManifest:
        capability = f"{name}.search"
        return PluginManifest(
            name=name,
            version=version,
            capabilities=(capability,),
            permissions=permissions,
            entrypoints={capability: tool},
            dependencies=dependencies,
        )

    @staticmethod
    def request() -> ToolRouteRequest:
        return ToolRouteRequest(
            task="web search latest current sources",
            task_class="plugin-test",
            subject_type="task",
            subject_id="task-plugin",
            resource_scope="network:public-web",
            network_allowed=True,
            filesystem_access="NONE",
            available_credentials=(),
            max_tools=1,
        )

    def test_manifest_is_strict_namespaced_and_never_loads_code(self):
        payload = self.manifest().as_dict()
        payload["unexpected"] = "value"
        with self.assertRaises(PluginManifestError):
            PluginManifest.from_dict(payload)
        with self.assertRaisesRegex(PluginManifestError, "namespaced"):
            PluginManifest(
                "research",
                "1.0.0",
                ("other.search",),
                (),
                {"other.search": "os:system"},
                (),
            )
        manifest = self.manifest(tool="os:system")
        result = self.plugins.register(manifest)
        self.assertFalse(result["registered"])
        self.assertIn("unknown_tool:os:system", result["reasons"])
        self.assertEqual(result["execution_model"], "governed_registered_tools_only")
        self.assertEqual(self.plugins.list(), [])

    def test_compatibility_requires_exact_dependencies_and_permission_union(self):
        self.add_tool()
        missing = self.plugins.register(
            self.manifest(dependencies=("foundation@1.0.0",))
        )
        self.assertFalse(missing["registered"])
        self.assertIn(
            "missing_dependency:foundation@1.0.0", missing["reasons"]
        )
        foundation = self.plugins.register(
            self.manifest(name="foundation")
        )
        self.assertTrue(foundation["registered"])
        compatible = self.plugins.register(
            self.manifest(dependencies=("foundation@1.0.0",))
        )
        self.assertTrue(compatible["registered"])
        overdeclared = self.plugins.register(
            self.manifest(
                name="overdeclared",
                permissions=("network.read", "filesystem.read"),
            )
        )
        self.assertFalse(overdeclared["registered"])
        self.assertIn(
            "unused_permissions:filesystem.read", overdeclared["reasons"]
        )

    def test_declared_permissions_cannot_bypass_central_checks(self):
        self.add_tool()
        plugin = self.plugins.register(self.manifest())
        self.assertTrue(plugin["registered"])

        denied = self.plugins.route(
            "research", "1.0.0", "research.search", self.request()
        )
        self.assertFalse(denied["allowed"])
        self.assertFalse(denied["execution_performed"])
        candidate = denied["tool_route"]["candidates"][0]
        self.assertEqual(
            denied["tool_route"]["request"]["exposure_selector"],
            "plugin-entrypoint-v1.0.0",
        )
        self.assertIn("missing_permissions", candidate["rejection_reasons"])
        decision = self.db.connection.execute(
            """
            SELECT allowed, reason
            FROM capability_decisions
            WHERE id=?
            """,
            (candidate["capability_decisions"][0],),
        ).fetchone()
        self.assertEqual((decision["allowed"], decision["reason"]), (0, "default_deny"))

        self.permissions.grant(
            CapabilityGrantRequest(
                subject_type="task",
                subject_id="task-plugin",
                capability="network.read",
                resource_scope="network:public-web",
                expires_at=(
                    datetime.now(timezone.utc) + timedelta(hours=1)
                ).isoformat(),
                delegable=False,
                grantor_type="trusted_workflow",
                grantor_id="operator-test",
                reason="Plugin route test",
                evidence=("test:plugin-system",),
            )
        )
        allowed = self.plugins.route(
            "research", "1.0.0", "research.search", self.request()
        )
        self.assertTrue(allowed["allowed"])
        self.assertEqual(allowed["tool_route"]["selected_tools"], ["web.search"])

    def test_manifests_are_idempotent_immutable_and_audited(self):
        self.add_tool()
        manifest = self.manifest()
        first = self.plugins.register(manifest)
        second = self.plugins.register(manifest)
        self.assertEqual(first["manifest_hash"], second["manifest_hash"])
        count = self.db.connection.execute(
            "SELECT COUNT(*) FROM plugin_manifests"
        ).fetchone()[0]
        self.assertEqual(count, 1)
        changed = PluginManifest(
            name="research",
            version="1.0.0",
            capabilities=("research.lookup",),
            permissions=("network.read",),
            entrypoints={"research.lookup": "web.search"},
            dependencies=(),
        )
        with self.assertRaisesRegex(ValueError, "new version"):
            self.plugins.register(changed)
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.connection.execute(
                """
                UPDATE plugin_manifests
                SET permissions_json='[]'
                WHERE name='research' AND version='1.0.0'
                """
            )
        self.db.connection.rollback()

    def test_cli_register_list_and_inspect(self):
        self.add_tool()
        manifest_path = Path(self.temp.name) / "plugin.json"
        manifest_path.write_text(
            json.dumps(self.manifest().as_dict()), encoding="utf-8"
        )
        output = io.StringIO()
        with redirect_stdout(output):
            code = main(
                [
                    "--db",
                    str(self.path),
                    "plugins",
                    "register",
                    str(manifest_path),
                ]
            )
        self.assertEqual(code, 0)
        self.assertTrue(json.loads(output.getvalue())["registered"])

        output = io.StringIO()
        with redirect_stdout(output):
            code = main(
                [
                    "--db",
                    str(self.path),
                    "plugins",
                    "inspect",
                    "research",
                    "1.0.0",
                ]
            )
        self.assertEqual(code, 0)
        self.assertEqual(
            json.loads(output.getvalue())["reference"], "research@1.0.0"
        )
