from __future__ import annotations

import argparse
import io
import ipaddress
import json
import os
import sys
from contextlib import redirect_stdout
from pathlib import Path

from .benchmark import BenchmarkDataset, BenchmarkRunner
from .memory_benchmark import MemoryBenchmarkDataset, MemoryBenchmarkRunner
from .token_benchmark import TokenBenchmarkDataset, TokenBenchmarkRunner
from .config import Settings
from .diagnostics import discover_ollama_models, run_doctor
from .execution import PassEvaluator, PassVerifier, Task, TaskEventBus, TaskRunner
from .failure import FailureCreate, FailurePlanningAdvisor, FailureQuery
from .experience import (
    ExperienceEvent,
    ExperienceEventKind,
    ExperienceTraceCreate,
    MAX_RAW_TRACE_BYTES,
)
from .migrations import MigrationManager
from .memory import MemoryType, Sensitivity
from .memory_scope import MemoryScopeKind
from .providers import OllamaProvider, ProviderExecutor
from .retrieval import RetrievalRequest
from .service import AdaptiveRuntime
from .telemetry import TelemetryRecorder
from .skill_validator import DockerSandboxAdapter, SandboxPolicy, SkillValidator
from .skill_evolution import SkillMutation
from .skill_genome import GenomeMutation, GenomeParameters
from .agent_spec import AgentSpec
from .write_controller import CandidateFact
from .model_router import ModelOutcome, ModelProfile, RouteAttempt, RouteRequest
from .local_model_router import LocalRouteRequest
from .multi_model import BaselineWorkflowOutcome, MultiModelWorkflowRequest
from .decision_memory import DecisionCheck, DecisionCreate
from .tool_registry import ToolAccessRequest, ToolDefinition
from .tool_router import ToolOutcome, ToolRouteRequest
from .plugin_system import PluginManifest
from .failure_recovery import RecoveryStep
from .audit_viewer import AuditQuery, ENTITY_TYPES, EVENT_TYPES
from .tool_exposure import ToolExposureBenchmarkSpec, ToolExposureTrial
from .reasoning_depth import (
    ReasoningBudgetPlanner,
    ReasoningBudgetRequest,
    ReasoningOutcome,
)
from .permissions import CapabilityCheck, CapabilityGrantRequest
from .content_security import (
    ORIGINS,
    ContentAssessmentRequest,
    TrustedWorkflowApprovalRequest,
    infer_content_origin,
)
from .secret_management import SecretReference, scan_staged_git_secrets
from .experiments import ExperimentCreate, ExperimentOutcome
from .regressions import RegressionRequest
from .skill_benchmark import SkillBenchmarkRequest
from .cost_accounting import LocalCostProfile, PriceRate

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


