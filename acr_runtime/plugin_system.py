from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from dataclasses import dataclass
from typing import Any

from .capability_vocab import CAPABILITIES
from .memory import utc_now
from .skill_format import IDENTIFIER, SEMVER
from .tool_registry import TOOL_ID, ToolRegistry
from .tool_router import ToolRouteRequest, ToolRouter

PLUGIN_FIELDS = frozenset(
    {
        "name",
        "version",
        "capabilities",
        "permissions",
        "entrypoints",
        "dependencies",
    }
)
MAX_ITEMS = 64


class PluginManifestError(ValueError):
    pass


def _strings(value: object, field: str) -> tuple[str, ...]:
    if (
        not isinstance(value, list)
        or len(value) > MAX_ITEMS
        or any(not isinstance(item, str) for item in value)
    ):
        raise PluginManifestError(
            f"{field} must be a list of at most {MAX_ITEMS} strings"
        )
    normalized = tuple(item.strip() for item in value)
    if any(
        not item or len(item) > 256 or item != source
        for item, source in zip(normalized, value, strict=True)
    ):
        raise PluginManifestError(f"{field} contains invalid text")
    if len(set(normalized)) != len(normalized):
        raise PluginManifestError(f"{field} contains duplicates")
    return normalized


@dataclass(frozen=True)
class PluginManifest:
    name: str
    version: str
    capabilities: tuple[str, ...]
    permissions: tuple[str, ...]
    entrypoints: dict[str, str]
    dependencies: tuple[str, ...]

    def __post_init__(self) -> None:
        for field, values in (
            ("capabilities", self.capabilities),
            ("permissions", self.permissions),
            ("dependencies", self.dependencies),
        ):
            if (
                not isinstance(values, tuple)
                or len(values) > MAX_ITEMS
                or any(
                    not isinstance(item, str)
                    or not item
                    or item != item.strip()
                    or len(item) > 256
                    for item in values
                )
                or len(set(values)) != len(values)
            ):
                raise PluginManifestError(
                    f"{field} must be a bounded unique string tuple"
                )
        if (
            not isinstance(self.entrypoints, dict)
            or len(self.entrypoints) > MAX_ITEMS
            or any(
                not isinstance(key, str) or not isinstance(value, str)
                for key, value in self.entrypoints.items()
            )
        ):
            raise PluginManifestError(
                "entrypoints must be a bounded string mapping"
            )
        if not IDENTIFIER.fullmatch(self.name):
            raise PluginManifestError(
                "Plugin name must be a stable lowercase identifier"
            )
        if not SEMVER.fullmatch(self.version):
            raise PluginManifestError(
                "Plugin version must follow Semantic Versioning"
            )
        if not self.capabilities:
            raise PluginManifestError(
                "Plugin capabilities cannot be empty"
            )
        if set(self.entrypoints) != set(self.capabilities):
            raise PluginManifestError(
                "Entrypoints must map every declared capability exactly once"
            )
        prefix = f"{self.name}."
        if any(
            not TOOL_ID.fullmatch(capability)
            or not capability.startswith(prefix)
            for capability in self.capabilities
        ):
            raise PluginManifestError(
                "Plugin capabilities must be namespaced by plugin name"
            )
        if any(
            not isinstance(target, str)
            or target != target.strip()
            or not TOOL_ID.fullmatch(target)
            for target in self.entrypoints.values()
        ):
            raise PluginManifestError(
                "Plugin entrypoints must name registered tool definitions"
            )
        unknown = set(self.permissions) - CAPABILITIES
        if unknown:
            raise PluginManifestError(
                "Plugin permissions must use the closed capability vocabulary: "
                + ", ".join(sorted(unknown))
            )
        for dependency in self.dependencies:
            if (
                "@" not in dependency
                or not IDENTIFIER.fullmatch(dependency.rsplit("@", 1)[0])
                or not SEMVER.fullmatch(dependency.rsplit("@", 1)[1])
            ):
                raise PluginManifestError(
                    "Dependencies must use exact plugin@semantic-version references"
                )
            if dependency.rsplit("@", 1)[0] == self.name:
                raise PluginManifestError(
                    "A plugin cannot depend on another version of itself"
                )

    @property
    def reference(self) -> str:
        return f"{self.name}@{self.version}"

    @property
    def manifest_hash(self) -> str:
        return hashlib.sha256(
            json.dumps(
                self.as_dict(), sort_keys=True, separators=(",", ":")
            ).encode("utf-8")
        ).hexdigest()

    def as_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "version": self.version,
            "capabilities": list(self.capabilities),
            "permissions": list(self.permissions),
            "entrypoints": dict(self.entrypoints),
            "dependencies": list(self.dependencies),
        }

    @classmethod
    def from_dict(cls, payload: object) -> "PluginManifest":
        if not isinstance(payload, dict) or set(payload) != PLUGIN_FIELDS:
            raise PluginManifestError(
                f"Plugin manifest must contain {sorted(PLUGIN_FIELDS)} only"
            )
        if not isinstance(payload["name"], str) or not isinstance(
            payload["version"], str
        ):
            raise PluginManifestError("Plugin name and version must be strings")
        entrypoints = payload["entrypoints"]
        if (
            not isinstance(entrypoints, dict)
            or len(entrypoints) > MAX_ITEMS
            or any(
                not isinstance(key, str) or not isinstance(value, str)
                for key, value in entrypoints.items()
            )
        ):
            raise PluginManifestError(
                f"entrypoints must map at most {MAX_ITEMS} strings to strings"
            )
        return cls(
            name=payload["name"],
            version=payload["version"],
            capabilities=_strings(payload["capabilities"], "capabilities"),
            permissions=_strings(payload["permissions"], "permissions"),
            entrypoints=dict(entrypoints),
            dependencies=_strings(payload["dependencies"], "dependencies"),
        )


