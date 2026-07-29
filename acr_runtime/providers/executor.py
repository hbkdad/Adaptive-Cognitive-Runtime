from __future__ import annotations

import json
from decimal import Decimal, ROUND_CEILING

from ..execution import ExecutionOutput, Executor, Step, Task
from ..resource_governor import ResourceGovernor, ResourceVector
from ..cost_accounting import CostAccounting
from .base import ChatMessage, ChatRequest, ModelProvider, ReasoningControl


class ProviderExecutor(Executor):
    """Adapts any chat-capable provider to the core execution protocol."""

    def __init__(
        self,
        provider: ModelProvider,
        *,
        model: str,
        governor: ResourceGovernor | None = None,
        resource_quote: ResourceVector | None = None,
        cost_accounting: CostAccounting | None = None,
        reasoning: ReasoningControl | None = None,
        reasoning_decision_id: str | None = None,
    ) -> None:
        capabilities = provider.capabilities(model)
        if not capabilities.chat:
            raise ValueError(f"{provider.name}/{model} does not support chat")
        self.provider = provider
        self.model = model
        self.cost_accounting = cost_accounting
        self.reasoning = reasoning or ReasoningControl()
        self.reasoning_decision_id = reasoning_decision_id
        if (
            self.reasoning.mode != "provider_default"
            and not reasoning_decision_id
        ):
            raise ValueError(
                "non-default reasoning control requires a policy decision id"
            )
        if (
            self.reasoning.mode != "provider_default"
            and self.reasoning.mode not in capabilities.reasoning_modes
        ):
            raise ValueError(
                f"{provider.name}/{model} does not declare "
                f"{self.reasoning.mode} reasoning support"
            )
        if (
            self.reasoning.mode == "effort"
            and self.reasoning.effort not in capabilities.reasoning_efforts
        ):
            raise ValueError(
                f"{provider.name}/{model} does not declare "
                f"{self.reasoning.effort} reasoning effort"
            )
        self.metadata = None
        if (governor is None) != (resource_quote is None):
            raise ValueError(
                "governor and resource_quote must be configured together"
            )
        if resource_quote is not None and resource_quote.model_calls != 1:
            raise ValueError("provider quote must reserve exactly one model call")
        if resource_quote is not None and (
            resource_quote.input_tokens < 1
            or resource_quote.output_tokens < 1
            or resource_quote.duration < 1
        ):
            raise ValueError(
                "provider quote requires positive input, output, and duration bounds"
            )
        if resource_quote is not None:
            metadata = next(
                (
                    item
                    for item in provider.list_models()
                    if item.model == model
                ),
                None,
            )
            if metadata is None:
                raise LookupError(f"Unknown {provider.name} model: {model}")
            self.metadata = metadata
            if (
                cost_accounting is None
                and not metadata.local
                and (
                    metadata.input_cost_per_million is None
                    or metadata.output_cost_per_million is None
                )
            ):
                raise ValueError(
                    "governed provider requires declared input and output pricing"
                )
            required_cost = self._cost_microunits(
                resource_quote.input_tokens,
                resource_quote.output_tokens,
            )
            if resource_quote.cost < required_cost:
                raise ValueError(
                    "provider quote cost is below the declared pricing upper bound"
                )
        self.governor = governor
        self.resource_quote = resource_quote

    def _cost_microunits(
        self,
        input_tokens: int,
        output_tokens: int,
    ) -> int:
        if self.metadata is None:
            raise RuntimeError("cost accounting requires governed model metadata")
        if self.metadata.local:
            return 0
        if self.cost_accounting is not None:
            return int(
                self.cost_accounting.quote_model_upper_bound(
                    provider=self.provider.name,
                    model=self.model,
                    operation="chat",
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                )["cost_micros"]
            )
        input_price = Decimal(str(self.metadata.input_cost_per_million))
        output_price = Decimal(str(self.metadata.output_cost_per_million))
        return int(
            (
                Decimal(input_tokens) * input_price
                + Decimal(output_tokens) * output_price
            ).to_integral_value(rounding=ROUND_CEILING)
        )

    def execute(self, task: Task, step: Step) -> ExecutionOutput:
        reservation = None
        if self.governor is not None and self.resource_quote is not None:
            reservation = self.governor.reserve(
                task.id,
                self.resource_quote,
                idempotency_key=f"model:{step.id}",
                kind="model",
                evidence=("provider_upper_bound_quote",),
            )
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
            max_output_tokens=(
                None
                if self.resource_quote is None
                else self.resource_quote.output_tokens
            ),
            task_id=task.id,
            step_id=step.id,
            reasoning=self.reasoning,
        )
        response = self.provider.chat(request)
        if reservation is not None and self.governor is not None:
            self.governor.commit(
                reservation.id,
                ResourceVector(
                    input_tokens=response.usage.input_tokens,
                    output_tokens=response.usage.output_tokens,
                    model_calls=1,
                    cost=self._cost_microunits(
                        response.usage.input_tokens,
                        response.usage.output_tokens,
                    ),
                    duration=response.latency_ms,
                ),
                evidence=("provider_authoritative_usage",),
            )
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
                    "reasoning_tokens": response.usage.reasoning_tokens,
                    "cached_tokens": response.usage.cached_tokens,
                    "usage_estimated": response.usage.estimated,
                    "latency_ms": response.latency_ms,
                    "finish_reason": response.finish_reason,
                    "reasoning_decision_id": self.reasoning_decision_id,
                    "provider_reasoning_mode": self.reasoning.mode,
                    "provider_reasoning_effort": self.reasoning.effort,
                },
                sort_keys=True,
            ),
        )
