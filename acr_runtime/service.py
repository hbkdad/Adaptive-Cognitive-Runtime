from __future__ import annotations

from pathlib import Path
from typing import Iterable

from .compiler import ContextCompiler, ContextRequest
from .attribution import AttributionSignals, ContextAttributor
from .consolidation import (
    ConsolidationPlan,
    MemoryConsolidator,
    SQLiteConsolidationAudit,
)
from .config import Settings
from .db import RuntimeDB
from .memory import MemoryCreate, MemoryStatus, MemoryType
from .lifecycle import (
    LifecyclePlan,
    MemoryLifecycleManager,
    SQLiteLifecycleAudit,
)
from .failure import (
    FailureCreate,
    FailureIntelligence,
    FailureMatch,
    FailureQuery,
    FailureRecord,
)
from .experience import (
    DistillationPlan,
    ExperienceDistiller,
    ExperienceTrace,
    ExperienceTraceCreate,
)
from .models import ContextBundle
from .retrieval import HybridMemoryRetriever, RetrievalRequest, RetrievalResult
from .temporal import TemporalMemory
from .write_controller import (
    CandidateFact,
    MemoryWriteController,
    SQLiteWriteDecisionAudit,
    WriteDecision,
)
from .skill_format import SkillPackage, SkillPackageLoader
from .skill_registry import SkillRegistry
from .skill_router import SkillRoute, SkillRouter
from .skill_generator import SkillGenerationPlan, SkillGenerator
from .skill_validator import SkillValidationRun, SkillValidator
from .skill_evolution import (
    SkillEvolutionEngine,
    SkillEvolutionRun,
    SkillMutation,
)
from .skill_merger import SkillMergeAnalysis, SkillMerger
from .skill_genome import (
    GenomeMutation,
    GenomeParameters,
    GenomeTournament,
    SkillGenome,
    SkillGenomeExperiment,
)
from .agent_spec import AgentSpec, AgentSpecRegistry, StoredAgentSpec