class PluginRegistry:
    """Declarative plugin registry with no dynamic code-loading surface."""

    def __init__(
        self,
        connection: sqlite3.Connection,
        tools: ToolRegistry,
        router: ToolRouter,
    ) -> None:
        self.connection = connection
        self.tools = tools
        self.router = router

    def validate(self, manifest: PluginManifest) -> dict[str, object]:
        reasons: list[str] = []
        dependency_snapshot: list[dict[str, object]] = []
        for dependency in manifest.dependencies:
            name, version = dependency.rsplit("@", 1)
            row = self.connection.execute(
                """
                SELECT manifest_hash
                FROM plugin_manifests
                WHERE name=? AND version=?
                """,
                (name, version),
            ).fetchone()
            dependency_snapshot.append(
                {
                    "reference": dependency,
                    "compatible": row is not None,
                    "manifest_hash": None if row is None else row["manifest_hash"],
                }
            )
            if row is None:
                reasons.append(f"missing_dependency:{dependency}")

        tool_snapshot: list[dict[str, object]] = []
        tool_permissions: set[str] = set()
        for capability in manifest.capabilities:
            target = manifest.entrypoints[capability]
            try:
                tool = self.tools.get(target)
            except LookupError:
                reasons.append(f"unknown_tool:{target}")
                tool_snapshot.append(
                    {
                        "capability": capability,
                        "tool": target,
                        "compatible": False,
                        "definition_hash": None,
                    }
                )
                continue
            tool_permissions.update(str(item) for item in tool["permissions"])
            tool_snapshot.append(
                {
                    "capability": capability,
                    "tool": target,
                    "compatible": True,
                    "definition_hash": tool["definition_hash"],
                }
            )
        declared = set(manifest.permissions)
        missing = sorted(tool_permissions - declared)
        unused = sorted(declared - tool_permissions)
        if missing:
            reasons.append("missing_permissions:" + ",".join(missing))
        if unused:
            reasons.append("unused_permissions:" + ",".join(unused))
        return {
            "plugin": manifest.reference,
            "manifest_hash": manifest.manifest_hash,
            "compatible": not reasons,
            "reasons": reasons,
            "dependencies": dependency_snapshot,
            "entrypoints": tool_snapshot,
            "execution_model": "governed_registered_tools_only",
        }

    def register(self, manifest: PluginManifest) -> dict[str, object]:
        existing = self.connection.execute(
            """
            SELECT manifest_hash
            FROM plugin_manifests
            WHERE name=? AND version=?
            """,
            (manifest.name, manifest.version),
        ).fetchone()
        if existing is not None:
            if existing["manifest_hash"] != manifest.manifest_hash:
                raise ValueError(
                    "Plugin manifests are immutable; publish a new version"
                )
            return self.get(manifest.name, manifest.version)

        validation = self.validate(manifest)
        validation_id = str(uuid.uuid4())
        now = utc_now()
        try:
            self.connection.execute(
                """
                INSERT INTO plugin_validation_runs (
                    id, name, version, manifest_hash, compatible,
                    reasons_json, dependency_snapshot_json,
                    entrypoint_snapshot_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    validation_id,
                    manifest.name,
                    manifest.version,
                    manifest.manifest_hash,
                    bool(validation["compatible"]),
                    json.dumps(validation["reasons"]),
                    json.dumps(validation["dependencies"]),
                    json.dumps(validation["entrypoints"]),
                    now,
                ),
            )
            if validation["compatible"]:
                self.connection.execute(
                    """
                    INSERT INTO plugin_manifests (
                        name, version, capabilities_json, permissions_json,
                        entrypoints_json, dependencies_json, manifest_hash,
                        validation_id, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        manifest.name,
                        manifest.version,
                        json.dumps(manifest.capabilities),
                        json.dumps(manifest.permissions),
                        json.dumps(manifest.entrypoints),
                        json.dumps(manifest.dependencies),
                        manifest.manifest_hash,
                        validation_id,
                        now,
                    ),
                )
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise
        if not validation["compatible"]:
            return {
                "registered": False,
                "status": "incompatible",
                "validation_id": validation_id,
                **validation,
            }
        return self.get(manifest.name, manifest.version)

    def get(self, name: str, version: str) -> dict[str, object]:
        row = self.connection.execute(
            """
            SELECT *
            FROM plugin_manifests
            WHERE name=? AND version=?
            """,
            (name, version),
        ).fetchone()
        if row is None:
            raise LookupError(f"Unknown compatible plugin: {name}@{version}")
        result = dict(row)
        for source, target in (
            ("capabilities_json", "capabilities"),
            ("permissions_json", "permissions"),
            ("entrypoints_json", "entrypoints"),
            ("dependencies_json", "dependencies"),
        ):
            result[target] = json.loads(result.pop(source))
        result["reference"] = f"{name}@{version}"
        result["registered"] = True
        result["status"] = "compatible"
        return result

    def list(self) -> list[dict[str, object]]:
        references = self.connection.execute(
            """
            SELECT name, version
            FROM plugin_manifests
            ORDER BY name, version
            """
        ).fetchall()
        return [self.get(row["name"], row["version"]) for row in references]

    def route(
        self,
        name: str,
        version: str,
        capability: str,
        request: ToolRouteRequest,
    ) -> dict[str, object]:
        plugin = self.get(name, version)
        entrypoints = plugin["entrypoints"]
        if capability not in entrypoints:
            raise LookupError(
                f"Plugin {name}@{version} has no capability {capability}"
            )
        tool_name = str(entrypoints[capability])
        route = self.router.route(
            request,
            allowed_tools=frozenset({tool_name}),
            allowed_tools_source="plugin",
        )
        allowed = tool_name in route["selected_tools"]
        route_id = str(uuid.uuid4())
        self.connection.execute(
            """
            INSERT INTO plugin_routes (
                id, plugin_name, plugin_version, capability, tool_name,
                tool_route_id, allowed, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                route_id,
                name,
                version,
                capability,
                tool_name,
                route["id"],
                allowed,
                utc_now(),
            ),
        )
        self.connection.commit()
        return {
            "id": route_id,
            "plugin": plugin["reference"],
            "capability": capability,
            "tool_name": tool_name,
            "allowed": allowed,
            "execution_performed": False,
            "tool_route": route,
        }

    def validation(self, validation_id: str) -> dict[str, Any]:
        row = self.connection.execute(
            "SELECT * FROM plugin_validation_runs WHERE id=?",
            (validation_id,),
        ).fetchone()
        if row is None:
            raise LookupError(f"Unknown plugin validation: {validation_id}")
        result = dict(row)
        result["compatible"] = bool(result["compatible"])
        for source, target in (
            ("reasons_json", "reasons"),
            ("dependency_snapshot_json", "dependencies"),
            ("entrypoint_snapshot_json", "entrypoints"),
        ):
            result[target] = json.loads(result.pop(source))
        return result
