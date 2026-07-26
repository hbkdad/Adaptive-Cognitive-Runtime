from __future__ import annotations

import json

from ..execution import ExecutionOutput, Executor, Step, Task
from .base import ChatMessage, ChatRequest, ModelProvider


class ProviderExecutor(Executor):
    """Adapts any chat-capable provider to the core execution protocol."""

    def __init__(self, provider: ModelProvider, *, model: str) -> None:
        capabilities = provider.capabilities(model)
        if not capabilities.chat:
            raise ValueError(f"{provider.name}/{model} does not support chat")
        self.provider = provider
        self.model = model

    def execute(self, task: Task, step: Step) -> ExecutionOutput:
        request = ChatRequest(
            model=self.model,
            messages=(
                ChatMessage(
                    role="system",
                    content=(
                        "Complete the bounded task step. Respect the task constraints "
                        "and return only the requested result."
                    ),
                ),
                ChatMessage(
                    role="user",
                    content=(
                        f"Objective: {task.objective}\n"
                        f"Step: {step.name}\n"
                        f"Constraints: {json.dumps(task.constraints)}"
                    ),
                ),
            ),
            task_id=task.id,
            step_id=step.id,
        )
        response = self.provider.chat(request)
        return ExecutionOutput(
            content=response.content,
            observation=(
                f"{response.provider}/{response.model} completed the step "
                f"with {response.usage.total_tokens} tokens"
            ),
            metadata_json=json.dumps(
                {
                    "provider": response.provider,
                    "model": response.model,
                    "input_tokens": response.usage.input_tokens,
                    "output_tokens": response.usage.output_tokens,
                    "cached_tokens": response.usage.cached_tokens,
                    "usage_estimated": response.usage.estimated,
                    "latency_ms": response.latency_ms,
                    "finish_reason": response.finish_reason,
                },
                sort_keys=True,
            ),
        )

