from __future__ import annotations

import json
from dataclasses import dataclass

from .db import RuntimeDB
from .memory import MemoryReader
from .models import (
    ContextBlock,
    ContextBundle,
    ContextCandidate,
    ContextRejection,
)
from .retrieval import HybridMemoryRetriever, RetrievalRequest
from .scoring import context_utility, estimate_tokens, lexical_relevance, token_roi


PIPELINE = (
    "DISCOVER",
    "FILTER",
    "RANK",
    "DEDUPLICATE",
    "RESOLVE_TEMPORAL_CONFLICTS",
    "DEPENDENCY_EXPAND",
    "COMPRESS",
    "TOKEN_PRICE",
    "OPTIMIZE",
    "ASSEMBLE",
)


@dataclass(frozen=True)
class ContextRequest:
    task: str
    scope: str = "global"
    token_budget: int = 4_000
    system_rules: tuple[ContextCandidate, ...] = ()
    relevant_files: tuple[ContextCandidate, ...] = ()
    tool_definitions: tuple[ContextCandidate, ...] = ()
    agent_state: tuple[ContextCandidate, ...] = ()
    previous_observations: tuple[ContextCandidate, ...] = ()

    def __post_init__(self) -> None:
        if not self.task.strip():
            raise ValueError("Context task cannot be empty")
        if self.token_budget < 1:
            raise ValueError("token_budget must be positive")


