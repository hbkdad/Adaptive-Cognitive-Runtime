from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

EXPECTED_SCHEMA_VERSION = 27


class MigrationRequired(RuntimeError):
    pass


MIGRATION_2_SQL = """
CREATE TABLE IF NOT EXISTS execution_runs (
    run_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    state TEXT NOT NULL,
    event_count INTEGER NOT NULL,
    step_count INTEGER NOT NULL,
    action_count INTEGER NOT NULL,
    duration_ms INTEGER NOT NULL,
    verification_score REAL,
    evaluation_score REAL,
    failure_kind TEXT,
    started_at TEXT NOT NULL,
    completed_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS telemetry_events (
    id TEXT PRIMARY KEY,
    sequence INTEGER,
    category TEXT NOT NULL,
    event_type TEXT NOT NULL,
    task_id TEXT,
    run_id TEXT,
    step_id TEXT,
    provider TEXT,
    model TEXT,
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    cached_tokens INTEGER NOT NULL DEFAULT 0,
    estimated_cost REAL NOT NULL DEFAULT 0,
    latency_ms INTEGER NOT NULL DEFAULT 0,
    status TEXT,
    context_bundle_id TEXT,
    skills_json TEXT NOT NULL DEFAULT '[]',
    memories_json TEXT NOT NULL DEFAULT '[]',
    payload_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS telemetry_events_task
ON telemetry_events(task_id, created_at);
CREATE INDEX IF NOT EXISTS telemetry_events_model
ON telemetry_events(provider, model, created_at);
"""

MIGRATION_4_SQL = """
ALTER TABLE memories
ADD COLUMN retention_reason_json TEXT NOT NULL
DEFAULT '["legacy_or_direct_write"]'
CHECK (json_valid(retention_reason_json));

CREATE TABLE memory_write_decisions (
    id TEXT PRIMARY KEY,
    candidate_hash TEXT NOT NULL,
    outcome TEXT NOT NULL CHECK (
        outcome IN (
            'ignore', 'store_temporary', 'store_candidate',
            'store_confirmed', 'update_existing', 'supersede_existing',
            'request_verification', 'quarantine'
        )
    ),
    memory_id TEXT REFERENCES memories(id),
    matched_memory_id TEXT REFERENCES memories(id),
    reasons_json TEXT NOT NULL CHECK (json_valid(reasons_json)),
    risk_flags_json TEXT NOT NULL DEFAULT '[]' CHECK (
        json_valid(risk_flags_json)
    ),
    scope TEXT,
    memory_type TEXT NOT NULL,
    confidence REAL NOT NULL,
    evidence_count INTEGER NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX memory_write_decisions_created
ON memory_write_decisions(created_at);
CREATE INDEX memory_write_decisions_memory
ON memory_write_decisions(memory_id);
"""

MIGRATION_5_SQL = """
CREATE TABLE memory_consolidation_runs (
    id TEXT PRIMARY KEY,
    status TEXT NOT NULL CHECK (
        status IN ('planned', 'applied', 'partially_applied', 'cancelled')
    ),
    scope TEXT,
    config_json TEXT NOT NULL CHECK (json_valid(config_json)),
    summary_json TEXT NOT NULL CHECK (json_valid(summary_json)),
    created_at TEXT NOT NULL,
    applied_at TEXT
);

CREATE TABLE memory_consolidation_actions (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES memory_consolidation_runs(id),
    kind TEXT NOT NULL CHECK (
        kind IN (
            'merge', 'archive', 'supersession',
            'promotion', 'conflict', 'decay'
        )
    ),
    target_ids_json TEXT NOT NULL CHECK (json_valid(target_ids_json)),
    expected_versions_json TEXT NOT NULL CHECK (
        json_valid(expected_versions_json)
    ),
    payload_json TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(payload_json)),
    reason TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'proposed' CHECK (
        status IN (
            'proposed', 'applied', 'skipped',
            'review_required', 'error'
        )
    ),
    error_type TEXT,
    created_at TEXT NOT NULL,
    applied_at TEXT
);

CREATE INDEX memory_consolidation_actions_run
ON memory_consolidation_actions(run_id, kind);
"""

MIGRATION_6_SQL = """
ALTER TABLE memories ADD COLUMN lifecycle_state TEXT NOT NULL DEFAULT 'active'
CHECK (lifecycle_state IN ('active', 'cold', 'archived', 'deleted'));
ALTER TABLE memories ADD COLUMN pinned INTEGER NOT NULL DEFAULT 0
CHECK (pinned IN (0, 1));
ALTER TABLE memories ADD COLUMN pinned_at TEXT;
ALTER TABLE memories ADD COLUMN pin_reason TEXT;
ALTER TABLE memories ADD COLUMN lifecycle_updated_at TEXT;
ALTER TABLE memories ADD COLUMN archived_at TEXT;
ALTER TABLE memories ADD COLUMN deleted_at TEXT;

UPDATE memories
SET lifecycle_state = CASE status
        WHEN 'archived' THEN 'archived'
        WHEN 'deleted' THEN 'deleted'
        ELSE 'active'
    END,
    lifecycle_updated_at = updated_at,
    archived_at = CASE WHEN status = 'archived' THEN updated_at END,
    deleted_at = CASE WHEN status = 'deleted' THEN updated_at END;

CREATE TABLE memory_scope_activity (
    scope TEXT PRIMARY KEY,
    last_active_at TEXT NOT NULL,
    access_count INTEGER NOT NULL DEFAULT 0 CHECK (access_count >= 0),
    updated_at TEXT NOT NULL
);

INSERT INTO memory_scope_activity(scope, last_active_at, access_count, updated_at)
SELECT scope, MAX(COALESCE(last_accessed, created_at)), SUM(access_count),
       MAX(updated_at)
FROM memories
GROUP BY scope;

CREATE TABLE memory_gc_runs (
    id TEXT PRIMARY KEY,
    status TEXT NOT NULL CHECK (
        status IN ('planned', 'applied', 'partially_applied', 'cancelled')
    ),
    scope TEXT,
    config_json TEXT NOT NULL CHECK (json_valid(config_json)),
    summary_json TEXT NOT NULL CHECK (json_valid(summary_json)),
    created_at TEXT NOT NULL,
    applied_at TEXT
);

CREATE TABLE memory_gc_actions (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES memory_gc_runs(id),
    memory_id TEXT NOT NULL REFERENCES memories(id),
    from_state TEXT NOT NULL CHECK (
        from_state IN ('active', 'cold', 'archived', 'deleted')
    ),
    to_state TEXT NOT NULL CHECK (
        to_state IN ('active', 'cold', 'archived', 'deleted')
    ),
    expected_updated_at TEXT NOT NULL,
    score_json TEXT NOT NULL CHECK (json_valid(score_json)),
    reason TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'proposed' CHECK (
        status IN ('proposed', 'applied', 'skipped', 'error')
    ),
    error_type TEXT,
    created_at TEXT NOT NULL,
    applied_at TEXT
);

CREATE INDEX memories_live_lifecycle
ON memories(scope, lifecycle_state, last_accessed)
WHERE lifecycle_state IN ('active', 'cold');
CREATE INDEX memories_pinned
ON memories(scope, pinned)
WHERE pinned = 1;
CREATE INDEX memory_gc_actions_run
ON memory_gc_actions(run_id, to_state);
"""

MIGRATION_7_SQL = """
CREATE TABLE failure_records (
    id TEXT PRIMARY KEY,
    memory_id TEXT NOT NULL UNIQUE REFERENCES memories(id),
    scope TEXT NOT NULL,
    task_class TEXT NOT NULL,
    strategy_attempted TEXT NOT NULL,
    environment_json TEXT NOT NULL DEFAULT '{}' CHECK (
        json_valid(environment_json)
    ),
    symptoms_json TEXT NOT NULL CHECK (json_valid(symptoms_json)),
    root_cause TEXT,
    failed_action TEXT NOT NULL,
    error_type TEXT,
    error_message TEXT,
    resolution TEXT,
    avoidance_rule TEXT,
    deterministic INTEGER NOT NULL DEFAULT 0 CHECK (
        deterministic IN (0, 1)
    ),
    occurrence_count INTEGER NOT NULL DEFAULT 1 CHECK (
        occurrence_count >= 1
    ),
    status TEXT NOT NULL DEFAULT 'unresolved' CHECK (
        status IN ('unresolved', 'resolved')
    ),
    remediation_memory_id TEXT REFERENCES memories(id),
    fingerprint TEXT NOT NULL,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    resolved_at TEXT,
    UNIQUE(scope, fingerprint)
);

CREATE INDEX failure_records_lookup
ON failure_records(scope, task_class, status, last_seen_at);
CREATE INDEX failure_records_memory
ON failure_records(memory_id);
CREATE INDEX failure_records_remediation
ON failure_records(remediation_memory_id)
WHERE remediation_memory_id IS NOT NULL;
"""

