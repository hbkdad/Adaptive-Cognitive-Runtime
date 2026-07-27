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
from .execution import PassEvaluator, PassVerifier, SingleStepPlanner, Task, TaskEventBus, TaskRunner
from .failure import FailureCreate, FailurePlanningAdvisor, FailureQuery
from .experience import (
    ExperienceEvent,
    ExperienceEventKind,
    ExperienceTraceCreate,
    MAX_RAW_TRACE_BYTES,
)
from .migrations import MigrationManager
from .memory import MemoryType, Sensitivity
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
from .tool_registry import ToolAccessRequest, ToolDefinition
from .tool_router import ToolOutcome, ToolRouteRequest
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
    evaluate_report = evaluate_sub.add_parser(
        "report", help="Inspect one retained evaluation run"
    )
    evaluate_report.add_argument("run_id")

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
            else:
                payload = runtime.model_router.profiles()
            print(json.dumps(payload, indent=2))
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
            elif args.tools_command == "outcome":
                payload = {"outcome_id": runtime.tool_router.record_outcome(
                    ToolOutcome.from_dict(
                        _read_bounded_json_object(args.outcome_file)
                    )
                )}
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

            if args.learn_command == "run":
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
                ).as_dict()
            else:
                payload = runtime.evaluation(args.run_id).as_dict()
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
            if run_result.result is not None and not args.json:
                print(run_result.result.content)
            print(
                json.dumps(
                    {
                        "task_id": task.id,
                        "run_id": run_result.id,
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
            else:
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
