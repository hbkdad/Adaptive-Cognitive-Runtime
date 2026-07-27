from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from dataclasses import dataclass
from typing import Literal

from .capability_vocab import CAPABILITIES
from .memory import utc_now

SideEffect = Literal["READ_ONLY", "REVERSIBLE_WRITE", "DESTRUCTIVE"]
TOOL_ID = re.compile(r"^[a-z0-9][a-z0-9._:/-]{1,127}$")
ACCESS = frozenset({"NONE", "READ", "WRITE"})


def _strings(value: object, field: str) -> tuple[str, ...]:
    if not isinstance(value, list) or len(value) > 64:
        raise ValueError(f"{field} must be a list of at most 64 items")
    result = tuple(str(item).strip() for item in value)
    if any(not item or len(item) > 256 for item in result):
        raise ValueError(f"{field} contains invalid text")
    if len(set(result)) != len(result):
        raise ValueError(f"{field} contains duplicates")
    return result


def _strict_schema(value: object, field: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ValueError(f"{field} must be a JSON Schema object")
    if value.get("type") != "object":
        raise ValueError(f"{field} root type must be object")
    properties = value.get("properties")
    required = value.get("required")
    if not isinstance(properties, dict) or not isinstance(required, list):
        raise ValueError(f"{field} requires properties and required")
    if set(required) != set(properties):
        raise ValueError(f"{field} must require every declared property")
    if value.get("additionalProperties") is not False:
        raise ValueError(f"{field} must set additionalProperties to false")
    json.dumps(value, sort_keys=True)
    return value


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    description: str
    input_schema: dict[str, object]
    output_schema: dict[str, object]
    permissions: tuple[str, ...]
    cost: float
    latency_estimate_ms: int
    side_effect: SideEffect
    network_access: bool
    filesystem_access: str
    credential_requirements: tuple[str, ...]

    def __post_init__(self) -> None:
        if not TOOL_ID.fullmatch(self.name):
            raise ValueError("Tool name is invalid")
        if not self.description.strip() or len(self.description) > 2_000:
            raise ValueError("Tool description must be bounded non-empty text")
        _strict_schema(self.input_schema, "input_schema")
        _strict_schema(self.output_schema, "output_schema")
        for field, values in (
            ("permissions", self.permissions),
            ("credential_requirements", self.credential_requirements),
        ):
            if (
                not isinstance(values, tuple)
                or len(values) > 64
                or any(
                    not isinstance(item, str)
                    or not item.strip()
                    or item != item.strip()
                    or len(item) > 256
                    for item in values
                )
                or len(set(values)) != len(values)
            ):
                raise ValueError(f"{field} must be a bounded unique string tuple")
        if self.cost < 0 or self.latency_estimate_ms < 0:
            raise ValueError("Tool cost and latency cannot be negative")
        if self.side_effect not in (
            "READ_ONLY", "REVERSIBLE_WRITE", "DESTRUCTIVE"
        ):
            raise ValueError("Invalid tool side_effect")
        if type(self.network_access) is not bool:
            raise ValueError("network_access must be a boolean")
        if self.filesystem_access not in ACCESS:
            raise ValueError("filesystem_access must be NONE, READ, or WRITE")
        unknown_permissions = set(self.permissions) - CAPABILITIES
        if unknown_permissions:
            raise ValueError(
                "Tool permissions must use the closed capability vocabulary: "
                + ", ".join(sorted(unknown_permissions))
            )
        if self.network_access and not (
            {"network.read", "network.write"} & set(self.permissions)
        ):
            raise ValueError("Network tools must declare a network capability")
        if self.filesystem_access == "READ" and not (
            {"filesystem.read", "filesystem.write"} & set(self.permissions)
        ):
            raise ValueError("Filesystem READ tools must declare a filesystem capability")
        if (
            self.filesystem_access == "WRITE"
            and "filesystem.write" not in self.permissions
        ):
            raise ValueError("Filesystem WRITE tools require filesystem.write")
        if (
            self.credential_requirements
            and "credential.use" not in self.permissions
        ):
            raise ValueError("Credential-bearing tools require credential.use")
        if self.side_effect == "READ_ONLY" and self.filesystem_access == "WRITE":
            raise ValueError("READ_ONLY tools cannot request filesystem WRITE")

    @property
    def definition_hash(self) -> str:
        return hashlib.sha256(
            json.dumps(self.as_dict(), sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()

    @classmethod
    def from_dict(cls, payload: object) -> "ToolDefinition":
        if not isinstance(payload, dict):
            raise ValueError("Tool definition must be an object")
        fields = {
            "name", "description", "input_schema", "output_schema",
            "permissions", "cost", "latency_estimate_ms", "side_effect",
            "network_access", "filesystem_access", "credential_requirements",
        }
        if set(payload) != fields:
            raise ValueError(f"Tool definition must contain {sorted(fields)} only")
        if not isinstance(payload["network_access"], bool):
            raise ValueError("network_access must be a boolean")
        return cls(
            name=str(payload["name"]), description=str(payload["description"]),
            input_schema=_strict_schema(payload["input_schema"], "input_schema"),
            output_schema=_strict_schema(payload["output_schema"], "output_schema"),
            permissions=_strings(payload["permissions"], "permissions"),
            cost=float(payload["cost"]),
            latency_estimate_ms=int(payload["latency_estimate_ms"]),
            side_effect=str(payload["side_effect"]),
            network_access=payload["network_access"],
            filesystem_access=str(payload["filesystem_access"]),
            credential_requirements=_strings(
                payload["credential_requirements"], "credential_requirements"
            ),
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "name": self.name, "description": self.description,
            "input_schema": self.input_schema, "output_schema": self.output_schema,
            "permissions": list(self.permissions), "cost": self.cost,
            "latency_estimate_ms": self.latency_estimate_ms,
            "side_effect": self.side_effect,
            "network_access": self.network_access,
            "filesystem_access": self.filesystem_access,
            "credential_requirements": list(self.credential_requirements),
        }


@dataclass(frozen=True)
class ToolAccessRequest:
    tool_name: str
    granted_permissions: tuple[str, ...]
    network_allowed: bool
    filesystem_access: str
    available_credentials: tuple[str, ...]
    approval_reference: str | None = None

    @classmethod
    def from_dict(cls, payload: object) -> "ToolAccessRequest":
        if not isinstance(payload, dict):
            raise ValueError("Tool access request must be an object")
        required = {
            "tool_name", "granted_permissions", "network_allowed",
            "filesystem_access", "available_credentials",
        }
        if not required <= set(payload) or set(payload) - required - {
            "approval_reference"
        }:
            raise ValueError(f"Tool access request requires {sorted(required)}")
        if not isinstance(payload["network_allowed"], bool):
            raise ValueError("network_allowed must be a boolean")
        return cls(
            tool_name=str(payload["tool_name"]),
            granted_permissions=_strings(
                payload["granted_permissions"], "granted_permissions"
            ),
            network_allowed=payload["network_allowed"],
            filesystem_access=str(payload["filesystem_access"]),
            available_credentials=_strings(
                payload["available_credentials"], "available_credentials"
            ),
            approval_reference=(
                None if payload.get("approval_reference") is None
                else str(payload["approval_reference"]).strip()
            ),
        )


class ToolRegistry:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def register(self, definition: ToolDefinition) -> dict[str, object]:
        existing = self.connection.execute(
            "SELECT definition_hash FROM tool_definitions WHERE name=?",
            (definition.name,),
        ).fetchone()
        if existing:
            if existing["definition_hash"] != definition.definition_hash:
                raise ValueError("Tool definitions are immutable; use a new name")
            return self.get(definition.name)
        self.connection.execute(
            """
            INSERT INTO tool_definitions (
                name, description, input_schema_json, output_schema_json,
                permissions_json, cost, latency_estimate_ms, side_effect,
                network_access, filesystem_access,
                credential_requirements_json, definition_hash, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                definition.name, definition.description,
                json.dumps(definition.input_schema),
                json.dumps(definition.output_schema),
                json.dumps(definition.permissions), definition.cost,
                definition.latency_estimate_ms, definition.side_effect,
                definition.network_access, definition.filesystem_access,
                json.dumps(definition.credential_requirements),
                definition.definition_hash, utc_now(),
            ),
        )
        self.connection.commit()
        return self.get(definition.name)

    def get(self, name: str) -> dict[str, object]:
        row = self.connection.execute(
            "SELECT * FROM tool_definitions WHERE name=?", (name,)
        ).fetchone()
        if row is None:
            raise LookupError(f"Unknown tool: {name}")
        result = dict(row)
        for source, target in (
            ("input_schema_json", "input_schema"),
            ("output_schema_json", "output_schema"),
            ("permissions_json", "permissions"),
            ("credential_requirements_json", "credential_requirements"),
        ):
            result[target] = json.loads(result.pop(source))
        result["network_access"] = bool(result["network_access"])
        result["requires_approval"] = result["side_effect"] == "DESTRUCTIVE"
        return result

    def list(self) -> list[dict[str, object]]:
        names = [
            row["name"] for row in self.connection.execute(
                "SELECT name FROM tool_definitions ORDER BY name"
            )
        ]
        return [self.get(name) for name in names]

    def authorize(self, request: ToolAccessRequest) -> dict[str, object]:
        tool = self.get(request.tool_name)
        reasons: list[str] = []
        if not set(tool["permissions"]) <= set(request.granted_permissions):
            reasons.append("missing_permissions")
        if tool["network_access"] and not request.network_allowed:
            reasons.append("network_not_allowed")
        levels = {"NONE": 0, "READ": 1, "WRITE": 2}
        if request.filesystem_access not in levels:
            reasons.append("invalid_filesystem_grant")
        elif levels[request.filesystem_access] < levels[tool["filesystem_access"]]:
            reasons.append("insufficient_filesystem_access")
        if not set(tool["credential_requirements"]) <= set(
            request.available_credentials
        ):
            reasons.append("missing_credentials")
        if tool["side_effect"] == "DESTRUCTIVE" and not request.approval_reference:
            reasons.append("destructive_action_requires_approval")
        return {
            "tool": tool, "allowed": not reasons,
            "rejection_reasons": reasons,
            "approval_reference_present": bool(request.approval_reference),
        }