class ContextCompiler:
    def __init__(
        self,
        db: RuntimeDB,
        memory_reader: MemoryReader | None = None,
        *,
        minimum_optional_utility: float = 0.05,
    ) -> None:
        self.db = db
        self.memory_reader = memory_reader or db.memories
        self.retriever = HybridMemoryRetriever(self.memory_reader)
        self.minimum_optional_utility = minimum_optional_utility

    def compile(
        self, task: str, *, scope: str = "global", token_budget: int = 4_000
    ) -> ContextBundle:
        return self.compile_request(
            ContextRequest(task=task, scope=scope, token_budget=token_budget)
        )

    def compile_request(self, request: ContextRequest) -> ContextBundle:
        task_tokens = estimate_tokens(request.task)
        available = request.token_budget - task_tokens
        if available < 0:
            raise ValueError("Task alone exceeds the hard token budget")
        discovered = [
            *self._memory_candidates(request.task, request.scope, available),
            *self._skill_candidates(request.task),
            *request.system_rules,
            *request.relevant_files,
            *request.tool_definitions,
            *request.agent_state,
            *request.previous_observations,
        ]
        rejected: list[ContextRejection] = []
        filtered: list[ContextCandidate] = []
        for item in discovered:
            relevance = lexical_relevance(request.task, f"{item.label} {item.content}")
            if not item.required and (
                relevance == 0 or item.expected_utility < self.minimum_optional_utility
            ):
                rejected.append(
                    ContextRejection(
                        item.source_type, item.source_id, "low_marginal_value"
                    )
                )
                continue
            filtered.append(item)

        by_content: dict[str, ContextCandidate] = {}
        for item in filtered:
            key = " ".join(item.content.casefold().split())
            current = by_content.get(key)
            if current is None or (
                item.required,
                item.expected_utility,
                item.confidence,
            ) > (
                current.required,
                current.expected_utility,
                current.confidence,
            ):
                if current:
                    rejected.append(
                        ContextRejection(
                            current.source_type, current.source_id, "duplicate"
                        )
                    )
                by_content[key] = item
            else:
                rejected.append(
                    ContextRejection(item.source_type, item.source_id, "duplicate")
                )

        candidates = list(by_content.values())
        by_id = {item.source_id: item for item in discovered}
        expanded = {item.source_id: item for item in candidates}
        for item in tuple(candidates):
            for dependency_id in item.dependencies:
                dependency = by_id.get(dependency_id)
                if dependency is None:
                    rejected.append(
                        ContextRejection(
                            item.source_type,
                            item.source_id,
                            f"missing_dependency:{dependency_id}",
                        )
                    )
                    expanded.pop(item.source_id, None)
                    break
                expanded[dependency_id] = ContextCandidate(
                    **{
                        **dependency.__dict__,
                        "required": True,
                        "reason": f"dependency_of:{item.source_id}",
                    }
                )

        priced = [self._price(item, request.task) for item in expanded.values()]
        required = sorted(
            (item for item in priced if item.required),
            key=lambda item: (item.source_type, item.source_id),
        )
        required_tokens = sum(item.tokens for item in required)
        if required_tokens > available:
            raise ValueError("Required context exceeds the hard token budget")
        optional = sorted(
            (item for item in priced if not item.required),
            key=lambda item: (
                item.roi,
                item.expected_utility,
                -item.tokens,
                item.source_id,
            ),
            reverse=True,
        )
        selected = list(required)
        selected_tokens = required_tokens
        for item in optional:
            if item.tokens > available - selected_tokens:
                rejected.append(
                    ContextRejection(item.source_type, item.source_id, "token_budget")
                )
                continue
            selected.append(item)
            selected_tokens += item.tokens

        task_id = self.db.create_task(
            objective=request.task,
            scope=request.scope,
            token_budget=request.token_budget,
        )
        self.db.record_context(
            task_id,
            (
                {
                    "source_type": block.source_type,
                    "source_id": block.source_id,
                    "tokens": block.tokens,
                    "utility": block.expected_utility,
                    "roi": block.roi,
                }
                for block in selected
            ),
            selected_tokens,
        )
        return ContextBundle(
            task_id=task_id,
            task=request.task,
            scope=request.scope,
            token_budget=request.token_budget,
            task_tokens=task_tokens,
            selected_tokens=selected_tokens,
            blocks=selected,
            rejected=tuple(rejected),
            pipeline=PIPELINE,
        )

    @staticmethod
    def _price(item: ContextCandidate, task: str) -> ContextBlock:
        content = "\n".join(line.rstrip() for line in item.content.strip().splitlines())
        tokens = estimate_tokens(content)
        relevance = lexical_relevance(task, f"{item.label} {content}")
        utility = max(
            item.expected_utility,
            relevance * item.confidence if item.required else 0,
        )
        return ContextBlock(
            source_type=item.source_type,
            source_id=item.source_id,
            label=item.label,
            content=content,
            tokens=tokens,
            relevance_score=relevance,
            confidence=item.confidence,
            expected_utility=utility,
            required=item.required,
            reason_selected=item.reason,
            roi=token_roi(utility, tokens),
            dependencies=item.dependencies,
        )

    def _memory_candidates(
        self, task: str, scope: str, token_budget: int
    ) -> list[ContextCandidate]:
        result = self.retriever.retrieve(
            RetrievalRequest(
                task=task,
                query=task,
                scope=scope,
                token_budget=token_budget,
                target_memories=24,
            )
        )
        return [
            ContextCandidate(
                source_type="memory",
                source_id=ranked.memory.id,
                label=f"{ranked.memory.type.value} memory",
                content=ranked.memory.content,
                confidence=ranked.memory.confidence,
                expected_utility=ranked.score,
                reason=ranked.explanation,
            )
            for ranked in result.selected
        ]

    def _skill_candidates(self, task: str) -> list[ContextCandidate]:
        candidates = []
        for row in self.db.active_skills():
            tags = " ".join(json.loads(row["tags_json"]))
            relevance = lexical_relevance(
                task, f"{row['name']} {row['description']} {tags}"
            )
            historical = (
                row["success_count"] / row["use_count"] if row["use_count"] else 0.5
            )
            utility = context_utility(
                relevance=relevance,
                confidence=0.85,
                importance=0.7,
                recency=1.0,
                historical_success=historical,
            )
            candidates.append(
                ContextCandidate(
                    source_type="skill",
                    source_id=row["id"],
                    label=f"{row['name']}@{row['version']}",
                    content=row["instructions"],
                    confidence=0.85,
                    expected_utility=utility,
                    reason=(
                        f"relevance={relevance:.2f}, "
                        f"historical_success={historical:.2f}"
                    ),
                )
            )
        return candidates
