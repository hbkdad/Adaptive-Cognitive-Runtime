from __future__ import annotations

import json
import uuid
import urllib.error
import urllib.request
from time import perf_counter
from typing import Any, Iterable, Iterator, Mapping, Protocol, Sequence
from urllib.parse import urlparse

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


class OllamaError(RuntimeError):
    pass


class OllamaTransport(Protocol):
    def get_json(self, path: str) -> dict[str, Any]: ...

    def post_json(self, path: str, payload: dict[str, Any]) -> dict[str, Any]: ...

    def post_stream(
        self, path: str, payload: dict[str, Any]
    ) -> Iterable[dict[str, Any]]: ...


class UrllibOllamaTransport:
    def __init__(self, base_url: str, *, timeout_seconds: float = 180.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    def _request(
        self, path: str, *, payload: dict[str, Any] | None = None
    ) -> urllib.response.addinfourl:
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        request = urllib.request.Request(
            f"{self.base_url}{path}",
            data=data,
            headers={"Accept": "application/json", "Content-Type": "application/json"},
            method="POST" if data is not None else "GET",
        )
        try:
            return urllib.request.urlopen(request, timeout=self.timeout_seconds)
        except urllib.error.HTTPError as error:
            body = error.read().decode("utf-8", errors="replace")
            raise OllamaError(f"Ollama HTTP {error.code}: {body[:300]}") from error
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            raise OllamaError(f"Ollama local API unavailable: {error}") from error

    def get_json(self, path: str) -> dict[str, Any]:
        with self._request(path) as response:
            return json.loads(response.read().decode("utf-8"))

    def post_json(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        with self._request(path, payload=payload) as response:
            return json.loads(response.read().decode("utf-8"))

    def post_stream(
        self, path: str, payload: dict[str, Any]
    ) -> Iterator[dict[str, Any]]:
        with self._request(path, payload=payload) as response:
            for raw_line in response:
                line = raw_line.decode("utf-8").strip()
                if line:
                    yield json.loads(line)


class OllamaProvider:
    name = "ollama"

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:11434",
        *,
        timeout_seconds: float = 180.0,
        allow_remote: bool = False,
        sink: ModelCallSink | None = None,
        transport: OllamaTransport | None = None,
        reasoning_modes_by_model: Mapping[str, Sequence[str]] | None = None,
        reasoning_efforts_by_model: Mapping[str, Sequence[str]] | None = None,
    ) -> None:
        parsed = urlparse(base_url)
        if parsed.scheme not in {"http", "https"}:
            raise ValueError("Ollama URL must use http or https")
        loopback = parsed.hostname in {"127.0.0.1", "localhost", "::1"}
        if not loopback and not allow_remote:
            raise ValueError(
                "Remote Ollama endpoints require allow_remote=True and an explicit policy"
            )
        self.base_url = base_url.rstrip("/")
        self._sink = sink
        self._transport = transport or UrllibOllamaTransport(
            self.base_url, timeout_seconds=timeout_seconds
        )
        allowed_modes = {"enabled", "disabled", "effort"}
        self._reasoning_modes_by_model: dict[str, tuple[str, ...]] = {}
        for model, modes in (reasoning_modes_by_model or {}).items():
            normalized = tuple(dict.fromkeys(str(item) for item in modes))
            if any(item not in allowed_modes for item in normalized):
                raise ValueError("Unsupported declared Ollama reasoning mode")
            self._reasoning_modes_by_model[str(model)] = normalized
        allowed_efforts = {"minimal", "low", "medium", "high", "xhigh", "max"}
        self._reasoning_efforts_by_model: dict[str, tuple[str, ...]] = {}
        for model, efforts in (reasoning_efforts_by_model or {}).items():
            normalized = tuple(dict.fromkeys(str(item) for item in efforts))
            if any(item not in allowed_efforts for item in normalized):
                raise ValueError("Unsupported declared Ollama reasoning effort")
            if "effort" not in self._reasoning_modes_by_model.get(
                str(model), ()
            ):
                raise ValueError(
                    "Ollama effort values require an explicit effort mode"
                )
            self._reasoning_efforts_by_model[str(model)] = normalized

    def _capabilities(self, model: str) -> ModelCapabilities:
        embedding_model = "embed" in model.lower()
        declared = self._reasoning_efforts_by_model.get(model, ())
        reasoning_modes = (
            self._reasoning_modes_by_model.get(model, ())
            if not embedding_model else ()
        )
        return ModelCapabilities(
            chat=not embedding_model,
            structured_output=not embedding_model,
            tool_calling=not embedding_model,
            streaming=not embedding_model,
            embeddings=embedding_model,
            vision=False,
            token_accounting=True,
            context_window=None,
            reasoning_modes=reasoning_modes,
            reasoning_efforts=declared,
            reasoning_token_accounting=False,
        )

    def list_models(self) -> Sequence[ModelMetadata]:
        payload = self._transport.get_json("/api/tags")
        models: list[ModelMetadata] = []
        for item in payload.get("models", []):
            if not isinstance(item, dict):
                continue
            model = item.get("name") or item.get("model")
            if not isinstance(model, str):
                continue
            models.append(
                ModelMetadata(
                    provider=self.name,
                    model=model,
                    capabilities=self._capabilities(model),
                    local=True,
                    input_cost_per_million=0.0,
                    output_cost_per_million=0.0,
                )
            )
        return tuple(sorted(models, key=lambda metadata: metadata.model))

    def inspect_model(self, model: str) -> ModelMetadata:
        """Read authoritative capabilities and context size from /api/show."""
        installed = {item.model for item in self.list_models()}
        if model not in installed:
            raise LookupError(f"Ollama model is not installed: {model}")
        payload = self._transport.post_json(
            "/api/show", {"model": model, "verbose": False}
        )
        advertised = {
            str(item).lower()
            for item in payload.get("capabilities", [])
            if isinstance(item, str)
        }
        model_info = payload.get("model_info") or {}
        context_values = [
            int(value)
            for key, value in model_info.items()
            if str(key).endswith(".context_length")
            and isinstance(value, (int, float))
            and int(value) > 0
        ]
        completion = "completion" in advertised
        embedding = bool({"embedding", "embeddings"} & advertised)
        return ModelMetadata(
            provider=self.name,
            model=model,
            capabilities=ModelCapabilities(
                chat=completion,
                structured_output=completion,
                tool_calling="tools" in advertised,
                streaming=completion,
                embeddings=embedding,
                vision="vision" in advertised,
                token_accounting=True,
                context_window=max(context_values) if context_values else None,
                reasoning_modes=self._capabilities(model).reasoning_modes,
                reasoning_efforts=self._capabilities(model).reasoning_efforts,
                reasoning_token_accounting=False,
            ),
            local=True,
            input_cost_per_million=0.0,
            output_cost_per_million=0.0,
        )

    def capabilities(self, model: str) -> ModelCapabilities:
        available = {metadata.model: metadata for metadata in self.list_models()}
        if model not in available:
            raise LookupError(f"Ollama model is not installed: {model}")
        return available[model].capabilities

    def _chat_payload(self, request: ChatRequest, *, stream: bool) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": request.model,
            "messages": [
                {"role": message.role, "content": message.content}
                for message in request.messages
            ],
            "stream": stream,
            "options": {"temperature": request.temperature},
        }
        if request.max_output_tokens is not None:
            payload["options"]["num_predict"] = request.max_output_tokens
        if request.response_schema_json is not None:
            payload["format"] = json.loads(request.response_schema_json)
        if request.tools_json is not None:
            payload["tools"] = json.loads(request.tools_json)
        control = request.reasoning
        if control.mode != "provider_default":
            capabilities = self.capabilities(request.model)
            if control.mode not in capabilities.reasoning_modes:
                raise ValueError(
                    f"{request.model} does not declare {control.mode} reasoning support"
                )
            if control.mode == "disabled":
                payload["think"] = False
            elif control.mode == "enabled":
                payload["think"] = True
            elif control.mode == "effort":
                if control.effort not in capabilities.reasoning_efforts:
                    raise ValueError(
                        f"{request.model} does not declare {control.effort} effort"
                    )
                payload["think"] = control.effort
            else:
                raise ValueError("Ollama does not support fixed reasoning budgets")
        return payload

    @staticmethod
    def _usage(payload: dict[str, Any]) -> TokenUsage:
        return TokenUsage(
            input_tokens=int(payload.get("prompt_eval_count") or 0),
            output_tokens=int(payload.get("eval_count") or 0),
            cached_tokens=0,
            estimated=False,
        )

    def _emit(
        self,
        request: ChatRequest,
        *,
        status: str,
        usage: TokenUsage,
        latency_ms: int,
        error_kind: str | None = None,
    ) -> None:
        if self._sink is None:
            return
        self._sink(
            ModelCallRecord(
                provider=self.name,
                model=request.model,
                operation="chat",
                status=status,
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
                error_kind=error_kind,
                attempt_id=str(uuid.uuid4()),
                usage_estimated=usage.estimated,
                local=True,
            )
        )

    def chat(self, request: ChatRequest) -> ChatResponse:
        capabilities = self.capabilities(request.model)
        if not capabilities.chat:
            raise ValueError(f"{request.model} is not chat-capable")
        started = perf_counter()
        try:
            payload = self._transport.post_json(
                "/api/chat", self._chat_payload(request, stream=False)
            )
        except Exception as error:
            latency_ms = max(0, int((perf_counter() - started) * 1_000))
            self._emit(
                request,
                status="failed",
                usage=TokenUsage(0, 0),
                latency_ms=latency_ms,
                error_kind=type(error).__name__,
            )
            raise
        latency_ms = int(payload.get("total_duration", 0) / 1_000_000)
        if latency_ms <= 0:
            latency_ms = max(0, int((perf_counter() - started) * 1_000))
        usage = self._usage(payload)
        message = payload.get("message") or {}
        content = str(message.get("content") or "")
        structured_json = content if request.response_schema_json is not None else None
        tool_calls = message.get("tool_calls")
        response = ChatResponse(
            provider=self.name,
            model=str(payload.get("model") or request.model),
            content=content,
            usage=usage,
            latency_ms=latency_ms,
            finish_reason=str(payload.get("done_reason") or "stop"),
            structured_json=structured_json,
            tool_calls_json=json.dumps(tool_calls) if tool_calls else None,
        )
        self._emit(
            request,
            status="succeeded",
            usage=usage,
            latency_ms=latency_ms,
        )
        return response

    def stream(self, request: ChatRequest) -> Iterator[StreamChunk]:
        capabilities = self.capabilities(request.model)
        if not capabilities.streaming:
            raise ValueError(f"{request.model} does not support streaming")
        started = perf_counter()
        final_usage = TokenUsage(0, 0)
        try:
            for payload in self._transport.post_stream(
                "/api/chat", self._chat_payload(request, stream=True)
            ):
                message = payload.get("message") or {}
                final = bool(payload.get("done"))
                if final:
                    final_usage = self._usage(payload)
                yield StreamChunk(
                    provider=self.name,
                    model=str(payload.get("model") or request.model),
                    content_delta=str(message.get("content") or ""),
                    finish_reason=(
                        str(payload.get("done_reason") or "stop") if final else None
                    ),
                    usage=final_usage if final else None,
                )
        except Exception as error:
            self._emit(
                request,
                status="failed",
                usage=final_usage,
                latency_ms=max(0, int((perf_counter() - started) * 1_000)),
                error_kind=type(error).__name__,
            )
            raise
        self._emit(
            request,
            status="succeeded",
            usage=final_usage,
            latency_ms=max(0, int((perf_counter() - started) * 1_000)),
        )

    def embed(self, request: EmbeddingRequest) -> EmbeddingResponse:
        capabilities = self.capabilities(request.model)
        if not capabilities.embeddings:
            raise ValueError(f"{request.model} is not an embedding model")
        started = perf_counter()
        payload = self._transport.post_json(
            "/api/embed",
            {"model": request.model, "input": list(request.inputs)},
        )
        latency_ms = int(payload.get("total_duration", 0) / 1_000_000)
        if latency_ms <= 0:
            latency_ms = max(0, int((perf_counter() - started) * 1_000))
        vectors = tuple(
            tuple(float(value) for value in vector)
            for vector in payload.get("embeddings", [])
        )
        return EmbeddingResponse(
            provider=self.name,
            model=str(payload.get("model") or request.model),
            vectors=vectors,
            usage=TokenUsage(
                input_tokens=int(payload.get("prompt_eval_count") or 0),
                output_tokens=0,
                estimated=False,
            ),
            latency_ms=latency_ms,
        )