MIGRATION_8_SQL = """
CREATE TABLE experience_traces (
    id TEXT PRIMARY KEY,
    task_id TEXT,
    scope TEXT NOT NULL,
    task_class TEXT NOT NULL,
    outcome TEXT NOT NULL CHECK (
        outcome IN ('succeeded', 'failed', 'partial', 'cancelled')
    ),
    significance_score REAL NOT NULL CHECK (
        significance_score BETWEEN 0 AND 1
    ),
    raw_trace_json TEXT NOT NULL CHECK (json_valid(raw_trace_json)),
    raw_tokens INTEGER NOT NULL CHECK (raw_tokens >= 0),
    event_count INTEGER NOT NULL CHECK (event_count >= 0),
    created_at TEXT NOT NULL
);

CREATE TABLE experience_distillations (
    id TEXT PRIMARY KEY,
    trace_id TEXT NOT NULL REFERENCES experience_traces(id),
    status TEXT NOT NULL CHECK (
        status IN ('planned', 'applied', 'partially_applied', 'rejected')
    ),
    extractor TEXT NOT NULL,
    raw_tokens INTEGER NOT NULL CHECK (raw_tokens >= 0),
    distilled_tokens INTEGER NOT NULL CHECK (distilled_tokens >= 0),
    compression_ratio REAL NOT NULL CHECK (compression_ratio >= 0),
    summary_json TEXT NOT NULL CHECK (json_valid(summary_json)),
    created_at TEXT NOT NULL,
    applied_at TEXT
);

CREATE TABLE experience_distilled_items (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES experience_distillations(id),
    kind TEXT NOT NULL CHECK (
        kind IN (
            'durable_fact', 'decision', 'successful_procedure',
            'failure_pattern', 'environment_discovery',
            'tool_sequence', 'candidate_skill'
        )
    ),
    content TEXT NOT NULL,
    evidence_json TEXT NOT NULL CHECK (json_valid(evidence_json)),
    confidence REAL NOT NULL CHECK (confidence BETWEEN 0 AND 1),
    importance REAL NOT NULL CHECK (importance BETWEEN 0 AND 1),
    source_event_indexes_json TEXT NOT NULL CHECK (
        json_valid(source_event_indexes_json)
    ),
    status TEXT NOT NULL DEFAULT 'proposed' CHECK (
        status IN ('proposed', 'applied', 'skipped', 'error')
    ),
    memory_id TEXT REFERENCES memories(id),
    skill_id TEXT REFERENCES skills(id),
    error_type TEXT,
    created_at TEXT NOT NULL,
    applied_at TEXT
);

CREATE INDEX experience_traces_scope
ON experience_traces(scope, task_class, created_at);
CREATE INDEX experience_distillations_trace
ON experience_distillations(trace_id, created_at);
CREATE INDEX experience_distilled_items_run
ON experience_distilled_items(run_id, kind);
"""

MIGRATION_9_SQL = """
ALTER TABLE context_uses RENAME TO context_uses_v8;
CREATE TABLE context_uses (
    task_id TEXT NOT NULL REFERENCES tasks(id),
    source_type TEXT NOT NULL CHECK (
        source_type IN (
            'system_rule', 'memory', 'skill', 'file', 'tool',
            'agent_state', 'observation'
        )
    ),
    source_id TEXT NOT NULL,
    tokens INTEGER NOT NULL,
    utility REAL NOT NULL,
    roi REAL NOT NULL,
    useful INTEGER,
    PRIMARY KEY(task_id, source_type, source_id)
);
INSERT INTO context_uses(
    task_id, source_type, source_id, tokens, utility, roi, useful
)
SELECT task_id, source_type, source_id, tokens, utility, roi, useful
FROM context_uses_v8;
DROP TABLE context_uses_v8;
"""

CONTEXT_USES_V9_SQL = """
CREATE TABLE context_uses (
    task_id TEXT NOT NULL REFERENCES tasks(id),
    source_type TEXT NOT NULL CHECK (
        source_type IN (
            'system_rule', 'memory', 'skill', 'file', 'tool',
            'agent_state', 'observation'
        )
    ),
    source_id TEXT NOT NULL,
    tokens INTEGER NOT NULL,
    utility REAL NOT NULL,
    roi REAL NOT NULL,
    useful INTEGER,
    PRIMARY KEY(task_id, source_type, source_id)
)
"""

MIGRATION_10_SQL = """
CREATE TABLE token_budget_plans (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL UNIQUE REFERENCES tasks(id),
    complexity TEXT NOT NULL CHECK (complexity IN ('low', 'medium', 'high')),
    task_importance REAL NOT NULL CHECK (task_importance BETWEEN 0 AND 1),
    model_context_window INTEGER NOT NULL CHECK (model_context_window > 0),
    requested_input_budget INTEGER NOT NULL CHECK (requested_input_budget > 0),
    output_headroom INTEGER NOT NULL CHECK (output_headroom >= 0),
    reasoning_headroom INTEGER NOT NULL CHECK (reasoning_headroom >= 0),
    effective_input_budget INTEGER NOT NULL CHECK (effective_input_budget > 0),
    context_budget INTEGER NOT NULL CHECK (context_budget >= 0),
    candidate_count INTEGER NOT NULL CHECK (candidate_count >= 0),
    selected_count INTEGER NOT NULL CHECK (selected_count >= 0),
    expected_utility REAL NOT NULL CHECK (expected_utility >= 0),
    created_at TEXT NOT NULL
);
CREATE INDEX token_budget_plans_complexity
ON token_budget_plans(complexity, created_at);
"""

MIGRATION_11_SQL = """
CREATE TABLE context_attributions (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES tasks(id),
    source_type TEXT NOT NULL CHECK (
        source_type IN (
            'system_rule', 'memory', 'skill', 'file', 'tool',
            'agent_state', 'observation'
        )
    ),
    source_id TEXT NOT NULL,
    role TEXT NOT NULL,
    outcome TEXT NOT NULL CHECK (
        outcome IN ('contributed', 'ignored', 'misled', 'uncertain')
    ),
    impact_score REAL NOT NULL CHECK (impact_score BETWEEN -1 AND 1),
    confidence REAL NOT NULL CHECK (confidence BETWEEN 0 AND 1),
    approximate_roi REAL NOT NULL,
    model_score REAL CHECK (model_score BETWEEN 0 AND 1),
    execution_score REAL CHECK (execution_score BETWEEN 0 AND 1),
    dependency_score REAL CHECK (dependency_score BETWEEN 0 AND 1),
    evaluator_score REAL CHECK (evaluator_score BETWEEN -1 AND 1),
    evidence_json TEXT NOT NULL CHECK (json_valid(evidence_json)),
    created_at TEXT NOT NULL,
    UNIQUE(task_id, source_type, source_id)
);
CREATE INDEX context_attributions_outcome
ON context_attributions(outcome, created_at);
"""

MIGRATION_12_SQL = """
ALTER TABLE context_uses
ADD COLUMN compression_strategy TEXT NOT NULL DEFAULT 'none';
ALTER TABLE context_uses
ADD COLUMN original_tokens INTEGER CHECK (original_tokens >= 0);
ALTER TABLE context_uses
ADD COLUMN exact_preserved INTEGER NOT NULL DEFAULT 1
CHECK (exact_preserved IN (0, 1));
"""

MIGRATION_13_SQL = """
CREATE TABLE IF NOT EXISTS skills (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    version TEXT NOT NULL,
    description TEXT NOT NULL,
    instructions TEXT NOT NULL,
    tags_json TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL DEFAULT 'quarantine' CHECK (
        status IN ('quarantine', 'active', 'deprecated')
    ),
    token_cost INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    use_count INTEGER NOT NULL DEFAULT 0,
    success_count INTEGER NOT NULL DEFAULT 0,
    UNIQUE(name, version)
);
ALTER TABLE skills ADD COLUMN manifest_id TEXT;
ALTER TABLE skills ADD COLUMN manifest_json TEXT NOT NULL DEFAULT '{}'
CHECK (json_valid(manifest_json));
ALTER TABLE skills ADD COLUMN package_path TEXT;
ALTER TABLE skills ADD COLUMN content_hash TEXT;
ALTER TABLE skills ADD COLUMN lifecycle_status TEXT NOT NULL DEFAULT 'quarantined'
CHECK (lifecycle_status IN (
    'experimental', 'quarantined', 'active', 'deprecated', 'retired'
));
ALTER TABLE skills ADD COLUMN reliability REAL NOT NULL DEFAULT 0.5
CHECK (reliability BETWEEN 0 AND 1);
ALTER TABLE skills ADD COLUMN task_classes_json TEXT NOT NULL DEFAULT '[]'
CHECK (json_valid(task_classes_json));
ALTER TABLE skills ADD COLUMN permissions_json TEXT NOT NULL DEFAULT '[]'
CHECK (json_valid(permissions_json));
ALTER TABLE skills ADD COLUMN models_json TEXT NOT NULL DEFAULT '[]'
CHECK (json_valid(models_json));
ALTER TABLE skills ADD COLUMN applicability_json TEXT NOT NULL DEFAULT '[]'
CHECK (json_valid(applicability_json));
ALTER TABLE skills ADD COLUMN contraindications_json TEXT NOT NULL DEFAULT '[]'
CHECK (json_valid(contraindications_json));
ALTER TABLE skills ADD COLUMN verification_json TEXT NOT NULL DEFAULT '[]'
CHECK (json_valid(verification_json));
ALTER TABLE skills ADD COLUMN verification_status TEXT NOT NULL DEFAULT 'untested'
CHECK (verification_status IN ('untested', 'static_passed', 'failed'));
ALTER TABLE skills ADD COLUMN failure_count INTEGER NOT NULL DEFAULT 0
CHECK (failure_count >= 0);
ALTER TABLE skills ADD COLUMN total_tokens INTEGER NOT NULL DEFAULT 0
CHECK (total_tokens >= 0);
ALTER TABLE skills ADD COLUMN total_cost REAL NOT NULL DEFAULT 0
CHECK (total_cost >= 0);
ALTER TABLE skills ADD COLUMN total_latency_ms INTEGER NOT NULL DEFAULT 0
CHECK (total_latency_ms >= 0);
ALTER TABLE skills ADD COLUMN last_used TEXT;

UPDATE skills SET
    manifest_id = lower(replace(name, ' ', '-')),
    lifecycle_status = CASE status
        WHEN 'active' THEN 'active'
        WHEN 'deprecated' THEN 'deprecated'
        ELSE 'quarantined'
    END;

CREATE TABLE skill_registry_history (
    id TEXT PRIMARY KEY,
    skill_id TEXT NOT NULL REFERENCES skills(id),
    event TEXT NOT NULL,
    from_status TEXT,
    to_status TEXT,
    details_json TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(details_json)),
    created_at TEXT NOT NULL
);
CREATE INDEX skill_registry_history_skill
ON skill_registry_history(skill_id, created_at);

CREATE TABLE skill_performance (
    skill_id TEXT NOT NULL REFERENCES skills(id),
    task_class TEXT NOT NULL,
    model TEXT NOT NULL DEFAULT '',
    uses INTEGER NOT NULL DEFAULT 0,
    successful_uses INTEGER NOT NULL DEFAULT 0,
    failures INTEGER NOT NULL DEFAULT 0,
    total_tokens INTEGER NOT NULL DEFAULT 0,
    total_cost REAL NOT NULL DEFAULT 0,
    total_latency_ms INTEGER NOT NULL DEFAULT 0,
    last_used TEXT,
    PRIMARY KEY(skill_id, task_class, model)
);

CREATE VIRTUAL TABLE skills_fts USING fts5(
    skill_id UNINDEXED, name, description, task_classes, applicability
);
INSERT INTO skills_fts(
    skill_id, name, description, task_classes, applicability
)
SELECT id, name, description, task_classes_json, applicability_json
FROM skills;
"""

