from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


ContextSourceType = Literal[
    "system_rule",
    "memory",
    "skill",
    "file",
    "tool",
    "agent_state",
    "observation",
]


@dataclass(frozen=True)
class ContextCandidate:
    source_type: ContextSourceType
    source_id: str
    label: str
    content: str
    confidence: float = 1.0
    expected_utility: float = 0.5
    required: bool = False
    dependencies: tuple[str, ...] = ()
    reason: str = "caller_provided"

    def __post_init__(self) -> None:
        if not self.source_id.strip() or not self.label.strip() or not self.content.strip():
            raise ValueError("Context candidate identity and content are required")
        for value in (self.confidence, self.expected_utility):
            if not 0 <= value <= 1:
                raise ValueError("Context candidate scores must be 0..1")


@dataclass(frozen=True)
class ContextBlock:
    source_type: ContextSourceType
    source_id: str
    label: str
    content: str
    tokens: int
    relevance_score: float
    confidence: float
    expected_utility: float
    required: bool
    reason_selected: str
    roi: float
    dependencies: tuple[str, ...] = ()
    historical_utility: float = 0.5
    task_importance: float = 0.5

    @property
    def utility(self) -> float:
        return self.expected_utility

    @property
    def reason(self) -> str:
        return self.reason_selected


@dataclass(frozen=True)
class ContextRejection:
    source_type: ContextSourceType
    source_id: str
    reason: str


@dataclass(frozen=True)
class ContextBundle:
    task_id: str
    task: str
    scope: str
    token_budget: int
    task_tokens: int
    selected_tokens: int
    blocks: list[ContextBlock] = field(default_factory=list)
    rejected: tuple[ContextRejection, ...] = ()
    pipeline: tuple[str, ...] = ()
    model_context_window: int | None = None
    output_headroom: int = 0
    reasoning_headroom: int = 0
    effective_input_budget: int | None = None
    complexity: str | None = None

    @property
    def total_tokens(self) -> int:
        return self.task_tokens + self.selected_tokens

    def render(self) -> str:
        sections = [f"# Objective\n{self.task}"]
        names = {
            "system_rule": "System rules",
            "memory": "Retrieved memory",
            "skill": "Selected skills",
            "file": "Relevant files",
            "tool": "Tool definitions",
            "agent_state": "Agent state",
            "observation": "Previous observations",
        }
        for source_type, heading in names.items():
            blocks = [
                block for block in self.blocks
                if block.source_type == source_type
            ]
            if blocks:
                sections.append(
                    f"# {heading}\n"
                    + "\n\n".join(
                        f"## {block.label}\n{block.content}" for block in blocks
                    )
                )
        return "\n\n".join(sections)
