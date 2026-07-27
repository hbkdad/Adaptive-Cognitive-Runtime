from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from typing import BinaryIO

from .provider_tools import AcrProviderTools, ProviderCallError

MCP_PROTOCOL_REVISION = "2025-11-25"
MAX_MCP_LINE_BYTES = 1_000_000
SERVER_INFO = {"name": "acr-runtime", "version": "0.1.0"}


def _object_schema(
    properties: dict[str, object], required: tuple[str, ...]
) -> dict[str, object]:
    return {
        "type": "object",
        "properties": properties,
        "required": list(required),
        "additionalProperties": False,
    }


def _tool_catalog() -> tuple[dict[str, object], ...]:
    text = {"type": "string", "minLength": 1, "maxLength": 16_000}
    scope = {"type": "string", "minLength": 1, "maxLength": 256}
    limit = {"type": "integer", "minimum": 1, "maximum": 100}
    budget = {"type": "integer", "minimum": 64, "maximum": 20_000}
    generic_items = {"type": "array", "items": {"type": "object"}}
    outputs = {
        "execute_skill": _object_schema({}, ()),
        "failure_lookup": _object_schema({"matches": generic_items}, ("matches",)),
        "find_skill": _object_schema(
            {
                "semantic_available": {"type": "boolean"},
                "results": generic_items,
            },
            ("semantic_available", "results"),
        ),
        "retrieve_context": _object_schema(
            {
                "task_id": {"type": "string"},
                "scope": {"type": "string"},
                "content": {"type": "string"},
                "selected_tokens": {"type": "integer"},
                "token_budget": {"type": "integer"},
                "pipeline": {"type": "array", "items": {"type": "string"}},
                "rejected_count": {"type": "integer"},
            },
            (
                "task_id",
                "scope",
                "content",
                "selected_tokens",
                "token_budget",
                "pipeline",
                "rejected_count",
            ),
        ),
        "search_memory": _object_schema(
            {
                "candidate_count": {"type": "integer"},
                "selected_tokens": {"type": "integer"},
                "semantic_available": {"type": "boolean"},
                "semantic_status": {"type": "string"},
                "memories": generic_items,
            },
            (
                "candidate_count",
                "selected_tokens",
                "semantic_available",
                "semantic_status",
                "memories",
            ),
        ),
        "task_history": _object_schema({"tasks": generic_items}, ("tasks",)),
    }
    read = {
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    }
    return (
        {
            "name": "execute_skill",
            "description": (
                "Report governed skill execution as unavailable. ACR never "
                "executes package scripts or substitutes skill activation."
            ),
            "inputSchema": _object_schema(
                {
                    "reference": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": 256,
                    },
                    "inputs": {"type": "object"},
                },
                ("reference", "inputs"),
            ),
            "outputSchema": outputs["execute_skill"],
            "annotations": {
                "readOnlyHint": False,
                "destructiveHint": True,
                "idempotentHint": False,
                "openWorldHint": True,
            },
        },
        {
            "name": "failure_lookup",
            "description": "Find content-minimized analogous failures in one exact scope.",
            "inputSchema": _object_schema(
                {
                    "task": text,
                    "task_class": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": 256,
                    },
                    "scope": scope,
                    "strategy": {
                        "type": ["string", "null"],
                        "maxLength": 16_000,
                    },
                    "limit": {"type": "integer", "minimum": 1, "maximum": 20},
                },
                ("task", "task_class", "scope", "limit"),
            ),
            "outputSchema": outputs["failure_lookup"],
            "annotations": read,
        },
        {
            "name": "find_skill",
            "description": "Find active declarative ACR skills without package paths.",
            "inputSchema": _object_schema(
                {"query": text, "limit": limit}, ("query", "limit")
            ),
            "outputSchema": outputs["find_skill"],
            "annotations": read,
        },
        {
            "name": "retrieve_context",
            "description": (
                "Compile bounded attributed context. This persists task and "
                "context-use audit records."
            ),
            "inputSchema": _object_schema(
                {"task": text, "scope": scope, "token_budget": budget},
                ("task", "scope", "token_budget"),
            ),
            "outputSchema": outputs["retrieve_context"],
            "annotations": {
                "readOnlyHint": False,
                "destructiveHint": False,
                "idempotentHint": False,
                "openWorldHint": False,
            },
        },
        {
            "name": "search_memory",
            "description": (
                "Search public/internal confirmed ACR memory in one exact scope."
            ),
            "inputSchema": _object_schema(
                {
                    "query": text,
                    "scope": scope,
                    "token_budget": budget,
                    "limit": limit,
                    "types": {
                        "type": "array",
                        "items": {
                            "type": "string",
                            "enum": [
                                "semantic",
                                "episodic",
                                "procedural",
                                "failure",
                                "decision",
                                "preference",
                                "environment",
                                "temporary",
                            ],
                        },
                        "maxItems": 8,
                        "uniqueItems": True,
                    },
                },
                ("query", "scope", "token_budget", "limit"),
            ),
            "outputSchema": outputs["search_memory"],
            "annotations": read,
        },
        {
            "name": "task_history",
            "description": (
                "List content-minimized task outcomes for one exact scope."
            ),
            "inputSchema": _object_schema(
                {"scope": scope, "limit": limit}, ("scope", "limit")
            ),
            "outputSchema": outputs["task_history"],
            "annotations": read,
        },
    )