MIGRATION_14_SQL = """
CREATE TABLE skill_routing_runs (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL UNIQUE REFERENCES tasks(id),
    task_class TEXT NOT NULL,
    token_budget INTEGER NOT NULL CHECK (token_budget >= 0),
    semantic_available INTEGER NOT NULL CHECK (semantic_available IN (0, 1)),
    candidate_count INTEGER NOT NULL CHECK (candidate_count >= 0),
    selected_count INTEGER NOT NULL CHECK (selected_count >= 0),
    created_at TEXT NOT NULL
);

CREATE TABLE skill_routing_candidates (
    run_id TEXT NOT NULL REFERENCES skill_routing_runs(id),
    skill_id TEXT NOT NULL REFERENCES skills(id),
    router_selected INTEGER NOT NULL CHECK (router_selected IN (0, 1)),
    compiler_selected INTEGER NOT NULL CHECK (compiler_selected IN (0, 1)),
    applicability REAL NOT NULL CHECK (applicability BETWEEN 0 AND 1),
    expected_benefit REAL NOT NULL,
    token_overhead INTEGER NOT NULL CHECK (token_overhead >= 0),
    historical_success REAL NOT NULL CHECK (historical_success BETWEEN 0 AND 1),
    reliability REAL NOT NULL CHECK (reliability BETWEEN 0 AND 1),
    overlap_penalty REAL NOT NULL CHECK (overlap_penalty BETWEEN 0 AND 1),
    final_score REAL NOT NULL,
    reason TEXT NOT NULL DEFAULT '',
    rejection_reason TEXT,
    outcome TEXT CHECK (
        outcome IS NULL OR outcome IN (
            'contributed', 'ignored', 'misled', 'uncertain'
        )
    ),
    PRIMARY KEY(run_id, skill_id)
);

CREATE INDEX skill_routing_candidates_outcome
ON skill_routing_candidates(outcome, router_selected, compiler_selected);
"""

MIGRATION_15_SQL = """
CREATE TABLE skill_generation_runs (
    id TEXT PRIMARY KEY,
    status TEXT NOT NULL CHECK (
        status IN ('planned', 'applied', 'partially_applied', 'rejected')
    ),
    scope TEXT,
    config_json TEXT NOT NULL CHECK (json_valid(config_json)),
    candidate_count INTEGER NOT NULL CHECK (candidate_count >= 0),
    created_at TEXT NOT NULL,
    applied_at TEXT
);

CREATE TABLE skill_generation_candidates (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES skill_generation_runs(id),
    pattern_hash TEXT NOT NULL,
    trigger_kind TEXT NOT NULL CHECK (
        trigger_kind IN (
            'repeated_successful_procedure',
            'repeated_expensive_reasoning',
            'repeated_tool_sequence',
            'repeated_human_instruction'
        )
    ),
    scope TEXT NOT NULL,
    task_class TEXT NOT NULL,
    occurrence_count INTEGER NOT NULL CHECK (occurrence_count >= 3),
    average_significance REAL NOT NULL CHECK (
        average_significance BETWEEN 0 AND 1
    ),
    procedure TEXT NOT NULL,
    applicability_json TEXT NOT NULL CHECK (json_valid(applicability_json)),
    inputs_json TEXT NOT NULL CHECK (json_valid(inputs_json)),
    outputs_json TEXT NOT NULL CHECK (json_valid(outputs_json)),
    verification_json TEXT NOT NULL CHECK (json_valid(verification_json)),
    failure_modes_json TEXT NOT NULL CHECK (json_valid(failure_modes_json)),
    permissions_json TEXT NOT NULL CHECK (json_valid(permissions_json)),
    tools_json TEXT NOT NULL CHECK (json_valid(tools_json)),
    evidence_json TEXT NOT NULL CHECK (json_valid(evidence_json)),
    trace_ids_json TEXT NOT NULL CHECK (json_valid(trace_ids_json)),
    status TEXT NOT NULL DEFAULT 'proposed' CHECK (
        status IN ('proposed', 'generated', 'skipped', 'error')
    ),
    package_path TEXT,
    skill_id TEXT REFERENCES skills(id),
    error_type TEXT,
    created_at TEXT NOT NULL,
    applied_at TEXT,
    UNIQUE(run_id, pattern_hash)
);

CREATE INDEX skill_generation_candidates_pattern
ON skill_generation_candidates(pattern_hash, status, created_at);
CREATE INDEX skill_generation_candidates_task
ON skill_generation_candidates(scope, task_class, trigger_kind);
"""

MIGRATION_16_SQL = """
INSERT INTO skill_registry_history(
    id, skill_id, event, from_status, to_status, details_json, created_at
)
SELECT lower(hex(randomblob(16))), id, 'validation_required',
       lifecycle_status, 'quarantined',
       '{"reason":"prompt20_mandatory_pipeline"}',
       strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
FROM skills
WHERE lifecycle_status = 'active';

UPDATE skills
SET status = 'quarantine', lifecycle_status = 'quarantined'
WHERE lifecycle_status = 'active';

CREATE TABLE skill_validation_runs (
    id TEXT PRIMARY KEY,
    skill_id TEXT NOT NULL REFERENCES skills(id),
    package_hash TEXT NOT NULL,
    status TEXT NOT NULL CHECK (
        status IN ('running', 'passed', 'failed', 'blocked', 'promoted')
    ),
    incumbent_skill_id TEXT REFERENCES skills(id),
    policy_json TEXT NOT NULL CHECK (json_valid(policy_json)),
    created_at TEXT NOT NULL,
    completed_at TEXT,
    promoted_at TEXT
);

CREATE TABLE skill_validation_results (
    run_id TEXT NOT NULL REFERENCES skill_validation_runs(id),
    stage_order INTEGER NOT NULL CHECK (stage_order BETWEEN 1 AND 10),
    stage TEXT NOT NULL CHECK (
        stage IN (
            'syntax_validation', 'dependency_validation',
            'static_security_scan', 'permission_analysis',
            'sandbox_execution', 'unit_tests', 'scenario_tests',
            'adversarial_tests', 'evaluator_review',
            'benchmark_comparison'
        )
    ),
    outcome TEXT NOT NULL CHECK (
        outcome IN ('passed', 'failed', 'blocked', 'error')
    ),
    score REAL CHECK (score BETWEEN 0 AND 1),
    token_cost INTEGER NOT NULL DEFAULT 0 CHECK (token_cost >= 0),
    estimated_cost REAL NOT NULL DEFAULT 0 CHECK (estimated_cost >= 0),
    latency_ms INTEGER NOT NULL DEFAULT 0 CHECK (latency_ms >= 0),
    details_json TEXT NOT NULL CHECK (json_valid(details_json)),
    created_at TEXT NOT NULL,
    PRIMARY KEY(run_id, stage_order),
    UNIQUE(run_id, stage)
);

CREATE INDEX skill_validation_runs_skill
ON skill_validation_runs(skill_id, created_at);
CREATE INDEX skill_validation_results_outcome
ON skill_validation_results(stage, outcome);
"""

MIGRATION_17_SQL = """
CREATE TABLE skill_evolution_runs (
    id TEXT PRIMARY KEY,
    source_skill_id TEXT NOT NULL REFERENCES skills(id),
    candidate_skill_id TEXT NOT NULL REFERENCES skills(id),
    source_version TEXT NOT NULL,
    candidate_version TEXT NOT NULL,
    status TEXT NOT NULL CHECK (
        status IN (
            'candidate', 'compared', 'promoted', 'rejected', 'rolled_back'
        )
    ),
    mutation_json TEXT NOT NULL CHECK (json_valid(mutation_json)),
    source_hash TEXT NOT NULL,
    candidate_hash TEXT NOT NULL,
    baseline_validation_id TEXT REFERENCES skill_validation_runs(id),
    candidate_validation_id TEXT REFERENCES skill_validation_runs(id),
    comparison_json TEXT CHECK (
        comparison_json IS NULL OR json_valid(comparison_json)
    ),
    winner TEXT CHECK (
        winner IS NULL OR winner IN ('source', 'candidate')
    ),
    created_at TEXT NOT NULL,
    compared_at TEXT,
    promoted_at TEXT,
    rolled_back_at TEXT
);

CREATE TABLE skill_evolution_rollbacks (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES skill_evolution_runs(id),
    from_skill_id TEXT NOT NULL REFERENCES skills(id),
    to_skill_id TEXT NOT NULL REFERENCES skills(id),
    reason TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX skill_evolution_runs_source
ON skill_evolution_runs(source_skill_id, created_at);
CREATE INDEX skill_evolution_runs_candidate
ON skill_evolution_runs(candidate_skill_id, created_at);
"""

