from __future__ import annotations

import math
import re
from dataclasses import dataclass
from enum import Enum
from typing import Sequence

from .models import ContextBlock
from .scoring import estimate_tokens, query_terms


class TaskComplexity(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


@dataclass(frozen=True)
class TokenEconomyConfig:
    output_headroom_fraction: float = 0.15
    reasoning_headroom_fraction: float = 0.10
    low_input_fraction: float = 0.60
    medium_input_fraction: float = 0.80
    high_input_fraction: float = 1.00
    minimum_headroom_tokens: int = 8

    def __post_init__(self) -> None:
        fractions = (
            self.output_headroom_fraction,
            self.reasoning_headroom_fraction,
            self.low_input_fraction,
            self.medium_input_fraction,
            self.high_input_fraction,
        )
        if any(not 0 <= value <= 1 for value in fractions):
            raise ValueError("Token economy fractions must be 0..1")
        if (
            self.output_headroom_fraction + self.reasoning_headroom_fraction
            >= 1
        ):
            raise ValueError("Headroom must leave model input capacity")
        if not (
            self.low_input_fraction
            <= self.medium_input_fraction
            <= self.high_input_fraction
        ):
            raise ValueError("Complexity budget fractions must be monotonic")


@dataclass(frozen=True)
class TokenBudgetPlan:
    complexity: TaskComplexity
    task_importance: float
    model_context_window: int
    requested_input_budget: int
    output_headroom: int
    reasoning_headroom: int
    effective_input_budget: int
    task_tokens: int

    @property
    def context_budget(self) -> int:
        return max(0, self.effective_input_budget - self.task_tokens)


class TokenEconomist:
    COMPLEX_MARKERS = re.compile(
        r"\b(migrate|debug|diagnose|architect|refactor|security|multi-step|compare)\b",
        re.IGNORECASE,
    )

    def __init__(self, config: TokenEconomyConfig | None = None) -> None:
        self.config = config or TokenEconomyConfig()

    def complexity(self, task: str) -> TaskComplexity:
        tokens = estimate_tokens(task)
        terms = len(query_terms(task))
        markers = len(self.COMPLEX_MARKERS.findall(task))
        score = int(tokens >= 80) + int(terms >= 12) + markers
        if score >= 3:
            return TaskComplexity.HIGH
        if score >= 1:
            return TaskComplexity.MEDIUM
        return TaskComplexity.LOW

    def budget(
        self,
        task: str,
        *,
        requested_input_budget: int,
        task_importance: float,
        model_context_window: int | None = None,
    ) -> TokenBudgetPlan:
        if requested_input_budget < 1:
            raise ValueError("requested_input_budget must be positive")
        if not 0 <= task_importance <= 1:
            raise ValueError("task_importance must be 0..1")
        context_window = model_context_window or max(
            requested_input_budget + 2 * self.config.minimum_headroom_tokens,
            math.ceil(
                requested_input_budget
                / (
                    1
                    - self.config.output_headroom_fraction
                    - self.config.reasoning_headroom_fraction
                )
            ),
        )
        if context_window < 1:
            raise ValueError("model_context_window must be positive")
        output = max(
            self.config.minimum_headroom_tokens,
            math.ceil(context_window * self.config.output_headroom_fraction),
        )
        reasoning = max(
            self.config.minimum_headroom_tokens,
            math.ceil(context_window * self.config.reasoning_headroom_fraction),
        )
        usable = context_window - output - reasoning
        if usable < 1:
            raise ValueError("Model context window is too small for headroom")
        complexity = self.complexity(task)
        fraction = {
            TaskComplexity.LOW: self.config.low_input_fraction,
            TaskComplexity.MEDIUM: self.config.medium_input_fraction,
            TaskComplexity.HIGH: self.config.high_input_fraction,
        }[complexity]
        effective = min(requested_input_budget, max(1, math.floor(usable * fraction)))
        return TokenBudgetPlan(
            complexity=complexity,
            task_importance=task_importance,
            model_context_window=context_window,
            requested_input_budget=requested_input_budget,
            output_headroom=output,
            reasoning_headroom=reasoning,
            effective_input_budget=effective,
            task_tokens=estimate_tokens(task),
        )

    @staticmethod
    def expected_value(
        *,
        relevance: float,
        confidence: float,
        historical_utility: float,
        task_importance: float,
        token_cost: int,
    ) -> tuple[float, float]:
        utility = relevance * confidence * historical_utility * task_importance
        return utility, utility / max(1, token_cost)

    @staticmethod
    def optimize(
        items: Sequence[ContextBlock], capacity: int
    ) -> tuple[ContextBlock, ...]:
        """Exact 0/1 knapsack with stable, fewer-token tie breaking."""
        if capacity < 0:
            raise ValueError("capacity cannot be negative")
        states: dict[int, tuple[int, tuple[int, ...]]] = {0: (0, ())}
        for index, item in enumerate(items):
            value = round(item.expected_utility * 1_000_000)
            updated = dict(states)
            for used, (total, selected) in states.items():
                next_used = used + item.tokens
                if next_used > capacity:
                    continue
                candidate = (total + value, (*selected, index))
                current = updated.get(next_used)
                if current is None or candidate > current:
                    updated[next_used] = candidate
            states = updated
        used, (_, indexes) = max(
            states.items(),
            key=lambda entry: (
                entry[1][0],
                -entry[0],
                tuple(items[index].source_id for index in entry[1][1]),
            ),
        )
        del used
        return tuple(items[index] for index in indexes)
