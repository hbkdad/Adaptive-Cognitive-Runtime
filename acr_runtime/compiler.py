from __future__ import annotations

import json

from .db import RuntimeDB
from .models import ContextBlock, ContextBundle
from .scoring import (
    context_utility,
    estimate_tokens,
    lexical_relevance,
    recency_score,
    token_roi,
)


class ContextCompiler:
    def __init__(self, db: RuntimeDB) -> None:
        self.db = db

    def compile(
        self, task: str, *, scope: str = "global", token_budget: int = 4_000
    ) -> ContextBundle:
        if token_budget < 1:
            raise ValueError("token_budget must be positive")

        task_tokens = estimate_tokens(task)
        available = max(0, token_budget - task_tokens)
        candidates = self._memory_candidates(task, scope)
        candidates.extend(self._skill_candidates(task))
        candidates.sort(key=lambda block: (block.roi, block.utility), reverse=True)

        selected: list[ContextBlock] = []
        selected_tokens = 0
        for block in candidates:
            if block.tokens > available - selected_tokens:
                continue
            selected.append(block)
            selected_tokens += block.tokens

        task_id = self.db.create_task(
            objective=task, scope=scope, token_budget=token_budget
        )
        self.db.record_context(
            task_id,
            (
                {
                    "source_type": block.source_type,
                    "source_id": block.source_id,
                    "tokens": block.tokens,
                    "utility": block.utility,
                    "roi": block.roi,
                }
                for block in selected
            ),
            selected_tokens,
        )
        return ContextBundle(
            task_id=task_id,
            task=task,
            scope=scope,
            token_budget=token_budget,
            task_tokens=task_tokens,
            selected_tokens=selected_tokens,
            blocks=selected,
        )

    def _memory_candidates(self, task: str, scope: str) -> list[ContextBlock]:
        blocks: list[ContextBlock] = []
        for row in self.db.search_memories(task, scope=scope):
            relevance = lexical_relevance(task, row["content"])
            historical = (
                row["success_count"] / row["use_count"]
                if row["use_count"]
                else 0.5
            )
            utility = context_utility(
                relevance=relevance,
                confidence=row["confidence"],
                importance=row["importance"],
                recency=recency_score(row["last_used_at"]),
                historical_success=historical,
            )
            blocks.append(
                ContextBlock(
                    source_type="memory",
                    source_id=row["id"],
                    label=f"{row['kind']} memory",
                    content=row["content"],
                    tokens=row["token_cost"],
                    utility=utility,
                    roi=token_roi(utility, row["token_cost"]),
                    reason=(
                        f"relevance={relevance:.2f}, confidence={row['confidence']:.2f}, "
                        f"importance={row['importance']:.2f}, success={historical:.2f}"
                    ),
                )
            )
        return blocks

    def _skill_candidates(self, task: str) -> list[ContextBlock]:
        blocks: list[ContextBlock] = []
        for row in self.db.active_skills():
            tags = " ".join(json.loads(row["tags_json"]))
            relevance = lexical_relevance(
                task, f"{row['name']} {row['description']} {tags}"
            )
            if relevance == 0:
                continue
            historical = (
                row["success_count"] / row["use_count"]
                if row["use_count"]
                else 0.5
            )
            utility = context_utility(
                relevance=relevance,
                confidence=0.85,
                importance=0.7,
                recency=1.0,
                historical_success=historical,
            )
            blocks.append(
                ContextBlock(
                    source_type="skill",
                    source_id=row["id"],
                    label=f"{row['name']}@{row['version']}",
                    content=row["instructions"],
                    tokens=row["token_cost"],
                    utility=utility,
                    roi=token_roi(utility, row["token_cost"]),
                    reason=(
                        f"relevance={relevance:.2f}, historical_success={historical:.2f}"
                    ),
                )
            )
        return blocks