MIGRATION_18_SQL = """
CREATE TABLE skill_merge_analysis_runs (
    id TEXT PRIMARY KEY,
    requested_skill_id TEXT REFERENCES skills(id),
    policy_json TEXT NOT NULL CHECK (json_valid(policy_json)),
    skill_count INTEGER NOT NULL CHECK (skill_count >= 0),
    pair_count INTEGER NOT NULL CHECK (pair_count >= 0),
    created_at TEXT NOT NULL
);

CREATE TABLE skill_merge_analysis_pairs (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES skill_merge_analysis_runs(id),
    left_skill_id TEXT NOT NULL REFERENCES skills(id),
    right_skill_id TEXT NOT NULL REFERENCES skills(id),
    recommendation TEXT NOT NULL CHECK (
        recommendation IN (
            'KEEP_SEPARATE', 'MERGE', 'DEPRECATE_ONE', 'COMPOSE'
        )
    ),
    deprecate_skill_id TEXT REFERENCES skills(id),
    active_involved INTEGER NOT NULL CHECK (active_involved IN (0, 1)),
    automatic_action_allowed INTEGER NOT NULL DEFAULT 0 CHECK (
        automatic_action_allowed = 0
    ),
    evidence_json TEXT NOT NULL CHECK (json_valid(evidence_json)),
    created_at TEXT NOT NULL,
    CHECK (
        deprecate_skill_id IS NULL
        OR deprecate_skill_id IN (left_skill_id, right_skill_id)
    ),
    UNIQUE(run_id, left_skill_id, right_skill_id)
);

CREATE INDEX skill_merge_analysis_pairs_run
ON skill_merge_analysis_pairs(run_id, recommendation);
CREATE INDEX skill_merge_analysis_pairs_left
ON skill_merge_analysis_pairs(left_skill_id, created_at);
CREATE INDEX skill_merge_analysis_pairs_right
ON skill_merge_analysis_pairs(right_skill_id, created_at);
"""

MIGRATION_19_SQL = """
CREATE TABLE skill_genomes (
    id TEXT PRIMARY KEY,
    source_skill_id TEXT NOT NULL REFERENCES skills(id),
    source_hash TEXT NOT NULL,
    parent_genome_id TEXT REFERENCES skill_genomes(id),
    generation INTEGER NOT NULL CHECK (generation >= 0),
    status TEXT NOT NULL CHECK (
        status IN ('baseline', 'experimental', 'selected', 'rejected')
    ),
    parameters_json TEXT NOT NULL CHECK (json_valid(parameters_json)),
    mutation_json TEXT CHECK (
        mutation_json IS NULL OR json_valid(mutation_json)
    ),
    created_at TEXT NOT NULL,
    selected_at TEXT,
    CHECK (
        (generation = 0 AND parent_genome_id IS NULL
         AND mutation_json IS NULL)
        OR
        (generation > 0 AND parent_genome_id IS NOT NULL
         AND mutation_json IS NOT NULL)
    )
);

CREATE TABLE skill_genome_tournaments (
    id TEXT PRIMARY KEY,
    baseline_genome_id TEXT NOT NULL REFERENCES skill_genomes(id),
    status TEXT NOT NULL CHECK (status IN ('blocked', 'completed')),
    adapter TEXT NOT NULL,
    isolation_json TEXT NOT NULL CHECK (json_valid(isolation_json)),
    policy_json TEXT NOT NULL CHECK (json_valid(policy_json)),
    winner_genome_id TEXT REFERENCES skill_genomes(id),
    blocked_reason TEXT,
    created_at TEXT NOT NULL,
    completed_at TEXT NOT NULL
);

CREATE TABLE skill_genome_tournament_candidates (
    id TEXT PRIMARY KEY,
    tournament_id TEXT NOT NULL REFERENCES skill_genome_tournaments(id),
    candidate_genome_id TEXT NOT NULL REFERENCES skill_genomes(id),
    observations_json TEXT NOT NULL CHECK (json_valid(observations_json)),
    statistics_json TEXT NOT NULL CHECK (json_valid(statistics_json)),
    qualified INTEGER NOT NULL CHECK (qualified IN (0, 1)),
    tournament_rank INTEGER,
    rejection_reasons_json TEXT NOT NULL CHECK (
        json_valid(rejection_reasons_json)
    ),
    created_at TEXT NOT NULL,
    UNIQUE(tournament_id, candidate_genome_id)
);

CREATE INDEX skill_genomes_source
ON skill_genomes(source_skill_id, generation, created_at);
CREATE INDEX skill_genome_tournaments_baseline
ON skill_genome_tournaments(baseline_genome_id, created_at);
CREATE INDEX skill_genome_candidates_tournament
ON skill_genome_tournament_candidates(tournament_id, qualified);
"""

MIGRATION_20_SQL = """
CREATE TABLE agent_specs (
    id TEXT PRIMARY KEY,
    role TEXT NOT NULL,
    objective TEXT NOT NULL,
    task_scope_json TEXT NOT NULL CHECK (json_valid(task_scope_json)),
    memory_scope_json TEXT NOT NULL CHECK (json_valid(memory_scope_json)),
    tools_json TEXT NOT NULL CHECK (json_valid(tools_json)),
    skills_json TEXT NOT NULL CHECK (json_valid(skills_json)),
    permissions_json TEXT NOT NULL CHECK (json_valid(permissions_json)),
    spec_json TEXT NOT NULL CHECK (json_valid(spec_json)),
    resolved_skills_json TEXT NOT NULL CHECK (
        json_valid(resolved_skills_json)
    ),
    content_hash TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'defined' CHECK (status = 'defined'),
    created_at TEXT NOT NULL
);

CREATE INDEX agent_specs_role
ON agent_specs(role, created_at);
"""

MIGRATION_21_SQL = """
CREATE TABLE agent_factory_plans (
    id TEXT PRIMARY KEY,
    request_json TEXT NOT NULL CHECK (json_valid(request_json)),
    selected_topology TEXT NOT NULL CHECK (
        selected_topology IN (
            'single_agent', 'multi_agent', 'parallel_workers',
            'specialist_critic', 'researchers_synthesizer'
        )
    ),
    selected_estimate_json TEXT NOT NULL CHECK (
        json_valid(selected_estimate_json)
    ),
    worker_count INTEGER NOT NULL CHECK (worker_count BETWEEN 1 AND 8),
    status TEXT NOT NULL DEFAULT 'proposed' CHECK (status = 'proposed'),
    created_at TEXT NOT NULL
);

CREATE TABLE agent_factory_candidates (
    id TEXT PRIMARY KEY,
    plan_id TEXT NOT NULL REFERENCES agent_factory_plans(id),
    topology TEXT NOT NULL,
    worker_count INTEGER NOT NULL CHECK (worker_count BETWEEN 1 AND 8),
    estimate_json TEXT NOT NULL CHECK (json_valid(estimate_json)),
    feasible INTEGER NOT NULL CHECK (feasible IN (0, 1)),
    selected INTEGER NOT NULL CHECK (selected IN (0, 1)),
    rejection_reasons_json TEXT NOT NULL CHECK (
        json_valid(rejection_reasons_json)
    ),
    created_at TEXT NOT NULL,
    UNIQUE(plan_id, topology)
);

CREATE TABLE agent_factory_workers (
    id TEXT PRIMARY KEY,
    plan_id TEXT NOT NULL REFERENCES agent_factory_plans(id),
    sequence INTEGER NOT NULL CHECK (sequence >= 1),
    responsibility TEXT NOT NULL,
    spec_json TEXT NOT NULL CHECK (json_valid(spec_json)),
    context_scope_json TEXT NOT NULL CHECK (json_valid(context_scope_json)),
    status TEXT NOT NULL DEFAULT 'proposed' CHECK (status = 'proposed'),
    created_at TEXT NOT NULL,
    UNIQUE(plan_id, sequence)
);

CREATE INDEX agent_factory_plans_topology
ON agent_factory_plans(selected_topology, created_at);
CREATE INDEX agent_factory_workers_plan
ON agent_factory_workers(plan_id, sequence);
"""

MIGRATION_22_SQL = """
CREATE TABLE agent_topology_recipes (
    id TEXT PRIMARY KEY,
    task_class TEXT NOT NULL,
    topology TEXT NOT NULL CHECK (
        topology IN (
            'single_agent', 'multi_agent', 'parallel_workers',
            'specialist_critic', 'researchers_synthesizer'
        )
    ),
    structure_hash TEXT NOT NULL CHECK (length(structure_hash) = 64),
    recipe_json TEXT NOT NULL CHECK (json_valid(recipe_json)),
    worker_count INTEGER NOT NULL CHECK (worker_count BETWEEN 1 AND 8),
    models_json TEXT NOT NULL CHECK (json_valid(models_json)),
    skills_json TEXT NOT NULL CHECK (json_valid(skills_json)),
    parallelism REAL NOT NULL CHECK (parallelism BETWEEN 0 AND 1),
    created_at TEXT NOT NULL,
    UNIQUE(task_class, structure_hash)
);

CREATE TABLE agent_topology_outcomes (
    id TEXT PRIMARY KEY,
    plan_id TEXT NOT NULL REFERENCES agent_factory_plans(id),
    task_class TEXT NOT NULL,
    topology TEXT NOT NULL,
    structure_hash TEXT NOT NULL CHECK (length(structure_hash) = 64),
    worker_count INTEGER NOT NULL CHECK (worker_count BETWEEN 1 AND 8),
    models_json TEXT NOT NULL CHECK (json_valid(models_json)),
    skills_json TEXT NOT NULL CHECK (json_valid(skills_json)),
    parallelism REAL NOT NULL CHECK (parallelism BETWEEN 0 AND 1),
    tokens INTEGER NOT NULL CHECK (tokens >= 0),
    latency_ms INTEGER NOT NULL CHECK (latency_ms >= 0),
    quality REAL NOT NULL CHECK (quality BETWEEN 0 AND 1),
    success INTEGER NOT NULL CHECK (success IN (0, 1)),
    verification_passed INTEGER NOT NULL CHECK (
        verification_passed IN (0, 1)
    ),
    verification_evidence_json TEXT NOT NULL CHECK (
        json_valid(verification_evidence_json)
    ),
    created_at TEXT NOT NULL,
    UNIQUE(plan_id)
);

CREATE INDEX agent_topology_recipes_task
ON agent_topology_recipes(task_class, worker_count);
CREATE INDEX agent_topology_outcomes_task
ON agent_topology_outcomes(task_class, structure_hash, created_at);
"""

