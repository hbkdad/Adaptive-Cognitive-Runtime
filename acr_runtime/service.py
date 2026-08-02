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
from .memory import (
    MemoryCreate,
    MemoryStatus,
    MemoryType,
    Sensitivity,
    SourceFreshness,
    SourceClass,
)
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
    NegativeProcedureAssessment,
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
from .skill_coevolution import MemorySkillCoevolution, SkillTrust
from .skill_validator import SkillValidationRun, SkillValidator
from .external_skill_importer import (
    ExternalSkillImporter,
    ExternalSkillImportResult,
)
from .coding_experiment import (
    AutonomousCodingExperiment,
    CodingExperimentRequest,
    CodingExperimentRun,
)
from .project_state import (
    ProjectCreate,
    ProjectItemCreate,
    ProjectItemUpdate,
    ProjectStateManager,
)
from .procedure_detector import (
    EmergentProcedureDetector,
    ProcedureDetectionRequest,
    ProcedureDetectionRun,
)
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
from .agent_factory import AgentFactory, AgentFactoryPlan, AgentFactoryRequest
from .topology_learning import (
    TopologyLearner,
    TopologyOutcome,
    TopologyOutcomeCreate,
    TopologyRecommendation,
    TopologyRecipe,
)
from .hierarchical_planner import (
    HierarchicalPlan,
    HierarchicalPlanner,
    PlanRevision,
    PlanSnapshot,
    PlanWorkHint,
    PlanningRequest,
)
from .evaluation import EvaluationCase, EvaluationRun, EvaluationStore, Judge
from .reflection import ReflectionEngine, ReflectionRequest, ReflectionRun
from .active_learning import (
    ActiveLearningAssessment,
    ActiveLearningEngine,
    ActiveLearningRequest,
)
from .task_similarity import (
    TaskFeatureProfile,
    TaskSimilarityEngine,
    TaskSimilarityResult,
)
from .replay import (
    ReplayAdapter,
    ReplayCase,
    ReplayCaseCreate,
    ReplayEngine,
    ReplayRequest,
    ReplayRun,
)
from .synthetic_benchmark import (
    SyntheticBenchmarkController,
    SyntheticBenchmarkCreate,
    SyntheticBenchmarkReviewCreate,
)
from .learning_controller import (
    LearningController,
    LearningReadinessPlan,
    LearningRequest,
    LearningRun,
)
from .model_router import (
    ModelOutcome,
    ModelProfile,
    ModelRoute,
    ModelRouter,
    RouteAttempt,
    RouteRequest,
)
from .local_model_router import LocalModelRouter, LocalRouteRequest
from .multi_model import (
    BaselineWorkflowOutcome,
    MultiModelCoordinator,
    MultiModelWorkflow,
    MultiModelWorkflowRequest,
)
from .tool_registry import ToolRegistry
from .tool_router import ToolRouter
from .plugin_system import PluginRegistry
from .failure_recovery import FailureRecovery
from .audit_viewer import AuditViewer
from .performance_profiler import PerformanceProfiler, profile_operation
from .permissions import PermissionController
from .content_security import ContentSecurityController
from .secret_management import SecretManager
from .privacy import PrivacyEngine
from .regressions import RegressionDetector
from .skill_benchmark import SkillBenchmarkController
from .experiments import ExperimentController
from .code_index import (
    CodebaseIndexer,
    CodeContextRequest,
    CodeContextResult,
    CodeIndexResult,
    IndexPolicy,
    StructuralCodeRetriever,
)
from .code_slicer import (
    PythonCodeSlicer,
    PythonSliceRequest,
    PythonSliceResult,
)
from .document_context import (
    DocumentContextEngine,
    DocumentContextRequest,
    DocumentIndexRequest,
)
from .decision_memory import DecisionCreate, DecisionMemory
from .knowledge_conflict import KnowledgeConflictEngine
from .knowledge_decay import KnowledgeDecayAssessment, KnowledgeDecayPolicy
from .confidence_calibration import (
    CalibrationDomain,
    CalibrationReport,
    ConfidenceCalibration,
    ConfidenceInterpretation,
)
from .resource_governor import ResourceBudget, ResourceGovernor
from .cache import SafeCache
from .deduplication import DeduplicationEngine, DeduplicationRun
from .autonomous_improvement import (
    AutonomousImprovementLoop,
    ImprovementPolicyRegistry,
)
from .meta_context import MetaContextEngine
from .utility_governance import UtilityGovernor, UtilitySnapshot
from .cost_accounting import CostAccounting
from .token_waste import TokenWasteAnalyzer
from .tool_exposure import ToolExposureEngine
from .reasoning_depth import ReasoningDepthEngine
from .parallel_research import ParallelResearchEngine
from .evidence_graph import EvidenceGraph
from .explainability import RuntimeExplainability
from .human_override import (
    HumanOverride,
    HumanOverrideController,
    HumanOverrideRequest,
)
from .safe_mode import SafeModeController


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
        self.safe_mode = SafeModeController(self.db.connection)
        self.human_overrides = HumanOverrideController(self.db.connection)
        self.improvement_policies = ImprovementPolicyRegistry(
            self.db.connection,
            mutation_guard=self.safe_mode.assert_allowed,
        )
        self.improvement_policies.bootstrap()
        self.improvements = AutonomousImprovementLoop(
            self.db.connection,
            self.improvement_policies,
            mutation_guard=self.safe_mode.assert_allowed,
        )
        self.meta_context = MetaContextEngine(self.db.connection)
        self.utility = UtilityGovernor(self.db.connection)
        self.costs = CostAccounting(self.db.connection)
        self.token_waste = TokenWasteAnalyzer(self.db.connection)
        self.reasoning_depth = ReasoningDepthEngine(self.db.connection)
        self.cache = SafeCache(self.db.connection)
        self.deduplication = DeduplicationEngine(self.db.connection)
        self.codebase_indexer = CodebaseIndexer(self.db.connection)
        self.code_context = StructuralCodeRetriever(self.db.connection)
        self.python_code_slicer = PythonCodeSlicer(self.db.connection)
        self.skill_packages = SkillPackageLoader()
        self.skill_registry = SkillRegistry(
            self.db.connection, loader=self.skill_packages
        )
        self.skill_router = SkillRouter(
            self.db.connection,
            self.skill_registry,
            config_provider=(
                lambda _scope: self.improvement_policies.routing_config()
            ),
            override_provider=self._skill_overrides,
        )
        self.content_security = ContentSecurityController(self.db.connection)
        self.parallel_research = ParallelResearchEngine(
            self.db.connection, self.content_security
        )
        self.evidence_graph = EvidenceGraph(self.db.connection)
        self.explainability = RuntimeExplainability(self.db.connection)
        self.document_context = DocumentContextEngine(
            self.db.connection, security=self.content_security
        )
        self.compiler = ContextCompiler(
            self.db,
            skill_router=self.skill_router,
            security=self.content_security,
            cache=self.cache,
            policy_registry=self.improvement_policies,
        )
        self.attributor = ContextAttributor()
        self.knowledge_decay = KnowledgeDecayPolicy()
        self.retriever = HybridMemoryRetriever(
            self.db.memories,
            cache=self.cache,
            decay_policy=self.knowledge_decay,
            config_provider=(
                lambda _scope: self.improvement_policies.retrieval_config()
            ),
        )
        self.decisions = DecisionMemory(self.db.memories, self.retriever)
        self.conflicts = KnowledgeConflictEngine(self.db.memories)
        self.memory = TemporalMemory(self.db.memories)
        self.write_audit = SQLiteWriteDecisionAudit(self.db.connection)
        self.writer = MemoryWriteController(
            self.db.memories,
            self.write_audit,
            security=self.content_security,
        )
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
            mutation_guard=self.safe_mode.assert_allowed,
        )
        self.skill_coevolution = MemorySkillCoevolution(self.db.connection)
        self.skill_validator = SkillValidator(
            self.db.connection,
            self.skill_registry,
            loader=self.skill_packages,
        )
        self.coding_experiment = AutonomousCodingExperiment(
            self.settings.state_dir / "coding-experiments",
            mutation_guard=self.safe_mode.assert_allowed,
        )
        self.projects = ProjectStateManager(
            self.db.connection,
            mutation_guard=self.safe_mode.assert_allowed,
        )
        self.procedure_detector = EmergentProcedureDetector(
            self.db.connection,
            mutation_guard=self.safe_mode.assert_allowed,
        )
        self.skill_evolution = SkillEvolutionEngine(
            self.db.connection,
            self.skill_registry,
            self.skill_validator,
            self.settings.skills_dir,
            loader=self.skill_packages,
            mutation_guard=self.safe_mode.assert_allowed,
        )
        self.skill_merger = SkillMerger(
            self.db.connection,
            self.skill_registry,
        )
        self.skill_genome = SkillGenomeExperiment(
            self.db.connection,
            self.skill_registry,
            loader=self.skill_packages,
            mutation_guard=self.safe_mode.assert_allowed,
        )
        self.agent_specs = AgentSpecRegistry(
            self.db.connection,
            self.skill_registry,
            loader=self.skill_packages,
        )
        self.agent_factory = AgentFactory(
            self.db.connection,
            self.agent_specs,
            max_agents_provider=self._agent_limit,
            mutation_guard=self.safe_mode.assert_allowed,
        )
        self.topology_learner = TopologyLearner(
            self.db.connection,
            self.agent_factory,
        )
        self.hierarchical_planner = HierarchicalPlanner(
            self.db.connection,
            self.skill_router,
            self.skill_registry,
            self.agent_factory,
            self.topology_learner,
        )
        self.evaluations = EvaluationStore(self.db.connection)
        self.calibration = ConfidenceCalibration(self.db.connection)
        self.resources = ResourceGovernor(self.db.connection)
        self.reflections = ReflectionEngine(self.db.connection)
        self.active_learning = ActiveLearningEngine(
            self.db.connection,
            mutation_guard=self.safe_mode.assert_allowed,
        )
        self.task_similarity = TaskSimilarityEngine(
            self.db.connection,
            mutation_guard=self.safe_mode.assert_allowed,
        )
        self.replay = ReplayEngine(
            self.db.connection,
            mutation_guard=self.safe_mode.assert_allowed,
        )
        self.synthetic_benchmarks = SyntheticBenchmarkController(
            self.db.connection,
            mutation_guard=self.safe_mode.assert_allowed,
        )
        self.learning = LearningController(
            self.db.connection,
            self.evaluations,
            self.attributor,
            self.experiences,
            self.skill_generator,
        )
        self.model_router = ModelRouter(
            self.db.connection,
            override_provider=self._forced_model,
        )
        self.multi_model = MultiModelCoordinator(
            self.db.connection, self.model_router
        )
        self.privacy = PrivacyEngine(
            self.db.connection,
            mutation_guard=self.safe_mode.assert_allowed,
        )
        self.experiments = ExperimentController(self.db.connection)
        self.regressions = RegressionDetector(self.db.connection)
        self.skill_benchmarks = SkillBenchmarkController(self.db.connection)
        self.local_model_router = LocalModelRouter(
            self.db.connection, self.model_router, self.privacy
        )
        self.tools = ToolRegistry(self.db.connection)
        self.permissions = PermissionController(
            self.db.connection,
            self.content_security,
            safe_mode_provider=self.safe_mode.enabled,
        )
        self.secrets = SecretManager(self.db.connection, self.permissions)
        self.tool_router = ToolRouter(
            self.db.connection, self.tools, self.permissions
        )
        self.plugins = PluginRegistry(
            self.db.connection, self.tools, self.tool_router
        )
        self.recovery = FailureRecovery(self.db.connection)
        self.audit = AuditViewer(self.db.connection)
        self.performance = PerformanceProfiler(self.db.connection)
        self.tool_exposure = ToolExposureEngine(
            self.db.connection,
            self.tools,
            self.tool_router,
            self.agent_specs,
            self.permissions,
        )

    def route_local_model(self, request: LocalRouteRequest) -> ModelRoute:
        return self.local_model_router.route(request)

    def register_model(self, profile: ModelProfile) -> ModelProfile:
        return self.model_router.register(profile)

    def record_model_outcome(self, outcome: ModelOutcome) -> str:
        return self.model_router.record_outcome(outcome)

    def route_model(self, request: RouteRequest) -> ModelRoute:
        return self.model_router.route(request)

    def record_model_attempt(
        self, route_id: str, attempt: RouteAttempt
    ) -> ModelRoute:
        return self.model_router.record_attempt(route_id, attempt)

    def model_route(self, route_id: str) -> ModelRoute:
        return self.model_router.get(route_id)

    def plan_multi_model(
        self, request: MultiModelWorkflowRequest
    ) -> MultiModelWorkflow:
        return self.multi_model.plan(request)

    def record_multi_model_outcome(
        self, workflow_id: str, baseline: BaselineWorkflowOutcome
    ) -> dict[str, object]:
        return self.multi_model.record_outcome(workflow_id, baseline)

    def close(self) -> None:
        self.db.close()

    def _forced_model(self, task_class: str) -> str | None:
        override = self.human_overrides.effective(
            "force_model", task_class
        )
        return None if override is None else override.target_id

    def _skill_overrides(
        self, task_class: str
    ) -> tuple[frozenset[str], frozenset[str]]:
        forced = self.human_overrides.effective(
            "force_skill", task_class
        )
        return (
            frozenset(
                () if forced is None or forced.target_id is None
                else (forced.target_id,)
            ),
            self.human_overrides.targets("disable_skill", task_class),
        )

    def _agent_limit(self, task_class: str) -> int | None:
        override = self.human_overrides.effective(
            "limit_agents", task_class
        )
        return (
            None
            if override is None
            else int(override.value["max_agents"])
        )

    def apply_human_override(
        self, request: HumanOverrideRequest
    ) -> HumanOverride:
        if request.action in {"pin_memory", "block_memory"}:
            if self.db.memories.get(str(request.target_id)) is None:
                raise KeyError(request.target_id)
        elif request.action == "force_model":
            model = self.db.connection.execute(
                "SELECT active FROM model_profiles WHERE id=?",
                (request.target_id,),
            ).fetchone()
            if model is None or not bool(model["active"]):
                raise ValueError("forced model must be registered and active")
        elif request.action in {"force_skill", "disable_skill"}:
            skill = self.skill_registry.inspect(str(request.target_id))
            if skill["id"] != request.target_id:
                raise ValueError("skill overrides require the exact skill ID")
            if (
                request.action == "force_skill"
                and (
                    skill["status"] != "active"
                    or skill["lifecycle_status"] != "active"
                )
            ):
                raise ValueError("forced skill must be active")

        override = self.human_overrides.begin(request)
        if request.action in {
            "force_model",
            "force_skill",
            "limit_agents",
            "disable_learning",
            "freeze_architecture",
        }:
            return override
        try:
            details: dict[str, object]
            if request.action == "pin_memory":
                record = self.lifecycle.pin(
                    str(request.target_id),
                    reason=f"human_override:{override.id}",
                )
                details = {"memory_id": record.id, "pinned": record.pinned}
            elif request.action == "block_memory":
                record = self.lifecycle.archive(
                    str(request.target_id), force=True
                )
                details = {
                    "memory_id": record.id,
                    "lifecycle_state": record.lifecycle_state.value,
                }
            elif request.action == "disable_skill":
                skill = self.skill_registry.quarantine(
                    str(request.target_id)
                )
                details = {
                    "skill_id": skill["id"],
                    "lifecycle_status": skill["lifecycle_status"],
                }
            elif request.value["version_kind"] == "skill_evolution":
                run = self.rollback_skill_evolution(
                    str(request.target_id), reason=request.reason
                )
                details = {
                    "version_kind": "skill_evolution",
                    "run_id": run.id,
                    "status": run.status,
                    "restored_skill_id": run.source_skill_id,
                }
            else:
                version = self.improvement_policies.rollback(
                    str(request.target_id),
                    expected_head_id=str(
                        request.value["expected_head_id"]
                    ),
                )
                details = {
                    "version_kind": "improvement_policy",
                    "target": request.target_id,
                    "restored_version_id": version.id,
                }
        except Exception as error:
            self.human_overrides.mark(
                override.id,
                "failed",
                details={"error_type": type(error).__name__},
            )
            raise
        return self.human_overrides.mark(
            override.id, "applied", details=details
        )

    def revoke_human_override(
        self, override_id: str, *, actor_id: str, reason: str
    ) -> HumanOverride:
        return self.human_overrides.revoke(
            override_id, actor_id=actor_id, reason=reason
        )

    def assert_architecture_mutable(self, scope: str = "global") -> None:
        if self.human_overrides.effective(
            "freeze_architecture", scope
        ) is not None:
            raise PermissionError(
                "architecture is frozen by an active human override"
            )

    def record_decision(self, request: DecisionCreate):
        return self.decisions.record(request)

    def utility_snapshot(
        self, kind: str, external_id: str
    ) -> UtilitySnapshot:
        with self.db.connection:
            return self.utility.snapshot(kind, external_id)

    def utility_inventory(
        self, *, kind: str | None = None
    ) -> list[dict[str, object]]:
        with self.db.connection:
            rows = self.utility.inventory()
        return [
            row for row in rows
            if kind is None or row["asset_kind"] == kind
        ]

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
        source_class: str | None = None,
        subject: str | None = None,
        structured_payload_json: str = "{}",
        status: str = "confirmed",
        valid_from: str | None = None,
        valid_until: str | None = None,
        supersedes: str | None = None,
        sensitivity: str = "internal",
        observed_at: str | None = None,
        source_freshness: str = "unknown",
        expected_half_life_days: float | None = None,
        requires_refresh: bool = False,
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
                source_class=(
                    SourceClass(source_class) if source_class is not None else None
                ),
                evidence=tuple(evidence),
                status=MemoryStatus(normalized_status),
                valid_from=valid_from,
                valid_until=valid_until,
                supersedes=supersedes,
                sensitivity=Sensitivity(sensitivity),
                observed_at=observed_at,
                source_freshness=SourceFreshness(source_freshness),
                expected_half_life_days=expected_half_life_days,
                requires_refresh=requires_refresh,
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

    def import_external_skill(
        self,
        source: str | Path,
        *,
        source_label: str = "local",
        importer: ExternalSkillImporter | None = None,
    ) -> ExternalSkillImportResult:
        selected = importer or ExternalSkillImporter(
            self.skill_registry,
            self.settings.skills_dir,
            loader=self.skill_packages,
        )
        return selected.import_local(source, source_label=source_label)

    def run_coding_experiment(
        self, request: CodingExperimentRequest
    ) -> CodingExperimentRun:
        return self.coding_experiment.run(request)

    def coding_experiment_report(self, run_id: str) -> CodingExperimentRun:
        return self.coding_experiment.load(run_id)

    def create_project_state(
        self, spec: ProjectCreate, *, actor: str
    ) -> dict[str, object]:
        return self.projects.create(spec, actor=actor)

    def add_project_item(
        self,
        project_key: str,
        spec: ProjectItemCreate,
        *,
        expected_project_revision: int,
        actor: str,
    ) -> dict[str, object]:
        return self.projects.add_item(
            project_key,
            spec,
            expected_project_revision=expected_project_revision,
            actor=actor,
        )

    def update_project_item(
        self,
        project_key: str,
        item_id: str,
        spec: ProjectItemUpdate,
        *,
        expected_project_revision: int,
        actor: str,
    ) -> dict[str, object]:
        return self.projects.update_item(
            project_key,
            item_id,
            spec,
            expected_project_revision=expected_project_revision,
            actor=actor,
        )

    def detect_procedures(
        self, request: ProcedureDetectionRequest
    ) -> ProcedureDetectionRun:
        return self.procedure_detector.detect(request)

    def procedure_detection_report(
        self, run_id: str
    ) -> ProcedureDetectionRun:
        return self.procedure_detector.load(run_id)

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
        skill = self.skill_registry.inspect(reference)
        if self.human_overrides.effective(
            "disable_skill", "global", target_id=str(skill["id"])
        ) is not None:
            raise PermissionError("skill is disabled by a human override")
        return self.skill_registry.activate(reference)

    def quarantine_skill(self, reference: str) -> dict[str, object]:
        return self.skill_registry.quarantine(reference)

    def retire_skill(self, reference: str) -> dict[str, object]:
        return self.skill_registry.retire(reference)

    def skill_history(self, reference: str) -> list[dict[str, object]]:
        return self.skill_registry.history(reference)

    def skill_evidence(self, reference: str) -> dict[str, object]:
        skill = self.skill_registry.inspect(reference)
        return self.skill_coevolution.report(str(skill["id"]))

    def reconcile_skill_evidence(self, reference: str) -> SkillTrust:
        skill = self.skill_registry.inspect(reference)
        return self.skill_coevolution.refresh(str(skill["id"]))

    def invalidate_skill_support(
        self,
        support_link_id: str,
        *,
        reason: str,
        actor: str,
    ) -> SkillTrust:
        return self.skill_coevolution.invalidate(
            support_link_id, reason=reason, actor=actor
        )

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

    def scan_duplicates(
        self,
        *,
        kinds: Iterable[str],
        scope: str,
        limit: int = 100,
    ) -> DeduplicationRun:
        return self.deduplication.scan_database(
            kinds=kinds, scope=scope, limit=limit
        )

    def deduplication_report(
        self, run_id: str, *, scope: str
    ) -> DeduplicationRun:
        return self.deduplication.load(run_id, scope=scope)

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

    def plan_agent_factory(
        self, request: AgentFactoryRequest
    ) -> AgentFactoryPlan:
        return self.agent_factory.plan(request)

    def agent_factory_plan(self, plan_id: str) -> AgentFactoryPlan:
        return self.agent_factory.load(plan_id)

    def record_topology_outcome(
        self, create: TopologyOutcomeCreate
    ) -> TopologyOutcome:
        return self.topology_learner.record(create)

    def recommend_topology(
        self, request: AgentFactoryRequest
    ) -> TopologyRecommendation:
        return self.topology_learner.recommend(request)

    def topology_outcome(self, outcome_id: str) -> TopologyOutcome:
        return self.topology_learner.outcome(outcome_id)

    def topology_recipes(
        self, *, task_class: str | None = None
    ) -> tuple[TopologyRecipe, ...]:
        return self.topology_learner.recipes(task_class=task_class)

    def create_hierarchical_plan(
        self, request: PlanningRequest
    ) -> HierarchicalPlan:
        self.assert_architecture_mutable(request.task_class)
        return self.hierarchical_planner.create(request)

    def hierarchical_plan(
        self, plan_id: str, *, revision: int | None = None
    ) -> HierarchicalPlan:
        return self.hierarchical_planner.load(plan_id, revision=revision)

    def revise_hierarchical_plan(
        self,
        plan_id: str,
        *,
        expected_revision: int,
        snapshot: PlanSnapshot,
        reason: str,
    ) -> HierarchicalPlan:
        current = self.hierarchical_planner.load(plan_id)
        self.assert_architecture_mutable(current.request.task_class)
        return self.hierarchical_planner.revise(
            plan_id,
            expected_revision=expected_revision,
            snapshot=snapshot,
            reason=reason,
            change_kind="edit",
        )

    def transition_hierarchical_plan(
        self,
        plan_id: str,
        *,
        expected_revision: int,
        phase: str,
        reason: str,
    ) -> HierarchicalPlan:
        current = self.hierarchical_planner.load(plan_id)
        self.assert_architecture_mutable(current.request.task_class)
        return self.hierarchical_planner.transition(
            plan_id,
            expected_revision=expected_revision,
            phase=phase,
            reason=reason,
        )

    def refine_hierarchical_plan(
        self,
        plan_id: str,
        *,
        expected_revision: int,
        target_node_id: str,
        children: tuple[PlanWorkHint, ...],
        reason: str,
    ) -> HierarchicalPlan:
        current = self.hierarchical_planner.load(plan_id)
        self.assert_architecture_mutable(current.request.task_class)
        return self.hierarchical_planner.refine(
            plan_id,
            expected_revision=expected_revision,
            target_node_id=target_node_id,
            children=children,
            reason=reason,
        )

    def hierarchical_plan_history(
        self, plan_id: str
    ) -> tuple[PlanRevision, ...]:
        return self.hierarchical_planner.history(plan_id)

    def evaluate(
        self,
        case: EvaluationCase,
        judges: tuple[Judge, ...] | None = None,
        *,
        task_id: str | None = None,
        pass_threshold: float = 0.7,
        predicted_confidence: float | None = None,
    ) -> EvaluationRun:
        return self.evaluations.evaluate(
            case,
            judges,
            task_id=task_id,
            pass_threshold=pass_threshold,
            predicted_confidence=predicted_confidence,
        )

    def evaluation(self, run_id: str) -> EvaluationRun:
        return self.evaluations.get(run_id)

    def calibration_report(
        self,
        domain: CalibrationDomain,
        *,
        group_key: str | None = None,
        bins: int = 10,
    ) -> CalibrationReport:
        return self.calibration.report(
            domain, group_key=group_key, bins=bins
        )

    def interpret_confidence(
        self,
        domain: CalibrationDomain,
        confidence: float,
        *,
        group_key: str | None = None,
        bins: int = 10,
        minimum_samples: int = 20,
    ) -> ConfidenceInterpretation:
        return self.calibration.interpret(
            domain,
            confidence,
            group_key=group_key,
            bins=bins,
            minimum_samples=minimum_samples,
        )

    def create_resource_budget(
        self, budget: ResourceBudget
    ) -> ResourceBudget:
        return self.resources.create_budget(budget)

    def resource_status(self, task_id: str) -> dict[str, object]:
        return self.resources.status(task_id)

    def reflect(self, request: ReflectionRequest) -> ReflectionRun:
        return self.reflections.reflect(request)

    def reflection(self, run_id: str) -> ReflectionRun:
        return self.reflections.get(run_id)

    def assess_active_learning(
        self, request: ActiveLearningRequest
    ) -> ActiveLearningAssessment:
        self.safe_mode.assert_allowed("autonomous_optimization")
        if self.human_overrides.effective(
            "disable_learning", request.task_class
        ) is not None:
            raise PermissionError("learning is disabled by a human override")
        return self.active_learning.assess(request)

    def active_learning_assessment(
        self, assessment_id: str
    ) -> ActiveLearningAssessment:
        return self.active_learning.get(assessment_id)

    def add_task_profile(
        self, profile: TaskFeatureProfile
    ) -> TaskFeatureProfile:
        return self.task_similarity.add_profile(profile)

    def similar_tasks(
        self,
        task_id: str,
        *,
        limit: int = 10,
        minimum_score_micros: int = 1,
    ) -> TaskSimilarityResult:
        return self.task_similarity.similar(
            task_id,
            limit=limit,
            minimum_score_micros=minimum_score_micros,
        )

    def add_replay_case(self, request: ReplayCaseCreate) -> ReplayCase:
        return self.replay.add_case(request)

    def replay_task(
        self, request: ReplayRequest, adapter: ReplayAdapter
    ) -> ReplayRun:
        return self.replay.run(request, adapter)

    def generate_synthetic_benchmark(
        self, request: SyntheticBenchmarkCreate
    ) -> dict[str, object]:
        return self.synthetic_benchmarks.generate(request)

    def review_synthetic_benchmark(
        self, request: SyntheticBenchmarkReviewCreate
    ) -> dict[str, object]:
        return self.synthetic_benchmarks.review(request)

    def learn(self, request: LearningRequest) -> LearningRun:
        self.safe_mode.assert_allowed("autonomous_optimization")
        if self.human_overrides.effective(
            "disable_learning", request.task_class
        ) is not None:
            raise PermissionError("learning is disabled by a human override")
        return self.learning.learn(request)

    def learning_plan(
        self,
        task_id: str,
        *,
        execution_run_id: str | None = None,
    ) -> LearningReadinessPlan:
        return self.learning.plan(
            task_id, execution_run_id=execution_run_id
        )

    def learning_run(self, run_id: str) -> LearningRun:
        return self.learning.get(run_id)

    def compile_context(
        self, task: str, *, scope: str = "global", token_budget: int = 4_000
    ) -> ContextBundle:
        with profile_operation(
            "context_compilation", "context.compile"
        ):
            return self.compiler.compile(
                task, scope=scope, token_budget=token_budget
            )

    def compile_context_request(self, request: ContextRequest) -> ContextBundle:
        with profile_operation(
            "context_compilation", "context.compile_request"
        ):
            return self.compiler.compile_request(request)

    def index_repository(
        self, root: str | Path, *, policy: IndexPolicy | None = None
    ) -> CodeIndexResult:
        return self.codebase_indexer.index(root, policy=policy)

    def retrieve_code_context(
        self, root: str | Path, request: CodeContextRequest
    ) -> CodeContextResult:
        return self.code_context.retrieve(root, request)

    def slice_python_context(
        self, root: str | Path, request: PythonSliceRequest
    ) -> PythonSliceResult:
        return self.python_code_slicer.slice(root, request)

    def index_documents(
        self, root: str | Path, request: DocumentIndexRequest | None = None
    ) -> dict[str, object]:
        return self.document_context.index(root, request)

    def retrieve_document_context(
        self, root: str | Path, request: DocumentContextRequest
    ) -> dict[str, object]:
        return self.document_context.retrieve(root, request)

    def retrieve_memory(self, request: RetrievalRequest) -> RetrievalResult:
        with profile_operation("retrieval_latency", "memory.retrieve"):
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

    def assess_memory_decay(
        self,
        memory_id: str,
        *,
        assessed_at: str | None = None,
    ) -> KnowledgeDecayAssessment:
        record = self.db.memories.get(memory_id)
        if record is None:
            raise KeyError(memory_id)
        return self.knowledge_decay.assess(record, assessed_at=assessed_at)

    def assess_negative_procedures(
        self,
        *,
        scope: str,
        task_class: str,
        limit: int = 50,
    ) -> tuple[NegativeProcedureAssessment, ...]:
        return self.failures.assess_negative_procedures(
            scope=scope,
            task_class=task_class,
            limit=limit,
        )

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
        return {
            **self.db.status_snapshot(),
            "safe_mode": self.safe_mode.status(),
        }

    def skills(self) -> list[dict[str, object]]:
        return self.skill_registry.list()

    def __enter__(self) -> "AdaptiveRuntime":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
