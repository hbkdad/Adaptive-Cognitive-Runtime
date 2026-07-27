from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Protocol

from .content_security import ContentAssessmentRequest
from .failure import FailureQuery
from .memory import MemoryType, Sensitivity
from .permissions import CapabilityCheck, PermissionController
from .retrieval import RetrievalRequest
from .secret_management import detect_secret_material
from .service import AdaptiveRuntime

MAX_PROVIDER_ARGUMENT_BYTES = 64 * 1024
MAX_PROVIDER_RESULT_BYTES = 1_000_000


class ProviderCallError(ValueError):
    """A stable, sanitized provider-domain failure."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class ProviderAccessContext:
    """Server-bound identity. Tool arguments never construct this object."""

    subject_type: str
    subject_id: str

    def __post_init__(self) -> None:
        if self.subject_type not in {"task", "agent", "skill"}:
            raise ValueError("provider subject type is invalid")
        if not self.subject_id.strip():
            raise ValueError("provider subject ID is required")


class SkillExecutionBackend(Protocol):
    """Future governed execution boundary; package scripts are never a backend."""

    def execute(
        self, reference: str, inputs: dict[str, object]
    ) -> dict[str, object]: ...


class AcrProviderTools:
    """Protocol-neutral, permissioned projections over ACR domain services."""

    def __init__(
        self,
        runtime: AdaptiveRuntime,
        access: ProviderAccessContext,
        *,
        skill_executor: SkillExecutionBackend | None = None,
    ) -> None:
        self.runtime = runtime
        self.access = access
        self.permissions = PermissionController(
            runtime.db.connection, runtime.content_security
        )
        self.skill_executor = skill_executor

    def _require(self, capability: str, resource_scope: str) -> None:
        decision = self.permissions.check(
            CapabilityCheck(
                self.access.subject_type,
                self.access.subject_id,
                capability,
                resource_scope,
            )
        )
        if not decision["allowed"]:
            raise ProviderCallError(
                "permission_denied",
                "the server-bound MCP identity lacks an exact active grant",
            )

    @staticmethod
    def _arguments(
        value: object,
        *,
        required: frozenset[str],
        optional: frozenset[str] = frozenset(),
    ) -> dict[str, object]:
        if not isinstance(value, dict):
            raise ProviderCallError("invalid_arguments", "arguments must be an object")
        keys = set(value)
        if not required <= keys or keys - required - optional:
            raise ProviderCallError(
                "invalid_arguments", "arguments do not match the closed schema"
            )
        try:
            encoded = json.dumps(value, ensure_ascii=False, allow_nan=False)
            encoded_bytes = encoded.encode("utf-8")
        except (TypeError, ValueError, UnicodeEncodeError):
            raise ProviderCallError(
                "invalid_arguments", "arguments must be finite JSON values"
            ) from None
        if len(encoded_bytes) > MAX_PROVIDER_ARGUMENT_BYTES:
            raise ProviderCallError("invalid_arguments", "arguments exceed 64 KiB")
        if detect_secret_material(encoded):
            raise ProviderCallError(
                "secret_material_rejected",
                "arguments contain secret material",
            )
        return value

    @staticmethod
    def _text(
        value: object, name: str, *, maximum: int = 16_000
    ) -> str:
        if (
            not isinstance(value, str)
            or not value.strip()
            or len(value) > maximum
            or any(ord(char) < 32 and char not in "\t\n\r" for char in value)
        ):
            raise ProviderCallError(
                "invalid_arguments", f"{name} must be bounded non-empty text"
            )
        try:
            value.encode("utf-8")
        except UnicodeEncodeError:
            raise ProviderCallError(
                "invalid_arguments", f"{name} must be valid UTF-8 text"
            ) from None
        return value

    @staticmethod
    def _integer(
        value: object, name: str, *, minimum: int, maximum: int
    ) -> int:
        if type(value) is not int or not minimum <= value <= maximum:
            raise ProviderCallError(
                "invalid_arguments", f"{name} must be {minimum}..{maximum}"
            )
        return value

    @staticmethod
    def bounded(result: dict[str, object]) -> dict[str, object]:
        try:
            encoded = json.dumps(
                result,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
                allow_nan=False,
            )
            encoded_bytes = encoded.encode("utf-8")
        except (TypeError, ValueError, UnicodeEncodeError):
            raise ProviderCallError(
                "invalid_result", "provider produced a non-JSON result"
            ) from None
        if len(encoded_bytes) > MAX_PROVIDER_RESULT_BYTES:
            raise ProviderCallError("result_too_large", "result exceeds 1 MB")
        if detect_secret_material(encoded):
            raise ProviderCallError(
                "secret_material_rejected", "result failed secret policy"
            )
        return result

    def call(self, name: str, arguments: object) -> dict[str, object]:
        handler = getattr(self, f"_{name}", None)
        if handler is None or name.startswith("_"):
            raise ProviderCallError("unknown_tool", "unknown ACR provider tool")
        return self.bounded(handler(arguments))

    def _search_memory(self, value: object) -> dict[str, object]:
        args = self._arguments(
            value,
            required=frozenset({"query", "scope", "token_budget", "limit"}),
            optional=frozenset({"types"}),
        )
        query = self._text(args["query"], "query")
        scope = self._text(args["scope"], "scope", maximum=256)
        budget = self._integer(
            args["token_budget"], "token_budget", minimum=64, maximum=20_000
        )
        limit = self._integer(args["limit"], "limit", minimum=1, maximum=100)
        raw_types = args.get("types", [])
        if not isinstance(raw_types, list) or len(raw_types) > len(MemoryType):
            raise ProviderCallError("invalid_arguments", "types is invalid")
        try:
            types = tuple(MemoryType(item) for item in raw_types)
        except (TypeError, ValueError):
            raise ProviderCallError("invalid_arguments", "types is invalid") from None
        if len(set(types)) != len(types):
            raise ProviderCallError("invalid_arguments", "types must be unique")
        self._require("memory.read", f"memory:{scope}")
        result = self.runtime.retrieve_memory(
            RetrievalRequest(
                task=query,
                query=query,
                scope=scope,
                token_budget=budget,
                target_memories=limit,
                types=types,
                sensitivities=(Sensitivity.PUBLIC, Sensitivity.INTERNAL),
                include_global=True,
            )
        )
        memories: list[dict[str, object]] = []
        for ranked in result.selected:
            record = ranked.memory
            request = ContentAssessmentRequest(
                origin="retrieved_memory",
                source_id=f"memory:{record.id}",
                content=record.content,
                provenance=tuple(record.evidence),
            )
            assessment = self.runtime.content_security.assess(request)
            memories.append(
                {
                    "id": record.id,
                    "type": record.type.value,
                    "scope": record.scope,
                    "subject": record.subject,
                    "content": self.runtime.content_security.frame_untrusted(
                        request, assessment
                    ),
                    "authority": "none",
                    "confidence": record.confidence,
                    "score": ranked.score,
                    "explanation": ranked.explanation,
                    "conflict_ids": list(ranked.conflict_ids),
                    "security_disposition": assessment["disposition"],
                }
            )
        return {
            "candidate_count": result.candidate_count,
            "selected_tokens": result.selected_tokens,
            "semantic_available": result.semantic_available,
            "semantic_status": result.semantic_status,
            "memories": memories,
        }

    def _retrieve_context(self, value: object) -> dict[str, object]:
        args = self._arguments(
            value, required=frozenset({"task", "scope", "token_budget"})
        )
        task = self._text(args["task"], "task")
        scope = self._text(args["scope"], "scope", maximum=256)
        budget = self._integer(
            args["token_budget"], "token_budget", minimum=64, maximum=20_000
        )
        self._require("memory.read", f"memory:{scope}")
        self._require("database.write", f"context:{scope}")
        bundle = self.runtime.compile_context(
            task, scope=scope, token_budget=budget
        )
        return {
            "task_id": bundle.task_id,
            "scope": bundle.scope,
            "content": bundle.render(),
            "selected_tokens": bundle.selected_tokens,
            "token_budget": bundle.token_budget,
            "pipeline": list(bundle.pipeline),
            "rejected_count": len(bundle.rejected),
        }

    def _find_skill(self, value: object) -> dict[str, object]:
        args = self._arguments(
            value, required=frozenset({"query", "limit"})
        )
        query = self._text(args["query"], "query")
        limit = self._integer(args["limit"], "limit", minimum=1, maximum=100)
        self._require("database.read", "skills:registry")
        result = self.runtime.skill_registry.search(
            query, limit=limit, lifecycle_statuses=frozenset({"active"})
        )
        return {
            "semantic_available": result["semantic_available"],
            "results": [
                {
                    key: item[key]
                    for key in (
                        "id",
                        "manifest_id",
                        "name",
                        "version",
                        "description",
                        "lifecycle_status",
                        "reliability",
                        "combined_score",
                        "reason",
                    )
                }
                for item in result["results"]
            ],
        }

    def _execute_skill(self, value: object) -> dict[str, object]:
        args = self._arguments(
            value,
            required=frozenset({"reference", "inputs"}),
        )
        self._text(args["reference"], "reference", maximum=256)
        if not isinstance(args["inputs"], dict):
            raise ProviderCallError(
                "invalid_arguments", "inputs must be an object"
            )
        # Prompt 36 has no skill.execute capability and ACR has no production
        # executor. Never substitute activation, validation, scripts, or a shell.
        raise ProviderCallError(
            "skill_execution_unavailable",
            "governed skill execution is not available in this runtime",
        )

    def _task_history(self, value: object) -> dict[str, object]:
        args = self._arguments(
            value, required=frozenset({"scope", "limit"})
        )
        scope = self._text(args["scope"], "scope", maximum=256)
        limit = self._integer(args["limit"], "limit", minimum=1, maximum=100)
        self._require("database.read", f"tasks:{scope}")
        rows = self.runtime.db.connection.execute(
            """
            SELECT id, scope, token_budget, selected_tokens, status,
                   critic_score, duration_ms, created_at, completed_at
            FROM tasks WHERE scope=?
            ORDER BY created_at DESC, id DESC LIMIT ?
            """,
            (scope, limit),
        ).fetchall()
        return {"tasks": [dict(row) for row in rows]}

    def _failure_lookup(self, value: object) -> dict[str, object]:
        args = self._arguments(
            value,
            required=frozenset({"task", "task_class", "scope", "limit"}),
            optional=frozenset({"strategy"}),
        )
        task = self._text(args["task"], "task")
        task_class = self._text(
            args["task_class"], "task_class", maximum=256
        )
        scope = self._text(args["scope"], "scope", maximum=256)
        limit = self._integer(args["limit"], "limit", minimum=1, maximum=20)
        strategy_value = args.get("strategy")
        strategy = (
            None
            if strategy_value is None
            else self._text(strategy_value, "strategy")
        )
        self._require("memory.read", f"memory:{scope}")
        matches = self.runtime.query_failures(
            FailureQuery(
                task=task,
                task_class=task_class,
                scope=scope,
                strategy=strategy,
                limit=limit,
            )
        )
        return {
            "matches": [
                {
                    "id": item.failure.id,
                    "task_class": item.failure.task_class,
                    "strategy_attempted": item.failure.strategy_attempted,
                    "avoidance_rule": item.failure.avoidance_rule,
                    "confidence": item.failure.confidence,
                    "occurrence_count": item.failure.occurrence_count,
                    "status": item.failure.status,
                    "first_seen_at": item.failure.first_seen_at,
                    "last_seen_at": item.failure.last_seen_at,
                    "analogy_score": item.analogy_score,
                    "avoidance_weight": item.avoidance_weight,
                    "repetition_weight": item.repetition_weight,
                    "absolute_prohibition": item.absolute_prohibition,
                    "explanation": item.explanation,
                }
                for item in matches
            ]
        }