MIGRATION_23_SQL = """
CREATE TABLE hierarchical_plans (
    id TEXT PRIMARY KEY,
    request_json TEXT NOT NULL CHECK (json_valid(request_json)),
    current_revision INTEGER NOT NULL DEFAULT 1 CHECK (current_revision >= 1),
    status TEXT NOT NULL CHECK (
        status IN (
            'proposed', 'blocked', 'executing', 'completed', 'cancelled'
        )
    ),
    agent_factory_plan_id TEXT REFERENCES agent_factory_plans(id),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE hierarchical_plan_revisions (
    id TEXT PRIMARY KEY,
    plan_id TEXT NOT NULL REFERENCES hierarchical_plans(id),
    revision INTEGER NOT NULL CHECK (revision >= 1),
    parent_revision_id TEXT REFERENCES hierarchical_plan_revisions(id),
    change_kind TEXT NOT NULL CHECK (
        change_kind IN ('initial', 'refinement', 'edit', 'phase')
    ),
    change_reason TEXT NOT NULL,
    snapshot_json TEXT NOT NULL CHECK (json_valid(snapshot_json)),
    content_hash TEXT NOT NULL CHECK (length(content_hash) = 64),
    created_at TEXT NOT NULL,
    UNIQUE(plan_id, revision)
);

CREATE INDEX hierarchical_plans_status
ON hierarchical_plans(status, updated_at);
CREATE INDEX hierarchical_plan_revisions_plan
ON hierarchical_plan_revisions(plan_id, revision);
"""

MIGRATION_24_SQL = """
CREATE TABLE evaluation_runs (
    id TEXT PRIMARY KEY,
    task_id TEXT,
    case_metadata_json TEXT NOT NULL CHECK (json_valid(case_metadata_json)),
    passed INTEGER NOT NULL CHECK (passed IN (0, 1)),
    score REAL NOT NULL CHECK (score BETWEEN 0 AND 1),
    max_disagreement REAL NOT NULL CHECK (max_disagreement BETWEEN 0 AND 1),
    created_at TEXT NOT NULL
);

CREATE TABLE evaluation_judge_results (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES evaluation_runs(id) ON DELETE CASCADE,
    sequence INTEGER NOT NULL CHECK (sequence >= 1),
    judge_id TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('deterministic', 'llm')),
    scores_json TEXT NOT NULL CHECK (json_valid(scores_json)),
    created_at TEXT NOT NULL,
    UNIQUE(run_id, judge_id),
    UNIQUE(run_id, sequence)
);

CREATE TABLE evaluation_criterion_results (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES evaluation_runs(id) ON DELETE CASCADE,
    criterion TEXT NOT NULL,
    score REAL NOT NULL CHECK (score BETWEEN 0 AND 1),
    disagreement REAL NOT NULL CHECK (disagreement BETWEEN 0 AND 1),
    judge_count INTEGER NOT NULL CHECK (judge_count >= 1),
    deterministic_count INTEGER NOT NULL CHECK (deterministic_count >= 0),
    llm_count INTEGER NOT NULL CHECK (llm_count >= 0),
    grounded INTEGER NOT NULL CHECK (grounded IN (0, 1)),
    passed INTEGER NOT NULL CHECK (passed IN (0, 1)),
    created_at TEXT NOT NULL,
    CHECK (judge_count = deterministic_count + llm_count),
    CHECK (grounded = (deterministic_count > 0)),
    UNIQUE(run_id, criterion)
);

CREATE INDEX evaluation_runs_created
ON evaluation_runs(created_at);
CREATE INDEX evaluation_runs_task
ON evaluation_runs(task_id, created_at);
CREATE INDEX evaluation_criteria_run
ON evaluation_criterion_results(run_id, criterion);
"""

MIGRATION_25_SQL = """
CREATE TABLE reflection_runs (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    evaluation_run_id TEXT REFERENCES evaluation_runs(id),
    reflection_depth INTEGER NOT NULL CHECK (reflection_depth = 1),
    budget_json TEXT NOT NULL CHECK (json_valid(budget_json)),
    input_metadata_json TEXT NOT NULL CHECK (json_valid(input_metadata_json)),
    finding_count INTEGER NOT NULL CHECK (finding_count = 9),
    estimated_output_tokens INTEGER NOT NULL CHECK (
        estimated_output_tokens > 0
    ),
    created_at TEXT NOT NULL
);

CREATE TABLE reflection_findings (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES reflection_runs(id) ON DELETE CASCADE,
    sequence INTEGER NOT NULL CHECK (sequence BETWEEN 1 AND 9),
    category TEXT NOT NULL CHECK (
        category IN (
            'what_worked', 'what_failed', 'unnecessary_context',
            'missing_information', 'memory_impact', 'skill_impact',
            'model_economy', 'tool_economy', 'reusable_experience'
        )
    ),
    verdict TEXT NOT NULL,
    subject_ids_json TEXT NOT NULL CHECK (json_valid(subject_ids_json)),
    evidence_json TEXT NOT NULL CHECK (json_valid(evidence_json)),
    metrics_json TEXT NOT NULL CHECK (json_valid(metrics_json)),
    created_at TEXT NOT NULL,
    UNIQUE(run_id, sequence),
    UNIQUE(run_id, category)
);

CREATE INDEX reflection_runs_task
ON reflection_runs(task_id, created_at);
CREATE INDEX reflection_runs_evaluation
ON reflection_runs(evaluation_run_id, created_at);
CREATE INDEX reflection_findings_run
ON reflection_findings(run_id, sequence);
"""

MIGRATION_26_SQL = """
CREATE TABLE learning_runs (
    id TEXT PRIMARY KEY,
    execution_run_id TEXT NOT NULL UNIQUE REFERENCES execution_runs(run_id),
    task_id TEXT NOT NULL REFERENCES tasks(id),
    evaluation_run_id TEXT NOT NULL REFERENCES evaluation_runs(id),
    experience_distillation_id TEXT REFERENCES experience_distillations(id),
    skill_generation_run_id TEXT NOT NULL REFERENCES skill_generation_runs(id),
    resource_efficiency_json TEXT NOT NULL CHECK (
        json_valid(resource_efficiency_json)
    ),
    baseline_json TEXT NOT NULL CHECK (json_valid(baseline_json)),
    stage_count INTEGER NOT NULL CHECK (stage_count = 10),
    memory_candidate_count INTEGER NOT NULL CHECK (
        memory_candidate_count >= 0
    ),
    skill_candidate_count INTEGER NOT NULL CHECK (skill_candidate_count >= 0),
    routing_improvement_count INTEGER NOT NULL CHECK (
        routing_improvement_count >= 0
    ),
    regression_count INTEGER NOT NULL CHECK (regression_count >= 0),
    status TEXT NOT NULL CHECK (status = 'completed'),
    created_at TEXT NOT NULL,
    completed_at TEXT NOT NULL
);

CREATE TABLE learning_stage_results (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES learning_runs(id) ON DELETE CASCADE
        DEFERRABLE INITIALLY DEFERRED,
    sequence INTEGER NOT NULL CHECK (sequence BETWEEN 1 AND 10),
    stage TEXT NOT NULL CHECK (
        stage IN (
            'evaluate', 'attribute_context',
            'calculate_resource_efficiency', 'distill_experience',
            'generate_memory_candidates', 'update_memory_utility',
            'update_skill_utility', 'identify_skill_candidate',
            'identify_routing_improvements', 'detect_regression'
        )
    ),
    status TEXT NOT NULL CHECK (status IN ('completed', 'skipped')),
    details_json TEXT NOT NULL CHECK (json_valid(details_json)),
    created_at TEXT NOT NULL,
    UNIQUE(run_id, sequence),
    UNIQUE(run_id, stage)
);

CREATE TABLE learning_memory_candidates (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES learning_runs(id) ON DELETE CASCADE
        DEFERRABLE INITIALLY DEFERRED,
    distilled_item_id TEXT NOT NULL REFERENCES experience_distilled_items(id),
    memory_type TEXT NOT NULL,
    content_hash TEXT NOT NULL CHECK (length(content_hash) = 64),
    candidate_json TEXT NOT NULL CHECK (json_valid(candidate_json)),
    status TEXT NOT NULL CHECK (status = 'proposed'),
    created_at TEXT NOT NULL,
    UNIQUE(run_id, distilled_item_id)
);

CREATE TABLE learning_routing_improvements (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES learning_runs(id) ON DELETE CASCADE
        DEFERRABLE INITIALLY DEFERRED,
    skill_id TEXT NOT NULL REFERENCES skills(id),
    attribution_outcome TEXT NOT NULL CHECK (
        attribution_outcome IN ('contributed', 'ignored', 'misled', 'uncertain')
    ),
    recommendation TEXT NOT NULL CHECK (
        recommendation IN (
            'reinforce', 'review_reduce', 'quarantine_review',
            'collect_evidence'
        )
    ),
    evidence_json TEXT NOT NULL CHECK (json_valid(evidence_json)),
    status TEXT NOT NULL CHECK (status = 'proposed'),
    created_at TEXT NOT NULL,
    UNIQUE(run_id, skill_id)
);

CREATE TABLE learning_regressions (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES learning_runs(id) ON DELETE CASCADE
        DEFERRABLE INITIALLY DEFERRED,
    metric TEXT NOT NULL CHECK (
        metric IN ('quality', 'total_tokens', 'duration_ms', 'estimated_cost')
    ),
    baseline_value REAL NOT NULL,
    observed_value REAL NOT NULL,
    delta REAL NOT NULL,
    severity TEXT NOT NULL CHECK (severity = 'review'),
    created_at TEXT NOT NULL,
    UNIQUE(run_id, metric)
);

CREATE INDEX learning_runs_task
ON learning_runs(task_id, created_at);
CREATE INDEX learning_stage_results_run
ON learning_stage_results(run_id, sequence);
CREATE INDEX learning_memory_candidates_run
ON learning_memory_candidates(run_id, status);
CREATE INDEX learning_regressions_run
ON learning_regressions(run_id, metric);
"""

