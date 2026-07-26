from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


@dataclass(frozen=True)
class ContextBlock:
    source_type: Literal["memory", "skill"]
    source_id: str
    label: str
    content: str
    tokens: int
    utility: float
    roi: float
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

    @property
    def total_tokens(self) -> int:
        return self.task_tokens + self.selected_tokens

    def render(self) -> str:
        sections = [f"# Objective\n{self.task}"]
        memories = [block for block in self.blocks if block.source_type == "memory"]
        skills = [block for block in self.blocks if block.source_type == "skill"]
        if memories:
            sections.append(
                "# Retrieved memory\n"
                + "\n\n".join(f"## {block.label}\n{block.content}" for block in memories)
            )
        if skills:
            sections.append(
                "# Selected skills\n"
                + "\n\n".join(f"## {block.label}\n{block.content}" for block in skills)
            )
        return "\n\n".join(sections)