class AdaptiveRuntime:
    """Small public API for the v0.1 memory/context/telemetry loop."""

    def __init__(
        self,
        database: str | Path | None = None,
        *,
        settings: Settings | None = None,
    ) -> None:
        self.settings = settings or Settings.from_env(database=database)
        self.settings.ensure_local_directories()
        self.db = RuntimeDB(self.settings.database)
        self.skill_packages = SkillPackageLoader()
        self.skill_registry = SkillRegistry(
            self.db.connection, loader=self.skill_packages
        )
        self.skill_router = SkillRouter(
            self.db.connection, self.skill_registry
        )
        self.compiler = ContextCompiler(
            self.db, skill_router=self.skill_router
        )
        self.attributor = ContextAttributor()
        self.retriever = HybridMemoryRetriever(self.db.memories)
        self.memory = TemporalMemory(self.db.memories)
        self.write_audit = SQLiteWriteDecisionAudit(self.db.connection)
        self.writer = MemoryWriteController(self.db.memories, self.write_audit)
        self.consolidation_audit = SQLiteConsolidationAudit(self.db.connection)
        self.consolidator = MemoryConsolidator(
            self.db.memories, self.consolidation_audit
        )
        self.lifecycle_audit = SQLiteLifecycleAudit(self.db.connection)
        self.lifecycle = MemoryLifecycleManager(
            self.db.memories, self.lifecycle_audit
        )
        self.failures = FailureIntelligence(
            self.db.connection, self.db.memories
        )
        self.experiences = ExperienceDistiller(
            self.db.connection, self.writer, self.db
        )
        self.skill_generator = SkillGenerator(
            self.db.connection,
            self.skill_registry,
            self.settings.skills_dir,
            loader=self.skill_packages,
        )
        self.skill_validator = SkillValidator(
            self.db.connection,
            self.skill_registry,
            loader=self.skill_packages,
        )
        self.skill_evolution = SkillEvolutionEngine(
            self.db.connection,
            self.skill_registry,
            self.skill_validator,
            self.settings.skills_dir,
            loader=self.skill_packages,
        )
        self.skill_merger = SkillMerger(
            self.db.connection,
            self.skill_registry,
        )
        self.skill_genome = SkillGenomeExperiment(
            self.db.connection,
            self.skill_registry,
            loader=self.skill_packages,
        )
        self.agent_specs = AgentSpecRegistry(
            self.db.connection,
            self.skill_registry,
            loader=self.skill_packages,
        )

    def close(self) -> None:
        self.db.close()

    def remember(
        self,
        kind: str,
        content: str,
        *,
        scope: str = "global",
        confidence: float = 0.8,
        importance: float = 0.5,
        evidence: Iterable[str] = (),
        source: str | None = None,
        source_type: str | None = None,
        source_id: str | None = None,
        subject: str | None = None,
        structured_payload_json: str = "{}",
        status: str = "confirmed",
        valid_from: str | None = None,
        valid_until: str | None = None,
        supersedes: str | None = None,
    ) -> str:
        normalized_status = "confirmed" if status == "active" else status
        record = self.db.memories.create(
            MemoryCreate(
                type=MemoryType(kind),
                content=content,
                scope=scope,
                subject=subject,
                structured_payload_json=structured_payload_json,
                confidence=confidence,
                importance=importance,
                source_type=source_type or ("legacy" if source else None),
                source_id=source_id or source,
                evidence=tuple(evidence),
                status=MemoryStatus(normalized_status),
                valid_from=valid_from,
                valid_until=valid_until,
                supersedes=supersedes,
            )
        )
        return record.id

    def register_skill(
        self,
        name: str,
        instructions: str,
        *,
        version: str = "0.1.0",
        description: str = "",
        tags: Iterable[str] = (),
        trusted: bool = False,
    ) -> str:
        return self.db.add_skill(
            name=name,
            version=version,
            description=description,
            instructions=instructions,
            tags=tags,
            status="active" if trusted else "quarantine",
        )

    def validate_skill_package(self, directory: str | Path) -> SkillPackage:
        return self.skill_packages.load(directory)

    def admit_skill_package(self, directory: str | Path) -> dict[str, object]:
        return self.skill_registry.admit(directory)

    def inspect_skill(self, reference: str) -> dict[str, object]:
        return self.skill_registry.inspect(reference)

    def search_skills(
        self, query: str, *, limit: int = 10
    ) -> dict[str, object]:
        return self.skill_registry.search(query, limit=limit)

    def route_skills(
        self,
        task: str,
        *,
        task_class: str = "general",
        token_budget: int = 4_000,
    ) -> SkillRoute:
        return self.skill_router.route(
            task, task_class=task_class, token_budget=token_budget
        )

    def test_skill(self, reference: str) -> dict[str, object]:
        return self.skill_registry.test(reference)

    def activate_skill(self, reference: str) -> dict[str, object]:
        return self.skill_registry.activate(reference)

    def quarantine_skill(self, reference: str) -> dict[str, object]:
        return self.skill_registry.quarantine(reference)

    def retire_skill(self, reference: str) -> dict[str, object]:
        return self.skill_registry.retire(reference)

    def skill_history(self, reference: str) -> list[dict[str, object]]:
        return self.skill_registry.history(reference)

    def plan_skill_generation(
        self, *, scope: str | None = None
    ) -> SkillGenerationPlan:
        return self.skill_generator.plan(scope=scope)

    def approve_skill_generation(self, run_id: str) -> SkillGenerationPlan:
        return self.skill_generator.approve(run_id)

    def skill_generation(self, run_id: str) -> SkillGenerationPlan:
        return self.skill_generator.load(run_id)

    def validate_skill_candidate(self, reference: str) -> SkillValidationRun:
        return self.skill_validator.validate(reference)

    def skill_validation(self, run_id: str) -> SkillValidationRun:
        return self.skill_validator.load(run_id)

    def promote_skill_validation(self, run_id: str) -> SkillValidationRun:
        return self.skill_validator.promote(run_id)

    def create_skill_evolution(
        self,
        source_reference: str,
        mutation: SkillMutation,
        *,
        version: str | None = None,
    ) -> SkillEvolutionRun:
        self.skill_evolution.validator = self.skill_validator
        return self.skill_evolution.create_candidate(
            source_reference, mutation, version=version
        )

    def compare_skill_evolution(
        self,
        run_id: str,
        *,
        baseline_validation_id: str,
        candidate_validation_id: str,
    ) -> SkillEvolutionRun:
        self.skill_evolution.validator = self.skill_validator
        return self.skill_evolution.compare(
            run_id,
            baseline_validation_id=baseline_validation_id,
            candidate_validation_id=candidate_validation_id,
        )

    def skill_evolution_run(self, run_id: str) -> SkillEvolutionRun:
        return self.skill_evolution.load(run_id)

    def promote_skill_evolution(self, run_id: str) -> SkillEvolutionRun:
        self.skill_evolution.validator = self.skill_validator
        return self.skill_evolution.promote(run_id)

    def rollback_skill_evolution(
        self, run_id: str, *, reason: str
    ) -> SkillEvolutionRun:
        return self.skill_evolution.rollback(run_id, reason=reason)

    def analyze_skill_merges(
        self,
        *,
        reference: str | None = None,
        limit: int = 50,
    ) -> SkillMergeAnalysis:
        return self.skill_merger.analyze(reference=reference, limit=limit)

    def skill_merge_analysis(self, run_id: str) -> SkillMergeAnalysis:
        return self.skill_merger.load(run_id)

    def create_skill_genome(
        self, source_reference: str, parameters: GenomeParameters
    ) -> SkillGenome:
        return self.skill_genome.create_baseline(source_reference, parameters)

    def mutate_skill_genome(
        self, parent_genome_id: str, mutation: GenomeMutation
    ) -> SkillGenome:
        return self.skill_genome.mutate(parent_genome_id, mutation)

    def inspect_skill_genome(self, genome_id: str) -> SkillGenome:
        return self.skill_genome.load_genome(genome_id)

    def run_skill_genome_tournament(
        self,
        baseline_genome_id: str,
        candidate_genome_ids: tuple[str, ...],
    ) -> GenomeTournament:
        return self.skill_genome.run_tournament(
            baseline_genome_id, candidate_genome_ids
        )

    def skill_genome_tournament(self, run_id: str) -> GenomeTournament:
        return self.skill_genome.load_tournament(run_id)

    def define_agent_spec(self, spec: AgentSpec) -> StoredAgentSpec:
        return self.agent_specs.define(spec)

    def inspect_agent_spec(self, agent_id: str) -> StoredAgentSpec:
        return self.agent_specs.inspect(agent_id)

    def list_agent_specs(self) -> tuple[dict[str, object], ...]:
        return self.agent_specs.list()

    def compile_context(
        self, task: str, *, scope: str = "global", token_budget: int = 4_000
    ) -> ContextBundle:
        return self.compiler.compile(task, scope=scope, token_budget=token_budget)

    def compile_context_request(self, request: ContextRequest) -> ContextBundle:
        return self.compiler.compile_request(request)

    def retrieve_memory(self, request: RetrievalRequest) -> RetrievalResult:
        return self.retriever.retrieve(request)

    def consider_memory(self, candidate: CandidateFact) -> WriteDecision:
        return self.writer.consider(candidate)

    def plan_consolidation(
        self, *, scope: str | None = None
    ) -> ConsolidationPlan:
        return self.consolidator.dry_run(scope=scope)

    def approve_consolidation(self, run_id: str) -> ConsolidationPlan:
        return self.consolidator.approve(run_id)

    def plan_memory_gc(self, *, scope: str | None = None) -> LifecyclePlan:
        return self.lifecycle.dry_run(scope=scope)

    def approve_memory_gc(self, run_id: str) -> LifecyclePlan:
        return self.lifecycle.approve(run_id)

    def record_failure(self, candidate: FailureCreate) -> FailureRecord:
        return self.failures.record(candidate)

    def query_failures(
        self, query: FailureQuery
    ) -> tuple[FailureMatch, ...]:
        return self.failures.query(query)

    def resolve_failure(
        self,
        failure_id: str,
        *,
        resolution: str,
        remediation_memory_id: str,
    ) -> FailureRecord:
        return self.failures.resolve(
            failure_id,
            resolution=resolution,
            remediation_memory_id=remediation_memory_id,
        )

    def capture_experience(
        self, trace: ExperienceTraceCreate
    ) -> ExperienceTrace:
        return self.experiences.capture(trace)

    def plan_distillation(self, trace_id: str) -> DistillationPlan:
        return self.experiences.plan(trace_id)

    def approve_distillation(self, run_id: str) -> DistillationPlan:
        return self.experiences.approve(run_id)

    def complete_task(
        self,
        bundle: ContextBundle,
        *,
        success: bool,
        critic_score: float,
        duration_ms: int,
        useful_source_ids: Iterable[str] = (),
        attribution_signals: AttributionSignals | None = None,
        task_class: str = "general",
        model: str | None = None,
        estimated_cost: float = 0,
    ) -> None:
        if not task_class.strip():
            raise ValueError("task_class cannot be empty")
        if estimated_cost < 0:
            raise ValueError("estimated_cost cannot be negative")
        useful_ids = set(useful_source_ids)
        legacy_model_sources = tuple(
            (block.source_type, block.source_id)
            for block in bundle.blocks
            if block.source_id in useful_ids
        )
        if attribution_signals is not None and useful_ids:
            raise ValueError(
                "Use attribution_signals or useful_source_ids, not both"
            )
        signals = attribution_signals or AttributionSignals(
            model_sources=legacy_model_sources
        )
        attributions = self.attributor.attribute(
            bundle,
            signals=signals,
            success=success,
            critic_score=critic_score,
        )
        self.db.complete_task(
            bundle.task_id,
            success=success,
            critic_score=critic_score,
            duration_ms=duration_ms,
            attributions=attributions,
            task_class=task_class,
            model=model,
            estimated_cost=estimated_cost,
        )

    def context_attributions(
        self, task_id: str
    ) -> list[dict[str, object]]:
        return self.db.context_attributions(task_id)

    def telemetry(self) -> dict[str, object]:
        return self.db.telemetry_summary()

    def telemetry_task(self, task_id: str) -> dict[str, object]:
        return self.db.telemetry_task(task_id)

    def telemetry_models(self) -> list[dict[str, object]]:
        return self.db.telemetry_models()

    def telemetry_skills(self) -> list[dict[str, object]]:
        return self.db.telemetry_skills()

    def telemetry_skill_routing(self) -> list[dict[str, object]]:
        return self.db.telemetry_skill_routing()

    def telemetry_memory(self) -> list[dict[str, object]]:
        return self.db.telemetry_memory()

    def telemetry_waste(self) -> list[dict[str, object]]:
        return self.db.telemetry_waste()

    def telemetry_token_economy(self) -> list[dict[str, object]]:
        return self.db.telemetry_token_economy()

    def telemetry_compression(self) -> list[dict[str, object]]:
        return self.db.telemetry_compression()

    def status(self) -> dict[str, object]:
        return self.db.status_snapshot()

    def skills(self) -> list[dict[str, object]]:
        return self.skill_registry.list()

    def __enter__(self) -> "AdaptiveRuntime":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