MIGRATION_27_SQL = """
CREATE TABLE model_profiles (
    id TEXT PRIMARY KEY,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    context_capacity INTEGER NOT NULL CHECK (context_capacity > 0),
    supports_tools INTEGER NOT NULL CHECK (supports_tools IN (0, 1)),
    input_cost_per_million REAL NOT NULL CHECK (input_cost_per_million >= 0),
    output_cost_per_million REAL NOT NULL CHECK (output_cost_per_million >= 0),
    active INTEGER NOT NULL CHECK (active IN (0, 1)),
    created_at TEXT NOT NULL,
    UNIQUE(provider, model)
);

CREATE TABLE model_outcomes (
    id TEXT PRIMARY KEY,
    model_id TEXT NOT NULL REFERENCES model_profiles(id),
    task_class TEXT NOT NULL,
    success INTEGER NOT NULL CHECK (success IN (0, 1)),
    quality REAL NOT NULL CHECK (quality BETWEEN 0 AND 1),
    latency_ms INTEGER NOT NULL CHECK (latency_ms >= 0),
    input_tokens INTEGER NOT NULL CHECK (input_tokens >= 0),
    output_tokens INTEGER NOT NULL CHECK (output_tokens >= 0),
    input_cost REAL NOT NULL CHECK (input_cost >= 0),
    output_cost REAL NOT NULL CHECK (output_cost >= 0),
    tool_attempts INTEGER NOT NULL CHECK (tool_attempts >= 0),
    tool_successes INTEGER NOT NULL CHECK (
        tool_successes >= 0 AND tool_successes <= tool_attempts
    ),
    evidence_json TEXT NOT NULL CHECK (
        json_valid(evidence_json) AND json_array_length(evidence_json) > 0
    ),
    created_at TEXT NOT NULL
);

CREATE TABLE model_routes (
    id TEXT PRIMARY KEY,
    task_class TEXT NOT NULL,
    request_json TEXT NOT NULL CHECK (json_valid(request_json)),
    candidates_json TEXT NOT NULL CHECK (json_valid(candidates_json)),
    selected_model_id TEXT REFERENCES model_profiles(id),
    escalation_model_id TEXT REFERENCES model_profiles(id),
    state TEXT NOT NULL CHECK (
        state IN ('selected', 'escalation_recommended', 'completed', 'exhausted')
    ),
    escalation_improved INTEGER CHECK (escalation_improved IN (0, 1)),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE model_route_attempts (
    id TEXT PRIMARY KEY,
    route_id TEXT NOT NULL REFERENCES model_routes(id) ON DELETE CASCADE,
    sequence INTEGER NOT NULL CHECK (sequence IN (1, 2)),
    model_id TEXT NOT NULL REFERENCES model_profiles(id),
    verification_passed INTEGER NOT NULL CHECK (verification_passed IN (0, 1)),
    confidence REAL NOT NULL CHECK (confidence BETWEEN 0 AND 1),
    quality REAL NOT NULL CHECK (quality BETWEEN 0 AND 1),
    latency_ms INTEGER NOT NULL CHECK (latency_ms >= 0),
    input_tokens INTEGER NOT NULL CHECK (input_tokens >= 0),
    output_tokens INTEGER NOT NULL CHECK (output_tokens >= 0),
    input_cost REAL NOT NULL CHECK (input_cost >= 0),
    output_cost REAL NOT NULL CHECK (output_cost >= 0),
    tool_attempts INTEGER NOT NULL CHECK (tool_attempts >= 0),
    tool_successes INTEGER NOT NULL CHECK (
        tool_successes >= 0 AND tool_successes <= tool_attempts
    ),
    evidence_json TEXT NOT NULL CHECK (
        json_valid(evidence_json) AND json_array_length(evidence_json) > 0
    ),
    outcome_id TEXT NOT NULL UNIQUE REFERENCES model_outcomes(id),
    created_at TEXT NOT NULL,
    UNIQUE(route_id, sequence)
);

CREATE INDEX model_outcomes_lookup
ON model_outcomes(task_class, model_id, created_at);
CREATE INDEX model_routes_task
ON model_routes(task_class, created_at);
CREATE INDEX model_route_attempts_route
ON model_route_attempts(route_id, sequence);
"""

MEMORY_TABLE_V3_SQL = """
CREATE TABLE {table_name} (
    id TEXT PRIMARY KEY,
    type TEXT NOT NULL CHECK (
        type IN (
            'semantic', 'episodic', 'procedural', 'failure',
            'decision', 'preference', 'environment', 'temporary'
        )
    ),
    scope TEXT NOT NULL DEFAULT 'global',
    subject TEXT,
    content TEXT NOT NULL,
    structured_payload_json TEXT NOT NULL DEFAULT '{}' CHECK (
        json_valid(structured_payload_json)
    ),
    confidence REAL NOT NULL CHECK (confidence BETWEEN 0 AND 1),
    importance REAL NOT NULL CHECK (importance BETWEEN 0 AND 1),
    utility_score REAL NOT NULL DEFAULT 0 CHECK (utility_score BETWEEN 0 AND 1),
    source_type TEXT,
    source_id TEXT,
    evidence_json TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(evidence_json)),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    valid_from TEXT NOT NULL,
    valid_until TEXT,
    last_accessed TEXT,
    access_count INTEGER NOT NULL DEFAULT 0 CHECK (access_count >= 0),
    successful_uses INTEGER NOT NULL DEFAULT 0 CHECK (successful_uses >= 0),
    failed_uses INTEGER NOT NULL DEFAULT 0 CHECK (failed_uses >= 0),
    supersedes TEXT REFERENCES {table_name}(id),
    superseded_by TEXT REFERENCES {table_name}(id),
    status TEXT NOT NULL DEFAULT 'candidate' CHECK (
        status IN (
            'candidate', 'confirmed', 'superseded',
            'archived', 'quarantined', 'deleted'
        )
    ),
    token_cost INTEGER NOT NULL CHECK (token_cost >= 0)
)
"""

MEMORY_FTS_V3_SQL = """
CREATE VIRTUAL TABLE memories_fts USING fts5(
    subject,
    content,
    scope,
    type,
    content='memories',
    content_rowid='rowid',
    tokenize='porter unicode61'
);

CREATE TRIGGER memories_ai AFTER INSERT ON memories BEGIN
    INSERT INTO memories_fts(rowid, subject, content, scope, type)
    VALUES (new.rowid, new.subject, new.content, new.scope, new.type);
END;
CREATE TRIGGER memories_ad AFTER DELETE ON memories BEGIN
    INSERT INTO memories_fts(
        memories_fts, rowid, subject, content, scope, type
    ) VALUES (
        'delete', old.rowid, old.subject, old.content, old.scope, old.type
    );
END;
CREATE TRIGGER memories_au AFTER UPDATE ON memories BEGIN
    INSERT INTO memories_fts(
        memories_fts, rowid, subject, content, scope, type
    ) VALUES (
        'delete', old.rowid, old.subject, old.content, old.scope, old.type
    );
    INSERT INTO memories_fts(rowid, subject, content, scope, type)
    VALUES (new.rowid, new.subject, new.content, new.scope, new.type);
END;
"""

MEMORY_FTS_V3_STATEMENTS = (
    """
    CREATE VIRTUAL TABLE memories_fts USING fts5(
        subject, content, scope, type, content='memories',
        content_rowid='rowid', tokenize='porter unicode61'
    )
    """,
    """
    CREATE TRIGGER memories_ai AFTER INSERT ON memories BEGIN
        INSERT INTO memories_fts(rowid, subject, content, scope, type)
        VALUES (new.rowid, new.subject, new.content, new.scope, new.type);
    END
    """,
    """
    CREATE TRIGGER memories_ad AFTER DELETE ON memories BEGIN
        INSERT INTO memories_fts(
            memories_fts, rowid, subject, content, scope, type
        ) VALUES (
            'delete', old.rowid, old.subject, old.content, old.scope, old.type
        );
    END
    """,
    """
    CREATE TRIGGER memories_au AFTER UPDATE ON memories BEGIN
        INSERT INTO memories_fts(
            memories_fts, rowid, subject, content, scope, type
        ) VALUES (
            'delete', old.rowid, old.subject, old.content, old.scope, old.type
        );
        INSERT INTO memories_fts(rowid, subject, content, scope, type)
        VALUES (new.rowid, new.subject, new.content, new.scope, new.type);
    END
    """,
)


@dataclass(frozen=True)
class MigrationStatus:
    current_version: int
    expected_version: int
    pending_versions: tuple[int, ...]


