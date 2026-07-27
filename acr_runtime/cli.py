from __future__ import annotations

import argparse
import json
from pathlib import Path

from .benchmark import BenchmarkDataset, BenchmarkRunner
from .config import Settings
from .diagnostics import discover_ollama_models, run_doctor
from .execution import PassEvaluator, PassVerifier, SingleStepPlanner, Task, TaskEventBus, TaskRunner
from .failure import FailureCreate, FailurePlanningAdvisor, FailureQuery
from .experience import (
    ExperienceEvent,
    ExperienceEventKind,
    ExperienceTraceCreate,
    MAX_RAW_TRACE_BYTES,
)
from .migrations import MigrationManager
from .memory import MemoryType
from .providers import OllamaProvider, ProviderExecutor
from .retrieval import RetrievalRequest
from .service import AdaptiveRuntime
from .telemetry import TelemetryRecorder
from .write_controller import CandidateFact

MEMORY_TYPES = [
    "semantic",
    "episodic",
    "procedural",
    "failure",
    "decision",
    "preference",
    "environment",
    "temporary",
]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="acr")
    parser.add_argument("--db", help="SQLite database path (overrides ACR_DATABASE)")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("doctor", help="Check the local ACR environment")
    sub.add_parser("status", help="Show runtime and storage status")
    sub.add_parser("migrate", help="Apply pending database migrations explicitly")

    run = sub.add_parser("run", help="Execute a bounded task with local Ollama")
    run.add_argument("task")
    run.add_argument("--model", help="Installed Ollama model name")
    run.add_argument("--max-output-tokens", type=int, default=512)
    run.add_argument("--scope", default="global")
    run.add_argument("--task-class", default="general")
    run.add_argument("--strategy")
    run.add_argument("--environment", default="{}")

    benchmark = sub.add_parser("benchmark", help="Validate or run a benchmark suite")
    benchmark_sub = benchmark.add_subparsers(dest="benchmark_command", required=True)
    benchmark_validate = benchmark_sub.add_parser(
        "validate", help="Validate a versioned JSONL dataset"
    )
    benchmark_validate.add_argument("dataset")
    benchmark_run = benchmark_sub.add_parser("run", help="Run with local Ollama")
    benchmark_run.add_argument("dataset")
    benchmark_run.add_argument("--model", required=True)
    benchmark_run.add_argument("--seed", type=int, default=0)
    benchmark_run.add_argument("--output", help="Optional JSON report path")

    remember = sub.add_parser("remember", help="Store an evidence-backed memory")
    remember.add_argument("kind", choices=MEMORY_TYPES)
    remember.add_argument("content")
    remember.add_argument("--scope", default="global")
    remember.add_argument("--confidence", type=float, default=0.8)
    remember.add_argument("--importance", type=float, default=0.5)
    remember.add_argument("--evidence", action="append", default=[])
    remember.add_argument("--subject")
    remember.add_argument("--valid-from")
    remember.add_argument("--valid-until")
    remember.add_argument("--supersedes")

    memory = sub.add_parser("memory", help="Inspect or write memory")
    memory_sub = memory.add_subparsers(dest="memory_command", required=True)
    memory_sub.add_parser("summary", help="Show memory counts by type and status")
    memory_add = memory_sub.add_parser("add", help="Store an evidence-backed memory")
    memory_add.add_argument(
        "kind", choices=MEMORY_TYPES
    )
    memory_add.add_argument("content")
    memory_add.add_argument("--scope", default="global")
    memory_add.add_argument("--confidence", type=float, default=0.8)
    memory_add.add_argument("--importance", type=float, default=0.5)
    memory_add.add_argument("--evidence", action="append", default=[])
    memory_add.add_argument("--subject")
    memory_add.add_argument("--valid-from")
    memory_add.add_argument("--valid-until")
    memory_add.add_argument("--supersedes")
    memory_retrieve = memory_sub.add_parser(
        "retrieve", help="Run explainable, token-budgeted memory retrieval"
    )
    memory_retrieve.add_argument("query")
    memory_retrieve.add_argument("--task")
    memory_retrieve.add_argument("--scope", default="global")
    memory_retrieve.add_argument("--type", choices=MEMORY_TYPES, action="append")
    memory_retrieve.add_argument("--budget", type=int, default=1_000)
    memory_retrieve.add_argument("--limit", type=int, default=12)
    memory_retrieve.add_argument("--at", help="ISO timestamp for validity filtering")
    memory_current = memory_sub.add_parser(
        "current", help="Resolve the current trusted value for a subject"
    )
    memory_current.add_argument("subject")
    memory_current.add_argument("--scope", default="global")
    memory_at = memory_sub.add_parser(
        "at", help="Resolve the trusted value for a subject at a point in time"
    )
    memory_at.add_argument("subject")
    memory_at.add_argument("timestamp")
    memory_at.add_argument("--scope", default="global")
    memory_history = memory_sub.add_parser(
        "history", help="Show the preserved timeline for a subject"
    )
    memory_history.add_argument("subject")
    memory_history.add_argument("--scope", default="global")
    memory_consider = memory_sub.add_parser(
        "consider", help="Apply governed write policy to a candidate fact"
    )
    memory_consider.add_argument("kind", choices=MEMORY_TYPES)
    memory_consider.add_argument("content")
    memory_consider.add_argument("--scope", default="global")
    memory_consider.add_argument("--subject")
    memory_consider.add_argument("--confidence", type=float, default=0.5)
    memory_consider.add_argument("--importance", type=float, default=0.5)
    memory_consider.add_argument("--usefulness", type=float, default=0.5)
    memory_consider.add_argument("--stability", type=float, default=0.5)
    memory_consider.add_argument("--evidence", action="append", default=[])
    memory_consider.add_argument("--source-type")
    memory_consider.add_argument("--source-id")
    memory_consider.add_argument("--trusted-source", action="store_true")
    memory_consider.add_argument("--temporary", action="store_true")
    memory_consider.add_argument("--privacy-risk", action="store_true")
    memory_consider.add_argument("--security-risk", action="store_true")
    memory_consider.add_argument("--valid-from")
    memory_consider.add_argument("--valid-until")
    memory_sub.add_parser(
        "decisions", help="Show recent content-minimized write decisions"
    ).add_argument("--limit", type=int, default=100)
    memory_consolidate = memory_sub.add_parser(
        "consolidate", help="Plan or explicitly approve memory consolidation"
    )
    consolidation_mode = memory_consolidate.add_mutually_exclusive_group(
        required=True
    )
    consolidation_mode.add_argument("--dry-run", action="store_true")
    consolidation_mode.add_argument("--approve", metavar="RUN_ID")
    memory_consolidate.add_argument("--scope")
    memory_gc = memory_sub.add_parser(
        "gc", help="Plan or explicitly approve conservative lifecycle changes"
    )
    gc_mode = memory_gc.add_mutually_exclusive_group(required=True)
    gc_mode.add_argument("--dry-run", action="store_true")
    gc_mode.add_argument("--approve", metavar="RUN_ID")
    memory_gc.add_argument("--scope")
    memory_pin = memory_sub.add_parser("pin", help="Protect memory from lifecycle GC")
    memory_pin.add_argument("id")
    memory_pin.add_argument("--reason")
    memory_unpin = memory_sub.add_parser(
        "unpin", help="Remove explicit lifecycle protection"
    )
    memory_unpin.add_argument("id")
    memory_archive = memory_sub.add_parser(
        "archive", help="Reversibly archive a memory"
    )
    memory_archive.add_argument("id")
    memory_archive.add_argument("--force", action="store_true")
    memory_restore = memory_sub.add_parser(
        "restore", help="Restore archived memory to active lifecycle"
    )
    memory_restore.add_argument("id")

    failure = sub.add_parser("failure", help="Record and query failure intelligence")
    failure_sub = failure.add_subparsers(dest="failure_command", required=True)
    failure_record = failure_sub.add_parser(
        "record", help="Store or reinforce an evidence-backed failure"
    )
    failure_record.add_argument("--task-class", required=True)
    failure_record.add_argument("--strategy", required=True)
    failure_record.add_argument("--environment", default="{}")
    failure_record.add_argument("--symptom", action="append", required=True)
    failure_record.add_argument("--root-cause")
    failure_record.add_argument("--failed-action", required=True)
    failure_record.add_argument("--error-type")
    failure_record.add_argument("--error-message")
    failure_record.add_argument("--avoidance-rule")
    failure_record.add_argument("--confidence", type=float, default=0.7)
    failure_record.add_argument("--evidence", action="append", required=True)
    failure_record.add_argument("--scope", default="global")
    failure_record.add_argument("--deterministic", action="store_true")
    failure_query = failure_sub.add_parser(
        "query", help="Find analogous failures and weighted planning advice"
    )
    failure_query.add_argument("task")
    failure_query.add_argument("--task-class", default="general")
    failure_query.add_argument("--strategy")
    failure_query.add_argument("--environment", default="{}")
    failure_query.add_argument("--scope", default="global")
    failure_query.add_argument("--limit", type=int, default=5)
    failure_resolve = failure_sub.add_parser(
        "resolve", help="Link a failure to a confirmed remediation memory"
    )
    failure_resolve.add_argument("id")
    failure_resolve.add_argument("--resolution", required=True)
    failure_resolve.add_argument("--remediation-memory", required=True)
    failure_show = failure_sub.add_parser("show", help="Inspect one failure record")
    failure_show.add_argument("id")

    experience = sub.add_parser(
        "experience", help="Capture raw traces and govern distillation"
    )
    experience_sub = experience.add_subparsers(
        dest="experience_command", required=True
    )
    experience_capture = experience_sub.add_parser(
        "capture", help="Store a bounded raw JSON trace outside memory retrieval"
    )
    experience_capture.add_argument("trace_file")
    experience_capture.add_argument("--scope", default="global")
    experience_capture.add_argument("--task-class", required=True)
    experience_capture.add_argument(
        "--outcome",
        choices=("succeeded", "failed", "partial", "cancelled"),
        required=True,
    )
    experience_capture.add_argument("--significance", type=float, required=True)
    experience_capture.add_argument("--task-id")
    experience_distill = experience_sub.add_parser(
        "distill", help="Plan or approve experience distillation"
    )
    distill_mode = experience_distill.add_mutually_exclusive_group(required=True)
    distill_mode.add_argument("--dry-run", metavar="TRACE_ID")
    distill_mode.add_argument("--approve", metavar="RUN_ID")
    experience_show = experience_sub.add_parser(
        "show", help="Inspect a raw trace or distillation plan"
    )
    experience_show.add_argument("id")
    experience_show.add_argument("--plan", action="store_true")

    skills = sub.add_parser("skills", help="Inspect the skill registry")
    skills.add_subparsers(dest="skills_command", required=True).add_parser(
        "list", help="List registered skills"
    )

    agents = sub.add_parser("agents", help="Inspect agent capabilities")
    agents.add_subparsers(dest="agents_command", required=True).add_parser(
        "list", help="List generated agents"
    )

    models = sub.add_parser("models", help="Inspect local model availability")
    models.add_subparsers(dest="models_command", required=True).add_parser(
        "list", help="List available local models"
    )

    compile_cmd = sub.add_parser("compile", help="Compile a token-budgeted context")
    compile_cmd.add_argument("task")
    compile_cmd.add_argument("--scope", default="global")
    compile_cmd.add_argument("--budget", type=int, default=4_000)

    telemetry = sub.add_parser("telemetry", help="Inspect runtime telemetry")
    telemetry_sub = telemetry.add_subparsers(dest="telemetry_command")
    telemetry_sub.add_parser("summary", help="Show aggregate metrics")
    telemetry_task = telemetry_sub.add_parser("task", help="Inspect one task")
    telemetry_task.add_argument("task_id")
    telemetry_sub.add_parser("models", help="Show model metrics")
    telemetry_sub.add_parser("skills", help="Show skill metrics")
    telemetry_sub.add_parser("memory", help="Show memory metrics")
    telemetry_sub.add_parser("waste", help="Show repeatedly unused context")
    telemetry_sub.add_parser(
        "economy", help="Show adaptive token-budget allocations"
    )
    sub.add_parser("demo", help="Run an end-to-end local demonstration")
    return parser