TOOL_CATALOG = _tool_catalog()


@dataclass
class McpStdioServer:
    provider: AcrProviderTools
    initialized: bool = False
    ready: bool = False

    @staticmethod
    def _response(identifier: object, result: object) -> dict[str, object]:
        return {"jsonrpc": "2.0", "id": identifier, "result": result}

    @staticmethod
    def _error(
        identifier: object, code: int, message: str
    ) -> dict[str, object]:
        return {
            "jsonrpc": "2.0",
            "id": identifier,
            "error": {"code": code, "message": message},
        }

    def handle(self, message: object) -> dict[str, object] | None:
        if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
            return self._error(None, -32600, "Invalid Request")
        identifier = message.get("id")
        if "id" in message and (
            isinstance(identifier, bool)
            or not isinstance(identifier, (str, int, type(None)))
        ):
            return self._error(None, -32600, "Invalid Request")
        method = message.get("method")
        if not isinstance(method, str):
            return self._error(identifier, -32600, "Invalid Request")
        params = message.get("params", {})
        if method == "initialize":
            if self.initialized or "id" not in message or not isinstance(params, dict):
                return self._error(identifier, -32600, "Invalid Request")
            if params.get("protocolVersion") != MCP_PROTOCOL_REVISION:
                return self._error(identifier, -32602, "Unsupported protocol version")
            self.initialized = True
            return self._response(
                identifier,
                {
                    "protocolVersion": MCP_PROTOCOL_REVISION,
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": SERVER_INFO,
                },
            )
        if method == "notifications/initialized":
            if "id" in message or not self.initialized or self.ready:
                return None
            self.ready = True
            return None
        if not self.ready:
            return self._error(identifier, -32002, "Server not initialized")
        if method == "ping":
            return self._response(identifier, {})
        if method == "tools/list":
            if not isinstance(params, dict) or params:
                return self._error(identifier, -32602, "Invalid params")
            return self._response(identifier, {"tools": list(TOOL_CATALOG)})
        if method == "tools/call":
            if (
                not isinstance(params, dict)
                or set(params) != {"name", "arguments"}
                or not isinstance(params.get("name"), str)
            ):
                return self._error(identifier, -32602, "Invalid params")
            name = params["name"]
            if name not in {item["name"] for item in TOOL_CATALOG}:
                return self._error(identifier, -32602, "Unknown tool")
            try:
                result = self.provider.call(name, params["arguments"])
            except ProviderCallError as error:
                structured = {
                    "error": {
                        "code": error.code,
                        "message": str(error),
                        "retryable": False,
                    }
                }
                text = json.dumps(
                    structured, sort_keys=True, separators=(",", ":")
                )
                return self._response(
                    identifier,
                    {
                        "content": [{"type": "text", "text": text}],
                        "structuredContent": structured,
                        "isError": True,
                    },
                )
            schema = next(
                item["outputSchema"]
                for item in TOOL_CATALOG
                if item["name"] == name
            )
            if set(result) != set(schema["required"]):
                structured = {
                    "error": {
                        "code": "invalid_result",
                        "message": "provider output failed its closed schema",
                        "retryable": False,
                    }
                }
                text = json.dumps(
                    structured, sort_keys=True, separators=(",", ":")
                )
                return self._response(
                    identifier,
                    {
                        "content": [{"type": "text", "text": text}],
                        "structuredContent": structured,
                        "isError": True,
                    },
                )
            text = json.dumps(
                result,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
                allow_nan=False,
            )
            return self._response(
                identifier,
                {
                    "content": [{"type": "text", "text": text}],
                    "structuredContent": result,
                    "isError": False,
                },
            )
        if method.startswith("notifications/"):
            return None
        return self._error(identifier, -32601, "Method not found")

    def run(
        self,
        input_stream: BinaryIO | None = None,
        output_stream: BinaryIO | None = None,
    ) -> int:
        source = input_stream or sys.stdin.buffer
        sink = output_stream or sys.stdout.buffer
        while True:
            line = source.readline(MAX_MCP_LINE_BYTES + 1)
            if not line:
                return 0
            if len(line) > MAX_MCP_LINE_BYTES or not line.endswith(b"\n"):
                response = self._error(None, -32700, "Parse error")
            else:
                try:
                    message = json.loads(line.decode("utf-8"))
                    if isinstance(message, list):
                        response = self._error(None, -32600, "Batch unsupported")
                    else:
                        response = self.handle(message)
                except (UnicodeDecodeError, json.JSONDecodeError):
                    response = self._error(None, -32700, "Parse error")
            if response is not None:
                payload = (
                    json.dumps(
                        response,
                        sort_keys=True,
                        separators=(",", ":"),
                        ensure_ascii=False,
                        allow_nan=False,
                    ).encode("utf-8")
                    + b"\n"
                )
                sink.write(payload)
                sink.flush()