class MigrationManager:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.last_backup_path: Path | None = None

    def status(self) -> MigrationStatus:
        if not self.path.exists() or self.path.stat().st_size == 0:
            return MigrationStatus(
                0, EXPECTED_SCHEMA_VERSION, tuple(range(1, EXPECTED_SCHEMA_VERSION + 1))
            )
        connection = sqlite3.connect(self.path)
        try:
            table = connection.execute(
                """
                SELECT COUNT(*) FROM sqlite_master
                WHERE type = 'table' AND name = 'schema_migrations'
                """
            ).fetchone()[0]
            if not table:
                return MigrationStatus(
                    0,
                    EXPECTED_SCHEMA_VERSION,
                    tuple(range(1, EXPECTED_SCHEMA_VERSION + 1)),
                )
            current = int(
                connection.execute(
                    "SELECT COALESCE(MAX(version), 0) FROM schema_migrations"
                ).fetchone()[0]
            )
        finally:
            connection.close()
        return MigrationStatus(
            current_version=current,
            expected_version=EXPECTED_SCHEMA_VERSION,
            pending_versions=tuple(range(current + 1, EXPECTED_SCHEMA_VERSION + 1)),
        )

    def _backup(self, from_version: int) -> Path:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        backup = self.path.with_name(
            f"{self.path.stem}.bak-v{from_version}-{timestamp}{self.path.suffix}"
        )
        source = sqlite3.connect(self.path)
        destination = sqlite3.connect(backup)
        try:
            source.backup(destination)
        finally:
            destination.close()
            source.close()
        self.last_backup_path = backup
        return backup

    @staticmethod
    def _apply_migration_3(connection: sqlite3.Connection) -> None:
        connection.execute("PRAGMA foreign_keys = OFF")
        try:
            connection.execute("BEGIN IMMEDIATE")
            old_ids = {
                row[0] for row in connection.execute("SELECT id FROM memories")
            }
            for trigger in ("memories_ai", "memories_ad", "memories_au"):
                connection.execute(f"DROP TRIGGER IF EXISTS {trigger}")
            connection.execute("DROP TABLE IF EXISTS memories_fts")
            connection.execute("ALTER TABLE memories RENAME TO memories_v2")
            connection.execute(
                MEMORY_TABLE_V3_SQL.replace("{table_name}", "memories")
            )
            connection.execute(
                """
                INSERT INTO memories (
                    id, type, scope, subject, content, structured_payload_json,
                    confidence, importance, utility_score, source_type, source_id,
                    evidence_json, created_at, updated_at, valid_from, valid_until,
                    last_accessed, access_count, successful_uses, failed_uses,
                    supersedes, superseded_by, status, token_cost
                )
                SELECT
                    id, kind, scope, NULL, content, '{}',
                    confidence, importance,
                    CASE WHEN use_count = 0 THEN 0.0
                         ELSE CAST(success_count AS REAL) / use_count END,
                    CASE WHEN source IS NULL THEN NULL ELSE 'legacy' END,
                    source, evidence_json, created_at,
                    COALESCE(last_used_at, created_at), valid_from, valid_to,
                    last_used_at, use_count, success_count,
                    MAX(use_count - success_count, 0),
                    supersedes, NULL,
                    CASE status
                        WHEN 'active' THEN 'confirmed'
                        WHEN 'candidate' THEN 'candidate'
                        WHEN 'superseded' THEN 'superseded'
                        WHEN 'archived' THEN 'archived'
                    END,
                    token_cost
                FROM memories_v2
                """
            )
            connection.execute(
                """
                UPDATE memories
                SET superseded_by = (
                    SELECT child.id FROM memories AS child
                    WHERE child.supersedes = memories.id
                    ORDER BY child.created_at DESC, child.id DESC
                    LIMIT 1
                )
                WHERE EXISTS (
                    SELECT 1 FROM memories AS child
                    WHERE child.supersedes = memories.id
                )
                """
            )
            connection.execute("DROP TABLE memories_v2")
            for statement in MEMORY_FTS_V3_STATEMENTS:
                connection.execute(statement)
            connection.execute(
                "INSERT INTO memories_fts(memories_fts) VALUES ('rebuild')"
            )
            new_ids = {
                row[0] for row in connection.execute("SELECT id FROM memories")
            }
            if old_ids != new_ids:
                raise RuntimeError("Memory migration did not preserve all IDs")
            fts_count = connection.execute(
                "SELECT COUNT(*) FROM memories_fts"
            ).fetchone()[0]
            if fts_count != len(new_ids):
                raise RuntimeError("Memory search index verification failed")
            connection.execute(
                """
                INSERT INTO schema_migrations(version, applied_at)
                VALUES (3, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
                """
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.execute("PRAGMA foreign_keys = ON")

    @staticmethod
    def _apply_migration_4(connection: sqlite3.Connection) -> None:
        try:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                ALTER TABLE memories
                ADD COLUMN retention_reason_json TEXT NOT NULL
                DEFAULT '["legacy_or_direct_write"]'
                CHECK (json_valid(retention_reason_json))
                """
            )
            connection.execute(
                """
                CREATE TABLE memory_write_decisions (
                    id TEXT PRIMARY KEY,
                    candidate_hash TEXT NOT NULL,
                    outcome TEXT NOT NULL CHECK (
                        outcome IN (
                            'ignore', 'store_temporary', 'store_candidate',
                            'store_confirmed', 'update_existing',
                            'supersede_existing', 'request_verification',
                            'quarantine'
                        )
                    ),
                    memory_id TEXT REFERENCES memories(id),
                    matched_memory_id TEXT REFERENCES memories(id),
                    reasons_json TEXT NOT NULL CHECK (json_valid(reasons_json)),
                    risk_flags_json TEXT NOT NULL DEFAULT '[]' CHECK (
                        json_valid(risk_flags_json)
                    ),
                    scope TEXT,
                    memory_type TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    evidence_count INTEGER NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX memory_write_decisions_created
                ON memory_write_decisions(created_at)
                """
            )
            connection.execute(
                """
                CREATE INDEX memory_write_decisions_memory
                ON memory_write_decisions(memory_id)
                """
            )
            connection.execute(
                """
                INSERT INTO schema_migrations(version, applied_at)
                VALUES (4, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
                """
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise

    @staticmethod
    def _apply_migration_5(connection: sqlite3.Connection) -> None:
        try:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                CREATE TABLE memory_consolidation_runs (
                    id TEXT PRIMARY KEY,
                    status TEXT NOT NULL CHECK (
                        status IN (
                            'planned', 'applied',
                            'partially_applied', 'cancelled'
                        )
                    ),
                    scope TEXT,
                    config_json TEXT NOT NULL CHECK (json_valid(config_json)),
                    summary_json TEXT NOT NULL CHECK (json_valid(summary_json)),
                    created_at TEXT NOT NULL,
                    applied_at TEXT
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE memory_consolidation_actions (
                    id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL
                        REFERENCES memory_consolidation_runs(id),
                    kind TEXT NOT NULL CHECK (
                        kind IN (
                            'merge', 'archive', 'supersession',
                            'promotion', 'conflict', 'decay'
                        )
                    ),
                    target_ids_json TEXT NOT NULL CHECK (
                        json_valid(target_ids_json)
                    ),
                    expected_versions_json TEXT NOT NULL CHECK (
                        json_valid(expected_versions_json)
                    ),
                    payload_json TEXT NOT NULL DEFAULT '{}' CHECK (
                        json_valid(payload_json)
                    ),
                    reason TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'proposed' CHECK (
                        status IN (
                            'proposed', 'applied', 'skipped',
                            'review_required', 'error'
                        )
                    ),
                    error_type TEXT,
                    created_at TEXT NOT NULL,
                    applied_at TEXT
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX memory_consolidation_actions_run
                ON memory_consolidation_actions(run_id, kind)
                """
            )
            connection.execute(
                """
                INSERT INTO schema_migrations(version, applied_at)
                VALUES (5, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
                """
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise

    @staticmethod
    def _apply_migration_6(connection: sqlite3.Connection) -> None:
        try:
            connection.execute("BEGIN IMMEDIATE")
            for statement in MIGRATION_6_SQL.split(";"):
                if statement.strip():
                    connection.execute(statement)
            connection.execute(
                """
                INSERT INTO schema_migrations(version, applied_at)
                VALUES (6, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
                """
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise

    @staticmethod
    def _apply_migration_7(connection: sqlite3.Connection) -> None:
        try:
            connection.execute("BEGIN IMMEDIATE")
            for statement in MIGRATION_7_SQL.split(";"):
                if statement.strip():
                    connection.execute(statement)
            connection.execute(
                """
                INSERT INTO schema_migrations(version, applied_at)
                VALUES (7, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
                """
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise

    @staticmethod
    def _apply_migration_8(connection: sqlite3.Connection) -> None:
        try:
            connection.execute("BEGIN IMMEDIATE")
            for statement in MIGRATION_8_SQL.split(";"):
                if statement.strip():
                    connection.execute(statement)
            connection.execute(
                """
                INSERT INTO schema_migrations(version, applied_at)
                VALUES (8, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
                """
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise

    @staticmethod
    def _apply_migration_9(connection: sqlite3.Connection) -> None:
        try:
            connection.execute("BEGIN IMMEDIATE")
            exists = bool(
                connection.execute(
                    """
                    SELECT COUNT(*) FROM sqlite_master
                    WHERE type = 'table' AND name = 'context_uses'
                    """
                ).fetchone()[0]
            )
            if exists:
                connection.execute(
                    "ALTER TABLE context_uses RENAME TO context_uses_v8"
                )
            connection.execute(CONTEXT_USES_V9_SQL)
            if exists:
                connection.execute(
                    """
                    INSERT INTO context_uses(
                        task_id, source_type, source_id, tokens,
                        utility, roi, useful
                    )
                    SELECT task_id, source_type, source_id, tokens,
                           utility, roi, useful
                    FROM context_uses_v8
                    """
                )
                connection.execute("DROP TABLE context_uses_v8")
            connection.execute(
                """
                INSERT INTO schema_migrations(version, applied_at)
                VALUES (9, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
                """
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise

    @staticmethod
    def _apply_migration_10(connection: sqlite3.Connection) -> None:
        try:
            connection.execute("BEGIN IMMEDIATE")
            for statement in MIGRATION_10_SQL.split(";"):
                if statement.strip():
                    connection.execute(statement)
            connection.execute(
                """
                INSERT INTO schema_migrations(version, applied_at)
                VALUES (10, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
                """
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise

    @staticmethod
    def _apply_migration_11(connection: sqlite3.Connection) -> None:
        try:
            connection.execute("BEGIN IMMEDIATE")
            for statement in MIGRATION_11_SQL.split(";"):
                if statement.strip():
                    connection.execute(statement)
            has_tasks = connection.execute(
                """
                SELECT COUNT(*) FROM sqlite_master
                WHERE type = 'table' AND name = 'tasks'
                """
            ).fetchone()[0]
            if has_tasks:
                connection.execute(
                    """
                    UPDATE context_uses SET useful = NULL
                    WHERE task_id IN (
                        SELECT id FROM tasks WHERE status = 'planned'
                    )
                    """
                )
            connection.execute(
                """
                INSERT INTO schema_migrations(version, applied_at)
                VALUES (11, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
                """
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise

    @staticmethod
    def _apply_migration_12(connection: sqlite3.Connection) -> None:
        try:
            connection.execute("BEGIN IMMEDIATE")
            for statement in MIGRATION_12_SQL.split(";"):
                if statement.strip():
                    connection.execute(statement)
            connection.execute(
                """
                INSERT INTO schema_migrations(version, applied_at)
                VALUES (12, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
                """
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise

    @staticmethod
    def _apply_migration_13(connection: sqlite3.Connection) -> None:
        try:
            connection.execute("BEGIN IMMEDIATE")
            for statement in MIGRATION_13_SQL.split(";"):
                if statement.strip():
                    connection.execute(statement)
            connection.execute(
                """
                INSERT INTO schema_migrations(version, applied_at)
                VALUES (13, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
                """
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise

    @staticmethod
    def _apply_migration_14(connection: sqlite3.Connection) -> None:
        try:
            connection.execute("BEGIN IMMEDIATE")
            for statement in MIGRATION_14_SQL.split(";"):
                if statement.strip():
                    connection.execute(statement)
            connection.execute(
                """
                INSERT INTO schema_migrations(version, applied_at)
                VALUES (14, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
                """
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise

    @staticmethod
    def _apply_migration_15(connection: sqlite3.Connection) -> None:
        try:
            connection.execute("BEGIN IMMEDIATE")
            for statement in MIGRATION_15_SQL.split(";"):
                if statement.strip():
                    connection.execute(statement)
            connection.execute(
                """
                INSERT INTO schema_migrations(version, applied_at)
                VALUES (15, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
                """
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise

    @staticmethod
    def _apply_migration_16(connection: sqlite3.Connection) -> None:
        try:
            connection.execute("BEGIN IMMEDIATE")
            for statement in MIGRATION_16_SQL.split(";"):
                if statement.strip():
                    connection.execute(statement)
            connection.execute(
                """
                INSERT INTO schema_migrations(version, applied_at)
                VALUES (16, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
                """
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise

    @staticmethod
    def _apply_migration_17(connection: sqlite3.Connection) -> None:
        try:
            connection.execute("BEGIN IMMEDIATE")
            for statement in MIGRATION_17_SQL.split(";"):
                if statement.strip():
                    connection.execute(statement)
            connection.execute(
                """
                INSERT INTO schema_migrations(version, applied_at)
                VALUES (17, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
                """
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise

    @staticmethod
    def _apply_migration_18(connection: sqlite3.Connection) -> None:
        try:
            connection.execute("BEGIN IMMEDIATE")
            for statement in MIGRATION_18_SQL.split(";"):
                if statement.strip():
                    connection.execute(statement)
            connection.execute(
                """
                INSERT INTO schema_migrations(version, applied_at)
                VALUES (18, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
                """
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise

    @staticmethod
    def _apply_migration_19(connection: sqlite3.Connection) -> None:
        try:
            connection.execute("BEGIN IMMEDIATE")
            for statement in MIGRATION_19_SQL.split(";"):
                if statement.strip():
                    connection.execute(statement)
            connection.execute(
                """
                INSERT INTO schema_migrations(version, applied_at)
                VALUES (19, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
                """
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise

    @staticmethod
    def _apply_migration_20(connection: sqlite3.Connection) -> None:
        try:
            connection.execute("BEGIN IMMEDIATE")
            for statement in MIGRATION_20_SQL.split(";"):
                if statement.strip():
                    connection.execute(statement)
            connection.execute(
                """
                INSERT INTO schema_migrations(version, applied_at)
                VALUES (20, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
                """
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise

    @staticmethod
    def _apply_migration_21(connection: sqlite3.Connection) -> None:
        try:
            connection.execute("BEGIN IMMEDIATE")
            for statement in MIGRATION_21_SQL.split(";"):
                if statement.strip():
                    connection.execute(statement)
            connection.execute(
                """
                INSERT INTO schema_migrations(version, applied_at)
                VALUES (21, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
                """
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise

    @staticmethod
    def _apply_migration_22(connection: sqlite3.Connection) -> None:
        try:
            connection.execute("BEGIN IMMEDIATE")
            for statement in MIGRATION_22_SQL.split(";"):
                if statement.strip():
                    connection.execute(statement)
            connection.execute(
                """
                INSERT INTO schema_migrations(version, applied_at)
                VALUES (22, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
                """
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise

    @staticmethod
    def _apply_migration_23(connection: sqlite3.Connection) -> None:
        try:
            connection.execute("BEGIN IMMEDIATE")
            for statement in MIGRATION_23_SQL.split(";"):
                if statement.strip():
                    connection.execute(statement)
            connection.execute(
                """
                INSERT INTO schema_migrations(version, applied_at)
                VALUES (23, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
                """
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise

    @staticmethod
    def _apply_migration_24(connection: sqlite3.Connection) -> None:
        try:
            connection.execute("BEGIN IMMEDIATE")
            for statement in MIGRATION_24_SQL.split(";"):
                if statement.strip():
                    connection.execute(statement)
            connection.execute(
                """
                INSERT INTO schema_migrations(version, applied_at)
                VALUES (24, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
                """
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise

    @staticmethod
    def _apply_migration_25(connection: sqlite3.Connection) -> None:
        try:
            connection.execute("BEGIN IMMEDIATE")
            for statement in MIGRATION_25_SQL.split(";"):
                if statement.strip():
                    connection.execute(statement)
            connection.execute(
                """
                INSERT INTO schema_migrations(version, applied_at)
                VALUES (25, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
                """
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise

    @staticmethod
    def _apply_migration_26(connection: sqlite3.Connection) -> None:
        try:
            connection.execute("BEGIN IMMEDIATE")
            for statement in MIGRATION_26_SQL.split(";"):
                if statement.strip():
                    connection.execute(statement)
            connection.execute(
                """
                INSERT INTO schema_migrations(version, applied_at)
                VALUES (26, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
                """
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise

    @staticmethod
    def _apply_migration_27(connection: sqlite3.Connection) -> None:
        try:
            connection.execute("BEGIN IMMEDIATE")
            for statement in MIGRATION_27_SQL.split(";"):
                if statement.strip():
                    connection.execute(statement)
            connection.execute(
                """
                INSERT INTO schema_migrations(version, applied_at)
                VALUES (27, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
                """
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise

    def apply_pending(self) -> MigrationStatus:
        status = self.status()
        if status.current_version == 0:
            raise MigrationRequired(
                "Unversioned databases require an explicit import or a fresh database"
            )
        if status.current_version > EXPECTED_SCHEMA_VERSION:
            raise MigrationRequired(
                f"Database schema {status.current_version} is newer than "
                f"runtime schema {EXPECTED_SCHEMA_VERSION}"
            )
        if not status.pending_versions:
            return status
        self._backup(status.current_version)
        connection = sqlite3.connect(self.path)
        try:
            if 2 in status.pending_versions:
                connection.executescript(MIGRATION_2_SQL)
                connection.execute(
                    """
                    INSERT INTO schema_migrations(version, applied_at)
                    VALUES (2, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
                    """
                )
                connection.commit()
            if 3 in status.pending_versions:
                self._apply_migration_3(connection)
            if 4 in status.pending_versions:
                self._apply_migration_4(connection)
            if 5 in status.pending_versions:
                self._apply_migration_5(connection)
            if 6 in status.pending_versions:
                self._apply_migration_6(connection)
            if 7 in status.pending_versions:
                self._apply_migration_7(connection)
            if 8 in status.pending_versions:
                self._apply_migration_8(connection)
            if 9 in status.pending_versions:
                self._apply_migration_9(connection)
            if 10 in status.pending_versions:
                self._apply_migration_10(connection)
            if 11 in status.pending_versions:
                self._apply_migration_11(connection)
            if 12 in status.pending_versions:
                self._apply_migration_12(connection)
            if 13 in status.pending_versions:
                self._apply_migration_13(connection)
            if 14 in status.pending_versions:
                self._apply_migration_14(connection)
            if 15 in status.pending_versions:
                self._apply_migration_15(connection)
            if 16 in status.pending_versions:
                self._apply_migration_16(connection)
            if 17 in status.pending_versions:
                self._apply_migration_17(connection)
            if 18 in status.pending_versions:
                self._apply_migration_18(connection)
            if 19 in status.pending_versions:
                self._apply_migration_19(connection)
            if 20 in status.pending_versions:
                self._apply_migration_20(connection)
            if 21 in status.pending_versions:
                self._apply_migration_21(connection)
            if 22 in status.pending_versions:
                self._apply_migration_22(connection)
            if 23 in status.pending_versions:
                self._apply_migration_23(connection)
            if 24 in status.pending_versions:
                self._apply_migration_24(connection)
            if 25 in status.pending_versions:
                self._apply_migration_25(connection)
            if 26 in status.pending_versions:
                self._apply_migration_26(connection)
            if 27 in status.pending_versions:
                self._apply_migration_27(connection)
        finally:
            connection.close()
        return self.status()