def _demo(runtime: AdaptiveRuntime) -> None:
    runtime.remember(
        "semantic",
        "The demo service uses SQLite with FTS5 and must remain local-first.",
        scope="acr-demo",
        confidence=0.98,
        importance=0.9,
        evidence=["pyproject.toml", "acr_runtime/db.py"],
    )
    runtime.remember(
        "failure",
        "Do not load entire repositories when a focused file or section answers the task.",
        scope="acr-demo",
        confidence=0.95,
        importance=0.95,
        evidence=["context engineering review"],
    )
    runtime.remember(
        "semantic",
        "The unrelated marketing site uses a blue hero and a cloud CMS.",
        scope="other-project",
        confidence=0.8,
        importance=0.3,
    )
    skill_id = runtime.register_skill(
        "sqlite-diagnostics",
        "Check the database schema, verify FTS5, run focused queries, and report evidence.",
        description="Diagnose SQLite and FTS5 storage problems.",
        tags=["sqlite", "database", "fts5", "diagnostics"],
        trusted=True,
    )
    bundle = runtime.compile_context(
        "Diagnose the SQLite FTS5 memory database without loading unrelated project context.",
        scope="acr-demo",
        token_budget=180,
    )
    useful = [block.source_id for block in bundle.blocks]
    runtime.complete_task(
        bundle,
        success=True,
        critic_score=0.94,
        duration_ms=28,
        useful_source_ids=useful,
    )
    print(bundle.render())
    print("\n# Attribution")
    for block in bundle.blocks:
        print(
            f"- {block.label}: {block.tokens} tokens, "
            f"utility={block.utility:.3f}, roi={block.roi:.5f}; {block.reason}"
        )
    print(f"\nTrusted demo skill: {skill_id}")
    print(json.dumps(runtime.telemetry(), indent=2))


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    settings = Settings.from_env(database=Path(args.db) if args.db else None)

    if args.command == "doctor":
        checks = run_doctor(settings)
        for check in checks:
            print(f"{check.status.upper():4}  {check.name}: {check.detail}")
        return 1 if any(check.status == "fail" for check in checks) else 0

    if args.command == "models":
        detail, models = discover_ollama_models(settings.ollama_url)
        print(json.dumps({"detail": detail, "models": models}, indent=2))
        return 0

    if args.command == "agents":
        print(
            json.dumps(
                {
                    "agents": [],
                    "enabled": False,
                    "planned_milestone": "M13 — dynamic agent factory",
                },
                indent=2,
            )
        )
        return 0

    if args.command == "benchmark":
        dataset = BenchmarkDataset.load(args.dataset)
        if args.benchmark_command == "validate":
            print(
                json.dumps(
                    {
                        "name": dataset.name,
                        "version": dataset.version,
                        "cases": len(dataset.cases),
                        "categories": sorted({case.category for case in dataset.cases}),
                    },
                    indent=2,
                )
            )
            return 0
        provider = OllamaProvider(settings.ollama_url)
        report = BenchmarkRunner(provider, model=args.model).run(
            dataset, seed=args.seed
        )
        report_json = json.dumps(report.to_dict(), indent=2)
        if args.output:
            Path(args.output).write_text(report_json + "\n", encoding="utf-8")
        print(report_json)
        return 0 if report.summary["failure_rate"] == 0 else 1

    if args.command == "migrate":
        manager = MigrationManager(settings.database)
        status = manager.status()
        if status.current_version == 0 and not settings.database.exists():
            with AdaptiveRuntime(settings=settings):
                pass
            status = manager.status()
        elif status.pending_versions:
            status = manager.apply_pending()
        print(
            json.dumps(
                {
                    "database": str(settings.database),
                    "current_version": status.current_version,
                    "expected_version": status.expected_version,
                    "pending_versions": status.pending_versions,
                    "backup": (
                        str(manager.last_backup_path)
                        if manager.last_backup_path is not None
                        else None
                    ),
                },
                indent=2,
            )
        )
        return 0

    with AdaptiveRuntime(settings=settings) as runtime:
        if args.command == "run":
            model = args.model or settings.ollama_model
            if not model:
                print(
                    "No model selected. Pass --model or set ACR_OLLAMA_MODEL.",
                )
                return 2
            recorder = TelemetryRecorder(runtime.db)
            event_bus = TaskEventBus()
            event_bus.subscribe(recorder)
            provider = OllamaProvider(
                settings.ollama_url,
                sink=recorder.record_model_call,
            )
            task = Task(
                args.task,
                token_budget=args.max_output_tokens,
                permissions=("local_model",),
                scope=args.scope,
                task_class=args.task_class,
                strategy=args.strategy,
                environment_json=args.environment,
            )
            runner = TaskRunner(
                planner=SingleStepPlanner(),
                executor=ProviderExecutor(provider, model=model),
                verifier=PassVerifier(),
                evaluator=PassEvaluator(),
                event_bus=event_bus,
                planning_advisors=(
                    FailurePlanningAdvisor(runtime.failures),
                ),
            )
            run_result = runner.run(task)
            recorder.record_run(run_result)
            if run_result.result is not None:
                print(run_result.result.content)
            print(
                json.dumps(
                    {
                        "task_id": task.id,
                        "run_id": run_result.id,
                        "state": run_result.state.value,
                        "failure": (
                            run_result.failure.kind
                            if run_result.failure is not None
                            else None
                        ),
                    },
                    indent=2,
                )
            )
            return 0 if run_result.state.value == "completed" else 1

        if args.command == "status":
            print(json.dumps(runtime.status(), indent=2))
        elif args.command == "failure":
            if args.failure_command == "record":
                failure_record = runtime.failures.record(
                    FailureCreate(
                        task_class=args.task_class,
                        strategy_attempted=args.strategy,
                        environment_json=args.environment,
                        symptoms=tuple(args.symptom),
                        root_cause=args.root_cause,
                        failed_action=args.failed_action,
                        error_type=args.error_type,
                        error_message=args.error_message,
                        avoidance_rule=args.avoidance_rule,
                        confidence=args.confidence,
                        evidence=tuple(args.evidence),
                        scope=args.scope,
                        deterministic=args.deterministic,
                    )
                )
                print(
                    json.dumps(
                        {
                            "id": failure_record.id,
                            "memory_id": failure_record.memory_id,
                            "status": failure_record.status,
                            "occurrence_count": failure_record.occurrence_count,
                            "confidence": failure_record.confidence,
                            "deterministic": failure_record.deterministic,
                        },
                        indent=2,
                    )
                )
            elif args.failure_command == "query":
                matches = runtime.failures.query(
                    FailureQuery(
                        task=args.task,
                        task_class=args.task_class,
                        strategy=args.strategy,
                        environment_json=args.environment,
                        scope=args.scope,
                        limit=args.limit,
                    )
                )
                print(
                    json.dumps(
                        [
                            {
                                "failure_id": match.failure.id,
                                "memory_id": match.failure.memory_id,
                                "status": match.failure.status,
                                "occurrence_count": match.failure.occurrence_count,
                                "analogy_score": match.analogy_score,
                                "avoidance_weight": match.avoidance_weight,
                                "repetition_weight": match.repetition_weight,
                                "absolute_prohibition": match.absolute_prohibition,
                                "explanation": match.explanation,
                                "avoidance_rule": match.failure.avoidance_rule,
                                "remediation_memory_id": (
                                    match.failure.remediation_memory_id
                                ),
                            }
                            for match in matches
                        ],
                        indent=2,
                    )
                )
            elif args.failure_command == "resolve":
                failure_record = runtime.failures.resolve(
                    args.id,
                    resolution=args.resolution,
                    remediation_memory_id=args.remediation_memory,
                )
                print(
                    json.dumps(
                        {
                            "id": failure_record.id,
                            "status": failure_record.status,
                            "resolution": failure_record.resolution,
                            "remediation_memory_id": (
                                failure_record.remediation_memory_id
                            ),
                            "resolved_at": failure_record.resolved_at,
                        },
                        indent=2,
                    )
                )
            else:
                failure_record = runtime.failures.get(args.id)
                if failure_record is None:
                    raise KeyError(args.id)
                print(
                    json.dumps(
                        {
                            **failure_record.__dict__,
                            "symptoms": failure_record.symptoms,
                            "evidence": failure_record.evidence,
                        },
                        indent=2,
                    )
                )
        elif args.command == "experience":
            if args.experience_command == "capture":
                trace_path = Path(args.trace_file)
                if trace_path.stat().st_size > MAX_RAW_TRACE_BYTES:
                    raise ValueError("Raw experience trace exceeds the 5 MB limit")
                payload = json.loads(trace_path.read_text(encoding="utf-8"))
                raw_events = payload.get("events", payload) if isinstance(
                    payload, dict
                ) else payload
                if not isinstance(raw_events, list):
                    raise ValueError("Trace JSON must be an event list or object")
                trace = runtime.capture_experience(
                    ExperienceTraceCreate(
                        task_id=args.task_id,
                        scope=args.scope,
                        task_class=args.task_class,
                        outcome=args.outcome,
                        significance_score=args.significance,
                        events=tuple(
                            ExperienceEvent(
                                kind=ExperienceEventKind(str(item["kind"])),
                                content=str(item["content"]),
                                evidence=tuple(
                                    str(value)
                                    for value in item.get("evidence", ())
                                ),
                                confidence=float(item.get("confidence", 0.7)),
                                importance=float(item.get("importance", 0.5)),
                                durable=bool(item.get("durable", True)),
                                metadata_json=json.dumps(
                                    item.get("metadata", {}),
                                    sort_keys=True,
                                ),
                            )
                            for item in raw_events
                        ),
                    )
                )
                print(
                    json.dumps(
                        {
                            "trace_id": trace.id,
                            "raw_tokens": trace.raw_tokens,
                            "event_count": len(trace.events),
                            "significance_score": trace.significance_score,
                        },
                        indent=2,
                    )
                )
            elif args.experience_command == "distill":
                plan = (
                    runtime.plan_distillation(args.dry_run)
                    if args.dry_run
                    else runtime.approve_distillation(args.approve)
                )
                print(
                    json.dumps(
                        {
                            "run_id": plan.id,
                            "trace_id": plan.trace_id,
                            "status": plan.status,
                            "extractor": plan.extractor,
                            "raw_tokens": plan.raw_tokens,
                            "distilled_tokens": plan.distilled_tokens,
                            "compression_ratio": plan.compression_ratio,
                            "reduction_ratio": plan.reduction_ratio,
                            "summary": plan.summary(),
                            "items": [
                                {
                                    "id": item.id,
                                    "kind": item.kind.value,
                                    "content": item.content,
                                    "evidence": item.evidence,
                                    "source_event_indexes": (
                                        item.source_event_indexes
                                    ),
                                    "status": item.status,
                                    "memory_id": item.memory_id,
                                    "skill_id": item.skill_id,
                                    "error_type": item.error_type,
                                }
                                for item in plan.items
                            ],
                        },
                        indent=2,
                    )
                )
            elif args.plan:
                plan = runtime.experiences.load_plan(args.id)
                print(
                    json.dumps(
                        {
                            **plan.__dict__,
                            "items": [
                                {
                                    **item.__dict__,
                                    "kind": item.kind.value,
                                }
                                for item in plan.items
                            ],
                            "reduction_ratio": plan.reduction_ratio,
                        },
                        indent=2,
                    )
                )
            else:
                trace = runtime.experiences.get_trace(args.id)
                if trace is None:
                    raise KeyError(args.id)
                print(
                    json.dumps(
                        {
                            **trace.__dict__,
                            "events": [
                                {
                                    **event.__dict__,
                                    "kind": event.kind.value,
                                }
                                for event in trace.events
                            ],
                        },
                        indent=2,
                    )
                )
        if args.command == "remember":
            memory_id = runtime.remember(
                args.kind,
                args.content,
                scope=args.scope,
                confidence=args.confidence,
                importance=args.importance,
                evidence=args.evidence,
                subject=args.subject,
                valid_from=args.valid_from,
                valid_until=args.valid_until,
                supersedes=args.supersedes,
            )
            print(memory_id)
        elif args.command == "memory":
            if args.memory_command == "summary":
                print(json.dumps(runtime.status()["memories"], indent=2))
            elif args.memory_command == "add":
                memory_id = runtime.remember(
                    args.kind,
                    args.content,
                    scope=args.scope,
                    confidence=args.confidence,
                    importance=args.importance,
                    evidence=args.evidence,
                    subject=args.subject,
                    valid_from=args.valid_from,
                    valid_until=args.valid_until,
                    supersedes=args.supersedes,
                )
                print(memory_id)
            elif args.memory_command == "retrieve":
                result = runtime.retrieve_memory(
                    RetrievalRequest(
                        task=args.task or args.query,
                        query=args.query,
                        scope=args.scope,
                        token_budget=args.budget,
                        types=tuple(
                            MemoryType(value) for value in (args.type or ())
                        ),
                        valid_at=args.at,
                        target_memories=args.limit,
                    )
                )
                print(
                    json.dumps(
                        {
                            "candidate_count": result.candidate_count,
                            "selected_tokens": result.selected_tokens,
                            "semantic_available": result.semantic_available,
                            "semantic_status": result.semantic_status,
                            "selected": [
                                {
                                    "id": item.memory.id,
                                    "type": item.memory.type.value,
                                    "scope": item.memory.scope,
                                    "subject": item.memory.subject,
                                    "content": item.memory.content,
                                    "score": item.score,
                                    "breakdown": item.breakdown.as_dict(),
                                    "explanation": item.explanation,
                                    "conflict_ids": item.conflict_ids,
                                }
                                for item in result.selected
                            ],
                            "rejected": [
                                {
                                    "id": item.memory.id,
                                    "score": item.score,
                                    "reason": item.rejection_reason,
                                }
                                for item in result.rejected
                            ],
                        },
                        indent=2,
                    )
                )
            elif args.memory_command in {"current", "at"}:
                resolution = (
                    runtime.memory.current(args.subject, scope=args.scope)
                    if args.memory_command == "current"
                    else runtime.memory.at(
                        args.subject, args.timestamp, scope=args.scope
                    )
                )
                print(
                    json.dumps(
                        {
                            "subject": resolution.subject,
                            "scope": resolution.scope,
                            "as_of": resolution.as_of,
                            "preferred": (
                                {
                                    "id": resolution.preferred.id,
                                    "content": resolution.preferred.content,
                                    "valid_from": resolution.preferred.valid_from,
                                    "valid_until": resolution.preferred.valid_until,
                                    "supersedes": resolution.preferred.supersedes,
                                    "superseded_by": resolution.preferred.superseded_by,
                                }
                                if resolution.preferred is not None
                                else None
                            ),
                            "alternatives": [
                                {
                                    "id": record.id,
                                    "content": record.content,
                                    "valid_from": record.valid_from,
                                    "valid_until": record.valid_until,
                                }
                                for record in resolution.alternatives
                            ],
                            "unresolved_conflict": resolution.unresolved_conflict,
                            "reason": resolution.reason,
                        },
                        indent=2,
                    )
                )
            elif args.memory_command == "history":
                history = runtime.memory.history(args.subject, scope=args.scope)
                print(
                    json.dumps(
                        {
                            "subject": history.subject,
                            "scope": history.scope,
                            "records": [
                                {
                                    "id": record.id,
                                    "content": record.content,
                                    "status": record.status.value,
                                    "valid_from": record.valid_from,
                                    "valid_until": record.valid_until,
                                    "supersedes": record.supersedes,
                                    "superseded_by": record.superseded_by,
                                    "evidence": record.evidence,
                                }
                                for record in history.records
                            ],
                        },
                        indent=2,
                    )
                )
            elif args.memory_command == "consider":
                decision = runtime.consider_memory(
                    CandidateFact(
                        type=MemoryType(args.kind),
                        content=args.content,
                        scope=args.scope,
                        subject=args.subject,
                        confidence=args.confidence,
                        importance=args.importance,
                        usefulness=args.usefulness,
                        stability=args.stability,
                        evidence=tuple(args.evidence),
                        source_type=args.source_type,
                        source_id=args.source_id,
                        trusted_source=args.trusted_source,
                        temporary=args.temporary,
                        privacy_risk=args.privacy_risk,
                        security_risk=args.security_risk,
                        valid_from=args.valid_from,
                        valid_until=args.valid_until,
                    )
                )
                print(
                    json.dumps(
                        {
                            "decision_id": decision.id,
                            "outcome": decision.outcome.value,
                            "memory_id": (
                                decision.memory.id if decision.memory else None
                            ),
                            "matched_memory_id": decision.matched_memory_id,
                            "reasons": decision.reasons,
                            "risk_flags": decision.risk_flags,
                        },
                        indent=2,
                    )
                )
            elif args.memory_command == "decisions":
                print(
                    json.dumps(
                        runtime.write_audit.recent(limit=args.limit),
                        indent=2,
                    )
                )
            elif args.memory_command == "consolidate":
                plan = (
                    runtime.plan_consolidation(scope=args.scope)
                    if args.dry_run
                    else runtime.approve_consolidation(args.approve)
                )
                groups = plan.grouped()
                print(
                    json.dumps(
                        {
                            "run_id": plan.id,
                            "status": plan.status,
                            "scope": plan.scope,
                            "created_at": plan.created_at,
                            "applied_at": plan.applied_at,
                            **{
                                name: [
                                    {
                                        "action_id": action.id,
                                        "target_ids": action.target_ids,
                                        "reason": action.reason,
                                        "status": action.status,
                                        "payload": action.payload,
                                        "error_type": action.error_type,
                                    }
                                    for action in actions
                                ]
                                for name, actions in groups.items()
                            },
                        },
                        indent=2,
                    )
                )
            elif args.memory_command == "gc":
                plan = (
                    runtime.plan_memory_gc(scope=args.scope)
                    if args.dry_run
                    else runtime.approve_memory_gc(args.approve)
                )
                print(
                    json.dumps(
                        {
                            "run_id": plan.id,
                            "status": plan.status,
                            "scope": plan.scope,
                            "created_at": plan.created_at,
                            "applied_at": plan.applied_at,
                            "summary": plan.summary(),
                            "actions": [
                                {
                                    "action_id": action.id,
                                    "memory_id": action.memory_id,
                                    "from": action.from_state.value,
                                    "to": action.to_state.value,
                                    "reason": action.reason,
                                    "score": action.score,
                                    "status": action.status,
                                    "error_type": action.error_type,
                                }
                                for action in plan.actions
                            ],
                        },
                        indent=2,
                    )
                )
            elif args.memory_command in {"pin", "unpin", "archive", "restore"}:
                if args.memory_command == "pin":
                    record = runtime.lifecycle.pin(args.id, reason=args.reason)
                elif args.memory_command == "unpin":
                    record = runtime.lifecycle.unpin(args.id)
                elif args.memory_command == "archive":
                    record = runtime.lifecycle.archive(args.id, force=args.force)
                else:
                    record = runtime.lifecycle.restore(args.id)
                print(
                    json.dumps(
                        {
                            "id": record.id,
                            "status": record.status.value,
                            "lifecycle_state": record.lifecycle_state.value,
                            "pinned": record.pinned,
                            "pinned_at": record.pinned_at,
                            "pin_reason": record.pin_reason,
                        },
                        indent=2,
                    )
                )
        elif args.command == "skills":
            print(json.dumps(runtime.skills(), indent=2))
        elif args.command == "compile":
            bundle = runtime.compile_context(
                args.task, scope=args.scope, token_budget=args.budget
            )
            print(bundle.render())
            print(
                f"\n# Budget\n{bundle.total_tokens}/{bundle.token_budget} estimated tokens"
            )
        elif args.command == "telemetry":
            telemetry_command = args.telemetry_command or "summary"
            if telemetry_command == "summary":
                payload = runtime.telemetry()
            elif telemetry_command == "task":
                payload = runtime.telemetry_task(args.task_id)
            elif telemetry_command == "models":
                payload = runtime.telemetry_models()
            elif telemetry_command == "skills":
                payload = runtime.telemetry_skills()
            elif telemetry_command == "memory":
                payload = runtime.telemetry_memory()
            elif telemetry_command == "economy":
                payload = runtime.telemetry_token_economy()
            else:
                payload = runtime.telemetry_waste()
            print(json.dumps(payload, indent=2))
        elif args.command == "demo":
            _demo(runtime)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
