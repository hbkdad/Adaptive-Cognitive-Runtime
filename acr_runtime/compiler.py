from __future__ import annotations

from dataclasses import dataclass

from .db import RuntimeDB
from .economist import TokenEconomist
from .compression import ContextCompressor
from .memory import MemoryReader
from .models import (
    ContextBlock,
    ContextBundle,
    ContextCandidate,
    ContextRejection,
)
from .retrieval import HybridMemoryRetriever, RetrievalRequest
from .skill_registry import SkillRegistry
from .skill_router import SkillRoute, SkillRouter
from .scoring import estimate_tokens, lexical_relevance


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
    task_importance: float = 0.5
    model_context_window: int | None = None
    task_class: str = "general"

    def __post_init__(self) -> None:
        if not self.task.strip():
            raise ValueError("Context task cannot be empty")
        if self.token_budget < 1:
            raise ValueError("token_budget must be positive")
        if not 0 <= self.task_importance <= 1:
            raise ValueError("task_importance must be 0..1")
        if not self.task_class.strip():
            raise ValueError("task_class cannot be empty")


class ContextCompiler:
    def __init__(
        self,
        db: RuntimeDB,
        memory_reader: MemoryReader | None = None,
        *,
        minimum_optional_utility: float = 0.05,
        economist: TokenEconomist | None = None,
        compressor: ContextCompressor | None = None,
        skill_router: SkillRouter | None = None,
    ) -> None:
        self.db = db
        self.memory_reader = memory_reader or db.memories
        self.retriever = HybridMemoryRetriever(self.memory_reader)
        self.minimum_optional_utility = minimum_optional_utility
        self.economist = economist or TokenEconomist()
        self.compressor = compressor or ContextCompressor()
        self.skill_router = skill_router or SkillRouter(
            db.connection, SkillRegistry(db.connection)
        )

    def compile(
        self, task: str, *, scope: str = "global", token_budget: int = 4_000
    ) -> ContextBundle:
        return self.compile_request(
            ContextRequest(task=task, scope=scope, token_budget=token_budget)
        )

    def compile_request(self, request: ContextRequest) -> ContextBundle:
        budget_plan = self.economist.budget(
            request.task,
            requested_input_budget=request.token_budget,
            task_importance=request.task_importance,
            model_context_window=request.model_context_window,
        )
        task_tokens = budget_plan.task_tokens
        available = budget_plan.context_budget
        if task_tokens > budget_plan.effective_input_budget:
            raise ValueError("Task alone exceeds the adaptive input budget")
        skill_route = self.skill_router.route(
            request.task,
            task_class=request.task_class,
            token_budget=available,
        )
        discovered = [
            *self._memory_candidates(request.task, request.scope, available),
            *self._skill_candidates(skill_route),
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

        priced = [
            self._price(
                item,
                request.task,
                task_importance=request.task_importance,
            )
            for item in expanded.values()
        ]
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
        optimized = self.economist.optimize(
            optional, available - selected_tokens
        )
        optimized_ids = {item.source_id for item in optimized}
        selected.extend(optimized)
        selected_tokens += sum(item.tokens for item in optimized)
        for item in optional:
            if item.source_id not in optimized_ids:
                reason = (
                    "token_budget"
                    if item.tokens > available - required_tokens
                    else "dominated_by_optimization"
                )
                rejected.append(
                    ContextRejection(item.source_type, item.source_id, reason)
                )

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
                    "compression_strategy": block.compression_strategy,
                    "original_tokens": block.original_tokens,
                    "exact_preserved": int(block.exact_preserved),
                }
                for block in selected
            ),
            selected_tokens,
        )
        self.db.record_token_budget_plan(
            task_id=task_id,
            plan=budget_plan,
            candidate_count=len(priced),
            selected_count=len(selected),
            expected_utility=sum(
                block.expected_utility for block in selected
            ),
        )
        self.db.record_skill_route(
            task_id,
            skill_route,
            {
                block.source_id
                for block in selected
                if block.source_type == "skill"
            },
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
            model_context_window=budget_plan.model_context_window,
            output_headroom=budget_plan.output_headroom,
            reasoning_headroom=budget_plan.reasoning_headroom,
            effective_input_budget=budget_plan.effective_input_budget,
            complexity=budget_plan.complexity.value,
            skill_route=skill_route.as_dict(),
        )

    def _price(
        self,
        item: ContextCandidate,
        task: str,
        *,
        task_importance: float,
    ) -> ContextBlock:
        compression = self.compressor.compress(item, task)
        content = "\n".join(
            line.rstrip() for line in compression.content.strip().splitlines()
        )
        tokens = estimate_tokens(content)
        relevance = lexical_relevance(task, f"{item.label} {content}")
        utility, roi = self.economist.expected_value(
            relevance=max(relevance, 1.0 if item.required else relevance),
            confidence=item.confidence,
            historical_utility=item.expected_utility,
            task_importance=task_importance,
            token_cost=tokens,
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
            roi=roi,
            dependencies=item.dependencies,
            historical_utility=item.expected_utility,
            task_importance=task_importance,
            compression_strategy=compression.strategy.value,
            original_tokens=compression.original_tokens,
            exact_preserved=compression.exact_preserved,
            artifact_uri=compression.artifact_uri,
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

    def _skill_candidates(self, route: SkillRoute) -> list[ContextCandidate]:
        candidates = []
        for routed in route.selected:
            row = self.db.connection.execute(
                "SELECT * FROM skills WHERE id = ?", (routed.id,)
            ).fetchone()
            if row is None:
                continue
            candidates.append(
                ContextCandidate(
                    source_type="skill",
                    source_id=row["id"],
                    label=f"{row['name']}@{row['version']}",
                    content=row["instructions"],
                    confidence=0.85,
                    expected_utility=routed.expected_benefit,
                    dependencies=routed.dependency_ids,
                    reason=routed.reason,
                )
            )
        return candidates