def _read_bounded_json_object(path: str, *, limit: int = 1_000_000) -> dict:
    source = Path(path)
    if source.stat().st_size > limit:
        raise ValueError("JSON input exceeds the 1 MB limit")
    payload = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("JSON input must contain an object")
    return payload


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="acr")
    parser.add_argument("--db", help="SQLite database path (overrides ACR_DATABASE)")
    parser.add_argument(
        "--json",
        action="store_true",
        help="Force machine-readable JSON output",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Write safe execution diagnostics to stderr",
    )
    parser.add_argument(
        "--dry-run",
        dest="global_dry_run",
        action="store_true",
        help="Describe the command without executing it",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("doctor", help="Check the local ACR environment")
    sub.add_parser("status", help="Show runtime and storage status")
    task = sub.add_parser("task", help="Inspect durable task records")
    task_sub = task.add_subparsers(dest="task_command", required=True)
    task_list = task_sub.add_parser("list", help="List recent tasks")
    task_list.add_argument("--limit", type=int, default=50)
    task_show = task_sub.add_parser("show", help="Inspect one task")
    task_show.add_argument("task_id")
    config = sub.add_parser("config", help="Inspect effective safe configuration")
    config.add_subparsers(dest="config_command", required=True).add_parser(
        "show", help="Show effective non-secret configuration"
    )
    sub.add_parser("migrate", help="Apply pending database migrations explicitly")
    backup = sub.add_parser(
        "backup",
        help="Create a fixed-scope, secret-scanned ACR backup archive",
    )
    backup.add_argument("output")
    backup.add_argument("--benchmarks-dir", default="benchmarks")
    verify_backup = sub.add_parser(
        "verify-backup",
        help="Verify archive hashes, SQLite integrity, and compatibility",
    )
    verify_backup.add_argument("backup")
    restore = sub.add_parser(
        "restore",
        help="Verify and restore an archive into a new target directory",
    )
    restore.add_argument("backup")
    restore.add_argument("target")
    serve = sub.add_parser("serve", help="Run the loopback-first FastAPI server")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    mcp = sub.add_parser("mcp", help="Run the local MCP provider")
    mcp_sub = mcp.add_subparsers(dest="mcp_command", required=True)
    mcp_serve = mcp_sub.add_parser(
        "serve", help="Serve the six-tool MCP catalog over stdio"
    )
    mcp_serve.add_argument(
        "--subject-type", choices=("task", "agent", "skill"), required=True
    )
    mcp_serve.add_argument("--subject-id", required=True)

    run = sub.add_parser("run", help="Execute a bounded task with local Ollama")
    run.add_argument("task")
    run.add_argument("--model", help="Installed Ollama model name")
    run.add_argument("--max-output-tokens", type=int, default=512)
    run.add_argument("--max-input-tokens", type=int, default=8_192)
    run.add_argument("--max-model-calls", type=int, default=1)
    run.add_argument("--max-tool-calls", type=int, default=0)
    run.add_argument("--max-agents", type=int, default=1)
    run.add_argument(
        "--max-cost",
        type=int,
        default=0,
        help="Hard cost budget in integer microunits",
    )
    run.add_argument("--max-duration-seconds", type=int, default=180)
    run.add_argument(
        "--reasoning-mode-supported",
        action="append",
        choices=("enabled", "disabled", "effort"),
        default=[],
        help="Exact Ollama think mode supported by the selected model",
    )
    run.add_argument(
        "--reasoning-effort-supported",
        action="append",
        choices=("minimal", "low", "medium", "high", "xhigh", "max"),
        default=[],
        help="Exact named Ollama effort supported by the selected model",
    )
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
    benchmark_validate_memory = benchmark_sub.add_parser(
        "validate-memory", help="Validate the adversarial memory dataset"
    )
    benchmark_validate_memory.add_argument("dataset")
    benchmark_memory = benchmark_sub.add_parser(
        "memory", help="Run the deterministic four-arm memory benchmark"
    )
    benchmark_memory.add_argument("dataset")
    benchmark_memory.add_argument("--output", help="Optional JSON report path")
    benchmark_skill = benchmark_sub.add_parser(
        "skill", help="Analyze paired no-skill, incumbent, and candidate trials"
    )
    benchmark_skill.add_argument("request_file")
    benchmark_skill_report = benchmark_sub.add_parser(
        "skill-report", help="Inspect a retained skill benchmark"
    )
    benchmark_skill_report.add_argument("run_id")
    benchmark_validate_token = benchmark_sub.add_parser(
        "validate-token", help="Validate the excessive-context dataset"
    )
    benchmark_validate_token.add_argument("dataset")
    benchmark_token = benchmark_sub.add_parser(
        "token", help="Run four context-selection strategies"
    )
    benchmark_token.add_argument("dataset")
    benchmark_token.add_argument("--output", help="Optional JSON report path")

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
    remember.add_argument(
        "--sensitivity",
        choices=tuple(item.value for item in Sensitivity),
        default="internal",
    )

    memory = sub.add_parser("memory", help="Inspect or write memory")
    memory_sub = memory.add_subparsers(dest="memory_command", required=True)
    memory_scope_add = memory_sub.add_parser(
        "scope-add", help="Register one explicit child scope"
    )
    memory_scope_add.add_argument("id")
    memory_scope_add.add_argument(
        "kind",
        choices=tuple(
            item.value for item in MemoryScopeKind
            if item is not MemoryScopeKind.GLOBAL
        ),
    )
    memory_scope_add.add_argument("--parent", required=True)
    memory_sub.add_parser(
        "scope-list", help="List registered memory scopes without content"
    )
    memory_scope_path = memory_sub.add_parser(
        "scope-path", help="Show one scope and its visible ancestors"
    )
    memory_scope_path.add_argument("id")
    memory_decision_add = memory_sub.add_parser(
        "decision-add", help="Store one structured evidence-backed decision"
    )
    memory_decision_add.add_argument("decision_file")
    memory_decision_check = memory_sub.add_parser(
        "decision-check",
        help="Retrieve decisions and validate their stored assumptions",
    )
    memory_decision_check.add_argument("query")
    memory_decision_check.add_argument("--task")
    memory_decision_check.add_argument("--scope", default="global")
    memory_decision_check.add_argument("--assumption", action="append", default=[])
    memory_decision_check.add_argument("--budget", type=int, default=1_000)
    memory_decision_check.add_argument("--limit", type=int, default=8)
    memory_decision_show = memory_sub.add_parser(
        "decision-show", help="Inspect one structured decision memory"
    )
    memory_decision_show.add_argument("id")
    memory_conflict_check = memory_sub.add_parser(
        "conflict-check", help="Classify contradictory claims for one subject"
    )
    memory_conflict_check.add_argument("subject")
    memory_conflict_check.add_argument("--scope", default="global")
    memory_conflict_compare = memory_sub.add_parser(
        "conflict-compare", help="Compare two exact memory records"
    )
    memory_conflict_compare.add_argument("left_id")
    memory_conflict_compare.add_argument("right_id")
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
    memory_add.add_argument(
        "--sensitivity",
        choices=tuple(item.value for item in Sensitivity),
        default="internal",
    )
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
    memory_retrieve.add_argument(
        "--cache-max-age",
        type=int,
        help="Opt into exact retrieval caching for at most this many seconds",
    )
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
    memory_consider.add_argument(
        "--content-origin", choices=tuple(sorted(ORIGINS))
    )
    memory_consider.add_argument("--provenance", action="append", default=[])
    memory_consider.add_argument("--security-assessment")
    memory_consider.add_argument("--workflow-approval")
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
    skills_sub = skills.add_subparsers(dest="skills_command", required=True)
    skills_sub.add_parser("list", help="List registered skills")

    utility = sub.add_parser(
        "utility", help="Inspect outcome-grounded lifecycle economics"
    )
    utility_sub = utility.add_subparsers(
        dest="utility_command", required=True
    )
    utility_list = utility_sub.add_parser(
        "list", help="List current utility asset revisions"
    )
    utility_list.add_argument(
        "--kind",
        choices=(
            "memory",
            "skill",
            "model",
            "tool",
            "agent_topology",
            "context_strategy",
        ),
    )
    utility_show = utility_sub.add_parser(
        "show", help="Show one asset's current utility revision"
    )
    utility_show.add_argument(
        "kind",
        choices=(
            "memory",
            "skill",
            "model",
            "tool",
            "agent_topology",
            "context_strategy",
        ),
    )

    cost = sub.add_parser(
        "cost", help="Manage versioned prices and authoritative cost accounting"
    )
    cost_sub = cost.add_subparsers(dest="cost_command", required=True)
    cost_rate_add = cost_sub.add_parser(
        "rate-add", help="Add one immutable effective-dated price rate"
    )
    cost_rate_add.add_argument("rate_file")
    cost_sub.add_parser("rates", help="List retained price rates")
    cost_model = cost_sub.add_parser(
        "record-model", help="Record one physical model attempt"
    )
    cost_model.add_argument("usage_file")
    cost_tool = cost_sub.add_parser(
        "record-tool", help="Record one physical tool API attempt"
    )
    cost_tool.add_argument("usage_file")
    cost_local_add = cost_sub.add_parser(
        "local-profile-add", help="Add an explicit local cost profile"
    )
    cost_local_add.add_argument("profile_file")
    cost_sub.add_parser(
        "local-status", help="Show opt-in local cost accounting status"
    )
    cost_local = cost_sub.add_parser(
        "record-local", help="Record a local inference estimate when enabled"
    )
    cost_local.add_argument("usage_file")
    cost_sub.add_parser(
        "report", help="Report cost/task, cost/success, and allocation views"
    )
    cost_event = cost_sub.add_parser(
        "event", help="Inspect one content-minimized cost event"
    )
    cost_event.add_argument("event_id")

    waste = sub.add_parser(
        "waste", help="Hunt token waste with evidence-tiered advisory findings"
    )
    waste_sub = waste.add_subparsers(dest="waste_command", required=True)
    waste_scan = waste_sub.add_parser(
        "scan", help="Produce one immutable nine-category waste report"
    )
    waste_scan.add_argument("--scope", default="global")
    waste_report = waste_sub.add_parser(
        "report", help="Inspect one retained content-minimized waste report"
    )
    waste_report.add_argument("run_id")
    waste_report.add_argument("--scope", default="global")
    utility_show.add_argument("external_id")
    skills_validate = skills_sub.add_parser(
        "validate", help="Validate an ACR Skill Format v1 directory"
    )
    skills_validate.add_argument("directory")
    skills_install = skills_sub.add_parser(
        "install", help="Admit a validated package in quarantine"
    )
    skills_install.add_argument("directory")
    skills_inspect = skills_sub.add_parser("inspect", help="Inspect one skill")
    skills_inspect.add_argument("skill")
    skills_evidence = skills_sub.add_parser(
        "evidence", help="Inspect content-minimized memory-skill lineage"
    )
    skills_evidence.add_argument("skill")
    skills_reconcile = skills_sub.add_parser(
        "reconcile-evidence",
        help="Recompute a skill's evidence validity and reliability",
    )
    skills_reconcile.add_argument("skill")
    skills_invalidate = skills_sub.add_parser(
        "invalidate-support",
        help="Append an explicit support invalidation",
    )
    skills_invalidate.add_argument("support_link_id")
    skills_invalidate.add_argument(
        "--reason",
        required=True,
        choices=(
            "memory_missing",
            "memory_not_current",
            "memory_untrusted",
            "trace_not_succeeded",
            "distillation_not_applied",
            "item_not_applied",
            "package_changed",
            "support_hash_changed",
            "operator_rejected",
        ),
    )
    skills_invalidate.add_argument("--actor", required=True)
    skills_search = skills_sub.add_parser(
        "search", help="Search indexed skill metadata"
    )
    skills_search.add_argument("query")
    skills_search.add_argument("--limit", type=int, default=10)
    skills_route = skills_sub.add_parser(
        "route", help="Select the smallest useful active skill set"
    )
    skills_route.add_argument("task")
    skills_route.add_argument("--task-class", default="general")
    skills_route.add_argument("--budget", type=int, default=4_000)
    skills_generate = skills_sub.add_parser(
        "generate",
        help="Plan or approve quarantined skills from repeated success",
    )
    generate_action = skills_generate.add_mutually_exclusive_group(
        required=True
    )
    generate_action.add_argument("--dry-run", action="store_true")
    generate_action.add_argument("--approve", metavar="RUN_ID")
    generate_action.add_argument("--show", metavar="RUN_ID")
    skills_generate.add_argument("--scope")
    skills_certify = skills_sub.add_parser(
        "certify", help="Run the mandatory retained validation pipeline"
    )
    skills_certify.add_argument("skill")
    skills_certify.add_argument(
        "--docker-sandbox",
        action="store_true",
        help="Use a preinstalled locked-down Docker image for runnable stages",
    )
    skills_certify.add_argument(
        "--sandbox-image", default="python:3.11-slim"
    )
    skills_certify.add_argument(
        "--sandbox-timeout", type=int, default=60
    )
    skills_certify.add_argument(
        "--sandbox-memory-mb", type=int, default=256
    )
    skills_certify.add_argument(
        "--sandbox-cpus", type=float, default=0.5
    )
    skills_certify.add_argument(
        "--sandbox-pids", type=int, default=64
    )
    skills_validation = skills_sub.add_parser(
        "validation", help="Inspect one retained validation run"
    )
    skills_validation.add_argument("run_id")
    skills_promote = skills_sub.add_parser(
        "promote", help="Promote a fully passed validation run"
    )
    skills_promote.add_argument("run_id")
    skills_evolve = skills_sub.add_parser(
        "evolve", help="Create an immutable candidate version from mutation JSON"
    )
    skills_evolve.add_argument("skill")
    skills_evolve.add_argument("mutation_file")
    skills_evolve.add_argument("--version")
    skills_evolution = skills_sub.add_parser(
        "evolution", help="Inspect one skill-evolution run"
    )
    skills_evolution.add_argument("run_id")
    skills_compare = skills_sub.add_parser(
        "compare-evolution",
        help="Record a validated multi-objective source/candidate comparison",
    )
    skills_compare.add_argument("run_id")
    skills_compare.add_argument("comparison_file")
    skills_promote_evolution = skills_sub.add_parser(
        "promote-evolution", help="Promote a multi-objective candidate winner"
    )
    skills_promote_evolution.add_argument("run_id")
    skills_rollback_evolution = skills_sub.add_parser(
        "rollback-evolution", help="Rollback to the validated source version"
    )
    skills_rollback_evolution.add_argument("run_id")
    skills_rollback_evolution.add_argument("--reason", required=True)
    skills_merge_analysis = skills_sub.add_parser(
        "merge-analysis",
        help="Retain advisory redundancy and composition evidence",
    )
    skills_merge_analysis.add_argument("--skill")
    skills_merge_analysis.add_argument("--limit", type=int, default=50)
    skills_merge_report = skills_sub.add_parser(
        "merge-report", help="Inspect one retained skill merge analysis"
    )
    skills_merge_report.add_argument("run_id")
    skills_genome_create = skills_sub.add_parser(
        "genome-create", help="Create an isolated experimental genome baseline"
    )
    skills_genome_create.add_argument("skill")
    skills_genome_create.add_argument("parameters_file")
    skills_genome_mutate = skills_sub.add_parser(
        "genome-mutate", help="Create a controlled experimental mutation"
    )
    skills_genome_mutate.add_argument("parent_genome_id")
    skills_genome_mutate.add_argument("mutation_file")
    skills_genome_inspect = skills_sub.add_parser(
        "genome", help="Inspect one experimental genome"
    )
    skills_genome_inspect.add_argument("genome_id")
    skills_genome_tournament = skills_sub.add_parser(
        "genome-tournament",
        help="Run the fail-closed isolated benchmark tournament",
    )
    skills_genome_tournament.add_argument("baseline_genome_id")
    skills_genome_tournament.add_argument(
        "candidate_genome_ids", nargs="+"
    )
    skills_genome_report = skills_sub.add_parser(
        "genome-tournament-report",
        help="Inspect one retained genome tournament",
    )
    skills_genome_report.add_argument("run_id")
    for command in ("test", "activate", "quarantine", "retire", "history"):
        skill_command = skills_sub.add_parser(command)
        skill_command.add_argument("skill")

    agents = sub.add_parser("agents", help="Define and inspect worker specs")
    agents_sub = agents.add_subparsers(dest="agents_command", required=True)
    agents_sub.add_parser("list", help="List defined AgentSpecs")
    agents_define = agents_sub.add_parser(
        "define", help="Define one immutable AgentSpec from JSON"
    )
    agents_define.add_argument("spec_file")
    agents_inspect = agents_sub.add_parser(
        "inspect", help="Inspect one AgentSpec"
    )
    agents_inspect.add_argument("agent_id")
    agents_factory_plan = agents_sub.add_parser(
        "factory-plan",
        help="Cost and propose the minimum justified temporary agent team",
    )
    agents_factory_plan.add_argument("request_file")
    agents_factory_report = agents_sub.add_parser(
        "factory-report", help="Inspect one retained agent factory proposal"
    )
    agents_factory_report.add_argument("plan_id")
    agents_topology_record = agents_sub.add_parser(
        "topology-record",
        help="Retain one verified agent topology outcome",
    )
    agents_topology_record.add_argument("outcome_file")
    agents_topology_recommend = agents_sub.add_parser(
        "topology-recommend",
        help="Get an advisory recipe from comparable retained outcomes",
    )
    agents_topology_recommend.add_argument("request_file")
    agents_topology_outcome = agents_sub.add_parser(
        "topology-outcome", help="Inspect one retained topology outcome"
    )
    agents_topology_outcome.add_argument("outcome_id")
    agents_topology_recipes = agents_sub.add_parser(
        "topology-recipes", help="List reusable successful topology recipes"
    )
    agents_topology_recipes.add_argument("--task-class")

    models = sub.add_parser("models", help="Inspect and route available models")
    models_sub = models.add_subparsers(dest="models_command", required=True)
    models_sub.add_parser(
        "list", help="List available local models"
    )
    models_register = models_sub.add_parser(
        "register", help="Register or update a priced model profile from JSON"
    )
    models_register.add_argument("profile_file")
    models_outcome = models_sub.add_parser(
        "outcome", help="Record one verified task-class outcome from JSON"
    )
    models_outcome.add_argument("outcome_file")
    models_route = models_sub.add_parser(
        "route", help="Select the cheapest model with sufficient evidence"
    )
    models_route.add_argument("request_file")
    models_attempt = models_sub.add_parser(
        "attempt", help="Record and re-evaluate a selected or escalated attempt"
    )
    models_attempt.add_argument("route_id")
    models_attempt.add_argument("attempt_file")
    models_report = models_sub.add_parser(
        "route-report", help="Inspect a retained model route and escalation"
    )
    models_report.add_argument("route_id")
    models_sub.add_parser(
        "profiles", help="List registered routing profiles"
    )
    models_sub.add_parser(
        "local-discover", help="Discover and register installed Ollama models"
    )
    local_benchmark = models_sub.add_parser(
        "local-benchmark",
        help="Run and retain the five-class local-routing benchmark",
    )
    local_benchmark.add_argument("dataset")
    local_benchmark.add_argument("--model", required=True)
    local_benchmark.add_argument("--seed", type=int, default=0)
    local_benchmark.add_argument("--discovery-id")
    local_route = models_sub.add_parser(
        "local-route", help="Apply local-first and sensitive-context policy"
    )
    local_route.add_argument("request_file")
    local_policy = models_sub.add_parser(
        "local-policy", help="Inspect the retained policy for a local route"
    )
    local_policy.add_argument("route_id")
    workflow_plan = models_sub.add_parser(
        "workflow-plan", help="Plan a role-specialized advisory model workflow"
    )
    workflow_plan.add_argument("request_file")
    workflow_report = models_sub.add_parser(
        "workflow-report", help="Inspect one retained multi-model workflow"
    )
    workflow_report.add_argument("workflow_id")
    workflow_outcome = models_sub.add_parser(
        "workflow-outcome",
        help="Compare completed specialized stages with one baseline run",
    )
    workflow_outcome.add_argument("workflow_id")
    workflow_outcome.add_argument("baseline_file")
    workflow_benefit = models_sub.add_parser(
        "workflow-benefit", help="Report repeated paired specialization benefit"
    )
    workflow_benefit.add_argument("workflow_class")
    workflow_benefit.add_argument("--minimum-pairs", type=int, default=3)

    reasoning = sub.add_parser(
        "reasoning", help="Classify and inspect governed reasoning budgets"
    )
    reasoning_sub = reasoning.add_subparsers(
        dest="reasoning_command", required=True
    )
    reasoning_classify = reasoning_sub.add_parser(
        "classify", help="Create one immutable reasoning-budget decision"
    )
    reasoning_classify.add_argument("request_file")
    reasoning_inspect = reasoning_sub.add_parser(
        "inspect", help="Inspect a retained reasoning-budget decision"
    )
    reasoning_inspect.add_argument("decision_id")
    reasoning_sub.add_parser(
        "policy", help="Inspect the active immutable baseline policy"
    )
    reasoning_outcome = reasoning_sub.add_parser(
        "outcome",
        help="Retain an untrusted caller outcome outside promotion evidence",
    )
    reasoning_outcome.add_argument("outcome_file")
    reasoning_refine = reasoning_sub.add_parser(
        "refine",
        help="Evaluate advisory threshold refinement from trusted outcomes",
    )
    reasoning_refine.add_argument("task_class")
    reasoning_refine.add_argument("--minimum-samples", type=int, default=8)

    research = sub.add_parser(
        "research", help="Plan and inspect bounded parallel research"
    )
    research_sub = research.add_subparsers(
        dest="research_command", required=True
    )
    research_reference = research_sub.add_parser(
        "reference-add", help="Store one immutable shared research reference"
    )
    research_reference.add_argument("reference_file")
    research_plan = research_sub.add_parser(
        "plan", help="Create a bounded independent-question research plan"
    )
    research_plan.add_argument("request_file")
    research_plan_inspect = research_sub.add_parser(
        "plan-inspect", help="Inspect one retained research plan"
    )
    research_plan_inspect.add_argument("plan_id")
    research_run_inspect = research_sub.add_parser(
        "run-inspect", help="Inspect one centrally synthesized research run"
    )
    research_run_inspect.add_argument("run_id")
    research_benchmark_inspect = research_sub.add_parser(
        "benchmark-inspect",
        help="Inspect one paired serial-versus-parallel benchmark",
    )
    research_benchmark_inspect.add_argument("benchmark_id")

    evidence_graph = sub.add_parser(
        "evidence-graph", help="Create and traverse typed relational provenance"
    )
    evidence_graph_sub = evidence_graph.add_subparsers(
        dest="evidence_graph_command", required=True
    )
    evidence_graph_create = evidence_graph_sub.add_parser(
        "create", help="Link one completed research run to canonical runtime records"
    )
    evidence_graph_create.add_argument("request_file")
    evidence_graph_inspect = evidence_graph_sub.add_parser(
        "inspect", help="Inspect one immutable evidence bundle"
    )
    evidence_graph_inspect.add_argument("bundle_id")
    evidence_graph_traverse = evidence_graph_sub.add_parser(
        "traverse", help="Run a bounded relational graph traversal"
    )
    evidence_graph_traverse.add_argument("bundle_id")
    evidence_graph_traverse.add_argument("node_id")
    evidence_graph_traverse.add_argument(
        "--direction", choices=("forward", "backward"), default="forward"
    )
    evidence_graph_traverse.add_argument("--max-depth", type=int, default=5)
    evidence_graph_traverse.add_argument("--limit", type=int, default=100)

    explain = sub.add_parser(
        "explain", help="Explain runtime choices from retained scoring evidence"
    )
    explain_sub = explain.add_subparsers(dest="explain_command", required=True)
    explain_model = explain_sub.add_parser("model")
    explain_model.add_argument("route_id")
    explain_skill = explain_sub.add_parser("skill")
    explain_skill.add_argument("task_id")
    explain_skill.add_argument("skill_id")
    explain_memory = explain_sub.add_parser("memory")
    explain_memory.add_argument("task_id")
    explain_memory.add_argument("memory_id")
    explain_agent = explain_sub.add_parser("agent")
    explain_agent.add_argument("plan_id")
    explain_agent.add_argument("--worker-id")
    explain_context = explain_sub.add_parser("context")
    explain_context.add_argument("task_id")
    explain_forgotten = explain_sub.add_parser("forgotten")
    explain_forgotten.add_argument("memory_id")

    safe_mode = sub.add_parser(
        "safe-mode",
        help="Inspect or change the persistent incident-containment mode",
    )
    safe_mode.add_argument(
        "safe_mode_command",
        nargs="?",
        choices=("status", "enable", "disable", "events"),
        default="status",
    )
    safe_mode.add_argument("--actor")
    safe_mode.add_argument("--reason")
    safe_mode.add_argument("--limit", type=int, default=100)

    overrides = sub.add_parser(
        "overrides", help="Apply and inspect recorded human overrides"
    )
    override_sub = overrides.add_subparsers(
        dest="override_command", required=True
    )
    override_apply = override_sub.add_parser("apply")
    override_apply.add_argument("request_file")
    override_list = override_sub.add_parser("list")
    override_list.add_argument("--active", action="store_true")
    override_show = override_sub.add_parser("show")
    override_show.add_argument("override_id")
    override_revoke = override_sub.add_parser("revoke")
    override_revoke.add_argument("override_id")
    override_revoke.add_argument("--actor", required=True)
    override_revoke.add_argument("--reason", required=True)

    tools = sub.add_parser("tools", help="Manage immutable tool boundaries")
    tools_sub = tools.add_subparsers(dest="tools_command", required=True)
    tools_register = tools_sub.add_parser(
        "register", help="Register a strict tool definition"
    )
    tools_register.add_argument("definition_file")
    tools_sub.add_parser("list", help="List tool definitions")
    tools_inspect = tools_sub.add_parser("inspect", help="Inspect one tool")
    tools_inspect.add_argument("name")
    tools_check = tools_sub.add_parser(
        "check", help="Check an agent's grants against a tool boundary"
    )
    tools_check.add_argument("request_file")
    tools_route = tools_sub.add_parser(
        "route", help="Select permitted deterministic tools for a task"
    )
    tools_route.add_argument("request_file")
    tools_outcome = tools_sub.add_parser(
        "outcome", help="Record one evidenced selected-tool outcome"
    )
    tools_outcome.add_argument("outcome_file")
    tools_report = tools_sub.add_parser(
        "route-report", help="Inspect one retained tool route"
    )
    tools_report.add_argument("route_id")
    tools_agent_route = tools_sub.add_parser(
        "agent-route",
        help="Route through an exact AgentSpec tool allowlist",
    )
    tools_agent_route.add_argument("request_file")
    tools_agent_route.add_argument("agent_spec_id")
    tools_exposure_project = tools_sub.add_parser(
        "exposure-project",
        help="Create an immutable authorization-filtered tool projection",
    )
    tools_exposure_project.add_argument("route_id")
    tools_exposure_project.add_argument("agent_spec_id")
    tools_exposure_inspect = tools_sub.add_parser(
        "exposure-inspect", help="Inspect one retained tool projection"
    )
    tools_exposure_inspect.add_argument("projection_id")
    tools_exposure_render = tools_sub.add_parser(
        "exposure-render",
        help="Revalidate and render canonical provider tool definitions",
    )
    tools_exposure_render.add_argument("projection_id")
    tools_benchmark_start = tools_sub.add_parser(
        "exposure-benchmark-start",
        help="Start a sealed-case paired exposure benchmark",
    )
    tools_benchmark_start.add_argument("spec_file")
    tools_benchmark_trial = tools_sub.add_parser(
        "exposure-benchmark-trial",
        help="Record one content-minimized paired benchmark trial",
    )
    tools_benchmark_trial.add_argument("trial_file")
    tools_benchmark_seal = tools_sub.add_parser(
        "exposure-benchmark-seal",
        help="Seal a complete paired benchmark without activating it",
    )
    tools_benchmark_seal.add_argument("run_id")
    tools_benchmark_report = tools_sub.add_parser(
        "exposure-benchmark-report",
        help="Inspect one retained exposure benchmark",
    )
    tools_benchmark_report.add_argument("run_id")

    plugins = sub.add_parser(
        "plugins", help="Manage declarative permission-governed plugins"
    )
    plugins_sub = plugins.add_subparsers(
        dest="plugins_command", required=True
    )
    plugins_register = plugins_sub.add_parser(
        "register", help="Validate and register a strict plugin manifest"
    )
    plugins_register.add_argument("manifest_file")
    plugins_sub.add_parser("list", help="List compatible plugins")
    plugins_inspect = plugins_sub.add_parser(
        "inspect", help="Inspect one exact compatible plugin"
    )
    plugins_inspect.add_argument("name")
    plugins_inspect.add_argument("version")
    plugins_validate = plugins_sub.add_parser(
        "validation", help="Inspect one retained compatibility validation"
    )
    plugins_validate.add_argument("validation_id")
    plugins_route = plugins_sub.add_parser(
        "route",
        help="Route one plugin capability through central permission checks",
    )
    plugins_route.add_argument("name")
    plugins_route.add_argument("version")
    plugins_route.add_argument("capability")
    plugins_route.add_argument("request_file")

    recovery = sub.add_parser(
        "recovery", help="Manage durable interruption-safe task checkpoints"
    )
    recovery_sub = recovery.add_subparsers(
        dest="recovery_command", required=True
    )
    recovery_create = recovery_sub.add_parser(
        "create", help="Persist an exact classified recovery plan"
    )
    recovery_create.add_argument("plan_file")
    recovery_inspect = recovery_sub.add_parser(
        "inspect", help="Inspect one recovery run and its immutable events"
    )
    recovery_inspect.add_argument("run_id")
    recovery_interrupt = recovery_sub.add_parser(
        "interrupt",
        help="Acknowledge a dead worker and classify its ambiguous step",
    )
    recovery_interrupt.add_argument("run_id")
    recovery_interrupt.add_argument("--actor", required=True)
    recovery_interrupt.add_argument("--reason", required=True)
    recovery_interrupt.add_argument(
        "--evidence", action="append", required=True
    )
    recovery_review = recovery_sub.add_parser(
        "review", help="Resolve a human-review-required recovery step"
    )
    recovery_review.add_argument("run_id")
    recovery_review.add_argument("sequence", type=int)
    recovery_review.add_argument(
        "decision", choices=("execute", "accept_completed", "abort")
    )
    recovery_review.add_argument("--actor", required=True)
    recovery_review.add_argument("--reason", required=True)
    recovery_review.add_argument(
        "--evidence", action="append", required=True
    )

    audit = sub.add_parser(
        "audit", help="Inspect immutable high-value mutation events"
    )
    audit_sub = audit.add_subparsers(dest="audit_command", required=True)
    audit_list = audit_sub.add_parser(
        "list", help="List audit events newest first"
    )
    audit_list.add_argument("--event-type", choices=sorted(EVENT_TYPES))
    audit_list.add_argument("--entity-type", choices=sorted(ENTITY_TYPES))
    audit_list.add_argument("--entity-id")
    audit_list.add_argument("--after")
    audit_list.add_argument("--before")
    audit_list.add_argument("--limit", type=int, default=100)
    audit_show = audit_sub.add_parser("show", help="Inspect one audit event")
    audit_show.add_argument("event_id")
    audit_sub.add_parser("summary", help="Summarize retained audit events")

    performance = sub.add_parser(
        "performance", help="Capture and inspect local runtime performance"
    )
    performance_sub = performance.add_subparsers(
        dest="performance_command", required=True
    )
    performance_profile = performance_sub.add_parser(
        "profile-local",
        help="Profile deterministic local database, retrieval, context, and JSON work",
    )
    performance_profile.add_argument("--scope", default="global")
    performance_profile.add_argument("--iterations", type=int, default=5)
    performance_report = performance_sub.add_parser(
        "report", help="Inspect one immutable performance profile"
    )
    performance_report.add_argument("run_id")
    performance_list = performance_sub.add_parser(
        "list", help="List recent performance profiles"
    )
    performance_list.add_argument("--limit", type=int, default=50)

    capabilities = sub.add_parser(
        "capabilities", help="Manage scoped default-deny capability grants"
    )
    capability_sub = capabilities.add_subparsers(
        dest="capabilities_command", required=True
    )
    capability_grant = capability_sub.add_parser(
        "grant", help="Issue one audited bounded capability grant"
    )
    capability_grant.add_argument("grant_file")
    capability_check = capability_sub.add_parser(
        "check", help="Record an exact capability authorization decision"
    )
    capability_check.add_argument("check_file")
    capability_revoke = capability_sub.add_parser(
        "revoke", help="Revoke a grant and all delegated descendants"
    )
    capability_revoke.add_argument("grant_id")
    capability_revoke.add_argument("--reason", required=True)
    capability_inspect = capability_sub.add_parser(
        "inspect", help="Inspect one retained capability grant"
    )
    capability_inspect.add_argument("grant_id")
    capability_list = capability_sub.add_parser(
        "list", help="List grants assigned to one subject"
    )
    capability_list.add_argument(
        "subject_type", choices=("task", "agent", "skill")
    )
    capability_list.add_argument("subject_id")

    secrets = sub.add_parser(
        "secrets", help="Resolve opaque secret references or scan staged files"
    )
    secrets_sub = secrets.add_subparsers(
        dest="secrets_command", required=True
    )
    secrets_resolve = secrets_sub.add_parser(
        "resolve", help="Verify a permitted reference without printing its value"
    )
    secrets_resolve.add_argument("reference")
    secrets_resolve.add_argument(
        "--subject-type", choices=("task", "agent", "skill"), required=True
    )
    secrets_resolve.add_argument("--subject-id", required=True)
    secrets_inspect = secrets_sub.add_parser(
        "inspect", help="Inspect one value-free secret access event"
    )
    secrets_inspect.add_argument("event_id")
    secrets_scan = secrets_sub.add_parser(
        "scan-staged", help="Reject high-confidence secrets in staged Git blobs"
    )
    secrets_scan.add_argument("--repository", default=".")

    privacy = sub.add_parser(
        "privacy", help="Manage memory classification, processing, and erasure"
    )
    privacy_sub = privacy.add_subparsers(
        dest="privacy_command", required=True
    )
    privacy_sub.add_parser("policies", help="List classification policies")
    privacy_policy = privacy_sub.add_parser(
        "policy-set", help="Replace one versioned classification policy"
    )
    privacy_policy.add_argument(
        "classification", choices=tuple(item.value for item in Sensitivity)
    )
    privacy_policy.add_argument("policy_file")
    privacy_policy.add_argument("--actor", required=True)
    privacy_policy.add_argument("--reason", required=True)
    privacy_classify = privacy_sub.add_parser(
        "classify", help="Tag one memory with a sensitivity class"
    )
    privacy_classify.add_argument("memory_id")
    privacy_classify.add_argument(
        "classification", choices=tuple(item.value for item in Sensitivity)
    )
    privacy_classify.add_argument("--actor", required=True)
    privacy_classify.add_argument("--reason", required=True)
    privacy_classify.add_argument("--allow-downgrade", action="store_true")
    privacy_provider = privacy_sub.add_parser(
        "provider-check", help="Check an exact provider for selected memories"
    )
    privacy_provider.add_argument("provider")
    privacy_provider.add_argument("memory_ids", nargs="+")
    privacy_provider.add_argument("--local", action="store_true")
    privacy_retention = privacy_sub.add_parser(
        "retention-due", help="List content-free expired-retention records"
    )
    privacy_retention.add_argument("--at")
    privacy_export = privacy_sub.add_parser(
        "export", help="Export only an all-exportable exact memory set"
    )
    privacy_export.add_argument("memory_ids", nargs="+")
    privacy_delete = privacy_sub.add_parser(
        "delete-plan", help="Plan exact memory erasure"
    )
    privacy_delete.add_argument("memory_id")
    privacy_delete.add_argument("--actor", required=True)
    privacy_delete.add_argument("--reason", required=True)
    privacy_approve = privacy_sub.add_parser(
        "delete-approve", help="Approve and verify one planned erasure"
    )
    privacy_approve.add_argument("request_id")
    privacy_report = privacy_sub.add_parser(
        "delete-report", help="Inspect one deletion request"
    )
    privacy_report.add_argument("request_id")

    experiments = sub.add_parser(
        "experiments", help="Run opt-in reproducible strategy comparisons"
    )
    experiments_sub = experiments.add_subparsers(
        dest="experiments_command", required=True
    )
    experiments_create = experiments_sub.add_parser(
        "create", help="Create an immutable draft experiment"
    )
    experiments_create.add_argument("request_file")
    experiments_start = experiments_sub.add_parser(
        "start", help="Explicitly start assignment"
    )
    experiments_start.add_argument("experiment_id")
    experiments_assign = experiments_sub.add_parser(
        "assign", help="Reproducibly assign one randomization unit"
    )
    experiments_assign.add_argument("experiment_id")
    experiments_assign.add_argument("unit_id")
    experiments_outcome = experiments_sub.add_parser(
        "outcome", help="Record one evidenced assigned outcome"
    )
    experiments_outcome.add_argument("experiment_id")
    experiments_outcome.add_argument("outcome_file")
    experiments_report = experiments_sub.add_parser(
        "report", help="Show descriptive results and allocation diagnostics"
    )
    experiments_report.add_argument("experiment_id")
    experiments_inspect = experiments_sub.add_parser(
        "inspect", help="Inspect an experiment definition and lifecycle"
    )
    experiments_inspect.add_argument("experiment_id")
    experiments_finish = experiments_sub.add_parser(
        "finish", help="Complete an experiment without changing defaults"
    )
    experiments_finish.add_argument("experiment_id")
    experiments_cancel = experiments_sub.add_parser(
        "cancel", help="Cancel an experiment without changing defaults"
    )
    experiments_cancel.add_argument("experiment_id")

    regressions = sub.add_parser(
        "regressions", help="Detect comparable metric shifts and recommend rollback"
    )
    regressions_sub = regressions.add_subparsers(
        dest="regressions_command", required=True
    )
    regressions_analyze = regressions_sub.add_parser(
        "analyze", help="Analyze six required metrics from a bounded JSON request"
    )
    regressions_analyze.add_argument("request_file")
    regressions_report = regressions_sub.add_parser(
        "report", help="Inspect a retained regression report"
    )
    regressions_report.add_argument("run_id")

    security = sub.add_parser(
        "security", help="Assess content provenance and trusted approvals"
    )
    security_sub = security.add_subparsers(
        dest="security_command", required=True
    )
    security_assess = security_sub.add_parser(
        "assess", help="Classify content authority without storing its text"
    )
    security_assess.add_argument("request_file")
    security_approve = security_sub.add_parser(
        "approve", help="Approve one exact sensitive derivation"
    )
    security_approve.add_argument("approval_file")
    security_inspect = security_sub.add_parser(
        "inspect", help="Inspect one content assessment"
    )
    security_inspect.add_argument("assessment_id")
    security_approval = security_sub.add_parser(
        "approval", help="Inspect one trusted workflow approval"
    )
    security_approval.add_argument("approval_id")

    plans = sub.add_parser(
        "plans", help="Create and revise progressive hierarchical plans"
    )
    plans_sub = plans.add_subparsers(dest="plans_command", required=True)
    plans_create = plans_sub.add_parser(
        "create", help="Create one coarse-to-fine plan from JSON"
    )
    plans_create.add_argument("request_file")
    plans_inspect = plans_sub.add_parser(
        "inspect", help="Inspect a current or historical plan revision"
    )
    plans_inspect.add_argument("plan_id")
    plans_inspect.add_argument("--revision", type=int)
    plans_revise = plans_sub.add_parser(
        "revise", help="Append a validated full-snapshot plan revision"
    )
    plans_revise.add_argument("plan_id")
    plans_revise.add_argument("snapshot_file")
    plans_revise.add_argument("--expected-revision", type=int, required=True)
    plans_revise.add_argument("--reason", required=True)
    plans_refine = plans_sub.add_parser(
        "refine", help="Progressively decompose one expandable plan node"
    )
    plans_refine.add_argument("plan_id")
    plans_refine.add_argument("target_node_id")
    plans_refine.add_argument("children_file")
    plans_refine.add_argument("--expected-revision", type=int, required=True)
    plans_refine.add_argument("--reason", required=True)
    plans_transition = plans_sub.add_parser(
        "transition", help="Append a plan lifecycle revision"
    )
    plans_transition.add_argument("plan_id")
    plans_transition.add_argument(
        "phase", choices=("executing", "completed", "cancelled")
    )
    plans_transition.add_argument("--expected-revision", type=int, required=True)
    plans_transition.add_argument("--reason", required=True)
    plans_history = plans_sub.add_parser(
        "history", help="Inspect immutable plan revision history"
    )
    plans_history.add_argument("plan_id")

    evaluate = sub.add_parser(
        "evaluate", help="Run or inspect independent retained evaluation"
    )
    evaluate_sub = evaluate.add_subparsers(
        dest="evaluate_command", required=True
    )
    evaluate_run = evaluate_sub.add_parser(
        "run", help="Run the deterministic evaluation panel from JSON"
    )
    evaluate_run.add_argument("case_file")
    evaluate_run.add_argument("--task-id")
    evaluate_run.add_argument("--pass-threshold", type=float, default=0.7)
    evaluate_run.add_argument(
        "--predicted-confidence",
        type=float,
        help="Optional confidence forecast retained before panel evaluation",
    )
    evaluate_report = evaluate_sub.add_parser(
        "report", help="Inspect one retained evaluation run"
    )
    evaluate_report.add_argument("run_id")

    calibration = sub.add_parser(
        "calibration",
        help="Compare confidence forecasts with retained outcomes",
    )
    calibration_sub = calibration.add_subparsers(
        dest="calibration_command", required=True
    )
    calibration_report = calibration_sub.add_parser(
        "report", help="Build a fixed-bin reliability curve"
    )
    calibration_report.add_argument(
        "domain", choices=("memory", "routing", "evaluation")
    )
    calibration_report.add_argument("--group")
    calibration_report.add_argument("--bins", type=int, default=10)
    calibration_interpret = calibration_sub.add_parser(
        "interpret", help="Interpret confidence from empirical outcomes"
    )
    calibration_interpret.add_argument(
        "domain", choices=("memory", "routing", "evaluation")
    )
    calibration_interpret.add_argument("confidence", type=float)
    calibration_interpret.add_argument("--group")
    calibration_interpret.add_argument("--bins", type=int, default=10)
    calibration_interpret.add_argument(
        "--minimum-samples", type=int, default=20
    )

    resources = sub.add_parser(
        "resources", help="Manage immutable task resource budgets"
    )
    resources_sub = resources.add_subparsers(
        dest="resources_command", required=True
    )
    resources_create = resources_sub.add_parser(
        "create", help="Create one immutable task budget from JSON"
    )
    resources_create.add_argument("task_id")
    resources_create.add_argument("budget_file")
    resources_status = resources_sub.add_parser(
        "status", help="Inspect held, used, and remaining task resources"
    )
    resources_status.add_argument("task_id")
    resources_approve = resources_sub.add_parser(
        "approve", help="Approve one exact soft-limit escalation"
    )
    resources_approve.add_argument("task_id")
    resources_approve.add_argument("quote_file")
    resources_approve.add_argument("--approval-reference", required=True)
    resources_approve.add_argument("--reason", required=True)
    resources_approve.add_argument("--evidence", action="append", required=True)
    resources_approve.add_argument("--expires-at")

    cache = sub.add_parser(
        "cache", help="Inspect or prune the safe local cache"
    )
    cache_sub = cache.add_subparsers(dest="cache_command", required=True)
    cache_sub.add_parser(
        "status", help="Report entries, outcomes, and estimated savings"
    )
    cache_sub.add_parser(
        "prune", help="Delete expired cache entries"
    )

    dedup = sub.add_parser(
        "dedup", help="Run or inspect advisory duplicate detection"
    )
    dedup_sub = dedup.add_subparsers(
        dest="dedup_command", required=True
    )
    dedup_scan = dedup_sub.add_parser(
        "scan", help="Scan bounded persisted artifact metadata and content"
    )
    dedup_scan.add_argument(
        "--kind",
        action="append",
        choices=(
            "memory", "context", "skill", "tool_output", "model_request"
        ),
        help="Artifact kind to scan; repeat as needed (default: all)",
    )
    dedup_scan.add_argument("--scope", required=True)
    dedup_scan.add_argument("--limit", type=int, default=100)
    dedup_report = dedup_sub.add_parser(
        "report", help="Load one immutable deduplication report"
    )
    dedup_report.add_argument("run_id")
    dedup_report.add_argument("--scope", required=True)

    improvements = sub.add_parser(
        "improvements",
        help="Inspect the bounded autonomous improvement safety envelope",
    )
    improvements_sub = improvements.add_subparsers(
        dest="improvements_command", required=True
    )
    improvements_sub.add_parser(
        "status", help="Show immutable active safe-policy versions"
    )
    improvements_ready = improvements_sub.add_parser(
        "readiness", help="Check target telemetry and governance prerequisites"
    )
    improvements_ready.add_argument(
        "target",
        choices=(
            "retrieval_weights",
            "context_thresholds",
            "skill_instructions",
            "skill_routing_thresholds",
        ),
    )
    improvements_ready.add_argument("--scope", default="global")
    improvements_report = improvements_sub.add_parser(
        "report", help="Load one content-minimized improvement run"
    )
    improvements_report.add_argument("run_id")
    improvements_rollback = improvements_sub.add_parser(
        "rollback", help="CAS rollback the still-current policy head"
    )
    improvements_rollback.add_argument(
        "target",
        choices=(
            "retrieval_weights",
            "context_thresholds",
            "skill_routing_thresholds",
        ),
    )

    meta_context = sub.add_parser(
        "meta-context", help="Manage experimental context strategy candidates"
    )
    meta_context_sub = meta_context.add_subparsers(
        dest="meta_context_command", required=True
    )
    meta_context_sub.add_parser(
        "readiness", help="Show experimental and production activation gates"
    )
    meta_context_propose = meta_context_sub.add_parser(
        "propose", help="Create one immutable strategy candidate from JSON"
    )
    meta_context_propose.add_argument("request_file")
    meta_context_inspect = meta_context_sub.add_parser(
        "inspect", help="Inspect one content-free strategy candidate"
    )
    meta_context_inspect.add_argument("strategy_id")
    meta_context_report = meta_context_sub.add_parser(
        "report", help="Inspect one content-minimized paired evaluation"
    )
    meta_context_report.add_argument("run_id")
    improvements_rollback.add_argument(
        "--expected-head", required=True,
        help="Exact current version id; prevents clobbering a newer head",
    )

    reflect = sub.add_parser(
        "reflect", help="Run or inspect one bounded structured reflection"
    )
    reflect_sub = reflect.add_subparsers(
        dest="reflect_command", required=True
    )
    reflect_run = reflect_sub.add_parser(
        "run", help="Run one evidence-driven reflection pass from JSON"
    )
    reflect_run.add_argument("request_file")
    reflect_report = reflect_sub.add_parser(
        "report", help="Inspect one retained reflection"
    )
    reflect_report.add_argument("run_id")

    learn = sub.add_parser(
        "learn", help="Run or inspect atomic post-task learning"
    )
    learn_sub = learn.add_subparsers(dest="learn_command", required=True)
    learn_plan = learn_sub.add_parser(
        "plan",
        help="Inspect task evidence and draft a learning request without writes",
    )
    learn_plan.add_argument("task_id")
    learn_plan.add_argument(
        "--run-id",
        help="Select one exact execution when the task has multiple runs",
    )
    learn_run = learn_sub.add_parser(
        "run", help="Run the transactional learning pipeline from JSON"
    )
    learn_run.add_argument("request_file")
    learn_report = learn_sub.add_parser(
        "report", help="Inspect one completed learning transaction"
    )
    learn_report.add_argument("run_id")

    code = sub.add_parser(
        "code", help="Index and structurally retrieve bounded repository context"
    )
    code_sub = code.add_subparsers(dest="code_command", required=True)
    code_index = code_sub.add_parser(
        "index", help="Build an atomic structural-metadata repository index"
    )
    code_index.add_argument("repository", nargs="?", default=".")
    code_index.add_argument("--include-untracked", action="store_true")
    code_index.add_argument("--allow-non-git", action="store_true")
    code_index.add_argument("--max-files", type=int, default=10_000)
    code_index.add_argument("--max-file-bytes", type=int, default=512 * 1024)
    code_index.add_argument(
        "--max-total-bytes", type=int, default=50 * 1024 * 1024
    )
    code_retrieve = code_sub.add_parser(
        "retrieve", help="Retrieve one exact symbol and useful one-hop context"
    )
    code_retrieve.add_argument("query")
    code_retrieve.add_argument("--repository", default=".")
    code_retrieve.add_argument("--budget", type=int, default=4_000)
    code_retrieve.add_argument("--max-files", type=int, default=12)
    code_slice = code_sub.add_parser(
        "slice",
        help="Retrieve a bounded Python AST dependency slice",
    )
    code_slice.add_argument("query")
    code_slice.add_argument("--repository", default=".")
    code_slice.add_argument("--budget", type=int, default=4_000)
    code_slice.add_argument("--max-dependencies", type=int, default=16)

    docs = sub.add_parser(
        "docs", help="Index and retrieve hash-verified document context"
    )
    docs_sub = docs.add_subparsers(dest="docs_command", required=True)
    docs_index = docs_sub.add_parser(
        "index", help="Build a semantic-section metadata index"
    )
    docs_index.add_argument("repository", nargs="?", default=".")
    docs_index.add_argument("--max-chunk-chars", type=int, default=8_000)
    docs_retrieve = docs_sub.add_parser(
        "retrieve", help="Retrieve lexical or exact document context"
    )
    docs_retrieve.add_argument("query")
    docs_retrieve.add_argument("--repository", default=".")
    docs_retrieve.add_argument("--mode", choices=("lexical", "exact"), default="lexical")
    docs_retrieve.add_argument("--document")
    docs_retrieve.add_argument("--section-id")
    docs_retrieve.add_argument("--occurrence", type=int)
    docs_retrieve.add_argument("--budget", type=int, default=4_000)
    docs_retrieve.add_argument("--max-chunks", type=int, default=8)
    docs_propose = docs_sub.add_parser(
        "propose-reference",
        help="Generate seven source-derived docs into a review directory",
    )
    docs_propose.add_argument("repository", nargs="?", default=".")
    docs_propose.add_argument("--output")
    docs_review = docs_sub.add_parser(
        "review-reference",
        help="Verify one exact documentation proposal and show its diff state",
    )
    docs_review.add_argument("candidate")
    docs_review.add_argument("--repository", default=".")
    docs_review.add_argument("--published")
    docs_publish = docs_sub.add_parser(
        "publish-reference",
        help="Publish one fresh, explicitly approved documentation proposal",
    )
    docs_publish.add_argument("candidate")
    docs_publish.add_argument("--repository", default=".")
    docs_publish.add_argument("--destination")
    docs_publish.add_argument("--review-hash", required=True)
    docs_publish.add_argument("--approve", action="store_true")

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
    telemetry_sub.add_parser(
        "routing", help="Show skill-routing outcomes by task class"
    )
    telemetry_sub.add_parser("memory", help="Show memory metrics")
    telemetry_sub.add_parser("waste", help="Show repeatedly unused context")
    telemetry_sub.add_parser(
        "economy", help="Show adaptive token-budget allocations"
    )
    telemetry_sub.add_parser(
        "compression", help="Show context compression savings"
    )
    telemetry_attribution = telemetry_sub.add_parser(
        "attribution", help="Inspect fused context attribution for one task"
    )
    telemetry_attribution.add_argument("task_id")
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


def _execute(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    settings = Settings.from_env(database=Path(args.db) if args.db else None)

    if args.verbose:
        print(
            f"acr: command={args.command} database={settings.database}",
            file=sys.stderr,
        )

    if args.global_dry_run:
        arguments = {
            key: value
            for key, value in vars(args).items()
            if key not in {"json", "verbose", "global_dry_run"}
        }
        print(json.dumps({
            "dry_run": True,
            "executed": False,
            "command": args.command,
            "arguments": arguments,
        }, indent=2, default=str))
        return 0

    if args.command == "config":
        print(json.dumps(settings.public_summary(), indent=2))
        return 0

    if args.command in {"backup", "verify-backup", "restore"}:
        from .backup_restore import BackupManager

        manager = BackupManager(
            settings,
            benchmarks_dir=(
                args.benchmarks_dir
                if args.command == "backup"
                else "benchmarks"
            ),
        )
        if args.command == "backup":
            payload = manager.create(args.output)
        elif args.command == "verify-backup":
            payload = manager.verify(args.backup)
        else:
            payload = manager.restore(args.backup, args.target)
        print(json.dumps(payload, indent=2))
        return 0

    if args.command == "mcp":
        from .mcp_stdio import McpStdioServer
        from .provider_tools import AcrProviderTools, ProviderAccessContext

        with AdaptiveRuntime(settings=settings) as runtime:
            provider = AcrProviderTools(
                runtime,
                ProviderAccessContext(args.subject_type, args.subject_id),
            )
            return McpStdioServer(provider).run()

    if args.command == "task":
        if (
            args.task_command == "list"
            and not 1 <= args.limit <= 200
        ):
            raise ValueError("task list --limit must be 1..200")
        with AdaptiveRuntime(settings=settings) as runtime:
            if args.task_command == "show":
                row = runtime.db.connection.execute(
                    """
                    SELECT id, objective, scope, token_budget, selected_tokens,
                           status, critic_score, duration_ms, created_at,
                           completed_at
                    FROM tasks WHERE id = ?
                    """,
                    (args.task_id,),
                ).fetchone()
                if row is None:
                    raise LookupError(f"Unknown task: {args.task_id}")
                payload = dict(row)
            else:
                rows = runtime.db.connection.execute(
                    """
                    SELECT id, objective, scope, token_budget, selected_tokens,
                           status, critic_score, duration_ms, created_at,
                           completed_at
                    FROM tasks
                    ORDER BY created_at DESC, id DESC
                    LIMIT ?
                    """,
                    (args.limit,),
                ).fetchall()
                payload = {"tasks": [dict(row) for row in rows]}
        print(json.dumps(payload, indent=2))
        return 0

    if args.command == "secrets" and args.secrets_command == "scan-staged":
        findings = scan_staged_git_secrets(args.repository)
        print(json.dumps({"findings": findings, "clean": not findings}, indent=2))
        return 1 if findings else 0

    if (
        args.command == "docs"
        and args.docs_command in {
            "propose-reference", "review-reference", "publish-reference"
        }
    ):
        from .documentation_agent import DocumentationAgent

        agent = DocumentationAgent(args.repository)
        if args.docs_command == "propose-reference":
            payload = agent.propose(args.output).as_dict()
        elif args.docs_command == "review-reference":
            payload = agent.review(
                args.candidate, published_dir=args.published
            )
        else:
            payload = agent.publish(
                args.candidate,
                review_hash=args.review_hash,
                approved=args.approve,
                destination=args.destination,
            )
        print(json.dumps(payload, indent=2))
        return 0

    if args.command == "doctor":
        checks = run_doctor(settings)
        if args.json:
            print(json.dumps(
                {"checks": [check.to_dict() for check in checks]},
                indent=2,
            ))
        else:
            for check in checks:
                print(f"{check.status.upper():4}  {check.name}: {check.detail}")
        return 1 if any(check.status == "fail" for check in checks) else 0

    if args.command == "models":
        if args.models_command == "list":
            detail, models = discover_ollama_models(settings.ollama_url)
            print(json.dumps({"detail": detail, "models": models}, indent=2))
            return 0
        runtime = AdaptiveRuntime(settings=settings)
        try:
            if args.models_command == "register":
                payload = runtime.register_model(ModelProfile.from_dict(
                    _read_bounded_json_object(args.profile_file)
                )).as_dict()
            elif args.models_command == "outcome":
                payload = {"outcome_id": runtime.record_model_outcome(
                    ModelOutcome.from_dict(
                        _read_bounded_json_object(args.outcome_file)
                    )
                )}
            elif args.models_command == "route":
                payload = runtime.route_model(RouteRequest.from_dict(
                    _read_bounded_json_object(args.request_file)
                )).as_dict()
            elif args.models_command == "attempt":
                payload = runtime.record_model_attempt(
                    args.route_id,
                    RouteAttempt.from_dict(
                        _read_bounded_json_object(args.attempt_file)
                    ),
                ).as_dict()
            elif args.models_command == "route-report":
                payload = runtime.model_route(args.route_id).as_dict()
            elif args.models_command == "local-discover":
                payload = runtime.local_model_router.discover(
                    OllamaProvider(settings.ollama_url)
                )
            elif args.models_command == "local-benchmark":
                payload = runtime.local_model_router.benchmark(
                    OllamaProvider(settings.ollama_url),
                    BenchmarkDataset.load(args.dataset),
                    model=args.model,
                    seed=args.seed,
                    discovery_id=args.discovery_id,
                )
            elif args.models_command == "local-route":
                payload = runtime.route_local_model(
                    LocalRouteRequest.from_dict(
                        _read_bounded_json_object(args.request_file)
                    )
                ).as_dict()
            elif args.models_command == "local-policy":
                payload = runtime.local_model_router.policy(args.route_id)
            elif args.models_command == "workflow-plan":
                payload = runtime.plan_multi_model(
                    MultiModelWorkflowRequest.from_dict(
                        _read_bounded_json_object(args.request_file)
                    )
                ).as_dict()
            elif args.models_command == "workflow-report":
                payload = runtime.multi_model.get(args.workflow_id).as_dict()
            elif args.models_command == "workflow-outcome":
                payload = runtime.record_multi_model_outcome(
                    args.workflow_id,
                    BaselineWorkflowOutcome.from_dict(
                        _read_bounded_json_object(args.baseline_file)
                    ),
                )
            elif args.models_command == "workflow-benefit":
                payload = runtime.multi_model.benefit_report(
                    args.workflow_class, minimum_pairs=args.minimum_pairs
                )
            else:
                payload = runtime.model_router.profiles()
            print(json.dumps(payload, indent=2))
            return 0
        finally:
            runtime.close()

    if args.command == "reasoning":
        runtime = AdaptiveRuntime(settings=settings)
        try:
            if args.reasoning_command == "classify":
                payload = runtime.reasoning_depth.decide(
                    ReasoningBudgetRequest.from_dict(
                        _read_bounded_json_object(args.request_file)
                    )
                ).as_dict()
            elif args.reasoning_command == "inspect":
                payload = runtime.reasoning_depth.inspect(args.decision_id)
            elif args.reasoning_command == "policy":
                payload = runtime.reasoning_depth.policy()
            elif args.reasoning_command == "outcome":
                payload = runtime.reasoning_depth.record_outcome(
                    ReasoningOutcome.from_dict(
                        _read_bounded_json_object(args.outcome_file)
                    ),
                    trusted_runtime=False,
                )
            else:
                payload = runtime.reasoning_depth.refine(
                    args.task_class, minimum_samples=args.minimum_samples
                )
            print(json.dumps(payload, indent=2))
            return 0
        finally:
            runtime.close()

    if args.command == "research":
        from .parallel_research import (
            ParallelResearchRequest,
            ResearchReferenceCreate,
        )

        runtime = AdaptiveRuntime(settings=settings)
        try:
            if args.research_command == "reference-add":
                reference = runtime.parallel_research.add_reference(
                    ResearchReferenceCreate.from_dict(
                        _read_bounded_json_object(args.reference_file)
                    )
                )
                payload = reference.as_dict(include_content=False)
            elif args.research_command == "plan":
                payload = runtime.parallel_research.plan(
                    ParallelResearchRequest.from_dict(
                        _read_bounded_json_object(args.request_file)
                    )
                )
            elif args.research_command == "plan-inspect":
                payload = runtime.parallel_research.get_plan(args.plan_id)
            elif args.research_command == "run-inspect":
                payload = runtime.parallel_research.get_run(args.run_id)
            else:
                payload = runtime.parallel_research.get_benchmark(
                    args.benchmark_id
                )
            print(json.dumps(payload, indent=2))
            return 0
        finally:
            runtime.close()

    if args.command == "evidence-graph":
        from .evidence_graph import EvidenceGraphRequest

        runtime = AdaptiveRuntime(settings=settings)
        try:
            if args.evidence_graph_command == "create":
                payload = runtime.evidence_graph.create(
                    EvidenceGraphRequest.from_dict(
                        _read_bounded_json_object(args.request_file)
                    )
                )
            elif args.evidence_graph_command == "inspect":
                payload = runtime.evidence_graph.get(args.bundle_id)
            else:
                payload = runtime.evidence_graph.traverse(
                    args.bundle_id,
                    args.node_id,
                    direction=args.direction,
                    max_depth=args.max_depth,
                    limit=args.limit,
                )
            print(json.dumps(payload, indent=2))
            return 0
        finally:
            runtime.close()

    if args.command == "explain":
        runtime = AdaptiveRuntime(settings=settings)
        try:
            if args.explain_command == "model":
                payload = runtime.explainability.model(args.route_id)
            elif args.explain_command == "skill":
                payload = runtime.explainability.skill(
                    args.task_id, args.skill_id
                )
            elif args.explain_command == "memory":
                payload = runtime.explainability.memory(
                    args.task_id, args.memory_id
                )
            elif args.explain_command == "agent":
                payload = runtime.explainability.agent(
                    args.plan_id, args.worker_id
                )
            elif args.explain_command == "context":
                payload = runtime.explainability.context(args.task_id)
            else:
                payload = runtime.explainability.forgotten(args.memory_id)
            print(json.dumps(payload, indent=2))
            return 0
        finally:
            runtime.close()

    if args.command == "safe-mode":
        runtime = AdaptiveRuntime(settings=settings)
        try:
            if args.safe_mode_command == "status":
                payload = runtime.safe_mode.status()
            elif args.safe_mode_command == "events":
                payload = runtime.safe_mode.events(limit=args.limit)
            else:
                if args.actor is None or args.reason is None:
                    raise ValueError(
                        "safe-mode enable/disable requires --actor and --reason"
                    )
                if args.safe_mode_command == "enable":
                    payload = runtime.safe_mode.enable(
                        actor_id=args.actor, reason=args.reason
                    )
                else:
                    payload = runtime.safe_mode.disable(
                        actor_id=args.actor, reason=args.reason
                    )
            print(json.dumps(payload, indent=2))
            return 0
        finally:
            runtime.close()

    if args.command == "overrides":
        runtime = AdaptiveRuntime(settings=settings)
        try:
            if args.override_command == "apply":
                from .human_override import HumanOverrideRequest

                result = runtime.apply_human_override(
                    HumanOverrideRequest.from_dict(
                        _read_bounded_json_object(args.request_file)
                    )
                )
            elif args.override_command == "list":
                print(json.dumps([
                    item.as_dict()
                    for item in runtime.human_overrides.list(
                        active_only=args.active
                    )
                ], indent=2))
                return 0
            elif args.override_command == "show":
                result = runtime.human_overrides.get(args.override_id)
            else:
                result = runtime.revoke_human_override(
                    args.override_id,
                    actor_id=args.actor,
                    reason=args.reason,
                )
            print(json.dumps(result.as_dict(), indent=2))
            return 0
        finally:
            runtime.close()

    if args.command == "tools":
        runtime = AdaptiveRuntime(settings=settings)
        try:
            if args.tools_command == "register":
                payload = runtime.tools.register(ToolDefinition.from_dict(
                    _read_bounded_json_object(args.definition_file)
                ))
            elif args.tools_command == "list":
                payload = runtime.tools.list()
            elif args.tools_command == "inspect":
                payload = runtime.tools.get(args.name)
            elif args.tools_command == "check":
                payload = runtime.tools.authorize(ToolAccessRequest.from_dict(
                    _read_bounded_json_object(args.request_file)
                ))
            elif args.tools_command == "route":
                payload = runtime.tool_router.route(ToolRouteRequest.from_dict(
                    _read_bounded_json_object(args.request_file)
                ))
            elif args.tools_command == "agent-route":
                payload = runtime.tool_exposure.route_for_agent(
                    ToolRouteRequest.from_dict(
                        _read_bounded_json_object(args.request_file)
                    ),
                    args.agent_spec_id,
                )
            elif args.tools_command == "outcome":
                payload = {"outcome_id": runtime.tool_router.record_outcome(
                    ToolOutcome.from_dict(
                        _read_bounded_json_object(args.outcome_file)
                    )
                )}
            elif args.tools_command == "exposure-project":
                payload = runtime.tool_exposure.create_projection(
                    args.route_id, args.agent_spec_id
                )
            elif args.tools_command == "exposure-inspect":
                payload = runtime.tool_exposure.get_projection(
                    args.projection_id
                )
            elif args.tools_command == "exposure-render":
                payload = runtime.tool_exposure.render(args.projection_id)
            elif args.tools_command == "exposure-benchmark-start":
                payload = runtime.tool_exposure.start_benchmark(
                    ToolExposureBenchmarkSpec.from_dict(
                        _read_bounded_json_object(args.spec_file)
                    )
                )
            elif args.tools_command == "exposure-benchmark-trial":
                payload = {
                    "trial_id": runtime.tool_exposure.record_trial(
                        ToolExposureTrial.from_dict(
                            _read_bounded_json_object(args.trial_file)
                        )
                    )
                }
            elif args.tools_command == "exposure-benchmark-seal":
                payload = runtime.tool_exposure.seal_benchmark(args.run_id)
            elif args.tools_command == "exposure-benchmark-report":
                payload = runtime.tool_exposure.get_benchmark(args.run_id)
            else:
                payload = runtime.tool_router.get(args.route_id)
            print(json.dumps(payload, indent=2))
            return 0
        finally:
            runtime.close()

    if args.command == "capabilities":
        runtime = AdaptiveRuntime(settings=settings)
        try:
            if args.capabilities_command == "grant":
                payload = runtime.permissions.grant(
                    CapabilityGrantRequest.from_dict(
                        _read_bounded_json_object(args.grant_file)
                    )
                )
            elif args.capabilities_command == "check":
                payload = runtime.permissions.check(
                    CapabilityCheck.from_dict(
                        _read_bounded_json_object(args.check_file)
                    )
                )
            elif args.capabilities_command == "revoke":
                payload = runtime.permissions.revoke(
                    args.grant_id, reason=args.reason
                )
            elif args.capabilities_command == "inspect":
                payload = runtime.permissions.get(args.grant_id)
            else:
                payload = runtime.permissions.subject_grants(
                    args.subject_type, args.subject_id
                )
            print(json.dumps(payload, indent=2))
            return 0
        finally:
            runtime.close()

    if args.command == "audit":
        runtime = AdaptiveRuntime(settings=settings)
        try:
            if args.audit_command == "show":
                payload = runtime.audit.get(args.event_id)
            elif args.audit_command == "summary":
                payload = runtime.audit.summary()
            else:
                payload = {
                    "events": runtime.audit.list(AuditQuery(
                        event_type=args.event_type,
                        entity_type=args.entity_type,
                        entity_id=args.entity_id,
                        after=args.after,
                        before=args.before,
                        limit=args.limit,
                    ))
                }
            print(json.dumps(payload, indent=2))
            return 0
        finally:
            runtime.close()

    if args.command == "performance":
        runtime = AdaptiveRuntime(settings=settings)
        try:
            if args.performance_command == "report":
                payload = runtime.performance.report(args.run_id)
            elif args.performance_command == "list":
                payload = {
                    "profiles": runtime.performance.list(limit=args.limit)
                }
            else:
                if (
                    isinstance(args.iterations, bool)
                    or not 1 <= args.iterations <= 100
                ):
                    raise ValueError(
                        "performance --iterations must be 1..100"
                    )
                with runtime.performance.capture(
                    "cli-local-profile", scope=args.scope
                ) as profile:
                    for iteration in range(args.iterations):
                        retrieval = runtime.retrieve_memory(
                            RetrievalRequest(
                                task="Profile the local runtime.",
                                query="runtime",
                                scope=args.scope,
                                token_budget=128,
                                target_memories=5,
                            )
                        )
                        bundle = runtime.compile_context(
                            "Profile the local runtime.",
                            scope=args.scope,
                            token_budget=256,
                        )
                        profile.serialize(
                            {
                                "iteration": iteration,
                                "retrieved": len(retrieval.selected),
                                "context_blocks": len(bundle.blocks),
                            },
                            operation="json.dumps.profile_summary",
                        )
                if profile.run_id is None:
                    raise RuntimeError("Performance profile was not persisted")
                payload = runtime.performance.report(profile.run_id)
            print(json.dumps(payload, indent=2))
            return 0
        finally:
            runtime.close()

    if args.command == "plugins":
        runtime = AdaptiveRuntime(settings=settings)
        try:
            if args.plugins_command == "register":
                payload = runtime.plugins.register(
                    PluginManifest.from_dict(
                        _read_bounded_json_object(args.manifest_file)
                    )
                )
            elif args.plugins_command == "list":
                payload = runtime.plugins.list()
            elif args.plugins_command == "inspect":
                payload = runtime.plugins.get(args.name, args.version)
            elif args.plugins_command == "validation":
                payload = runtime.plugins.validation(args.validation_id)
            else:
                payload = runtime.plugins.route(
                    args.name,
                    args.version,
                    args.capability,
                    ToolRouteRequest.from_dict(
                        _read_bounded_json_object(args.request_file)
                    ),
                )
            print(json.dumps(payload, indent=2))
            return 0
        finally:
            runtime.close()

    if args.command == "recovery":
        runtime = AdaptiveRuntime(settings=settings)
        try:
            if args.recovery_command == "create":
                plan = _read_bounded_json_object(args.plan_file)
                if set(plan) != {"task_id", "steps"} or not isinstance(
                    plan["task_id"], str
                ) or not isinstance(plan["steps"], list):
                    raise ValueError(
                        "Recovery plan requires task_id and steps only"
                    )
                payload = runtime.recovery.create(
                    plan["task_id"],
                    tuple(RecoveryStep.from_dict(step) for step in plan["steps"]),
                )
            elif args.recovery_command == "inspect":
                payload = runtime.recovery.get(args.run_id)
            elif args.recovery_command == "interrupt":
                payload = runtime.recovery.mark_interrupted(
                    args.run_id,
                    actor=args.actor,
                    reason=args.reason,
                    evidence=tuple(args.evidence),
                )
            else:
                payload = runtime.recovery.resolve_review(
                    args.run_id,
                    args.sequence,
                    args.decision,
                    actor=args.actor,
                    reason=args.reason,
                    evidence=tuple(args.evidence),
                )
            print(json.dumps(payload, indent=2))
            return 0
        finally:
            runtime.close()

    if args.command == "secrets":
        runtime = AdaptiveRuntime(settings=settings)
        try:
            if args.secrets_command == "inspect":
                payload = runtime.secrets.inspect(args.event_id)
            else:
                reference = SecretReference.parse(args.reference)
                lease = runtime.secrets.resolve(
                    reference,
                    subject_type=args.subject_type,
                    subject_id=args.subject_id,
                )
                payload = {
                    **reference.public_summary(),
                    "lease_id": lease.id,
                    "audit_id": lease.audit_id,
                    "resolved": True,
                }
                lease.close()
            print(json.dumps(payload, indent=2))
            return 0
        finally:
            runtime.close()

    if args.command == "privacy":
        runtime = AdaptiveRuntime(settings=settings)
        try:
            if args.privacy_command == "policies":
                payload = runtime.privacy.policies()
            elif args.privacy_command == "policy-set":
                policy = _read_bounded_json_object(args.policy_file)
                required = {
                    "allowed_providers", "retention_days", "exportable",
                    "deletion_requirement",
                }
                if set(policy) != required:
                    raise ValueError(
                        f"Privacy policy must contain {sorted(required)}"
                    )
                if not isinstance(policy["allowed_providers"], list):
                    raise ValueError("allowed_providers must be a list")
                payload = runtime.privacy.update_policy(
                    args.classification,
                    allowed_providers=tuple(policy["allowed_providers"]),
                    retention_days=policy["retention_days"],
                    exportable=policy["exportable"],
                    deletion_requirement=policy["deletion_requirement"],
                    actor=args.actor,
                    reason=args.reason,
                ).as_dict()
            elif args.privacy_command == "classify":
                record = runtime.privacy.classify(
                    args.memory_id,
                    args.classification,
                    actor=args.actor,
                    reason=args.reason,
                    allow_downgrade=args.allow_downgrade,
                )
                payload = {
                    "memory_id": record.id,
                    "sensitivity": record.sensitivity.value,
                    "retention_until": record.retention_until,
                    "privacy_policy_version": record.privacy_policy_version,
                }
            elif args.privacy_command == "provider-check":
                payload = runtime.privacy.authorize_provider(
                    tuple(args.memory_ids),
                    provider=args.provider,
                    local=args.local,
                )
            elif args.privacy_command == "retention-due":
                payload = runtime.privacy.retention_due(at=args.at)
            elif args.privacy_command == "export":
                payload = runtime.privacy.export(tuple(args.memory_ids))
            elif args.privacy_command == "delete-plan":
                payload = runtime.privacy.plan_deletion(
                    args.memory_id,
                    requested_by=args.actor,
                    reason=args.reason,
                )
            elif args.privacy_command == "delete-approve":
                payload = runtime.privacy.approve_deletion(args.request_id)
            else:
                payload = runtime.privacy.deletion_request(args.request_id)
            print(json.dumps(payload, indent=2))
            return 0
        finally:
            runtime.close()

    if args.command == "experiments":
        runtime = AdaptiveRuntime(settings=settings)
        try:
            if args.experiments_command == "create":
                payload = runtime.experiments.create(
                    ExperimentCreate.from_dict(
                        _read_bounded_json_object(args.request_file)
                    )
                )
            elif args.experiments_command == "start":
                payload = runtime.experiments.start(args.experiment_id)
            elif args.experiments_command == "assign":
                payload = runtime.experiments.assign(
                    args.experiment_id, args.unit_id
                )
            elif args.experiments_command == "outcome":
                payload = {
                    "outcome_id": runtime.experiments.record(
                        args.experiment_id,
                        ExperimentOutcome.from_dict(
                            _read_bounded_json_object(args.outcome_file)
                        ),
                    )
                }
            elif args.experiments_command == "report":
                payload = runtime.experiments.report(args.experiment_id)
            elif args.experiments_command == "finish":
                payload = runtime.experiments.finish(args.experiment_id)
            elif args.experiments_command == "cancel":
                payload = runtime.experiments.finish(
                    args.experiment_id, cancelled=True
                )
            else:
                payload = runtime.experiments.get(args.experiment_id)
            print(json.dumps(payload, indent=2))
            return 0
        finally:
            runtime.close()

    if args.command == "regressions":
        runtime = AdaptiveRuntime(settings=settings)
        try:
            if args.regressions_command == "analyze":
                payload = runtime.regressions.analyze(
                    RegressionRequest.from_dict(
                        _read_bounded_json_object(args.request_file)
                    )
                )
            else:
                payload = runtime.regressions.report(args.run_id)
            print(json.dumps(payload, indent=2))
            return 0
        finally:
            runtime.close()

    if args.command == "security":
        runtime = AdaptiveRuntime(settings=settings)
        try:
            if args.security_command == "assess":
                payload = runtime.content_security.assess(
                    ContentAssessmentRequest.from_dict(
                        _read_bounded_json_object(args.request_file)
                    )
                )
            elif args.security_command == "approve":
                payload = runtime.content_security.approve(
                    TrustedWorkflowApprovalRequest.from_dict(
                        _read_bounded_json_object(args.approval_file)
                    )
                )
            elif args.security_command == "inspect":
                payload = runtime.content_security.get(args.assessment_id)
            else:
                payload = runtime.content_security.approval(args.approval_id)
            print(json.dumps(payload, indent=2))
            return 0
        finally:
            runtime.close()

    if args.command == "benchmark":
        if args.benchmark_command in ("skill", "skill-report"):
            runtime = AdaptiveRuntime(settings=settings)
            try:
                if args.benchmark_command == "skill":
                    payload = runtime.skill_benchmarks.analyze(
                        SkillBenchmarkRequest.from_dict(
                            _read_bounded_json_object(args.request_file)
                        )
                    )
                else:
                    payload = runtime.skill_benchmarks.report(args.run_id)
                print(json.dumps(payload, indent=2))
                return 0
            finally:
                runtime.close()
        if args.benchmark_command in ("validate-token", "token"):
            token_dataset = TokenBenchmarkDataset.load(args.dataset)
            if args.benchmark_command == "validate-token":
                print(json.dumps({
                    "name": token_dataset.name,
                    "version": token_dataset.version,
                    "cases": len(token_dataset.cases),
                    "categories": sorted(
                        {case.category for case in token_dataset.cases}
                    ),
                    "arms": list((
                        "full_context", "semantic_retrieval",
                        "hybrid_retrieval", "acr_context_compiler",
                    )),
                }, indent=2))
                return 0
            token_report = TokenBenchmarkRunner().run(token_dataset)
            token_json = json.dumps(token_report.to_dict(), indent=2)
            if args.output:
                Path(args.output).write_text(
                    token_json + "\n", encoding="utf-8"
                )
            print(token_json)
            return 0
        if args.benchmark_command in ("validate-memory", "memory"):
            memory_dataset = MemoryBenchmarkDataset.load(args.dataset)
            if args.benchmark_command == "validate-memory":
                print(json.dumps({
                    "name": memory_dataset.name,
                    "version": memory_dataset.version,
                    "cases": len(memory_dataset.cases),
                    "categories": sorted(
                        {case.category for case in memory_dataset.cases}
                    ),
                    "arms": [
                        "no_memory", "raw_conversation",
                        "simple_rag", "acr_memory",
                    ],
                }, indent=2))
                return 0
            memory_report = MemoryBenchmarkRunner().run(memory_dataset)
            memory_json = json.dumps(memory_report.to_dict(), indent=2)
            if args.output:
                Path(args.output).write_text(
                    memory_json + "\n", encoding="utf-8"
                )
            print(memory_json)
            return 0
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

    if args.command == "serve":
        try:
            address = ipaddress.ip_address(args.host)
        except ValueError as error:
            raise ValueError("serve --host must be an IP address") from error
        token = os.environ.get("ACR_API_TOKEN")
        operator_id = os.environ.get("ACR_API_OPERATOR_ID")
        if not address.is_loopback and not token:
            raise ValueError(
                "Non-loopback API binding requires ACR_API_TOKEN"
            )
        if not 1 <= args.port <= 65_535:
            raise ValueError("serve --port must be 1..65535")
        from .api import create_app
        import uvicorn

        uvicorn.run(
            create_app(
                settings.database,
                api_token=token,
                operator_id=operator_id,
            ),
            host=args.host,
            port=args.port,
            access_log=True,
        )
        return 0

    with AdaptiveRuntime(settings=settings) as runtime:
        if args.command == "learn":
            from .learning_controller import LearningRequest

            if args.learn_command == "plan":
                payload = runtime.learning_plan(
                    args.task_id,
                    execution_run_id=args.run_id,
                ).as_dict()
            elif args.learn_command == "run":
                request = LearningRequest.from_dict(
                    _read_bounded_json_object(args.request_file)
                )
                payload = runtime.learn(request).as_dict()
            else:
                payload = runtime.learning_run(args.run_id).as_dict()
            print(json.dumps(payload, indent=2))
        elif args.command == "reflect":
            from .reflection import ReflectionRequest

            if args.reflect_command == "run":
                request = ReflectionRequest.from_dict(
                    _read_bounded_json_object(args.request_file)
                )
                payload = runtime.reflect(request).as_dict()
            else:
                payload = runtime.reflection(args.run_id).as_dict()
            print(json.dumps(payload, indent=2))
        elif args.command == "evaluate":
            from .evaluation import EvaluationCase

            if args.evaluate_command == "run":
                case = EvaluationCase.from_dict(
                    _read_bounded_json_object(args.case_file)
                )
                payload = runtime.evaluate(
                    case,
                    task_id=args.task_id,
                    pass_threshold=args.pass_threshold,
                    predicted_confidence=args.predicted_confidence,
                ).as_dict()
            else:
                payload = runtime.evaluation(args.run_id).as_dict()
            print(json.dumps(payload, indent=2))
        elif args.command == "calibration":
            if args.calibration_command == "report":
                payload = runtime.calibration_report(
                    args.domain,
                    group_key=args.group,
                    bins=args.bins,
                ).as_dict()
            else:
                payload = runtime.interpret_confidence(
                    args.domain,
                    args.confidence,
                    group_key=args.group,
                    bins=args.bins,
                    minimum_samples=args.minimum_samples,
                ).as_dict()
            print(json.dumps(payload, indent=2))
        elif args.command == "resources":
            from .resource_governor import ResourceBudget, ResourceVector

            if args.resources_command == "create":
                payload = runtime.create_resource_budget(
                    ResourceBudget.from_dict(
                        args.task_id,
                        _read_bounded_json_object(args.budget_file),
                    )
                )
                print(
                    json.dumps(
                        {
                            "task_id": payload.task_id,
                            "soft": payload.soft.as_dict(),
                            "hard": payload.hard.as_dict(),
                            "escalation_mode": payload.escalation_mode,
                        },
                        indent=2,
                    )
                )
            elif args.resources_command == "approve":
                escalation_id = runtime.resources.approve_escalation(
                    args.task_id,
                    ResourceVector.from_dict(
                        _read_bounded_json_object(args.quote_file)
                    ),
                    approval_reference=args.approval_reference,
                    reason=args.reason,
                    evidence=tuple(args.evidence),
                    expires_at=args.expires_at,
                )
                print(json.dumps({"escalation_id": escalation_id}, indent=2))
            else:
                print(
                    json.dumps(
                        runtime.resource_status(args.task_id), indent=2
                    )
                )
        elif args.command == "cache":
            if args.cache_command == "prune":
                print(
                    json.dumps(
                        {"removed_entries": runtime.cache.prune()}, indent=2
                    )
                )
            else:
                print(json.dumps(runtime.cache.status(), indent=2))
        elif args.command == "dedup":
            if args.dedup_command == "scan":
                kinds = args.kind or [
                    "memory", "context", "skill",
                    "tool_output", "model_request",
                ]
                payload = runtime.scan_duplicates(
                    kinds=kinds, scope=args.scope, limit=args.limit
                ).as_dict()
            else:
                payload = runtime.deduplication_report(
                    args.run_id, scope=args.scope
                ).as_dict()
            print(json.dumps(payload, indent=2))
        elif args.command == "improvements":
            if args.improvements_command == "status":
                payload = runtime.improvement_policies.status()
            elif args.improvements_command == "readiness":
                payload = runtime.improvements.readiness(
                    args.target, scope=args.scope
                )
            elif args.improvements_command == "report":
                payload = runtime.improvements.report(args.run_id)
            else:
                version = runtime.improvement_policies.rollback(
                    args.target, expected_head_id=args.expected_head
                )
                payload = {
                    "target": version.target,
                    "restored_version_id": version.id,
                    "restored_version": version.version,
                    "config_hash": version.config_hash,
                }
            print(json.dumps(payload, indent=2))
        elif args.command == "meta-context":
            if args.meta_context_command == "readiness":
                payload = runtime.meta_context.readiness()
            elif args.meta_context_command == "propose":
                request = _read_bounded_json_object(args.request_file)
                if set(request) != {"strategy", "hypothesis"}:
                    raise ValueError(
                        "meta-context proposal requires strategy and hypothesis"
                    )
                if not isinstance(request["strategy"], dict):
                    raise ValueError("strategy must be an object")
                payload = runtime.meta_context.propose(
                    request["strategy"],
                    hypothesis=str(request["hypothesis"]),
                )
            elif args.meta_context_command == "inspect":
                payload = runtime.meta_context.get(args.strategy_id)
            else:
                payload = runtime.meta_context.report(args.run_id)
            print(json.dumps(payload, indent=2))
        elif args.command == "plans":
            from .hierarchical_planner import PlanSnapshot, PlanningRequest

            if args.plans_command == "create":
                request = PlanningRequest.from_dict(
                    _read_bounded_json_object(args.request_file)
                )
                payload = runtime.create_hierarchical_plan(request).as_dict()
            elif args.plans_command == "inspect":
                payload = runtime.hierarchical_plan(
                    args.plan_id, revision=args.revision
                ).as_dict()
            elif args.plans_command == "revise":
                snapshot = PlanSnapshot.from_dict(
                    _read_bounded_json_object(args.snapshot_file)
                )
                payload = runtime.revise_hierarchical_plan(
                    args.plan_id,
                    expected_revision=args.expected_revision,
                    snapshot=snapshot,
                    reason=args.reason,
                ).as_dict()
            elif args.plans_command == "refine":
                from .hierarchical_planner import PlanWorkHint

                child_payload = _read_bounded_json_object(
                    args.children_file
                )
                if set(child_payload) != {"children"} or not isinstance(
                    child_payload["children"], list
                ):
                    raise ValueError(
                        "refinement file must contain only a children list"
                    )
                payload = runtime.refine_hierarchical_plan(
                    args.plan_id,
                    expected_revision=args.expected_revision,
                    target_node_id=args.target_node_id,
                    children=tuple(
                        PlanWorkHint.from_dict(item)
                        for item in child_payload["children"]
                    ),
                    reason=args.reason,
                ).as_dict()
            elif args.plans_command == "transition":
                payload = runtime.transition_hierarchical_plan(
                    args.plan_id,
                    expected_revision=args.expected_revision,
                    phase=args.phase,
                    reason=args.reason,
                ).as_dict()
            else:
                payload = {
                    "revisions": [
                        item.as_dict()
                        for item in runtime.hierarchical_plan_history(
                            args.plan_id
                        )
                    ]
                }
            print(json.dumps(payload, indent=2))
        elif args.command == "agents":
            if args.agents_command == "define":
                spec = AgentSpec.from_dict(
                    _read_bounded_json_object(args.spec_file)
                )
                payload = runtime.define_agent_spec(spec).as_dict()
            elif args.agents_command == "inspect":
                payload = runtime.inspect_agent_spec(args.agent_id).as_dict()
            elif args.agents_command == "factory-plan":
                from .agent_factory import AgentFactoryRequest

                request = AgentFactoryRequest.from_dict(
                    _read_bounded_json_object(args.request_file)
                )
                payload = runtime.plan_agent_factory(request).as_dict()
            elif args.agents_command == "factory-report":
                payload = runtime.agent_factory_plan(args.plan_id).as_dict()
            elif args.agents_command == "topology-record":
                from .topology_learning import TopologyOutcomeCreate

                create = TopologyOutcomeCreate.from_dict(
                    _read_bounded_json_object(args.outcome_file)
                )
                payload = runtime.record_topology_outcome(create).as_dict()
            elif args.agents_command == "topology-recommend":
                from .agent_factory import AgentFactoryRequest

                request = AgentFactoryRequest.from_dict(
                    _read_bounded_json_object(args.request_file)
                )
                payload = runtime.recommend_topology(request).as_dict()
            elif args.agents_command == "topology-outcome":
                payload = runtime.topology_outcome(
                    args.outcome_id
                ).as_dict()
            elif args.agents_command == "topology-recipes":
                payload = {
                    "recipes": [
                        item.as_dict()
                        for item in runtime.topology_recipes(
                            task_class=args.task_class
                        )
                    ]
                }
            else:
                payload = {"agents": list(runtime.list_agent_specs())}
            print(json.dumps(payload, indent=2))
        elif args.command == "run":
            from .resource_governor import ResourceBudget, ResourceVector

            if (
                min(
                    args.max_input_tokens,
                    args.max_output_tokens,
                    args.max_model_calls,
                    args.max_agents,
                    args.max_duration_seconds,
                )
                < 1
                or args.max_tool_calls < 0
                or args.max_cost < 0
            ):
                print("Resource limits must be finite non-negative bounds.")
                return 2
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
                timeout_seconds=args.max_duration_seconds,
                sink=recorder.record_model_call,
                reasoning_modes_by_model={
                    model: tuple(args.reasoning_mode_supported)
                } if args.reasoning_mode_supported else None,
                reasoning_efforts_by_model={
                    model: tuple(args.reasoning_effort_supported)
                } if args.reasoning_effort_supported else None,
            )
            capabilities = provider.capabilities(model)
            reasoning_decision = runtime.reasoning_depth.decide(
                ReasoningBudgetRequest(
                    task=args.task,
                    task_class=args.task_class,
                ),
                provider_capabilities=capabilities,
            )
            reasoning_control = runtime.reasoning_depth.control_for(
                reasoning_decision, capabilities
            )
            task = Task(
                args.task,
                token_budget=args.max_output_tokens,
                money_budget=args.max_cost / 1_000_000,
                time_budget_seconds=args.max_duration_seconds,
                permissions=("local_model",),
                scope=args.scope,
                task_class=args.task_class,
                strategy=args.strategy,
                environment_json=args.environment,
            )
            hard_resources = ResourceVector(
                input_tokens=args.max_input_tokens,
                output_tokens=args.max_output_tokens,
                model_calls=args.max_model_calls,
                tool_calls=args.max_tool_calls,
                agents=args.max_agents,
                cost=args.max_cost,
                duration=args.max_duration_seconds * 1_000,
            )
            runtime.create_resource_budget(
                ResourceBudget(
                    task_id=task.id,
                    soft=hard_resources,
                    hard=hard_resources,
                    escalation_mode="none",
                    evidence=("cli_run_hard_limits",),
                )
            )
            root_reservation = runtime.resources.reserve(
                task.id,
                ResourceVector(agents=1),
                idempotency_key="root-agent",
                kind="agent",
                evidence=("cli_root_agent",),
            )
            runtime.resources.commit(
                root_reservation.id,
                ResourceVector(agents=1),
                evidence=("root_agent_started",),
            )
            runner = TaskRunner(
                planner=ReasoningBudgetPlanner(reasoning_decision),
                executor=ProviderExecutor(
                    provider,
                    model=model,
                    governor=runtime.resources,
                    cost_accounting=runtime.costs,
                    resource_quote=ResourceVector(
                        input_tokens=max(
                            1,
                            (
                                args.max_input_tokens
                                * reasoning_decision.context_fraction_micros
                            ) // 1_000_000,
                        ),
                        output_tokens=args.max_output_tokens,
                        model_calls=1,
                        cost=args.max_cost,
                        duration=args.max_duration_seconds * 1_000,
                    ),
                    reasoning=reasoning_control,
                    reasoning_decision_id=reasoning_decision.id,
                ),
                verifier=PassVerifier(),
                evaluator=PassEvaluator(),
                event_bus=event_bus,
                planning_advisors=(
                    FailurePlanningAdvisor(runtime.failures),
                ),
            )
            run_result = runner.run(task)
            recorder.record_run(run_result)
            action_usage = [
                json.loads(action.output_json)
                for action in run_result.actions
            ]
            policy_conformant = (
                reasoning_decision.verification_mode == "deterministic"
                and run_result.verification is not None
                and run_result.verification.source == "deterministic-verifier"
            )
            reasoning_receipt = runtime.reasoning_depth.record_outcome(
                ReasoningOutcome(
                    decision_id=reasoning_decision.id,
                    success=(
                        run_result.state.value == "completed"
                        and run_result.evaluation is not None
                        and run_result.evaluation.passed
                    ),
                    quality=(
                        run_result.evaluation.score
                        if run_result.evaluation is not None else 0.0
                    ),
                    verification_passed=(
                        run_result.verification.passed
                        if run_result.verification is not None else False
                    ),
                    hard_violation=False,
                    policy_conformant=policy_conformant,
                    input_tokens=sum(
                        int(item.get("input_tokens") or 0)
                        for item in action_usage
                    ),
                    output_tokens=sum(
                        int(item.get("output_tokens") or 0)
                        for item in action_usage
                    ),
                    reasoning_tokens=(
                        sum(
                            int(item.get("reasoning_tokens") or 0)
                            for item in action_usage
                        )
                        if action_usage and all(
                            item.get("reasoning_tokens") is not None
                            for item in action_usage
                        )
                        else None
                    ),
                    latency_ms=sum(
                        int(item.get("latency_ms") or 0)
                        for item in action_usage
                    ),
                    cost_microunits=0,
                    evidence=(
                        f"task_run:{run_result.id}",
                        (
                            f"verifier:{run_result.verification.source}"
                            if run_result.verification is not None
                            else "verifier:none"
                        ),
                    ),
                ),
                trusted_runtime=True,
            )
            if run_result.result is not None and not args.json:
                print(run_result.result.content)
            print(
                json.dumps(
                    {
                        "task_id": task.id,
                        "run_id": run_result.id,
                        "reasoning_decision_id": reasoning_decision.id,
                        "reasoning_complexity": reasoning_decision.complexity,
                        "provider_reasoning_mode": reasoning_control.mode,
                        "reasoning_outcome_id": reasoning_receipt["id"],
                        "refinement_eligible": reasoning_receipt[
                            "eligible_for_refinement"
                        ],
                        "state": run_result.state.value,
                        "content": (
                            run_result.result.content
                            if args.json and run_result.result is not None
                            else None
                        ),
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
                sensitivity=args.sensitivity,
            )
            print(memory_id)
        elif args.command == "memory":
            if args.memory_command == "scope-add":
                scope = runtime.db.scopes.register(
                    args.id,
                    MemoryScopeKind(args.kind),
                    parent_id=args.parent,
                )
                print(json.dumps({
                    "id": scope.id,
                    "kind": scope.kind.value,
                    "parent_id": scope.parent_id,
                }, indent=2))
            elif args.memory_command == "scope-list":
                print(json.dumps([
                    {
                        "id": scope.id,
                        "kind": scope.kind.value,
                        "parent_id": scope.parent_id,
                    }
                    for scope in runtime.db.scopes.list()
                ], indent=2))
            elif args.memory_command == "scope-path":
                print(json.dumps([
                    {
                        "id": scope.id,
                        "kind": scope.kind.value,
                        "parent_id": scope.parent_id,
                    }
                    for scope in runtime.db.scopes.ancestors(args.id)
                ], indent=2))
            elif args.memory_command == "decision-add":
                record = runtime.record_decision(
                    DecisionCreate.from_dict(
                        _read_bounded_json_object(args.decision_file)
                    )
                )
                print(json.dumps({
                    "id": record.id,
                    "topic": record.subject,
                    "scope": record.scope,
                    "valid_from": record.valid_from,
                    "supersedes": record.supersedes,
                }, indent=2))
            elif args.memory_command == "decision-check":
                assumptions: dict[str, str] = {}
                for item in args.assumption:
                    if "=" not in item:
                        raise ValueError("--assumption must use name=value")
                    name, value = item.split("=", 1)
                    if not name.strip() or not value.strip():
                        raise ValueError("--assumption must use non-empty name=value")
                    if name.strip().casefold() in {
                        key.casefold() for key in assumptions
                    }:
                        raise ValueError("Assumption names must be unique")
                    assumptions[name.strip()] = value.strip()
                print(json.dumps(runtime.decisions.check(DecisionCheck(
                    task=args.task or args.query,
                    query=args.query,
                    scope=args.scope,
                    assumptions=assumptions,
                    token_budget=args.budget,
                    limit=args.limit,
                )), indent=2))
            elif args.memory_command == "decision-show":
                print(json.dumps(runtime.decisions.inspect(args.id), indent=2))
            elif args.memory_command == "conflict-check":
                print(json.dumps(runtime.conflicts.analyze_subject(
                    args.subject, scope=args.scope
                ), indent=2))
            elif args.memory_command == "conflict-compare":
                print(json.dumps(runtime.conflicts.compare(
                    args.left_id, args.right_id
                ).as_dict(), indent=2))
            elif args.memory_command == "summary":
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
                    sensitivity=args.sensitivity,
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
                        cache_max_age_seconds=args.cache_max_age,
                    )
                )
                print(
                    json.dumps(
                        {
                            "candidate_count": result.candidate_count,
                            "selected_tokens": result.selected_tokens,
                            "semantic_available": result.semantic_available,
                            "semantic_status": result.semantic_status,
                            "cache_status": result.cache_status,
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
                        content_origin=(
                            args.content_origin
                            or infer_content_origin(args.source_type)
                        ),
                        provenance=tuple(args.provenance),
                        security_assessment_id=args.security_assessment,
                        workflow_approval_id=args.workflow_approval,
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
                            "security_assessment_id": (
                                decision.security_assessment_id
                            ),
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
        elif args.command == "cost":
            if args.cost_command == "rate-add":
                payload = runtime.costs.add_rate(
                    PriceRate.from_dict(
                        _read_bounded_json_object(args.rate_file)
                    )
                )
            elif args.cost_command == "rates":
                payload = {"rates": runtime.costs.list_rates()}
            elif args.cost_command == "record-model":
                payload = runtime.costs.record_model(
                    **_read_bounded_json_object(args.usage_file)
                )
            elif args.cost_command == "record-tool":
                payload = runtime.costs.record_tool(
                    **_read_bounded_json_object(args.usage_file)
                )
            elif args.cost_command == "local-profile-add":
                payload = runtime.costs.add_local_profile(
                    LocalCostProfile.from_dict(
                        _read_bounded_json_object(args.profile_file)
                    )
                )
            elif args.cost_command == "local-status":
                payload = runtime.costs.local_status()
            elif args.cost_command == "record-local":
                payload = runtime.costs.record_local(
                    **_read_bounded_json_object(args.usage_file)
                )
            elif args.cost_command == "event":
                payload = runtime.costs.event(args.event_id)
            else:
                payload = runtime.costs.report()
            print(json.dumps(payload, indent=2))
        elif args.command == "waste":
            if args.waste_command == "scan":
                payload = runtime.token_waste.scan(
                    scope=args.scope
                ).as_dict()
            elif args.waste_command == "report":
                payload = runtime.token_waste.load(
                    args.run_id, scope=args.scope
                ).as_dict()
            print(json.dumps(payload, indent=2))
        elif args.command == "utility":
            if args.utility_command == "list":
                payload = runtime.utility_inventory(kind=args.kind)
            else:
                payload = runtime.utility_snapshot(
                    args.kind, args.external_id
                ).as_dict()
            print(json.dumps(payload, indent=2))
        elif args.command == "skills":
            if args.skills_command == "validate":
                package = runtime.validate_skill_package(args.directory)
                print(
                    json.dumps(
                        {
                            "id": package.manifest.id,
                            "version": package.manifest.version,
                            "status": package.manifest.status.value,
                            "content_hash": package.content_hash,
                            "instruction_tokens": package.actual_instruction_tokens,
                        },
                        indent=2,
                    )
                )
            elif args.skills_command == "install":
                print(
                    json.dumps(
                        runtime.admit_skill_package(args.directory), indent=2
                    )
                )
            elif args.skills_command == "inspect":
                print(json.dumps(runtime.inspect_skill(args.skill), indent=2))
            elif args.skills_command == "evidence":
                print(json.dumps(runtime.skill_evidence(args.skill), indent=2))
            elif args.skills_command == "reconcile-evidence":
                print(
                    json.dumps(
                        runtime.reconcile_skill_evidence(args.skill).as_dict(),
                        indent=2,
                    )
                )
            elif args.skills_command == "invalidate-support":
                print(
                    json.dumps(
                        runtime.invalidate_skill_support(
                            args.support_link_id,
                            reason=args.reason,
                            actor=args.actor,
                        ).as_dict(),
                        indent=2,
                    )
                )
            elif args.skills_command == "search":
                print(
                    json.dumps(
                        runtime.search_skills(args.query, limit=args.limit),
                        indent=2,
                    )
                )
            elif args.skills_command == "route":
                print(
                    json.dumps(
                        runtime.route_skills(
                            args.task,
                            task_class=args.task_class,
                            token_budget=args.budget,
                        ).as_dict(),
                        indent=2,
                    )
                )
            elif args.skills_command == "generate":
                if args.dry_run:
                    payload = runtime.plan_skill_generation(scope=args.scope)
                elif args.approve:
                    payload = runtime.approve_skill_generation(args.approve)
                else:
                    payload = runtime.skill_generation(args.show)
                print(json.dumps(payload.as_dict(), indent=2))
            elif args.skills_command == "certify":
                if args.docker_sandbox:
                    runtime.skill_validator = SkillValidator(
                        runtime.db.connection,
                        runtime.skill_registry,
                        loader=runtime.skill_packages,
                        sandbox=DockerSandboxAdapter(
                            image=args.sandbox_image,
                            policy=SandboxPolicy(
                                timeout_seconds=args.sandbox_timeout,
                                memory_mb=args.sandbox_memory_mb,
                                cpu_count=args.sandbox_cpus,
                                pids_limit=args.sandbox_pids,
                            ),
                        ),
                    )
                payload = runtime.validate_skill_candidate(args.skill)
                print(json.dumps(payload.as_dict(), indent=2))
            elif args.skills_command == "validation":
                payload = runtime.skill_validation(args.run_id)
                print(json.dumps(payload.as_dict(), indent=2))
            elif args.skills_command == "promote":
                payload = runtime.promote_skill_validation(args.run_id)
                print(json.dumps(payload.as_dict(), indent=2))
            elif args.skills_command == "evolve":
                mutation_data = _read_bounded_json_object(args.mutation_file)
                for field in ("workflow", "tools", "verification"):
                    if mutation_data.get(field) is not None:
                        mutation_data[field] = tuple(mutation_data[field])
                payload = runtime.create_skill_evolution(
                    args.skill,
                    SkillMutation(**mutation_data),
                    version=args.version,
                )
                print(json.dumps(payload.as_dict(), indent=2))
            elif args.skills_command == "evolution":
                payload = runtime.skill_evolution_run(args.run_id)
                print(json.dumps(payload.as_dict(), indent=2))
            elif args.skills_command == "compare-evolution":
                comparison = _read_bounded_json_object(
                    args.comparison_file
                )
                payload = runtime.compare_skill_evolution(
                    args.run_id,
                    baseline_validation_id=comparison[
                        "baseline_validation_id"
                    ],
                    candidate_validation_id=comparison[
                        "candidate_validation_id"
                    ],
                )
                print(json.dumps(payload.as_dict(), indent=2))
            elif args.skills_command == "promote-evolution":
                payload = runtime.promote_skill_evolution(args.run_id)
                print(json.dumps(payload.as_dict(), indent=2))
            elif args.skills_command == "rollback-evolution":
                payload = runtime.rollback_skill_evolution(
                    args.run_id, reason=args.reason
                )
                print(json.dumps(payload.as_dict(), indent=2))
            elif args.skills_command == "merge-analysis":
                payload = runtime.analyze_skill_merges(
                    reference=args.skill,
                    limit=args.limit,
                )
                print(json.dumps(payload.as_dict(), indent=2))
            elif args.skills_command == "merge-report":
                payload = runtime.skill_merge_analysis(args.run_id)
                print(json.dumps(payload.as_dict(), indent=2))
            elif args.skills_command == "genome-create":
                parameters = GenomeParameters.from_dict(
                    _read_bounded_json_object(args.parameters_file)
                )
                payload = runtime.create_skill_genome(
                    args.skill, parameters
                )
                print(json.dumps(payload.as_dict(), indent=2))
            elif args.skills_command == "genome-mutate":
                mutation = GenomeMutation.from_dict(
                    _read_bounded_json_object(args.mutation_file)
                )
                payload = runtime.mutate_skill_genome(
                    args.parent_genome_id, mutation
                )
                print(json.dumps(payload.as_dict(), indent=2))
            elif args.skills_command == "genome":
                payload = runtime.inspect_skill_genome(args.genome_id)
                print(json.dumps(payload.as_dict(), indent=2))
            elif args.skills_command == "genome-tournament":
                payload = runtime.run_skill_genome_tournament(
                    args.baseline_genome_id,
                    tuple(args.candidate_genome_ids),
                )
                print(json.dumps(payload.as_dict(), indent=2))
            elif args.skills_command == "genome-tournament-report":
                payload = runtime.skill_genome_tournament(args.run_id)
                print(json.dumps(payload.as_dict(), indent=2))
            elif args.skills_command == "test":
                print(json.dumps(runtime.test_skill(args.skill), indent=2))
            elif args.skills_command == "activate":
                print(json.dumps(runtime.activate_skill(args.skill), indent=2))
            elif args.skills_command == "quarantine":
                print(json.dumps(runtime.quarantine_skill(args.skill), indent=2))
            elif args.skills_command == "retire":
                print(json.dumps(runtime.retire_skill(args.skill), indent=2))
            elif args.skills_command == "history":
                print(json.dumps(runtime.skill_history(args.skill), indent=2))
            else:
                print(json.dumps(runtime.skills(), indent=2))
        elif args.command == "code":
            from .code_index import CodeContextRequest, IndexPolicy
            from .code_slicer import PythonSliceRequest

            if args.code_command == "index":
                payload = runtime.index_repository(
                    args.repository,
                    policy=IndexPolicy(
                        max_files=args.max_files,
                        max_file_bytes=args.max_file_bytes,
                        max_total_bytes=args.max_total_bytes,
                        include_untracked=args.include_untracked,
                        allow_non_git=args.allow_non_git,
                    ),
                ).as_dict()
            elif args.code_command == "retrieve":
                payload = runtime.retrieve_code_context(
                    args.repository,
                    CodeContextRequest(
                        query=args.query,
                        max_tokens=args.budget,
                        max_files=args.max_files,
                    ),
                ).as_dict()
            else:
                payload = runtime.slice_python_context(
                    args.repository,
                    PythonSliceRequest(
                        query=args.query,
                        max_tokens=args.budget,
                        max_dependencies=args.max_dependencies,
                    ),
                ).as_dict()
            print(json.dumps(payload, indent=2))
            if (
                args.code_command in {"retrieve", "slice"}
                and payload["status"] not in {"available", "partial"}
            ):
                return 2
        elif args.command == "docs":
            from .document_context import (
                DocumentContextRequest,
                DocumentIndexRequest,
            )

            if args.docs_command == "index":
                payload = runtime.index_documents(
                    args.repository,
                    DocumentIndexRequest(
                        max_chunk_chars=args.max_chunk_chars
                    ),
                )
            elif args.docs_command == "retrieve":
                payload = runtime.retrieve_document_context(
                    args.repository,
                    DocumentContextRequest(
                        query=args.query,
                        mode=args.mode,
                        document=args.document,
                        section_id=args.section_id,
                        occurrence=args.occurrence,
                        max_tokens=args.budget,
                        max_chunks=args.max_chunks,
                    ),
                )
            else:
                raise ValueError("Unsupported documentation command")
            print(json.dumps(payload, indent=2))
            if (
                args.docs_command == "retrieve"
                and payload["status"] not in {"available", "partial"}
            ):
                return 2
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
            elif telemetry_command == "routing":
                payload = runtime.telemetry_skill_routing()
            elif telemetry_command == "memory":
                payload = runtime.telemetry_memory()
            elif telemetry_command == "economy":
                payload = runtime.telemetry_token_economy()
            elif telemetry_command == "compression":
                payload = runtime.telemetry_compression()
            elif telemetry_command == "attribution":
                payload = runtime.context_attributions(args.task_id)
            else:
                payload = runtime.telemetry_waste()
            print(json.dumps(payload, indent=2))
        elif args.command == "demo":
            _demo(runtime)
    return 0


def _human_lines(value: object, *, indent: int = 0) -> list[str]:
    prefix = " " * indent
    if isinstance(value, dict):
        lines: list[str] = []
        for key, item in value.items():
            label = str(key).replace("_", " ").capitalize()
            if isinstance(item, (dict, list)):
                lines.append(f"{prefix}{label}:")
                lines.extend(_human_lines(item, indent=indent + 2))
            else:
                rendered = (
                    "none" if item is None
                    else str(item).lower() if isinstance(item, bool)
                    else str(item)
                )
                lines.append(f"{prefix}{label}: {rendered}")
        return lines
    if isinstance(value, list):
        if not value:
            return [f"{prefix}(none)"]
        lines = []
        for item in value:
            if isinstance(item, (dict, list)):
                lines.append(f"{prefix}-")
                lines.extend(_human_lines(item, indent=indent + 2))
            else:
                lines.append(f"{prefix}- {item}")
        return lines
    return [f"{prefix}{value}"]


def _render_human_output(raw: str) -> str:
    stripped = raw.strip()
    if not stripped:
        return raw
    try:
        payload = json.loads(stripped)
    except json.JSONDecodeError:
        return raw
    return "\n".join(_human_lines(payload)) + "\n"


def _normalize_global_flags(arguments: list[str]) -> list[str]:
    normalized = list(arguments)
    prefix: list[str] = []
    index = 0
    while index < len(normalized):
        item = normalized[index]
        if item == "--db":
            if index + 1 >= len(normalized):
                break
            prefix.extend(normalized[index:index + 2])
            del normalized[index:index + 2]
            continue
        if item.startswith("--db="):
            prefix.append(item)
            del normalized[index]
            continue
        index += 1
    for flag in ("--json", "--verbose"):
        if flag in normalized:
            normalized = [item for item in normalized if item != flag]
            prefix.append(flag)
    local_dry_run = (
        {"memory", "consolidate"} <= set(normalized)
        or {"memory", "gc"} <= set(normalized)
        or {"experience", "distill"} <= set(normalized)
        or {"skills", "generate"} <= set(normalized)
    )
    if "--dry-run" in normalized and not local_dry_run:
        normalized = [item for item in normalized if item != "--dry-run"]
        prefix.append("--dry-run")
    return [*prefix, *normalized]


def main(argv: list[str] | None = None) -> int:
    effective_argv = _normalize_global_flags(
        list(sys.argv[1:] if argv is None else argv)
    )
    human_mode = "--json" not in effective_argv and sys.stdout.isatty()
    if not human_mode:
        return _execute(effective_argv)
    captured = io.StringIO()
    with redirect_stdout(captured):
        result = _execute(effective_argv)
    sys.stdout.write(_render_human_output(captured.getvalue()))
    return result


if __name__ == "__main__":
    raise SystemExit(main())
