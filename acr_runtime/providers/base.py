from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Literal, Protocol, Sequence

from ..secret_management import assert_secret_free
MessageRole = Literal["system", "user", "assistant", "tool"]


@dataclass(frozen=True)
class ModelCapabilities:
    chat: bool = True
    structured_output: bool = False
    tool_calling: bool = False
    streaming: bool = False
    embeddings: bool = False
    vision: bool = False
    token_accounting: bool = False
    context_window: int | None = None
    reasoning_modes: tuple[str, ...] = ()
    reasoning_efforts: tuple[str, ...] = ()
    reasoning_token_accounting: bool = False


@dataclass(frozen=True)
class ReasoningControl:
    """Provider-neutral request intent; adapters must validate exact support."""

    mode: Literal[
        "provider_default", "enabled", "disabled", "adaptive", "effort",
        "fixed_budget"
    ] = "provider_default"
    effort: Literal["minimal", "low", "medium", "high", "xhigh", "max"] | None = None
    budget_tokens: int | None = None

    def __post_init__(self) -> None:
        allowed_modes = {
            "provider_default", "enabled", "disabled", "adaptive", "effort",
            "fixed_budget",
        }
        allowed_efforts = {"minimal", "low", "medium", "high", "xhigh", "max"}
        if self.mode not in allowed_modes:
            raise ValueError("unsupported reasoning control mode")
        if self.effort is not None and self.effort not in allowed_efforts:
            raise ValueError("unsupported reasoning effort")
        if self.mode == "effort":
            if self.effort is None or self.budget_tokens is not None:
                raise ValueError("effort mode requires only an effort value")
        elif self.mode == "fixed_budget":
            if (
                self.budget_tokens is None
                or isinstance(self.budget_tokens, bool)
                or self.budget_tokens < 1
                or self.effort is not None
            ):
                raise ValueError(
                    "fixed_budget mode requires only positive budget_tokens"
                )
        elif self.effort is not None or self.budget_tokens is not None:
            raise ValueError(
                "non-effort reasoning modes cannot carry effort or budget"
            )


@dataclass(frozen=True)
class ModelMetadata:
    provider: str
    model: str
    capabilities: ModelCapabilities
    local: bool
    input_cost_per_million: float | None = None
    output_cost_per_million: float | None = None


@dataclass(frozen=True)
class ChatMessage:
    role: MessageRole
    content: str

    def __post_init__(self) -> None:
        if not self.content.strip():
            raise ValueError("chat message content cannot be empty")
        assert_secret_free(self.content, "model prompt")


@dataclass(frozen=True)
class ChatRequest:
    model: str
    messages: tuple[ChatMessage, ...]
    max_output_tokens: int | None = None
    temperature: float = 0.0
    response_schema_json: str | None = None
    tools_json: str | None = None
    task_id: str | None = None
    step_id: str | None = None
    context_bundle_id: str | None = None
    loaded_skill_ids: tuple[str, ...] = ()
    loaded_memory_ids: tuple[str, ...] = ()
    reasoning: ReasoningControl = field(default_factory=ReasoningControl)

    def __post_init__(self) -> None:
        if not self.model.strip():
            raise ValueError("model cannot be empty")
        if not self.messages:
            raise ValueError("messages cannot be empty")
        if self.max_output_tokens is not None and self.max_output_tokens < 1:
            raise ValueError("max_output_tokens must be positive")
        if not 0 <= self.temperature <= 2:
            raise ValueError("temperature must be between 0 and 2")


@dataclass(frozen=True)
class TokenUsage:
    input_tokens: int
    output_tokens: int
    cached_tokens: int = 0
    estimated: bool = False
    reasoning_tokens: int | None = None

    def __post_init__(self) -> None:
        for name, value in (
            ("input_tokens", self.input_tokens),
            ("output_tokens", self.output_tokens),
            ("cached_tokens", self.cached_tokens),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")
        if self.cached_tokens > self.input_tokens:
            raise ValueError("cached_tokens cannot exceed input_tokens")
        if self.reasoning_tokens is not None and (
            isinstance(self.reasoning_tokens, bool)
            or not isinstance(self.reasoning_tokens, int)
            or self.reasoning_tokens < 0
            or self.reasoning_tokens > self.output_tokens
        ):
            raise ValueError(
                "reasoning_tokens must be null or within inclusive output_tokens"
            )

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


@dataclass(frozen=True)
class ChatResponse:
    provider: str
    model: str
    content: str
    usage: TokenUsage
    latency_ms: int
    finish_reason: str
    structured_json: str | None = None
    tool_calls_json: str | None = None


@dataclass(frozen=True)
class StreamChunk:
    provider: str
    model: str
    content_delta: str
    finish_reason: str | None = None
    usage: TokenUsage | None = None


@dataclass(frozen=True)
class EmbeddingRequest:
    model: str
    inputs: tuple[str, ...]
    task_id: str | None = None

    def __post_init__(self) -> None:
        if not self.inputs:
            raise ValueError("embedding inputs cannot be empty")
        for value in self.inputs:
            assert_secret_free(value, "embedding input")


@dataclass(frozen=True)
class EmbeddingResponse:
    provider: str
    model: str
    vectors: tuple[tuple[float, ...], ...]
    usage: TokenUsage
    latency_ms: int


class ModelProvider(Protocol):
    @property
    def name(self) -> str: ...

    def list_models(self) -> Sequence[ModelMetadata]: ...

    def capabilities(self, model: str) -> ModelCapabilities: ...

    def chat(self, request: ChatRequest) -> ChatResponse: ...

    def stream(self, request: ChatRequest) -> Iterable[StreamChunk]: ...

    def embed(self, request: EmbeddingRequest) -> EmbeddingResponse: ...


@dataclass(frozen=True)
class ModelCallRecord:
    provider: str
    model: str
    operation: Literal["chat", "embedding"]
    status: Literal["succeeded", "failed"]
    task_id: str | None
    step_id: str | None
    context_bundle_id: str | None
    input_tokens: int
    output_tokens: int
    cached_tokens: int
    latency_ms: int
    estimated_cost: float
    attempt_id: str
    loaded_skill_ids: tuple[str, ...] = ()
    loaded_memory_ids: tuple[str, ...] = ()
    error_kind: str | None = None
    cache_write_tokens: int = 0
    usage_estimated: bool = False
    local: bool = False

    def __post_init__(self) -> None:
        for name, value in (
            ("input_tokens", self.input_tokens),
            ("output_tokens", self.output_tokens),
            ("cached_tokens", self.cached_tokens),
            ("cache_write_tokens", self.cache_write_tokens),
            ("latency_ms", self.latency_ms),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")
        if self.cached_tokens + self.cache_write_tokens > self.input_tokens:
            raise ValueError("cache token categories cannot exceed input_tokens")
        if not self.attempt_id.strip() or len(self.attempt_id) > 200:
            raise ValueError("attempt_id must be a bounded opaque identifier")
        assert_secret_free(self.attempt_id, "attempt_id")
        if not isinstance(self.usage_estimated, bool):
            raise ValueError("usage_estimated must be boolean")
        if not isinstance(self.local, bool):
            raise ValueError("local must be boolean")


class ModelCallSink(Protocol):
    def __call__(self, record: ModelCallRecord) -> None: ...
