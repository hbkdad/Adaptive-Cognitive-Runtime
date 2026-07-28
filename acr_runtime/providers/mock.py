from __future__ import annotations

import uuid

import hashlib
from time import perf_counter
from typing import Callable, Iterable, Sequence

from ..scoring import estimate_tokens
from .base import (
    ChatRequest,
    ChatResponse,
    EmbeddingRequest,
    EmbeddingResponse,
    ModelCallRecord,
    ModelCallSink,
    ModelCapabilities,
    ModelMetadata,
    StreamChunk,
    TokenUsage,
)

MockResponder = Callable[[ChatRequest], str]


class MockProvider:
    """Deterministic provider used for tests and offline integration work."""

    name = "mock"

    def __init__(
        self,
        responder: MockResponder | None = None,
        *,
        sink: ModelCallSink | None = None,
    ) -> None:
        self._responder = responder or self._default_response
        self._sink = sink
        self._models = (
            ModelMetadata(
                provider=self.name,
                model="mock-chat",
                capabilities=ModelCapabilities(
                    chat=True,
                    structured_output=True,
                    streaming=True,
                    embeddings=False,
                    token_accounting=True,
                    context_window=8_192,
                ),
                local=True,
                input_cost_per_million=0.0,
                output_cost_per_million=0.0,
            ),
        )

    @staticmethod
    def _default_response(request: ChatRequest) -> str:
        return request.messages[-1].content

    def list_models(self) -> Sequence[ModelMetadata]:
        return self._models

    def capabilities(self, model: str) -> ModelCapabilities:
        for metadata in self._models:
            if metadata.model == model:
                return metadata.capabilities
        raise LookupError(f"Unknown mock model: {model}")

    def chat(self, request: ChatRequest) -> ChatResponse:
        self.capabilities(request.model)
        started = perf_counter()
        input_tokens = sum(estimate_tokens(message.content) for message in request.messages)
        try:
            content = self._responder(request)
        except Exception as error:
            latency_ms = max(0, int((perf_counter() - started) * 1_000))
            if self._sink is not None:
                self._sink(
                    ModelCallRecord(
                        provider=self.name,
                        model=request.model,
                        operation="chat",
                        status="failed",
                        task_id=request.task_id,
                        step_id=request.step_id,
                        context_bundle_id=request.context_bundle_id,
                        input_tokens=input_tokens,
                        output_tokens=0,
                        cached_tokens=0,
                        latency_ms=latency_ms,
                        estimated_cost=0.0,
                        loaded_skill_ids=request.loaded_skill_ids,
                        loaded_memory_ids=request.loaded_memory_ids,
                        error_kind=type(error).__name__,
                        attempt_id=str(uuid.uuid4()),
                        usage_estimated=True,
                        local=True,
                    )
                )
            raise
        latency_ms = max(0, int((perf_counter() - started) * 1_000))
        usage = TokenUsage(
            input_tokens=input_tokens,
            output_tokens=estimate_tokens(content),
            estimated=True,
        )
        response = ChatResponse(
            provider=self.name,
            model=request.model,
            content=content,
            usage=usage,
            latency_ms=latency_ms,
            finish_reason="stop",
        )
        if self._sink is not None:
            self._sink(
                ModelCallRecord(
                    provider=self.name,
                    model=request.model,
                    operation="chat",
                    status="succeeded",
                    task_id=request.task_id,
                    step_id=request.step_id,
                    context_bundle_id=request.context_bundle_id,
                    input_tokens=usage.input_tokens,
                    output_tokens=usage.output_tokens,
                    cached_tokens=usage.cached_tokens,
                    latency_ms=latency_ms,
                    estimated_cost=0.0,
                    loaded_skill_ids=request.loaded_skill_ids,
                    loaded_memory_ids=request.loaded_memory_ids,
                    attempt_id=str(uuid.uuid4()),
                    usage_estimated=usage.estimated,
                    local=True,
                )
            )
        return response

    def stream(self, request: ChatRequest) -> Iterable[StreamChunk]:
        response = self.chat(request)
        for word in response.content.split():
            yield StreamChunk(
                provider=self.name,
                model=request.model,
                content_delta=f"{word} ",
            )
        yield StreamChunk(
            provider=self.name,
            model=request.model,
            content_delta="",
            finish_reason=response.finish_reason,
            usage=response.usage,
        )

    def embed(self, request: EmbeddingRequest) -> EmbeddingResponse:
        raise NotImplementedError("mock-chat does not support embeddings")
