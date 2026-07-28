from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

EXPECTED_SCHEMA_VERSION = 53


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

MIGRATION_28_SQL = """
ALTER TABLE model_profiles
ADD COLUMN local INTEGER NOT NULL DEFAULT 0 CHECK (local IN (0, 1));

CREATE TABLE local_model_discoveries (
    id TEXT PRIMARY KEY,
    provider TEXT NOT NULL CHECK (provider = 'ollama'),
    status TEXT NOT NULL CHECK (status IN ('completed', 'failed')),
    models_json TEXT NOT NULL CHECK (json_valid(models_json)),
    error_kind TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE local_benchmark_runs (
    id TEXT PRIMARY KEY,
    discovery_id TEXT REFERENCES local_model_discoveries(id),
    model_id TEXT NOT NULL REFERENCES model_profiles(id),
    dataset TEXT NOT NULL,
    dataset_version INTEGER NOT NULL,
    seed INTEGER NOT NULL,
    case_count INTEGER NOT NULL CHECK (case_count > 0),
    outcome_ids_json TEXT NOT NULL CHECK (
        json_valid(outcome_ids_json)
        AND json_array_length(outcome_ids_json) = case_count
    ),
    summary_json TEXT NOT NULL CHECK (json_valid(summary_json)),
    created_at TEXT NOT NULL
);

CREATE TABLE local_route_policies (
    route_id TEXT PRIMARY KEY REFERENCES model_routes(id) ON DELETE CASCADE,
    risk_level TEXT NOT NULL CHECK (risk_level IN ('low', 'medium', 'high')),
    contains_sensitive_context INTEGER NOT NULL CHECK (
        contains_sensitive_context IN (0, 1)
    ),
    cloud_escalation_configured INTEGER NOT NULL CHECK (
        cloud_escalation_configured IN (0, 1)
    ),
    external_permission_reference_hash TEXT CHECK (
        external_permission_reference_hash IS NULL
        OR length(external_permission_reference_hash) = 64
    ),
    local_candidate_count INTEGER NOT NULL CHECK (local_candidate_count >= 0),
    cloud_candidate_count INTEGER NOT NULL CHECK (cloud_candidate_count >= 0),
    cloud_candidates_allowed INTEGER NOT NULL CHECK (
        cloud_candidates_allowed IN (0, 1)
    ),
    decision_reason TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX local_benchmark_runs_model
ON local_benchmark_runs(model_id, created_at);
"""

MIGRATION_29_SQL = """
CREATE TABLE tool_definitions (
    name TEXT PRIMARY KEY,
    description TEXT NOT NULL,
    input_schema_json TEXT NOT NULL CHECK (json_valid(input_schema_json)),
    output_schema_json TEXT NOT NULL CHECK (json_valid(output_schema_json)),
    permissions_json TEXT NOT NULL CHECK (json_valid(permissions_json)),
    cost REAL NOT NULL CHECK (cost >= 0),
    latency_estimate_ms INTEGER NOT NULL CHECK (latency_estimate_ms >= 0),
    side_effect TEXT NOT NULL CHECK (
        side_effect IN ('READ_ONLY', 'REVERSIBLE_WRITE', 'DESTRUCTIVE')
    ),
    network_access INTEGER NOT NULL CHECK (network_access IN (0, 1)),
    filesystem_access TEXT NOT NULL CHECK (
        filesystem_access IN ('NONE', 'READ', 'WRITE')
    ),
    credential_requirements_json TEXT NOT NULL CHECK (
        json_valid(credential_requirements_json)
    ),
    definition_hash TEXT NOT NULL UNIQUE CHECK (length(definition_hash) = 64),
    created_at TEXT NOT NULL
);

CREATE INDEX tool_definitions_side_effect
ON tool_definitions(side_effect, network_access, filesystem_access);
"""

MIGRATION_30_SQL = """
CREATE TABLE tool_routes (
    id TEXT PRIMARY KEY,
    task_class TEXT NOT NULL,
    request_json TEXT NOT NULL CHECK (json_valid(request_json)),
    selected_tools_json TEXT NOT NULL CHECK (json_valid(selected_tools_json)),
    deterministic_tool_required INTEGER NOT NULL CHECK (
        deterministic_tool_required IN (0, 1)
    ),
    created_at TEXT NOT NULL
);

CREATE TABLE tool_route_candidates (
    route_id TEXT NOT NULL REFERENCES tool_routes(id) ON DELETE CASCADE,
    sequence INTEGER NOT NULL CHECK (sequence > 0),
    tool_name TEXT NOT NULL REFERENCES tool_definitions(name),
    selected INTEGER NOT NULL CHECK (selected IN (0, 1)),
    candidate_json TEXT NOT NULL CHECK (json_valid(candidate_json)),
    PRIMARY KEY(route_id, sequence),
    UNIQUE(route_id, tool_name)
);

CREATE TABLE tool_outcomes (
    id TEXT PRIMARY KEY,
    route_id TEXT NOT NULL REFERENCES tool_routes(id) ON DELETE CASCADE,
    tool_name TEXT NOT NULL REFERENCES tool_definitions(name),
    task_class TEXT NOT NULL,
    success INTEGER NOT NULL CHECK (success IN (0, 1)),
    latency_ms INTEGER NOT NULL CHECK (latency_ms >= 0),
    cost REAL NOT NULL CHECK (cost >= 0),
    evidence_json TEXT NOT NULL CHECK (
        json_valid(evidence_json) AND json_array_length(evidence_json) > 0
    ),
    created_at TEXT NOT NULL,
    UNIQUE(route_id, tool_name)
);

CREATE INDEX tool_outcomes_history
ON tool_outcomes(tool_name, task_class, created_at);
"""

MIGRATION_31_SQL = """
CREATE TABLE capability_grants (
    id TEXT PRIMARY KEY,
    subject_type TEXT NOT NULL CHECK (
        subject_type IN ('task', 'agent', 'skill')
    ),
    subject_id TEXT NOT NULL,
    capability TEXT NOT NULL CHECK (
        capability IN (
            'network.read', 'network.write',
            'filesystem.read', 'filesystem.write',
            'shell.execute',
            'database.read', 'database.write',
            'memory.read', 'memory.write',
            'skill.create', 'skill.activate',
            'agent.create', 'credential.use'
        )
    ),
    resource_scope TEXT NOT NULL CHECK (
        length(trim(resource_scope)) > 0
        AND resource_scope NOT IN ('*', 'all', 'global')
        AND length(resource_scope) <= 512
    ),
    expires_at TEXT NOT NULL,
    delegable INTEGER NOT NULL CHECK (delegable IN (0, 1)),
    grantor_type TEXT NOT NULL CHECK (
        grantor_type IN ('trusted_workflow', 'task', 'agent', 'skill')
    ),
    grantor_id TEXT NOT NULL,
    parent_grant_id TEXT REFERENCES capability_grants(id),
    reason TEXT NOT NULL CHECK (length(trim(reason)) > 0),
    evidence_json TEXT NOT NULL CHECK (
        json_valid(evidence_json) AND json_array_length(evidence_json) > 0
    ),
    created_at TEXT NOT NULL,
    revoked_at TEXT,
    revocation_reason TEXT,
    CHECK (
        (revoked_at IS NULL AND revocation_reason IS NULL)
        OR (revoked_at IS NOT NULL AND length(trim(revocation_reason)) > 0)
    )
);

CREATE INDEX capability_grants_active
ON capability_grants(
    subject_type, subject_id, capability, resource_scope, expires_at
);

CREATE INDEX capability_grants_parent
ON capability_grants(parent_grant_id);

CREATE TABLE capability_decisions (
    id TEXT PRIMARY KEY,
    subject_type TEXT NOT NULL CHECK (
        subject_type IN ('task', 'agent', 'skill')
    ),
    subject_id TEXT NOT NULL,
    capability TEXT NOT NULL,
    resource_scope TEXT NOT NULL,
    allowed INTEGER NOT NULL CHECK (allowed IN (0, 1)),
    grant_id TEXT REFERENCES capability_grants(id),
    reason TEXT NOT NULL,
    created_at TEXT NOT NULL,
    CHECK (
        (allowed = 1 AND grant_id IS NOT NULL)
        OR (allowed = 0 AND grant_id IS NULL)
    )
);

CREATE INDEX capability_decisions_subject
ON capability_decisions(subject_type, subject_id, created_at);
"""

MIGRATION_32_SQL = """
CREATE TABLE content_security_assessments (
    id TEXT PRIMARY KEY,
    assessment_hash TEXT NOT NULL UNIQUE CHECK (length(assessment_hash) = 64),
    origin TEXT NOT NULL CHECK (
        origin IN (
            'system_policy', 'developer_instruction', 'user_instruction',
            'skill_instruction', 'retrieved_memory', 'web_content',
            'document', 'tool_output'
        )
    ),
    source_id TEXT NOT NULL CHECK (
        length(trim(source_id)) > 0 AND length(source_id) <= 512
    ),
    content_hash TEXT NOT NULL CHECK (length(content_hash) = 64),
    authority TEXT NOT NULL CHECK (
        authority IN ('system', 'developer', 'user', 'scoped_skill', 'none')
    ),
    disposition TEXT NOT NULL CHECK (
        disposition IN (
            'trusted_instruction', 'scoped_instruction',
            'data_only', 'quarantine'
        )
    ),
    suspicious_signals_json TEXT NOT NULL CHECK (
        json_valid(suspicious_signals_json)
    ),
    provenance_json TEXT NOT NULL CHECK (json_valid(provenance_json)),
    created_at TEXT NOT NULL
);

CREATE INDEX content_security_source
ON content_security_assessments(origin, source_id, created_at);

CREATE TABLE trusted_workflow_approvals (
    id TEXT PRIMARY KEY,
    assessment_id TEXT NOT NULL
        REFERENCES content_security_assessments(id),
    action TEXT NOT NULL CHECK (
        action IN (
            'memory.create', 'skill.create',
            'agent.create', 'permission.grant'
        )
    ),
    target_ref TEXT NOT NULL CHECK (
        length(trim(target_ref)) > 0 AND length(target_ref) <= 512
    ),
    approver_origin TEXT NOT NULL CHECK (
        approver_origin IN (
            'system_policy', 'developer_instruction', 'user_instruction'
        )
    ),
    approver_id TEXT NOT NULL CHECK (
        length(trim(approver_id)) > 0 AND length(approver_id) <= 128
    ),
    reason TEXT NOT NULL CHECK (
        length(trim(reason)) > 0 AND length(reason) <= 2000
    ),
    evidence_json TEXT NOT NULL CHECK (
        json_valid(evidence_json) AND json_array_length(evidence_json) > 0
    ),
    created_at TEXT NOT NULL,
    consumed_at TEXT
);

CREATE INDEX trusted_workflow_approval_scope
ON trusted_workflow_approvals(
    assessment_id, action, target_ref, consumed_at
);

ALTER TABLE context_uses
ADD COLUMN security_assessment_id TEXT
REFERENCES content_security_assessments(id);

ALTER TABLE context_uses
ADD COLUMN content_origin TEXT CHECK (
    content_origin IS NULL OR content_origin IN (
        'system_policy', 'developer_instruction', 'user_instruction',
        'skill_instruction', 'retrieved_memory', 'web_content',
        'document', 'tool_output'
    )
);

ALTER TABLE context_uses
ADD COLUMN security_authority TEXT CHECK (
    security_authority IS NULL OR security_authority IN (
        'system', 'developer', 'user', 'scoped_skill', 'none'
    )
);

ALTER TABLE memory_write_decisions
ADD COLUMN security_assessment_id TEXT
REFERENCES content_security_assessments(id);

ALTER TABLE capability_grants
ADD COLUMN source_assessment_id TEXT
REFERENCES content_security_assessments(id);

ALTER TABLE capability_grants
ADD COLUMN workflow_approval_id TEXT
REFERENCES trusted_workflow_approvals(id);
"""

MIGRATION_33_SQL = """
CREATE TABLE secret_access_events (
    id TEXT PRIMARY KEY,
    reference_hash TEXT NOT NULL CHECK (length(reference_hash) = 64),
    provider TEXT NOT NULL CHECK (
        provider IN ('env', 'keyring', 'external')
    ),
    subject_type TEXT NOT NULL CHECK (
        subject_type IN ('task', 'agent', 'skill')
    ),
    subject_id TEXT NOT NULL CHECK (
        length(trim(subject_id)) > 0 AND length(subject_id) <= 128
    ),
    decision TEXT NOT NULL CHECK (
        decision IN (
            'denied', 'missing', 'granted',
            'provider_unavailable', 'provider_error'
        )
    ),
    capability_decision_id TEXT NOT NULL
        REFERENCES capability_decisions(id),
    created_at TEXT NOT NULL
);

CREATE INDEX secret_access_subject
ON secret_access_events(subject_type, subject_id, created_at);

CREATE INDEX secret_access_reference
ON secret_access_events(reference_hash, created_at);
"""

MIGRATION_34_SQL = """
ALTER TABLE memories ADD COLUMN sensitivity TEXT NOT NULL DEFAULT 'internal'
CHECK (
    sensitivity IN ('public', 'internal', 'personal', 'confidential', 'secret')
);
ALTER TABLE memories ADD COLUMN retention_until TEXT;
ALTER TABLE memories ADD COLUMN privacy_policy_version INTEGER NOT NULL DEFAULT 1
CHECK (privacy_policy_version >= 1);

CREATE TABLE privacy_policies (
    classification TEXT PRIMARY KEY CHECK (
        classification IN (
            'public', 'internal', 'personal', 'confidential', 'secret'
        )
    ),
    allowed_providers_json TEXT NOT NULL CHECK (
        json_valid(allowed_providers_json)
        AND json_type(allowed_providers_json) = 'array'
    ),
    retention_days INTEGER CHECK (
        retention_days IS NULL OR retention_days BETWEEN 1 AND 36500
    ),
    exportable INTEGER NOT NULL CHECK (exportable IN (0, 1)),
    deletion_requirement TEXT NOT NULL CHECK (
        deletion_requirement IN ('standard', 'secure')
    ),
    version INTEGER NOT NULL CHECK (version >= 1),
    reason TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

INSERT INTO privacy_policies VALUES
('public', '["local"]', NULL, 1, 'standard', 1, 'conservative_default',
 strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
('internal', '["local"]', NULL, 1, 'standard', 1, 'conservative_default',
 strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
('personal', '["local"]', 365, 1, 'secure', 1, 'conservative_default',
 strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
('confidential', '["local"]', 90, 0, 'secure', 1, 'conservative_default',
 strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
('secret', '["local"]', 30, 0, 'secure', 1, 'conservative_default',
 strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));

CREATE TABLE privacy_policy_events (
    id TEXT PRIMARY KEY,
    classification TEXT NOT NULL,
    version INTEGER NOT NULL,
    policy_json TEXT NOT NULL CHECK (json_valid(policy_json)),
    actor TEXT NOT NULL,
    reason TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(classification, version)
);

CREATE TABLE privacy_decisions (
    id TEXT PRIMARY KEY,
    action TEXT NOT NULL CHECK (
        action IN ('classify', 'provider', 'export', 'retention', 'delete')
    ),
    memory_ids_json TEXT NOT NULL CHECK (json_valid(memory_ids_json)),
    classifications_json TEXT NOT NULL CHECK (
        json_valid(classifications_json)
    ),
    provider TEXT,
    allowed INTEGER NOT NULL CHECK (allowed IN (0, 1)),
    reason TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE memory_deletion_requests (
    id TEXT PRIMARY KEY,
    memory_id TEXT NOT NULL REFERENCES memories(id),
    classification TEXT NOT NULL,
    expected_updated_at TEXT NOT NULL,
    deletion_requirement TEXT NOT NULL CHECK (
        deletion_requirement IN ('standard', 'secure')
    ),
    requested_by TEXT NOT NULL,
    reason TEXT NOT NULL,
    status TEXT NOT NULL CHECK (
        status IN ('planned', 'completed', 'failed')
    ),
    verification_json TEXT NOT NULL DEFAULT '{}' CHECK (
        json_valid(verification_json)
    ),
    created_at TEXT NOT NULL,
    completed_at TEXT
);

CREATE INDEX privacy_decisions_created
ON privacy_decisions(action, created_at);
CREATE INDEX memory_deletion_requests_memory
ON memory_deletion_requests(memory_id, created_at);

CREATE INDEX memories_privacy_retention
ON memories(sensitivity, retention_until)
WHERE lifecycle_state != 'deleted';
"""

MIGRATION_35_SQL = """
CREATE TABLE runtime_experiments (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    domain TEXT NOT NULL CHECK (
        domain IN (
            'retrieval_algorithm', 'context_budget', 'skill_version',
            'model_router', 'planner_strategy'
        )
    ),
    hypothesis TEXT NOT NULL,
    randomization_unit TEXT NOT NULL,
    seed INTEGER NOT NULL CHECK (seed BETWEEN 0 AND 2147483647),
    variants_json TEXT NOT NULL CHECK (
        json_valid(variants_json) AND json_type(variants_json) = 'array'
    ),
    primary_metric TEXT NOT NULL CHECK (
        primary_metric IN (
            'quality', 'tokens', 'cost', 'latency_ms', 'failure_rate'
        )
    ),
    status TEXT NOT NULL CHECK (
        status IN ('draft', 'running', 'completed', 'cancelled')
    ),
    created_at TEXT NOT NULL,
    started_at TEXT,
    completed_at TEXT
);

CREATE TABLE experiment_assignments (
    id TEXT PRIMARY KEY,
    experiment_id TEXT NOT NULL REFERENCES runtime_experiments(id),
    unit_hash TEXT NOT NULL CHECK (length(unit_hash) = 64),
    variant_id TEXT NOT NULL,
    bucket INTEGER NOT NULL CHECK (bucket BETWEEN 0 AND 9999),
    assigned_at TEXT NOT NULL,
    UNIQUE(experiment_id, unit_hash)
);

CREATE TABLE experiment_outcomes (
    id TEXT PRIMARY KEY,
    experiment_id TEXT NOT NULL REFERENCES runtime_experiments(id),
    assignment_id TEXT NOT NULL UNIQUE REFERENCES experiment_assignments(id),
    quality REAL NOT NULL CHECK (quality BETWEEN 0 AND 1),
    tokens INTEGER NOT NULL CHECK (tokens >= 0),
    cost REAL NOT NULL CHECK (cost >= 0),
    latency_ms INTEGER NOT NULL CHECK (latency_ms >= 0),
    failed INTEGER NOT NULL CHECK (failed IN (0, 1)),
    evidence_json TEXT NOT NULL CHECK (
        json_valid(evidence_json) AND json_type(evidence_json) = 'array'
    ),
    created_at TEXT NOT NULL
);

CREATE INDEX experiment_assignments_variant
ON experiment_assignments(experiment_id, variant_id);
CREATE INDEX experiment_outcomes_experiment
ON experiment_outcomes(experiment_id, created_at);
"""

MIGRATION_36_SQL = """
CREATE TABLE regression_runs (
    id TEXT PRIMARY KEY,
    scope TEXT NOT NULL,
    task_class TEXT NOT NULL,
    baseline_start TEXT NOT NULL,
    baseline_end TEXT NOT NULL,
    candidate_start TEXT NOT NULL,
    candidate_end TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('completed')),
    created_at TEXT NOT NULL
);

CREATE TABLE regression_changes (
    run_id TEXT NOT NULL REFERENCES regression_runs(id),
    change_id TEXT NOT NULL,
    domain TEXT NOT NULL,
    changed_at TEXT NOT NULL,
    before_ref TEXT NOT NULL,
    after_ref TEXT NOT NULL,
    rollback_ref TEXT,
    affected_metrics_json TEXT NOT NULL CHECK (json_valid(affected_metrics_json)),
    evidence_json TEXT NOT NULL CHECK (json_valid(evidence_json)),
    PRIMARY KEY (run_id, change_id)
);

CREATE TABLE regression_metrics (
    run_id TEXT NOT NULL REFERENCES regression_runs(id),
    metric TEXT NOT NULL CHECK (
        metric IN (
            'token_consumption', 'quality', 'latency', 'model_escalation',
            'memory_retrieval', 'skill_failure'
        )
    ),
    baseline_value REAL NOT NULL,
    baseline_samples INTEGER NOT NULL CHECK (baseline_samples >= 0),
    baseline_stddev REAL NOT NULL CHECK (baseline_stddev >= 0),
    candidate_value REAL NOT NULL,
    candidate_samples INTEGER NOT NULL CHECK (candidate_samples >= 0),
    adverse_delta REAL NOT NULL,
    relative_delta REAL NOT NULL,
    effective_threshold REAL NOT NULL CHECK (effective_threshold >= 0),
    minimum_samples INTEGER NOT NULL CHECK (minimum_samples > 0),
    status TEXT NOT NULL CHECK (
        status IN ('regressed', 'within_limit', 'insufficient_data')
    ),
    PRIMARY KEY (run_id, metric)
);

CREATE TABLE regression_alerts (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES regression_runs(id),
    metric TEXT NOT NULL,
    severity TEXT NOT NULL CHECK (severity IN ('warning', 'critical')),
    likely_change_id TEXT,
    attribution TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (run_id, likely_change_id)
        REFERENCES regression_changes(run_id, change_id)
);

CREATE TABLE rollback_recommendations (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES regression_runs(id),
    change_id TEXT NOT NULL,
    rollback_ref TEXT NOT NULL,
    reason TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('proposed')),
    created_at TEXT NOT NULL,
    FOREIGN KEY (run_id, change_id)
        REFERENCES regression_changes(run_id, change_id)
);

CREATE INDEX regression_runs_scope
ON regression_runs(scope, task_class, created_at);
CREATE INDEX regression_alerts_run
ON regression_alerts(run_id, severity);
"""

MIGRATION_37_SQL = """
CREATE TABLE skill_benchmark_runs (
    id TEXT PRIMARY KEY,
    skill_name TEXT NOT NULL,
    existing_ref TEXT NOT NULL,
    candidate_ref TEXT NOT NULL,
    policy_json TEXT NOT NULL CHECK (json_valid(policy_json)),
    status TEXT NOT NULL CHECK (status IN ('completed')),
    created_at TEXT NOT NULL
);

CREATE TABLE skill_benchmark_trials (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES skill_benchmark_runs(id),
    case_id TEXT NOT NULL,
    task_class TEXT NOT NULL,
    arm TEXT NOT NULL CHECK (
        arm IN ('without_skill', 'existing_skill', 'candidate_skill')
    ),
    quality REAL NOT NULL CHECK (quality BETWEEN 0 AND 1),
    tokens INTEGER NOT NULL CHECK (tokens >= 0),
    latency_ms INTEGER NOT NULL CHECK (latency_ms >= 0),
    cost REAL NOT NULL CHECK (cost >= 0),
    failed INTEGER NOT NULL CHECK (failed IN (0, 1)),
    evidence_json TEXT NOT NULL CHECK (json_valid(evidence_json)),
    created_at TEXT NOT NULL,
    UNIQUE(run_id, case_id, arm)
);

CREATE TABLE skill_benchmark_recommendations (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES skill_benchmark_runs(id),
    target_ref TEXT NOT NULL,
    action TEXT NOT NULL CHECK (
        action IN (
            'keep', 'deprecate', 'consider_candidate', 'reject_candidate',
            'insufficient_evidence'
        )
    ),
    reason TEXT NOT NULL,
    evidence_json TEXT NOT NULL CHECK (json_valid(evidence_json)),
    status TEXT NOT NULL CHECK (status IN ('proposed')),
    created_at TEXT NOT NULL
);

CREATE INDEX skill_benchmark_runs_skill
ON skill_benchmark_runs(skill_name, created_at);
CREATE INDEX skill_benchmark_trials_run
ON skill_benchmark_trials(run_id, arm);
"""

MIGRATION_38_SQL = """
CREATE TABLE skill_lab_actions (
    id TEXT PRIMARY KEY,
    operator_id TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    action TEXT NOT NULL CHECK (
        action IN ('activate', 'quarantine', 'retire', 'rollback', 'benchmark')
    ),
    target_ref TEXT NOT NULL,
    request_hash TEXT NOT NULL,
    reason_hash TEXT,
    status TEXT NOT NULL CHECK (status IN ('completed')),
    result_json TEXT NOT NULL CHECK (json_valid(result_json)),
    created_at TEXT NOT NULL,
    UNIQUE(operator_id, idempotency_key)
);

CREATE INDEX skill_lab_actions_target
ON skill_lab_actions(target_ref, created_at);
"""

MIGRATION_39_SQL = """
CREATE TABLE code_repositories (
    id TEXT PRIMARY KEY,
    repository_key TEXT NOT NULL UNIQUE,
    discovery_mode TEXT NOT NULL CHECK (
        discovery_mode IN ('git', 'filesystem')
    ),
    snapshot_hash TEXT NOT NULL,
    parser_version TEXT NOT NULL,
    index_config_json TEXT NOT NULL CHECK (json_valid(index_config_json)),
    generation INTEGER NOT NULL DEFAULT 0 CHECK (generation >= 0),
    current_run_id TEXT,
    indexed_at TEXT NOT NULL
);

CREATE TABLE code_index_runs (
    id TEXT PRIMARY KEY,
    repository_id TEXT NOT NULL REFERENCES code_repositories(id)
        ON DELETE CASCADE,
    generation INTEGER NOT NULL CHECK (generation >= 1),
    discovery_mode TEXT NOT NULL CHECK (
        discovery_mode IN ('git', 'filesystem')
    ),
    snapshot_hash TEXT NOT NULL,
    parser_version TEXT NOT NULL,
    index_config_json TEXT NOT NULL CHECK (json_valid(index_config_json)),
    status TEXT NOT NULL CHECK (status IN ('completed')),
    files_seen INTEGER NOT NULL CHECK (files_seen >= 0),
    files_indexed INTEGER NOT NULL CHECK (files_indexed >= 0),
    files_skipped INTEGER NOT NULL CHECK (files_skipped >= 0),
    symbols_indexed INTEGER NOT NULL CHECK (symbols_indexed >= 0),
    imports_indexed INTEGER NOT NULL CHECK (imports_indexed >= 0),
    references_indexed INTEGER NOT NULL CHECK (references_indexed >= 0),
    dependencies_indexed INTEGER NOT NULL CHECK (dependencies_indexed >= 0),
    bytes_read INTEGER NOT NULL CHECK (bytes_read >= 0),
    skip_counts_json TEXT NOT NULL CHECK (json_valid(skip_counts_json)),
    started_at TEXT NOT NULL,
    completed_at TEXT NOT NULL,
    UNIQUE(repository_id, generation)
);

CREATE TABLE code_files (
    id TEXT PRIMARY KEY,
    repository_id TEXT NOT NULL REFERENCES code_repositories(id)
        ON DELETE CASCADE,
    relative_path TEXT NOT NULL,
    language TEXT NOT NULL,
    file_kind TEXT NOT NULL CHECK (
        file_kind IN ('source', 'test', 'documentation', 'configuration')
    ),
    size_bytes INTEGER NOT NULL CHECK (size_bytes >= 0),
    mtime_ns INTEGER NOT NULL CHECK (mtime_ns >= 0),
    line_count INTEGER NOT NULL CHECK (line_count >= 0),
    content_hash TEXT NOT NULL,
    parse_status TEXT NOT NULL CHECK (
        parse_status IN ('indexed', 'partial', 'unsupported', 'invalid')
    ),
    error_kind TEXT,
    is_test INTEGER NOT NULL CHECK (is_test IN (0, 1)),
    is_documentation INTEGER NOT NULL CHECK (is_documentation IN (0, 1)),
    is_configuration INTEGER NOT NULL CHECK (is_configuration IN (0, 1)),
    UNIQUE(repository_id, relative_path)
);

CREATE TABLE code_symbols (
    id TEXT PRIMARY KEY,
    file_id TEXT NOT NULL REFERENCES code_files(id) ON DELETE CASCADE,
    parent_symbol_id TEXT REFERENCES code_symbols(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    qualified_name TEXT NOT NULL,
    symbol_kind TEXT NOT NULL CHECK (
        symbol_kind IN (
            'class', 'function', 'method', 'interface',
            'type', 'constant', 'documentation_section', 'configuration'
        )
    ),
    interface TEXT NOT NULL,
    start_line INTEGER NOT NULL CHECK (start_line >= 1),
    end_line INTEGER NOT NULL CHECK (end_line >= start_line),
    UNIQUE(file_id, symbol_kind, qualified_name, start_line)
);

CREATE TABLE code_imports (
    id TEXT PRIMARY KEY,
    file_id TEXT NOT NULL REFERENCES code_files(id) ON DELETE CASCADE,
    module TEXT NOT NULL,
    imported_name TEXT,
    alias TEXT,
    import_kind TEXT NOT NULL CHECK (
        import_kind IN ('import', 'from', 'require', 'dynamic')
    ),
    line INTEGER NOT NULL CHECK (line >= 1)
);

CREATE TABLE code_references (
    id TEXT PRIMARY KEY,
    file_id TEXT NOT NULL REFERENCES code_files(id) ON DELETE CASCADE,
    caller_symbol_id TEXT REFERENCES code_symbols(id) ON DELETE CASCADE,
    target_name TEXT NOT NULL,
    reference_kind TEXT NOT NULL CHECK (
        reference_kind IN ('call', 'inheritance', 'type_reference')
    ),
    line INTEGER NOT NULL CHECK (line >= 1)
);

CREATE TABLE code_dependencies (
    id TEXT PRIMARY KEY,
    repository_id TEXT NOT NULL REFERENCES code_repositories(id)
        ON DELETE CASCADE,
    source_file_id TEXT NOT NULL REFERENCES code_files(id) ON DELETE CASCADE,
    ecosystem TEXT NOT NULL,
    dependency_name TEXT NOT NULL,
    dependency_scope TEXT NOT NULL CHECK (
        dependency_scope IN ('runtime', 'development', 'optional', 'build')
    ),
    UNIQUE(
        repository_id, source_file_id, ecosystem,
        dependency_name, dependency_scope
    )
);

CREATE INDEX code_index_runs_repository
ON code_index_runs(repository_id, generation DESC);
CREATE INDEX code_files_repository_kind
ON code_files(repository_id, file_kind, relative_path);
CREATE INDEX code_symbols_name
ON code_symbols(name, symbol_kind);
CREATE INDEX code_symbols_qualified
ON code_symbols(qualified_name);
CREATE INDEX code_imports_module
ON code_imports(module);
CREATE INDEX code_references_target
ON code_references(target_name, reference_kind);
CREATE INDEX code_dependencies_name
ON code_dependencies(dependency_name);
"""

MIGRATION_40_SQL = """
CREATE TABLE document_indexes (
    repository_id TEXT PRIMARY KEY REFERENCES code_repositories(id)
        ON DELETE CASCADE,
    generation INTEGER NOT NULL CHECK (generation >= 1),
    snapshot_hash TEXT NOT NULL,
    parser_version TEXT NOT NULL,
    parser_config_hash TEXT NOT NULL,
    counts_json TEXT NOT NULL CHECK (json_valid(counts_json)),
    indexed_at TEXT NOT NULL
);

CREATE TABLE documents (
    id TEXT PRIMARY KEY,
    repository_id TEXT NOT NULL REFERENCES code_repositories(id)
        ON DELETE CASCADE,
    source_file_id TEXT NOT NULL REFERENCES code_files(id) ON DELETE CASCADE,
    generation INTEGER NOT NULL CHECK (generation >= 1),
    relative_path TEXT NOT NULL,
    source_bytes_hash TEXT NOT NULL,
    title TEXT NOT NULL,
    media_type TEXT NOT NULL CHECK (
        media_type IN ('text/markdown', 'text/plain')
    ),
    encoding TEXT NOT NULL CHECK (encoding IN ('utf-8', 'utf-8-sig')),
    size_bytes INTEGER NOT NULL CHECK (size_bytes >= 0),
    char_count INTEGER NOT NULL CHECK (char_count >= 0),
    line_count INTEGER NOT NULL CHECK (line_count >= 0),
    parser_version TEXT NOT NULL,
    parser_config_hash TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('indexed', 'partial')),
    suspicious_signals_json TEXT NOT NULL CHECK (
        json_valid(suspicious_signals_json)
    ),
    indexed_at TEXT NOT NULL,
    UNIQUE(repository_id, relative_path)
);

CREATE TABLE document_headings (
    id TEXT PRIMARY KEY,
    document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    parent_heading_id TEXT REFERENCES document_headings(id) ON DELETE SET NULL,
    ordinal INTEGER NOT NULL CHECK (ordinal >= 0),
    level INTEGER NOT NULL CHECK (level BETWEEN 1 AND 6),
    heading TEXT NOT NULL,
    qualified_path TEXT NOT NULL,
    anchor TEXT NOT NULL,
    start_char INTEGER NOT NULL CHECK (start_char >= 0),
    end_char INTEGER NOT NULL CHECK (end_char >= start_char),
    start_byte INTEGER NOT NULL CHECK (start_byte >= 0),
    end_byte INTEGER NOT NULL CHECK (end_byte >= start_byte),
    line INTEGER NOT NULL CHECK (line >= 1),
    content_hash TEXT NOT NULL,
    UNIQUE(document_id, ordinal)
);

CREATE TABLE document_sections (
    id TEXT PRIMARY KEY,
    document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    heading_id TEXT REFERENCES document_headings(id) ON DELETE SET NULL,
    parent_section_id TEXT REFERENCES document_sections(id) ON DELETE SET NULL,
    ordinal INTEGER NOT NULL CHECK (ordinal >= 0),
    level INTEGER NOT NULL CHECK (level BETWEEN 0 AND 6),
    start_char INTEGER NOT NULL CHECK (start_char >= 0),
    end_char INTEGER NOT NULL CHECK (end_char >= start_char),
    start_byte INTEGER NOT NULL CHECK (start_byte >= 0),
    end_byte INTEGER NOT NULL CHECK (end_byte >= start_byte),
    start_line INTEGER NOT NULL CHECK (start_line >= 1),
    end_line INTEGER NOT NULL CHECK (end_line >= start_line),
    content_hash TEXT NOT NULL,
    UNIQUE(document_id, ordinal)
);

CREATE TABLE document_chunks (
    id TEXT PRIMARY KEY,
    document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    section_id TEXT NOT NULL REFERENCES document_sections(id)
        ON DELETE CASCADE,
    ordinal INTEGER NOT NULL CHECK (ordinal >= 0),
    chunk_kind TEXT NOT NULL CHECK (
        chunk_kind IN (
            'semantic_section', 'paragraph_group', 'oversize_atomic_block'
        )
    ),
    split_reason TEXT NOT NULL CHECK (
        split_reason IN (
            'section_boundary', 'oversized_section', 'oversized_block'
        )
    ),
    start_char INTEGER NOT NULL CHECK (start_char >= 0),
    end_char INTEGER NOT NULL CHECK (end_char >= start_char),
    start_byte INTEGER NOT NULL CHECK (start_byte >= 0),
    end_byte INTEGER NOT NULL CHECK (end_byte >= start_byte),
    start_line INTEGER NOT NULL CHECK (start_line >= 1),
    end_line INTEGER NOT NULL CHECK (end_line >= start_line),
    content_hash TEXT NOT NULL,
    token_cost INTEGER NOT NULL CHECK (token_cost >= 0),
    exact_preserved INTEGER NOT NULL CHECK (exact_preserved IN (0, 1)),
    UNIQUE(document_id, ordinal)
);

CREATE TABLE document_relationships (
    id TEXT PRIMARY KEY,
    document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    source_section_id TEXT NOT NULL REFERENCES document_sections(id)
        ON DELETE CASCADE,
    target_section_id TEXT REFERENCES document_sections(id)
        ON DELETE CASCADE,
    relationship_kind TEXT NOT NULL CHECK (
        relationship_kind IN ('parent', 'previous', 'next', 'link')
    ),
    target_ref TEXT,
    CHECK (
        (relationship_kind IN ('parent', 'previous', 'next')
         AND target_section_id IS NOT NULL AND target_ref IS NULL)
        OR
        (relationship_kind = 'link' AND target_ref IS NOT NULL)
    )
);

CREATE INDEX documents_title ON documents(title);
CREATE INDEX documents_repository ON documents(repository_id, relative_path);
CREATE INDEX document_headings_document
ON document_headings(document_id, ordinal);
CREATE INDEX document_sections_document
ON document_sections(document_id, ordinal);
CREATE INDEX document_chunks_document
ON document_chunks(document_id, ordinal);
CREATE INDEX document_relationships_source
ON document_relationships(source_section_id, relationship_kind);
CREATE UNIQUE INDEX document_relationships_unique
ON document_relationships(
    document_id, source_section_id, relationship_kind,
    COALESCE(target_section_id, ''), COALESCE(target_ref, '')
);
"""

MIGRATION_41_SQL = """
CREATE TABLE memory_scopes (
    id TEXT PRIMARY KEY CHECK (length(id) BETWEEN 1 AND 255),
    kind TEXT NOT NULL CHECK (
        kind IN (
            'global', 'organization', 'user', 'project',
            'repository', 'task', 'agent'
        )
    ),
    parent_id TEXT REFERENCES memory_scopes(id),
    created_at TEXT NOT NULL,
    CHECK (
        (kind = 'global' AND id = 'global' AND parent_id IS NULL)
        OR
        (kind != 'global' AND id != 'global' AND parent_id IS NOT NULL)
    )
);

CREATE INDEX memory_scopes_parent ON memory_scopes(parent_id, kind);

INSERT INTO memory_scopes(id, kind, parent_id, created_at)
VALUES ('global', 'global', NULL, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));

INSERT OR IGNORE INTO memory_scopes(id, kind, parent_id, created_at)
SELECT DISTINCT scope, 'project', 'global',
       strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
FROM memories
WHERE scope != 'global';
"""

MIGRATION_42_SQL = """
ALTER TABLE model_profiles
ADD COLUMN tier TEXT NOT NULL DEFAULT 'medium' CHECK (
    tier IN ('small', 'medium', 'strong')
);

CREATE TABLE multi_model_workflows (
    id TEXT PRIMARY KEY,
    workflow_class TEXT NOT NULL,
    baseline_model_id TEXT NOT NULL REFERENCES model_profiles(id),
    request_json TEXT NOT NULL CHECK (json_valid(request_json)),
    state TEXT NOT NULL CHECK (
        state IN ('planned', 'unavailable', 'evaluated')
    ),
    reasons_json TEXT NOT NULL CHECK (json_valid(reasons_json)),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE multi_model_stages (
    id TEXT PRIMARY KEY,
    workflow_id TEXT NOT NULL REFERENCES multi_model_workflows(id)
        ON DELETE CASCADE,
    stage_key TEXT NOT NULL,
    sequence INTEGER NOT NULL CHECK (sequence >= 1),
    role TEXT NOT NULL CHECK (
        role IN (
            'classification', 'memory_extraction', 'routing',
            'implementation', 'summarization', 'architecture',
            'complex_debugging', 'critique'
        )
    ),
    required_tier TEXT NOT NULL CHECK (
        required_tier IN ('small', 'medium', 'strong')
    ),
    route_id TEXT NOT NULL UNIQUE REFERENCES model_routes(id),
    selected_model_id TEXT REFERENCES model_profiles(id),
    dependencies_json TEXT NOT NULL CHECK (json_valid(dependencies_json)),
    UNIQUE(workflow_id, sequence),
    UNIQUE(workflow_id, stage_key)
);

CREATE TABLE multi_model_outcomes (
    id TEXT PRIMARY KEY,
    workflow_id TEXT NOT NULL UNIQUE REFERENCES multi_model_workflows(id),
    workflow_class TEXT NOT NULL,
    specialized_json TEXT NOT NULL CHECK (json_valid(specialized_json)),
    baseline_json TEXT NOT NULL CHECK (json_valid(baseline_json)),
    quality_delta REAL NOT NULL,
    success_delta INTEGER NOT NULL CHECK (success_delta BETWEEN -1 AND 1),
    latency_saved_ms INTEGER NOT NULL,
    tokens_saved INTEGER NOT NULL,
    cost_saved REAL NOT NULL,
    evidence_json TEXT NOT NULL CHECK (
        json_valid(evidence_json) AND json_array_length(evidence_json) > 0
    ),
    created_at TEXT NOT NULL
);

CREATE INDEX multi_model_workflows_class
ON multi_model_workflows(workflow_class, created_at);
CREATE INDEX multi_model_stages_workflow
ON multi_model_stages(workflow_id, sequence);
CREATE INDEX multi_model_outcomes_class
ON multi_model_outcomes(workflow_class, created_at);
"""

MIGRATION_43_SQL = """
CREATE TABLE confidence_predictions (
    id TEXT PRIMARY KEY,
    domain TEXT NOT NULL CHECK (
        domain IN ('memory', 'routing', 'evaluation')
    ),
    source_id TEXT NOT NULL,
    group_key TEXT NOT NULL DEFAULT 'all',
    predicted_confidence REAL NOT NULL CHECK (
        predicted_confidence BETWEEN 0 AND 1
    ),
    actual_outcome INTEGER CHECK (actual_outcome IN (0, 1)),
    evidence_json TEXT NOT NULL DEFAULT '[]' CHECK (
        json_valid(evidence_json)
    ),
    outcome_evidence_json TEXT NOT NULL DEFAULT '[]' CHECK (
        json_valid(outcome_evidence_json)
    ),
    created_at TEXT NOT NULL,
    resolved_at TEXT,
    CHECK (
        (
            actual_outcome IS NULL AND resolved_at IS NULL
            AND json_array_length(outcome_evidence_json) = 0
        )
        OR
        (
            actual_outcome IS NOT NULL AND resolved_at IS NOT NULL
            AND json_array_length(outcome_evidence_json) > 0
        )
    ),
    UNIQUE(domain, source_id)
);

CREATE INDEX confidence_predictions_curve
ON confidence_predictions(domain, group_key, predicted_confidence, created_at);

CREATE TRIGGER confidence_predictions_resolve_once
BEFORE UPDATE ON confidence_predictions
WHEN
    NEW.domain != OLD.domain
    OR NEW.source_id != OLD.source_id
    OR NEW.group_key != OLD.group_key
    OR NEW.predicted_confidence != OLD.predicted_confidence
    OR NEW.evidence_json != OLD.evidence_json
    OR NEW.created_at != OLD.created_at
    OR OLD.actual_outcome IS NOT NULL
    OR NEW.actual_outcome IS NULL
BEGIN
    SELECT RAISE(ABORT, 'confidence prediction is immutable or resolved');
END;
"""

MIGRATION_44_SQL = """
CREATE TABLE task_resource_budgets (
    task_id TEXT PRIMARY KEY,
    soft_input_tokens INTEGER NOT NULL CHECK (soft_input_tokens >= 0),
    soft_output_tokens INTEGER NOT NULL CHECK (soft_output_tokens >= 0),
    soft_model_calls INTEGER NOT NULL CHECK (soft_model_calls >= 0),
    soft_tool_calls INTEGER NOT NULL CHECK (soft_tool_calls >= 0),
    soft_agents INTEGER NOT NULL CHECK (soft_agents >= 0),
    soft_cost INTEGER NOT NULL CHECK (soft_cost >= 0),
    soft_duration INTEGER NOT NULL CHECK (soft_duration >= 0),
    max_input_tokens INTEGER NOT NULL CHECK (max_input_tokens >= 0),
    max_output_tokens INTEGER NOT NULL CHECK (max_output_tokens >= 0),
    max_model_calls INTEGER NOT NULL CHECK (max_model_calls >= 0),
    max_tool_calls INTEGER NOT NULL CHECK (max_tool_calls >= 0),
    max_agents INTEGER NOT NULL CHECK (max_agents >= 0),
    max_cost INTEGER NOT NULL CHECK (max_cost >= 0),
    max_duration INTEGER NOT NULL CHECK (max_duration >= 0),
    escalation_mode TEXT NOT NULL CHECK (
        escalation_mode IN ('none', 'manual_exact')
    ),
    evidence_json TEXT NOT NULL CHECK (
        json_valid(evidence_json) AND json_array_length(evidence_json) > 0
    ),
    created_at TEXT NOT NULL,
    CHECK (soft_input_tokens <= max_input_tokens),
    CHECK (soft_output_tokens <= max_output_tokens),
    CHECK (soft_model_calls <= max_model_calls),
    CHECK (soft_tool_calls <= max_tool_calls),
    CHECK (soft_agents <= max_agents),
    CHECK (soft_cost <= max_cost),
    CHECK (soft_duration <= max_duration)
);

CREATE TABLE task_resource_usage (
    task_id TEXT PRIMARY KEY REFERENCES task_resource_budgets(task_id),
    held_input_tokens INTEGER NOT NULL DEFAULT 0 CHECK (held_input_tokens >= 0),
    held_output_tokens INTEGER NOT NULL DEFAULT 0 CHECK (held_output_tokens >= 0),
    held_model_calls INTEGER NOT NULL DEFAULT 0 CHECK (held_model_calls >= 0),
    held_tool_calls INTEGER NOT NULL DEFAULT 0 CHECK (held_tool_calls >= 0),
    held_agents INTEGER NOT NULL DEFAULT 0 CHECK (held_agents >= 0),
    held_cost INTEGER NOT NULL DEFAULT 0 CHECK (held_cost >= 0),
    held_duration INTEGER NOT NULL DEFAULT 0 CHECK (held_duration >= 0),
    used_input_tokens INTEGER NOT NULL DEFAULT 0 CHECK (used_input_tokens >= 0),
    used_output_tokens INTEGER NOT NULL DEFAULT 0 CHECK (used_output_tokens >= 0),
    used_model_calls INTEGER NOT NULL DEFAULT 0 CHECK (used_model_calls >= 0),
    used_tool_calls INTEGER NOT NULL DEFAULT 0 CHECK (used_tool_calls >= 0),
    used_agents INTEGER NOT NULL DEFAULT 0 CHECK (used_agents >= 0),
    used_cost INTEGER NOT NULL DEFAULT 0 CHECK (used_cost >= 0),
    used_duration INTEGER NOT NULL DEFAULT 0 CHECK (used_duration >= 0),
    updated_at TEXT NOT NULL
);

CREATE TABLE task_resource_escalations (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES task_resource_budgets(task_id),
    input_tokens INTEGER NOT NULL CHECK (input_tokens >= 0),
    output_tokens INTEGER NOT NULL CHECK (output_tokens >= 0),
    model_calls INTEGER NOT NULL CHECK (model_calls >= 0),
    tool_calls INTEGER NOT NULL CHECK (tool_calls >= 0),
    agents INTEGER NOT NULL CHECK (agents >= 0),
    cost INTEGER NOT NULL CHECK (cost >= 0),
    duration INTEGER NOT NULL CHECK (duration >= 0),
    approval_reference TEXT NOT NULL,
    reason TEXT NOT NULL,
    evidence_json TEXT NOT NULL CHECK (
        json_valid(evidence_json) AND json_array_length(evidence_json) > 0
    ),
    expires_at TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE task_resource_reservations (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES task_resource_budgets(task_id),
    idempotency_key TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (
        kind IN ('context', 'model', 'tool', 'agent', 'task', 'other')
    ),
    state TEXT NOT NULL CHECK (
        state IN ('reserved', 'committed', 'released')
    ),
    reserved_input_tokens INTEGER NOT NULL CHECK (reserved_input_tokens >= 0),
    reserved_output_tokens INTEGER NOT NULL CHECK (reserved_output_tokens >= 0),
    reserved_model_calls INTEGER NOT NULL CHECK (reserved_model_calls >= 0),
    reserved_tool_calls INTEGER NOT NULL CHECK (reserved_tool_calls >= 0),
    reserved_agents INTEGER NOT NULL CHECK (reserved_agents >= 0),
    reserved_cost INTEGER NOT NULL CHECK (reserved_cost >= 0),
    reserved_duration INTEGER NOT NULL CHECK (reserved_duration >= 0),
    actual_input_tokens INTEGER CHECK (actual_input_tokens >= 0),
    actual_output_tokens INTEGER CHECK (actual_output_tokens >= 0),
    actual_model_calls INTEGER CHECK (actual_model_calls >= 0),
    actual_tool_calls INTEGER CHECK (actual_tool_calls >= 0),
    actual_agents INTEGER CHECK (actual_agents >= 0),
    actual_cost INTEGER CHECK (actual_cost >= 0),
    actual_duration INTEGER CHECK (actual_duration >= 0),
    escalation_id TEXT UNIQUE REFERENCES task_resource_escalations(id),
    evidence_json TEXT NOT NULL CHECK (
        json_valid(evidence_json) AND json_array_length(evidence_json) > 0
    ),
    completion_evidence_json TEXT NOT NULL DEFAULT '[]' CHECK (
        json_valid(completion_evidence_json)
    ),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(task_id, idempotency_key),
    CHECK (
        (state = 'reserved' AND actual_input_tokens IS NULL
         AND actual_output_tokens IS NULL AND actual_model_calls IS NULL
         AND actual_tool_calls IS NULL AND actual_agents IS NULL
         AND actual_cost IS NULL AND actual_duration IS NULL
         AND json_array_length(completion_evidence_json) = 0)
        OR
        (state = 'committed' AND actual_input_tokens IS NOT NULL
         AND actual_output_tokens IS NOT NULL AND actual_model_calls IS NOT NULL
         AND actual_tool_calls IS NOT NULL AND actual_agents IS NOT NULL
         AND actual_cost IS NOT NULL AND actual_duration IS NOT NULL
         AND actual_input_tokens <= reserved_input_tokens
         AND actual_output_tokens <= reserved_output_tokens
         AND actual_model_calls <= reserved_model_calls
         AND actual_tool_calls <= reserved_tool_calls
         AND actual_agents <= reserved_agents
         AND actual_cost <= reserved_cost
         AND actual_duration <= reserved_duration
         AND json_array_length(completion_evidence_json) > 0)
        OR
        (state = 'released' AND actual_input_tokens IS NULL
         AND actual_output_tokens IS NULL AND actual_model_calls IS NULL
         AND actual_tool_calls IS NULL AND actual_agents IS NULL
         AND actual_cost IS NULL AND actual_duration IS NULL
         AND json_array_length(completion_evidence_json) > 0)
    )
);

CREATE INDEX task_resource_reservations_task
ON task_resource_reservations(task_id, state, created_at);
CREATE INDEX task_resource_escalations_task
ON task_resource_escalations(task_id, expires_at);

CREATE TRIGGER task_resource_budgets_immutable
BEFORE UPDATE ON task_resource_budgets
BEGIN
    SELECT RAISE(ABORT, 'task resource budget is immutable');
END;

CREATE TRIGGER task_resource_usage_hard_limits
BEFORE UPDATE ON task_resource_usage
BEGIN
    SELECT CASE WHEN
        NEW.held_input_tokens + NEW.used_input_tokens > (
            SELECT max_input_tokens FROM task_resource_budgets
            WHERE task_id = NEW.task_id
        )
        OR NEW.held_output_tokens + NEW.used_output_tokens > (
            SELECT max_output_tokens FROM task_resource_budgets
            WHERE task_id = NEW.task_id
        )
        OR NEW.held_model_calls + NEW.used_model_calls > (
            SELECT max_model_calls FROM task_resource_budgets
            WHERE task_id = NEW.task_id
        )
        OR NEW.held_tool_calls + NEW.used_tool_calls > (
            SELECT max_tool_calls FROM task_resource_budgets
            WHERE task_id = NEW.task_id
        )
        OR NEW.held_agents + NEW.used_agents > (
            SELECT max_agents FROM task_resource_budgets
            WHERE task_id = NEW.task_id
        )
        OR NEW.held_cost + NEW.used_cost > (
            SELECT max_cost FROM task_resource_budgets
            WHERE task_id = NEW.task_id
        )
        OR NEW.held_duration + NEW.used_duration > (
            SELECT max_duration FROM task_resource_budgets
            WHERE task_id = NEW.task_id
        )
    THEN RAISE(ABORT, 'hard resource limit exceeded') END;
END;
"""

MIGRATION_45_SQL = """
CREATE TABLE cache_generations (
    namespace TEXT PRIMARY KEY,
    generation INTEGER NOT NULL DEFAULT 0 CHECK (generation >= 0),
    updated_at TEXT NOT NULL
);

INSERT INTO cache_generations(namespace, generation, updated_at)
VALUES ('memory_retrieval', 0, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));

CREATE TABLE cache_entries (
    id TEXT PRIMARY KEY,
    cache_type TEXT NOT NULL CHECK (cache_type = 'retrieval'),
    key_hash TEXT NOT NULL CHECK (
        length(key_hash) = 64 AND key_hash NOT GLOB '*[^0-9a-f]*'
    ),
    scope TEXT NOT NULL,
    algorithm_version TEXT NOT NULL,
    source_generation INTEGER NOT NULL CHECK (source_generation >= 0),
    payload_json TEXT NOT NULL CHECK (json_valid(payload_json)),
    payload_bytes INTEGER NOT NULL CHECK (
        payload_bytes >= 2 AND payload_bytes <= 262144
    ),
    compute_duration_ms INTEGER NOT NULL CHECK (compute_duration_ms >= 0),
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    last_hit_at TEXT,
    hit_count INTEGER NOT NULL DEFAULT 0 CHECK (hit_count >= 0),
    CHECK (length(CAST(payload_json AS BLOB)) = payload_bytes),
    UNIQUE(cache_type, key_hash)
);

CREATE INDEX cache_entries_expiry
ON cache_entries(cache_type, expires_at);

CREATE TABLE cache_events (
    id TEXT PRIMARY KEY,
    cache_type TEXT NOT NULL CHECK (cache_type = 'retrieval'),
    entry_id TEXT,
    outcome TEXT NOT NULL CHECK (
        outcome IN (
            'hit', 'miss', 'bypass', 'fill', 'expired',
            'invalidated', 'error', 'pruned'
        )
    ),
    reason TEXT NOT NULL,
    saved_input_tokens INTEGER NOT NULL DEFAULT 0
        CHECK (saved_input_tokens >= 0),
    saved_output_tokens INTEGER NOT NULL DEFAULT 0
        CHECK (saved_output_tokens >= 0),
    saved_model_calls INTEGER NOT NULL DEFAULT 0
        CHECK (saved_model_calls >= 0),
    saved_tool_calls INTEGER NOT NULL DEFAULT 0
        CHECK (saved_tool_calls >= 0),
    saved_cost INTEGER NOT NULL DEFAULT 0 CHECK (saved_cost >= 0),
    saved_duration_ms INTEGER NOT NULL DEFAULT 0
        CHECK (saved_duration_ms >= 0),
    created_at TEXT NOT NULL
);

CREATE INDEX cache_events_type_time
ON cache_events(cache_type, created_at);

CREATE TRIGGER cache_invalidate_memories_insert
AFTER INSERT ON memories
BEGIN
    UPDATE cache_generations
    SET generation = generation + 1,
        updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
    WHERE namespace = 'memory_retrieval';
    DELETE FROM cache_entries WHERE cache_type = 'retrieval';
END;

CREATE TRIGGER cache_invalidate_memories_update
AFTER UPDATE ON memories
BEGIN
    UPDATE cache_generations
    SET generation = generation + 1,
        updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
    WHERE namespace = 'memory_retrieval';
    DELETE FROM cache_entries WHERE cache_type = 'retrieval';
END;

CREATE TRIGGER cache_invalidate_memories_delete
AFTER DELETE ON memories
BEGIN
    UPDATE cache_generations
    SET generation = generation + 1,
        updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
    WHERE namespace = 'memory_retrieval';
    DELETE FROM cache_entries WHERE cache_type = 'retrieval';
END;

CREATE TRIGGER cache_invalidate_scopes_insert
AFTER INSERT ON memory_scopes
BEGIN
    UPDATE cache_generations
    SET generation = generation + 1,
        updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
    WHERE namespace = 'memory_retrieval';
    DELETE FROM cache_entries WHERE cache_type = 'retrieval';
END;

CREATE TRIGGER cache_invalidate_scopes_update
AFTER UPDATE ON memory_scopes
BEGIN
    UPDATE cache_generations
    SET generation = generation + 1,
        updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
    WHERE namespace = 'memory_retrieval';
    DELETE FROM cache_entries WHERE cache_type = 'retrieval';
END;

CREATE TRIGGER cache_invalidate_scopes_delete
AFTER DELETE ON memory_scopes
BEGIN
    UPDATE cache_generations
    SET generation = generation + 1,
        updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
    WHERE namespace = 'memory_retrieval';
    DELETE FROM cache_entries WHERE cache_type = 'retrieval';
END;

CREATE TRIGGER cache_invalidate_privacy_update
AFTER UPDATE ON privacy_policies
BEGIN
    UPDATE cache_generations
    SET generation = generation + 1,
        updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
    WHERE namespace = 'memory_retrieval';
    DELETE FROM cache_entries WHERE cache_type = 'retrieval';
END;

CREATE TRIGGER cache_invalidate_privacy_insert
AFTER INSERT ON privacy_policies
BEGIN
    UPDATE cache_generations
    SET generation = generation + 1,
        updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
    WHERE namespace = 'memory_retrieval';
    DELETE FROM cache_entries WHERE cache_type = 'retrieval';
END;

CREATE TRIGGER cache_invalidate_privacy_delete
AFTER DELETE ON privacy_policies
BEGIN
    UPDATE cache_generations
    SET generation = generation + 1,
        updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
    WHERE namespace = 'memory_retrieval';
    DELETE FROM cache_entries WHERE cache_type = 'retrieval';
END;
"""

MIGRATION_46_SQL = """
CREATE TABLE deduplication_runs (
    id TEXT PRIMARY KEY,
    algorithm_version TEXT NOT NULL,
    scope_hash TEXT CHECK (
        scope_hash IS NULL OR (
            length(scope_hash) = 64
            AND scope_hash NOT GLOB '*[^0-9a-f]*'
        )
    ),
    kinds_json TEXT NOT NULL CHECK (json_valid(kinds_json)),
    policy_json TEXT NOT NULL CHECK (json_valid(policy_json)),
    item_count INTEGER NOT NULL CHECK (item_count >= 0),
    match_count INTEGER NOT NULL CHECK (match_count >= 0),
    sealed INTEGER NOT NULL DEFAULT 0 CHECK (sealed IN (0, 1)),
    created_at TEXT NOT NULL
);

CREATE TABLE deduplication_items (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES deduplication_runs(id),
    kind TEXT NOT NULL CHECK (
        kind IN (
            'memory', 'context', 'skill', 'tool_output', 'model_request'
        )
    ),
    source_id TEXT NOT NULL,
    source_version TEXT NOT NULL DEFAULT '',
    content_hash TEXT NOT NULL CHECK (
        length(content_hash) = 64
        AND content_hash NOT GLOB '*[^0-9a-f]*'
    ),
    evidence_json TEXT NOT NULL CHECK (json_valid(evidence_json)),
    provenance_json TEXT NOT NULL CHECK (json_valid(provenance_json)),
    created_at TEXT NOT NULL,
    UNIQUE(run_id, id),
    UNIQUE(run_id, kind, source_id, source_version)
);

CREATE TABLE deduplication_matches (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES deduplication_runs(id),
    left_item_id TEXT NOT NULL,
    right_item_id TEXT NOT NULL,
    relation TEXT NOT NULL CHECK (
        relation IN (
            'exact_duplicate', 'semantic_duplicate', 'near_duplicate',
            'version_successor', 'overlapping_capability'
        )
    ),
    recommendation TEXT NOT NULL CHECK (
        recommendation IN (
            'MERGE', 'REFERENCE', 'SUPERSEDE', 'COMPOSE', 'KEEP_SEPARATE'
        )
    ),
    score REAL NOT NULL CHECK (score BETWEEN 0 AND 1),
    method_id TEXT NOT NULL,
    method_version TEXT NOT NULL,
    evidence_json TEXT NOT NULL CHECK (json_valid(evidence_json)),
    provenance_json TEXT NOT NULL CHECK (json_valid(provenance_json)),
    automatic_action_allowed INTEGER NOT NULL DEFAULT 0 CHECK (
        automatic_action_allowed = 0
    ),
    review_required INTEGER NOT NULL DEFAULT 1 CHECK (
        review_required = 1
    ),
    created_at TEXT NOT NULL,
    CHECK (left_item_id <> right_item_id),
    CHECK (left_item_id < right_item_id),
    FOREIGN KEY(run_id, left_item_id)
        REFERENCES deduplication_items(run_id, id),
    FOREIGN KEY(run_id, right_item_id)
        REFERENCES deduplication_items(run_id, id),
    UNIQUE(run_id, left_item_id, right_item_id)
);

CREATE INDEX deduplication_runs_created
ON deduplication_runs(created_at);
CREATE INDEX deduplication_items_source
ON deduplication_items(run_id, kind, source_id);
CREATE INDEX deduplication_matches_run
ON deduplication_matches(run_id, relation, recommendation);
CREATE INDEX deduplication_matches_left
ON deduplication_matches(left_item_id);
CREATE INDEX deduplication_matches_right
ON deduplication_matches(right_item_id);

CREATE TRIGGER deduplication_runs_seal_only
BEFORE UPDATE ON deduplication_runs
WHEN NOT (
    OLD.sealed = 0
    AND NEW.sealed = 1
    AND NEW.id = OLD.id
    AND NEW.algorithm_version = OLD.algorithm_version
    AND NEW.scope_hash IS OLD.scope_hash
    AND NEW.kinds_json = OLD.kinds_json
    AND NEW.policy_json = OLD.policy_json
    AND NEW.item_count = OLD.item_count
    AND NEW.match_count = OLD.match_count
    AND NEW.created_at = OLD.created_at
    AND (
        SELECT COUNT(*) FROM deduplication_items
        WHERE run_id = OLD.id
    ) = OLD.item_count
    AND (
        SELECT COUNT(*) FROM deduplication_matches
        WHERE run_id = OLD.id
    ) = OLD.match_count
)
BEGIN
    SELECT RAISE(ABORT, 'deduplication run can only be sealed once');
END;
CREATE TRIGGER deduplication_runs_no_delete
BEFORE DELETE ON deduplication_runs
BEGIN
    SELECT RAISE(ABORT, 'deduplication audit is append-only');
END;
CREATE TRIGGER deduplication_items_unsealed_insert
BEFORE INSERT ON deduplication_items
WHEN EXISTS (
    SELECT 1 FROM deduplication_runs
    WHERE id = NEW.run_id AND sealed = 1
)
BEGIN
    SELECT RAISE(ABORT, 'sealed deduplication run is immutable');
END;
CREATE TRIGGER deduplication_items_no_update
BEFORE UPDATE ON deduplication_items
BEGIN
    SELECT RAISE(ABORT, 'deduplication audit is append-only');
END;
CREATE TRIGGER deduplication_items_no_delete
BEFORE DELETE ON deduplication_items
BEGIN
    SELECT RAISE(ABORT, 'deduplication audit is append-only');
END;
CREATE TRIGGER deduplication_matches_unsealed_insert
BEFORE INSERT ON deduplication_matches
WHEN EXISTS (
    SELECT 1 FROM deduplication_runs
    WHERE id = NEW.run_id AND sealed = 1
)
BEGIN
    SELECT RAISE(ABORT, 'sealed deduplication run is immutable');
END;
CREATE TRIGGER deduplication_matches_no_update
BEFORE UPDATE ON deduplication_matches
BEGIN
    SELECT RAISE(ABORT, 'deduplication audit is append-only');
END;
CREATE TRIGGER deduplication_matches_no_delete
BEFORE DELETE ON deduplication_matches
BEGIN
    SELECT RAISE(ABORT, 'deduplication audit is append-only');
END;
"""

MIGRATION_47_SQL = """
CREATE TABLE improvement_policy_versions (
    id TEXT PRIMARY KEY,
    target TEXT NOT NULL CHECK (
        target IN (
            'retrieval_weights',
            'context_thresholds',
            'skill_routing_thresholds'
        )
    ),
    version INTEGER NOT NULL CHECK (version >= 1),
    parent_id TEXT REFERENCES improvement_policy_versions(id),
    config_json TEXT NOT NULL CHECK (
        json_valid(config_json) AND json_type(config_json) = 'object'
        AND (
            (
                target = 'retrieval_weights'
                AND json_type(config_json, '$.keyword_bps') = 'integer'
                AND json_type(config_json, '$.semantic_bps') = 'integer'
                AND json_type(config_json, '$.scope_bps') = 'integer'
                AND json_type(config_json, '$.recency_bps') = 'integer'
                AND json_type(config_json, '$.temporal_bps') = 'integer'
                AND json_type(config_json, '$.confidence_bps') = 'integer'
                AND json_type(config_json, '$.historical_utility_bps') = 'integer'
                AND json_type(config_json, '$.importance_bps') = 'integer'
                AND json_type(config_json, '$.task_similarity_bps') = 'integer'
                AND json_type(config_json, '$.source_reliability_bps') = 'integer'
                AND json_remove(
                    config_json, '$.keyword_bps', '$.semantic_bps',
                    '$.scope_bps', '$.recency_bps', '$.temporal_bps',
                    '$.confidence_bps', '$.historical_utility_bps',
                    '$.importance_bps', '$.task_similarity_bps',
                    '$.source_reliability_bps'
                ) = '{}'
                AND (
                    json_extract(config_json, '$.keyword_bps')
                    + json_extract(config_json, '$.semantic_bps')
                    + json_extract(config_json, '$.scope_bps')
                    + json_extract(config_json, '$.recency_bps')
                    + json_extract(config_json, '$.temporal_bps')
                    + json_extract(config_json, '$.confidence_bps')
                    + json_extract(config_json, '$.historical_utility_bps')
                    + json_extract(config_json, '$.importance_bps')
                    + json_extract(config_json, '$.task_similarity_bps')
                    + json_extract(config_json, '$.source_reliability_bps')
                ) = 10000
                AND MIN(
                    json_extract(config_json, '$.keyword_bps'),
                    json_extract(config_json, '$.semantic_bps'),
                    json_extract(config_json, '$.scope_bps'),
                    json_extract(config_json, '$.recency_bps'),
                    json_extract(config_json, '$.temporal_bps'),
                    json_extract(config_json, '$.confidence_bps'),
                    json_extract(config_json, '$.historical_utility_bps'),
                    json_extract(config_json, '$.importance_bps'),
                    json_extract(config_json, '$.task_similarity_bps'),
                    json_extract(config_json, '$.source_reliability_bps')
                ) >= 0
                AND MAX(
                    json_extract(config_json, '$.keyword_bps'),
                    json_extract(config_json, '$.semantic_bps'),
                    json_extract(config_json, '$.scope_bps'),
                    json_extract(config_json, '$.recency_bps'),
                    json_extract(config_json, '$.temporal_bps'),
                    json_extract(config_json, '$.confidence_bps'),
                    json_extract(config_json, '$.historical_utility_bps'),
                    json_extract(config_json, '$.importance_bps'),
                    json_extract(config_json, '$.task_similarity_bps'),
                    json_extract(config_json, '$.source_reliability_bps')
                ) <= 10000
            )
            OR (
                target = 'context_thresholds'
                AND json_type(
                    config_json, '$.minimum_optional_utility_bps'
                ) = 'integer'
                AND json_remove(
                    config_json, '$.minimum_optional_utility_bps'
                ) = '{}'
                AND json_extract(
                    config_json, '$.minimum_optional_utility_bps'
                ) BETWEEN 0 AND 10000
            )
            OR (
                target = 'skill_routing_thresholds'
                AND json_type(
                    config_json, '$.minimum_benefit_bps'
                ) = 'integer'
                AND json_type(
                    config_json, '$.overlap_threshold_bps'
                ) = 'integer'
                AND json_remove(
                    config_json, '$.minimum_benefit_bps',
                    '$.overlap_threshold_bps'
                ) = '{}'
                AND json_extract(
                    config_json, '$.minimum_benefit_bps'
                ) BETWEEN 0 AND 10000
                AND json_extract(
                    config_json, '$.overlap_threshold_bps'
                ) BETWEEN 0 AND 10000
            )
        )
    ),
    config_hash TEXT NOT NULL CHECK (
        length(config_hash) = 64
        AND config_hash NOT GLOB '*[^0-9a-f]*'
    ),
    provenance_json TEXT NOT NULL CHECK (
        json_valid(provenance_json) AND json_type(provenance_json) = 'object'
    ),
    created_at TEXT NOT NULL,
    UNIQUE(target, version),
    UNIQUE(target, config_hash)
);

CREATE TABLE improvement_policy_heads (
    target TEXT PRIMARY KEY CHECK (
        target IN (
            'retrieval_weights',
            'context_thresholds',
            'skill_routing_thresholds'
        )
    ),
    version_id TEXT NOT NULL REFERENCES improvement_policy_versions(id),
    revision INTEGER NOT NULL CHECK (revision >= 1),
    updated_at TEXT NOT NULL
);

CREATE TABLE improvement_authorizations (
    id TEXT PRIMARY KEY,
    target TEXT NOT NULL CHECK (
        target IN (
            'retrieval_weights',
            'context_thresholds',
            'skill_instructions',
            'skill_routing_thresholds'
        )
    ),
    scope_hash TEXT NOT NULL CHECK (
        length(scope_hash) = 64 AND scope_hash NOT GLOB '*[^0-9a-f]*'
    ),
    incumbent_hash TEXT NOT NULL CHECK (
        length(incumbent_hash) = 64
        AND incumbent_hash NOT GLOB '*[^0-9a-f]*'
    ),
    candidate_hash TEXT NOT NULL CHECK (
        length(candidate_hash) = 64
        AND candidate_hash NOT GLOB '*[^0-9a-f]*'
    ),
    benchmark_hash TEXT NOT NULL CHECK (
        length(benchmark_hash) = 64
        AND benchmark_hash NOT GLOB '*[^0-9a-f]*'
    ),
    max_cases INTEGER NOT NULL CHECK (max_cases BETWEEN 1 AND 10000),
    expires_at TEXT NOT NULL,
    consumed_at TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE improvement_runs (
    id TEXT PRIMARY KEY,
    target TEXT NOT NULL CHECK (
        target IN (
            'retrieval_weights',
            'context_thresholds',
            'skill_instructions',
            'skill_routing_thresholds'
        )
    ),
    scope_hash TEXT NOT NULL CHECK (length(scope_hash) = 64),
    incumbent_version_id TEXT,
    candidate_version_id TEXT,
    authorization_id TEXT REFERENCES improvement_authorizations(id),
    hypothesis_hash TEXT NOT NULL CHECK (length(hypothesis_hash) = 64),
    benchmark_hash TEXT NOT NULL CHECK (length(benchmark_hash) = 64),
    seed INTEGER NOT NULL,
    status TEXT NOT NULL CHECK (
        status IN ('observed', 'benchmarked', 'promoted', 'rejected', 'blocked')
    ),
    decision_reason TEXT,
    created_at TEXT NOT NULL,
    completed_at TEXT
);

CREATE TABLE improvement_benchmark_results (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL UNIQUE REFERENCES improvement_runs(id),
    case_count INTEGER NOT NULL CHECK (case_count > 0),
    complete INTEGER NOT NULL CHECK (complete IN (0, 1)),
    hard_violations INTEGER NOT NULL CHECK (hard_violations >= 0),
    incumbent_utility_micros INTEGER NOT NULL,
    candidate_utility_micros INTEGER NOT NULL,
    protected_regressions INTEGER NOT NULL CHECK (protected_regressions >= 0),
    result_hash TEXT NOT NULL CHECK (length(result_hash) = 64),
    summary_json TEXT NOT NULL CHECK (
        json_valid(summary_json) AND json_type(summary_json) = 'object'
    ),
    created_at TEXT NOT NULL
);

CREATE TABLE improvement_policy_events (
    id TEXT PRIMARY KEY,
    target TEXT NOT NULL CHECK (
        target IN (
            'retrieval_weights',
            'context_thresholds',
            'skill_routing_thresholds'
        )
    ),
    event_type TEXT NOT NULL CHECK (
        event_type IN (
            'bootstrap', 'candidate', 'benchmark', 'promote',
            'reject', 'rollback', 'blocked'
        )
    ),
    run_id TEXT,
    from_version_id TEXT,
    to_version_id TEXT,
    evidence_hash TEXT NOT NULL CHECK (length(evidence_hash) = 64),
    created_at TEXT NOT NULL
);

CREATE TABLE task_policy_attributions (
    task_id TEXT NOT NULL REFERENCES tasks(id),
    target TEXT NOT NULL CHECK (
        target IN (
            'retrieval_weights',
            'context_thresholds',
            'skill_routing_thresholds'
        )
    ),
    version_id TEXT NOT NULL REFERENCES improvement_policy_versions(id),
    config_hash TEXT NOT NULL CHECK (length(config_hash) = 64),
    PRIMARY KEY(task_id, target)
);

CREATE INDEX improvement_versions_target
ON improvement_policy_versions(target, version);
CREATE INDEX improvement_runs_target
ON improvement_runs(target, created_at);
CREATE INDEX improvement_events_target
ON improvement_policy_events(target, created_at);

CREATE TRIGGER improvement_policy_versions_no_update
BEFORE UPDATE ON improvement_policy_versions
BEGIN
    SELECT RAISE(ABORT, 'improvement policy versions are immutable');
END;
CREATE TRIGGER improvement_policy_versions_no_delete
BEFORE DELETE ON improvement_policy_versions
BEGIN
    SELECT RAISE(ABORT, 'improvement policy versions are immutable');
END;
CREATE TRIGGER improvement_policy_events_no_update
BEFORE UPDATE ON improvement_policy_events
BEGIN
    SELECT RAISE(ABORT, 'improvement policy events are append-only');
END;
CREATE TRIGGER improvement_policy_events_no_delete
BEFORE DELETE ON improvement_policy_events
BEGIN
    SELECT RAISE(ABORT, 'improvement policy events are append-only');
END;
"""

MIGRATION_48_SQL = """
CREATE TABLE meta_context_strategies (
    id TEXT PRIMARY KEY,
    version INTEGER NOT NULL UNIQUE CHECK (version >= 1),
    parent_hash TEXT NOT NULL CHECK (
        length(parent_hash) = 64
        AND parent_hash NOT GLOB '*[^0-9a-f]*'
    ),
    config_json TEXT NOT NULL CHECK (
        json_valid(config_json)
        AND json_type(config_json) = 'object'
        AND json_type(config_json, '$.ordering_profile') = 'text'
        AND json_extract(config_json, '$.ordering_profile')
            IN ('production', 'utility_desc', 'roi_desc')
        AND json_type(
            config_json, '$.compression_minimum_tokens'
        ) = 'integer'
        AND json_extract(
            config_json, '$.compression_minimum_tokens'
        ) BETWEEN 40 AND 200
        AND json_type(config_json, '$.max_memories') = 'integer'
        AND json_extract(config_json, '$.max_memories') BETWEEN 4 AND 32
        AND json_type(config_json, '$.max_skills') = 'integer'
        AND json_extract(config_json, '$.max_skills') BETWEEN 1 AND 4
        AND json_remove(
            config_json, '$.ordering_profile',
            '$.compression_minimum_tokens', '$.max_memories', '$.max_skills'
        ) = '{}'
    ),
    config_hash TEXT NOT NULL UNIQUE CHECK (
        length(config_hash) = 64
        AND config_hash NOT GLOB '*[^0-9a-f]*'
    ),
    hypothesis_hash TEXT NOT NULL CHECK (
        length(hypothesis_hash) = 64
        AND hypothesis_hash NOT GLOB '*[^0-9a-f]*'
    ),
    status TEXT NOT NULL DEFAULT 'candidate' CHECK (
        status IN ('candidate', 'evaluated', 'rejected', 'promotion_eligible')
    ),
    created_at TEXT NOT NULL
);

CREATE TABLE meta_context_runs (
    id TEXT PRIMARY KEY,
    strategy_id TEXT NOT NULL UNIQUE REFERENCES meta_context_strategies(id),
    production_hash TEXT NOT NULL CHECK (length(production_hash) = 64),
    dataset_hash TEXT NOT NULL CHECK (length(dataset_hash) = 64),
    harness_hash TEXT NOT NULL CHECK (length(harness_hash) = 64),
    seed INTEGER NOT NULL,
    expected_cases INTEGER NOT NULL CHECK (
        expected_cases BETWEEN 1 AND 10000
    ),
    status TEXT NOT NULL CHECK (
        status IN ('running', 'promotion_eligible', 'rejected', 'blocked')
    ),
    decision_reason TEXT,
    created_at TEXT NOT NULL,
    completed_at TEXT
);

CREATE TABLE meta_context_case_results (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES meta_context_runs(id),
    case_hash TEXT NOT NULL CHECK (length(case_hash) = 64),
    incumbent_quality_micros INTEGER NOT NULL,
    candidate_quality_micros INTEGER NOT NULL,
    incumbent_tokens INTEGER NOT NULL CHECK (incumbent_tokens >= 0),
    candidate_tokens INTEGER NOT NULL CHECK (candidate_tokens >= 0),
    hard_violations INTEGER NOT NULL CHECK (hard_violations >= 0),
    protected_regression INTEGER NOT NULL CHECK (
        protected_regression IN (0, 1)
    ),
    authority_invariant INTEGER NOT NULL CHECK (authority_invariant IN (0, 1)),
    provenance_invariant INTEGER NOT NULL CHECK (
        provenance_invariant IN (0, 1)
    ),
    created_at TEXT NOT NULL,
    UNIQUE(run_id, case_hash)
);

CREATE TABLE meta_context_events (
    id TEXT PRIMARY KEY,
    strategy_id TEXT NOT NULL REFERENCES meta_context_strategies(id),
    run_id TEXT,
    event_type TEXT NOT NULL CHECK (
        event_type IN ('candidate', 'benchmark', 'eligible', 'reject', 'blocked')
    ),
    evidence_hash TEXT NOT NULL CHECK (length(evidence_hash) = 64),
    created_at TEXT NOT NULL
);

CREATE INDEX meta_context_runs_created
ON meta_context_runs(created_at);
CREATE INDEX meta_context_cases_run
ON meta_context_case_results(run_id);

CREATE TRIGGER meta_context_strategies_guard
BEFORE UPDATE ON meta_context_strategies
WHEN NOT (
    OLD.status = 'candidate'
    AND NEW.status IN ('evaluated', 'rejected', 'promotion_eligible')
    AND NEW.id = OLD.id
    AND NEW.version = OLD.version
    AND NEW.parent_hash = OLD.parent_hash
    AND NEW.config_json = OLD.config_json
    AND NEW.config_hash = OLD.config_hash
    AND NEW.hypothesis_hash = OLD.hypothesis_hash
    AND NEW.created_at = OLD.created_at
)
BEGIN
    SELECT RAISE(ABORT, 'meta-context strategy is immutable');
END;
CREATE TRIGGER meta_context_strategies_no_delete
BEFORE DELETE ON meta_context_strategies
BEGIN
    SELECT RAISE(ABORT, 'meta-context strategies are retained');
END;
CREATE TRIGGER meta_context_cases_no_update
BEFORE UPDATE ON meta_context_case_results
BEGIN
    SELECT RAISE(ABORT, 'meta-context case evidence is append-only');
END;
CREATE TRIGGER meta_context_cases_running_insert
BEFORE INSERT ON meta_context_case_results
WHEN NOT EXISTS (
    SELECT 1 FROM meta_context_runs
    WHERE id = NEW.run_id AND status = 'running'
)
BEGIN
    SELECT RAISE(ABORT, 'meta-context run is sealed');
END;
CREATE TRIGGER meta_context_cases_no_delete
BEFORE DELETE ON meta_context_case_results
BEGIN
    SELECT RAISE(ABORT, 'meta-context case evidence is append-only');
END;
CREATE TRIGGER meta_context_runs_terminal_guard
BEFORE UPDATE ON meta_context_runs
WHEN NOT (
    OLD.status = 'running'
    AND NEW.status IN ('promotion_eligible', 'rejected', 'blocked')
    AND NEW.id = OLD.id
    AND NEW.strategy_id = OLD.strategy_id
    AND NEW.production_hash = OLD.production_hash
    AND NEW.dataset_hash = OLD.dataset_hash
    AND NEW.harness_hash = OLD.harness_hash
    AND NEW.seed = OLD.seed
    AND NEW.expected_cases = OLD.expected_cases
    AND NEW.created_at = OLD.created_at
    AND NEW.completed_at IS NOT NULL
)
BEGIN
    SELECT RAISE(ABORT, 'meta-context run is sealed');
END;
CREATE TRIGGER meta_context_runs_no_delete
BEFORE DELETE ON meta_context_runs
BEGIN
    SELECT RAISE(ABORT, 'meta-context runs are retained');
END;
CREATE TRIGGER meta_context_events_no_update
BEFORE UPDATE ON meta_context_events
BEGIN
    SELECT RAISE(ABORT, 'meta-context events are append-only');
END;
CREATE TRIGGER meta_context_events_no_delete
BEFORE DELETE ON meta_context_events
BEGIN
    SELECT RAISE(ABORT, 'meta-context events are append-only');
END;
"""

MIGRATION_49_SQL = """
CREATE TABLE skill_support_links (
    id TEXT PRIMARY KEY,
    skill_id TEXT NOT NULL REFERENCES skills(id),
    generation_candidate_id TEXT NOT NULL
        REFERENCES skill_generation_candidates(id),
    root_trace_id TEXT NOT NULL REFERENCES experience_traces(id),
    distillation_id TEXT NOT NULL REFERENCES experience_distillations(id),
    distilled_item_id TEXT NOT NULL REFERENCES experience_distilled_items(id),
    memory_id TEXT NOT NULL REFERENCES memories(id),
    scope_hash TEXT NOT NULL CHECK (
        length(scope_hash) = 64
        AND scope_hash NOT GLOB '*[^0-9a-f]*'
    ),
    task_class_hash TEXT NOT NULL CHECK (
        length(task_class_hash) = 64
        AND task_class_hash NOT GLOB '*[^0-9a-f]*'
    ),
    support_hash TEXT NOT NULL CHECK (
        length(support_hash) = 64
        AND support_hash NOT GLOB '*[^0-9a-f]*'
    ),
    package_hash TEXT NOT NULL CHECK (
        length(package_hash) = 64
        AND package_hash NOT GLOB '*[^0-9a-f]*'
    ),
    created_at TEXT NOT NULL,
    UNIQUE(skill_id, root_trace_id),
    UNIQUE(skill_id, distilled_item_id)
);

CREATE TABLE skill_support_invalidations (
    id TEXT PRIMARY KEY,
    support_link_id TEXT NOT NULL UNIQUE REFERENCES skill_support_links(id),
    reason TEXT NOT NULL CHECK (
        reason IN (
            'memory_missing', 'memory_not_current', 'memory_untrusted',
            'trace_not_succeeded', 'distillation_not_applied',
            'item_not_applied', 'package_changed', 'support_hash_changed',
            'operator_rejected'
        )
    ),
    reason_hash TEXT NOT NULL CHECK (
        length(reason_hash) = 64
        AND reason_hash NOT GLOB '*[^0-9a-f]*'
    ),
    actor_type TEXT NOT NULL CHECK (
        actor_type IN ('reconciler', 'operator')
    ),
    actor_hash TEXT NOT NULL CHECK (
        length(actor_hash) = 64
        AND actor_hash NOT GLOB '*[^0-9a-f]*'
    ),
    created_at TEXT NOT NULL
);

CREATE TABLE skill_reliability_snapshots (
    id TEXT PRIMARY KEY,
    skill_id TEXT NOT NULL REFERENCES skills(id),
    evidence_revision TEXT NOT NULL CHECK (
        length(evidence_revision) = 64
        AND evidence_revision NOT GLOB '*[^0-9a-f]*'
    ),
    support_total INTEGER NOT NULL CHECK (support_total >= 0),
    support_valid INTEGER NOT NULL CHECK (
        support_valid >= 0 AND support_valid <= support_total
    ),
    execution_successes INTEGER NOT NULL CHECK (execution_successes >= 0),
    execution_failures INTEGER NOT NULL CHECK (execution_failures >= 0),
    wilson_lower_micros INTEGER NOT NULL CHECK (
        wilson_lower_micros BETWEEN 0 AND 1000000
    ),
    reliability_micros INTEGER NOT NULL CHECK (
        reliability_micros BETWEEN 0 AND 1000000
    ),
    assessment TEXT NOT NULL CHECK (
        assessment IN ('unassessed', 'grounded', 'probation', 'invalidated')
    ),
    created_at TEXT NOT NULL,
    UNIQUE(skill_id, evidence_revision)
);

CREATE TABLE skill_coevolution_events (
    id TEXT PRIMARY KEY,
    skill_id TEXT NOT NULL REFERENCES skills(id),
    event_type TEXT NOT NULL CHECK (
        event_type IN (
            'lineage_linked', 'reliability_updated',
            'support_invalidated', 'auto_quarantined'
        )
    ),
    evidence_hash TEXT NOT NULL CHECK (
        length(evidence_hash) = 64
        AND evidence_hash NOT GLOB '*[^0-9a-f]*'
    ),
    created_at TEXT NOT NULL,
    UNIQUE(skill_id, event_type, evidence_hash)
);

CREATE INDEX skill_support_links_skill
ON skill_support_links(skill_id, created_at);
CREATE INDEX skill_support_links_trace
ON skill_support_links(root_trace_id);
CREATE INDEX skill_reliability_skill
ON skill_reliability_snapshots(skill_id, created_at);

CREATE TRIGGER skill_support_links_no_update
BEFORE UPDATE ON skill_support_links
BEGIN
    SELECT RAISE(ABORT, 'skill support lineage is immutable');
END;
CREATE TRIGGER skill_support_links_no_delete
BEFORE DELETE ON skill_support_links
BEGIN
    SELECT RAISE(ABORT, 'skill support lineage is retained');
END;
CREATE TRIGGER skill_support_invalidations_no_update
BEFORE UPDATE ON skill_support_invalidations
BEGIN
    SELECT RAISE(ABORT, 'skill support invalidations are append-only');
END;
CREATE TRIGGER skill_support_invalidations_no_delete
BEFORE DELETE ON skill_support_invalidations
BEGIN
    SELECT RAISE(ABORT, 'skill support invalidations are retained');
END;
CREATE TRIGGER skill_reliability_no_update
BEFORE UPDATE ON skill_reliability_snapshots
BEGIN
    SELECT RAISE(ABORT, 'skill reliability snapshots are append-only');
END;
CREATE TRIGGER skill_reliability_no_delete
BEFORE DELETE ON skill_reliability_snapshots
BEGIN
    SELECT RAISE(ABORT, 'skill reliability snapshots are retained');
END;
CREATE TRIGGER skill_coevolution_events_no_update
BEFORE UPDATE ON skill_coevolution_events
BEGIN
    SELECT RAISE(ABORT, 'skill coevolution events are append-only');
END;
CREATE TRIGGER skill_coevolution_events_no_delete
BEFORE DELETE ON skill_coevolution_events
BEGIN
    SELECT RAISE(ABORT, 'skill coevolution events are retained');
END;
"""

MIGRATION_50_SQL = """
CREATE TABLE utility_assets (
    id TEXT PRIMARY KEY,
    asset_kind TEXT NOT NULL CHECK (
        asset_kind IN (
            'memory', 'skill', 'model', 'tool',
            'agent_topology', 'context_strategy'
        )
    ),
    external_id_hash TEXT NOT NULL CHECK (
        length(external_id_hash) = 64
        AND external_id_hash NOT GLOB '*[^0-9a-f]*'
    ),
    revision_hash TEXT NOT NULL CHECK (
        length(revision_hash) = 64
        AND revision_hash NOT GLOB '*[^0-9a-f]*'
    ),
    scope_hash TEXT NOT NULL CHECK (
        length(scope_hash) = 64
        AND scope_hash NOT GLOB '*[^0-9a-f]*'
    ),
    registered_at TEXT NOT NULL,
    UNIQUE(asset_kind, external_id_hash, revision_hash)
);

CREATE TABLE utility_observations (
    id TEXT PRIMARY KEY,
    asset_id TEXT NOT NULL REFERENCES utility_assets(id),
    root_kind TEXT NOT NULL CHECK (
        root_kind IN (
            'task', 'model_route', 'tool_route', 'agent_plan'
        )
    ),
    root_id_hash TEXT NOT NULL CHECK (
        length(root_id_hash) = 64
        AND root_id_hash NOT GLOB '*[^0-9a-f]*'
    ),
    role TEXT NOT NULL CHECK (
        role IN (
            'context_memory', 'context_skill', 'model_attempt',
            'tool_invocation', 'agent_topology', 'context_strategy'
        )
    ),
    outcome TEXT NOT NULL CHECK (
        outcome IN (
            'contributed', 'ignored', 'misled', 'failed', 'uncertain'
        )
    ),
    evidenced INTEGER NOT NULL CHECK (evidenced IN (0, 1)),
    benefit_micros INTEGER NOT NULL CHECK (
        benefit_micros BETWEEN -1000000 AND 1000000
    ),
    harmful INTEGER NOT NULL CHECK (harmful IN (0, 1)),
    tokens INTEGER NOT NULL DEFAULT 0 CHECK (tokens >= 0),
    latency_ms INTEGER NOT NULL DEFAULT 0 CHECK (latency_ms >= 0),
    measured_cost_micros INTEGER NOT NULL DEFAULT 0 CHECK (
        measured_cost_micros >= 0
    ),
    evidence_hash TEXT NOT NULL CHECK (
        length(evidence_hash) = 64
        AND evidence_hash NOT GLOB '*[^0-9a-f]*'
    ),
    observed_at TEXT NOT NULL,
    UNIQUE(asset_id, root_kind, root_id_hash, role)
);

CREATE TABLE utility_snapshots (
    id TEXT PRIMARY KEY,
    asset_id TEXT NOT NULL REFERENCES utility_assets(id),
    evidence_revision TEXT NOT NULL CHECK (
        length(evidence_revision) = 64
        AND evidence_revision NOT GLOB '*[^0-9a-f]*'
    ),
    observed_uses INTEGER NOT NULL CHECK (observed_uses >= 0),
    evidenced_uses INTEGER NOT NULL CHECK (
        evidenced_uses BETWEEN 0 AND observed_uses
    ),
    positive_count INTEGER NOT NULL CHECK (positive_count >= 0),
    ignored_count INTEGER NOT NULL CHECK (ignored_count >= 0),
    misled_count INTEGER NOT NULL CHECK (misled_count >= 0),
    failed_count INTEGER NOT NULL CHECK (failed_count >= 0),
    utility_micros INTEGER NOT NULL CHECK (
        utility_micros BETWEEN 0 AND 1000000
    ),
    signed_utility_micros INTEGER NOT NULL CHECK (
        signed_utility_micros BETWEEN -1000000 AND 1000000
    ),
    confidence_micros INTEGER NOT NULL CHECK (
        confidence_micros BETWEEN 0 AND 1000000
    ),
    assessment TEXT NOT NULL CHECK (
        assessment IN (
            'unassessed', 'probation', 'productive', 'degrading'
        )
    ),
    recommendation TEXT NOT NULL CHECK (
        recommendation IN (
            'collect_evidence', 'retain', 'review', 'lifecycle_review'
        )
    ),
    last_observed_at TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(asset_id, evidence_revision)
);

CREATE TABLE context_strategy_uses (
    task_id TEXT PRIMARY KEY REFERENCES tasks(id),
    asset_id TEXT NOT NULL REFERENCES utility_assets(id),
    config_hash TEXT NOT NULL CHECK (
        length(config_hash) = 64
        AND config_hash NOT GLOB '*[^0-9a-f]*'
    ),
    status TEXT NOT NULL CHECK (status IN ('selected', 'resolved')),
    selected_at TEXT NOT NULL,
    resolved_at TEXT
);

CREATE TABLE utility_context_selections (
    task_id TEXT NOT NULL REFERENCES tasks(id),
    source_type TEXT NOT NULL CHECK (source_type IN ('memory', 'skill')),
    source_id_hash TEXT NOT NULL CHECK (
        length(source_id_hash) = 64
        AND source_id_hash NOT GLOB '*[^0-9a-f]*'
    ),
    asset_id TEXT NOT NULL REFERENCES utility_assets(id),
    selected_at TEXT NOT NULL,
    PRIMARY KEY(task_id, source_type, source_id_hash)
);

CREATE INDEX utility_assets_kind
ON utility_assets(asset_kind, external_id_hash, registered_at);
CREATE INDEX utility_observations_asset
ON utility_observations(asset_id, observed_at);
CREATE INDEX utility_snapshots_asset
ON utility_snapshots(asset_id, created_at);

CREATE TRIGGER utility_assets_no_update
BEFORE UPDATE ON utility_assets
BEGIN
    SELECT RAISE(ABORT, 'utility asset revisions are immutable');
END;
CREATE TRIGGER utility_assets_no_delete
BEFORE DELETE ON utility_assets
BEGIN
    SELECT RAISE(ABORT, 'utility asset revisions are retained');
END;
CREATE TRIGGER utility_observations_no_update
BEFORE UPDATE ON utility_observations
BEGIN
    SELECT RAISE(ABORT, 'utility observations are append-only');
END;
CREATE TRIGGER utility_observations_no_delete
BEFORE DELETE ON utility_observations
BEGIN
    SELECT RAISE(ABORT, 'utility observations are retained');
END;
CREATE TRIGGER utility_snapshots_no_update
BEFORE UPDATE ON utility_snapshots
BEGIN
    SELECT RAISE(ABORT, 'utility snapshots are append-only');
END;
CREATE TRIGGER utility_snapshots_no_delete
BEFORE DELETE ON utility_snapshots
BEGIN
    SELECT RAISE(ABORT, 'utility snapshots are retained');
END;
CREATE TRIGGER context_strategy_uses_guard
BEFORE UPDATE ON context_strategy_uses
WHEN NOT (
    OLD.status = 'selected'
    AND NEW.status = 'resolved'
    AND NEW.task_id = OLD.task_id
    AND NEW.asset_id = OLD.asset_id
    AND NEW.config_hash = OLD.config_hash
    AND NEW.selected_at = OLD.selected_at
    AND NEW.resolved_at IS NOT NULL
)
BEGIN
    SELECT RAISE(ABORT, 'context strategy use transition is invalid');
END;
CREATE TRIGGER context_strategy_uses_no_delete
BEFORE DELETE ON context_strategy_uses
BEGIN
    SELECT RAISE(ABORT, 'context strategy uses are retained');
END;
CREATE TRIGGER utility_context_selections_no_update
BEFORE UPDATE ON utility_context_selections
BEGIN
    SELECT RAISE(ABORT, 'utility context selections are immutable');
END;
CREATE TRIGGER utility_context_selections_no_delete
BEFORE DELETE ON utility_context_selections
BEGIN
    SELECT RAISE(ABORT, 'utility context selections are retained');
END;
"""

MIGRATION_51_SQL = """
CREATE TABLE price_rates (
    id TEXT PRIMARY KEY,
    service_kind TEXT NOT NULL CHECK (
        service_kind IN ('model', 'tool')
    ),
    provider TEXT NOT NULL CHECK (length(provider) BETWEEN 1 AND 100),
    sku TEXT NOT NULL CHECK (length(sku) BETWEEN 1 AND 200),
    operation TEXT NOT NULL CHECK (length(operation) BETWEEN 1 AND 50),
    meter_kind TEXT NOT NULL CHECK (
        meter_kind IN (
            'uncached_input_token', 'cache_read_token',
            'cache_write_token', 'output_token', 'tool_call'
        )
    ),
    currency_code TEXT NOT NULL CHECK (currency_code IN ('CAD', 'USD')),
    price_micros INTEGER NOT NULL CHECK (
        price_micros BETWEEN 0 AND 1000000000
    ),
    unit_size INTEGER NOT NULL CHECK (
        unit_size BETWEEN 1 AND 1000000000
    ),
    effective_from TEXT NOT NULL,
    effective_until TEXT,
    source_url TEXT NOT NULL CHECK (
        source_url GLOB 'https://*'
    ),
    source_hash TEXT NOT NULL CHECK (
        length(source_hash) = 64
        AND source_hash NOT GLOB '*[^0-9a-f]*'
    ),
    created_at TEXT NOT NULL,
    CHECK (
        effective_until IS NULL OR effective_until > effective_from
    )
);

CREATE INDEX price_rates_lookup
ON price_rates(
    service_kind, provider, sku, operation, meter_kind,
    effective_from, effective_until
);

CREATE TABLE local_cost_profiles (
    id TEXT PRIMARY KEY,
    provider TEXT NOT NULL CHECK (length(provider) BETWEEN 1 AND 100),
    sku TEXT NOT NULL CHECK (length(sku) BETWEEN 1 AND 200),
    currency_code TEXT NOT NULL CHECK (currency_code IN ('CAD', 'USD')),
    enabled INTEGER NOT NULL CHECK (enabled IN (0, 1)),
    power_milliwatts INTEGER NOT NULL CHECK (power_milliwatts >= 0),
    electricity_micros_per_kwh INTEGER NOT NULL CHECK (
        electricity_micros_per_kwh >= 0
    ),
    hardware_micros_per_hour INTEGER NOT NULL CHECK (
        hardware_micros_per_hour >= 0
    ),
    effective_from TEXT NOT NULL,
    effective_until TEXT,
    evidence_hash TEXT NOT NULL CHECK (
        length(evidence_hash) = 64
        AND evidence_hash NOT GLOB '*[^0-9a-f]*'
    ),
    created_at TEXT NOT NULL,
    CHECK (
        effective_until IS NULL OR effective_until > effective_from
    )
);

CREATE INDEX local_cost_profiles_lookup
ON local_cost_profiles(provider, sku, effective_from, effective_until);

CREATE TABLE cost_events (
    id TEXT PRIMARY KEY,
    attempt_id TEXT NOT NULL UNIQUE CHECK (
        length(attempt_id) BETWEEN 1 AND 200
    ),
    source_kind TEXT NOT NULL CHECK (
        source_kind IN ('model', 'tool', 'local')
    ),
    task_id TEXT REFERENCES tasks(id),
    project_scope TEXT,
    provider TEXT NOT NULL CHECK (length(provider) BETWEEN 1 AND 100),
    sku TEXT NOT NULL CHECK (length(sku) BETWEEN 1 AND 200),
    operation TEXT NOT NULL CHECK (length(operation) BETWEEN 1 AND 50),
    call_status TEXT NOT NULL CHECK (
        call_status IN ('succeeded', 'failed', 'partial', 'unknown')
    ),
    usage_quality TEXT NOT NULL CHECK (
        usage_quality IN (
            'provider_reported', 'locally_measured', 'estimated', 'unknown'
        )
    ),
    accounting_status TEXT NOT NULL CHECK (
        accounting_status IN (
            'priced', 'partially_priced', 'unpriced',
            'local_estimate', 'local_disabled'
        )
    ),
    expected_meter_lines INTEGER NOT NULL CHECK (
        expected_meter_lines BETWEEN 0 AND 7
    ),
    expected_skill_allocations INTEGER NOT NULL CHECK (
        expected_skill_allocations BETWEEN 0 AND 64
    ),
    currency_code TEXT CHECK (
        currency_code IS NULL OR currency_code IN ('CAD', 'USD')
    ),
    local_profile_id TEXT REFERENCES local_cost_profiles(id),
    evidence_hash TEXT NOT NULL CHECK (
        length(evidence_hash) = 64
        AND evidence_hash NOT GLOB '*[^0-9a-f]*'
    ),
    occurred_at TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX cost_events_task ON cost_events(task_id, occurred_at);
CREATE INDEX cost_events_project ON cost_events(project_scope, occurred_at);
CREATE INDEX cost_events_model ON cost_events(provider, sku, occurred_at);

CREATE TABLE cost_meter_lines (
    event_id TEXT NOT NULL REFERENCES cost_events(id),
    meter_kind TEXT NOT NULL CHECK (
        meter_kind IN (
            'uncached_input_token', 'cache_read_token',
            'cache_write_token', 'output_token', 'tool_call',
            'electricity', 'hardware'
        )
    ),
    quantity INTEGER NOT NULL CHECK (
        quantity BETWEEN 0 AND 1000000000
    ),
    quantity_unit TEXT NOT NULL CHECK (
        quantity_unit IN ('token', 'call', 'millisecond')
    ),
    rate_id TEXT REFERENCES price_rates(id),
    amount_micros INTEGER NOT NULL CHECK (amount_micros >= 0),
    pricing_status TEXT NOT NULL CHECK (
        pricing_status IN ('priced', 'unpriced', 'local_estimate')
    ),
    PRIMARY KEY(event_id, meter_kind)
);

CREATE TABLE cost_skill_allocations (
    event_id TEXT NOT NULL REFERENCES cost_events(id),
    skill_id TEXT NOT NULL REFERENCES skills(id),
    weight_millionths INTEGER NOT NULL CHECK (
        weight_millionths BETWEEN 1 AND 1000000
    ),
    allocated_micros INTEGER NOT NULL CHECK (allocated_micros >= 0),
    allocation_basis TEXT NOT NULL CHECK (
        allocation_basis IN ('equal_share', 'token_share', 'direct')
    ),
    PRIMARY KEY(event_id, skill_id)
);

CREATE TABLE cost_event_seals (
    event_id TEXT PRIMARY KEY REFERENCES cost_events(id),
    seal_hash TEXT NOT NULL CHECK (
        length(seal_hash) = 64
        AND seal_hash NOT GLOB '*[^0-9a-f]*'
    ),
    total_micros INTEGER NOT NULL CHECK (total_micros >= 0),
    allocated_micros INTEGER NOT NULL CHECK (allocated_micros >= 0),
    sealed_at TEXT NOT NULL
);

CREATE TRIGGER price_rates_no_update
BEFORE UPDATE ON price_rates
BEGIN
    SELECT RAISE(ABORT, 'price rates are immutable');
END;
CREATE TRIGGER price_rates_no_delete
BEFORE DELETE ON price_rates
BEGIN
    SELECT RAISE(ABORT, 'price rates are retained');
END;
CREATE TRIGGER local_cost_profiles_no_update
BEFORE UPDATE ON local_cost_profiles
BEGIN
    SELECT RAISE(ABORT, 'local cost profiles are immutable');
END;
CREATE TRIGGER local_cost_profiles_no_delete
BEFORE DELETE ON local_cost_profiles
BEGIN
    SELECT RAISE(ABORT, 'local cost profiles are retained');
END;
CREATE TRIGGER cost_events_no_update
BEFORE UPDATE ON cost_events
BEGIN
    SELECT RAISE(ABORT, 'cost events are append-only');
END;
CREATE TRIGGER cost_events_no_delete
BEFORE DELETE ON cost_events
BEGIN
    SELECT RAISE(ABORT, 'cost events are retained');
END;
CREATE TRIGGER cost_meter_lines_no_update
BEFORE UPDATE ON cost_meter_lines
BEGIN
    SELECT RAISE(ABORT, 'cost meter lines are append-only');
END;
CREATE TRIGGER cost_meter_lines_guard_insert
BEFORE INSERT ON cost_meter_lines
WHEN EXISTS (
    SELECT 1 FROM cost_event_seals
    WHERE event_id=NEW.event_id
)
OR (
    SELECT COUNT(*) FROM cost_meter_lines
    WHERE event_id=NEW.event_id
) >= (
    SELECT expected_meter_lines FROM cost_events
    WHERE id=NEW.event_id
)
BEGIN
    SELECT RAISE(ABORT, 'cost event meter lines are sealed');
END;
CREATE TRIGGER cost_meter_lines_validate_rate
BEFORE INSERT ON cost_meter_lines
WHEN NEW.rate_id IS NOT NULL AND NOT EXISTS (
    SELECT 1
    FROM price_rates r
    JOIN cost_events e ON e.id=NEW.event_id
    WHERE r.id=NEW.rate_id
      AND r.service_kind=e.source_kind
      AND r.provider=e.provider
      AND r.sku=e.sku
      AND r.operation=e.operation
      AND r.meter_kind=NEW.meter_kind
      AND r.currency_code=e.currency_code
      AND r.effective_from <= e.occurred_at
      AND (r.effective_until IS NULL OR r.effective_until > e.occurred_at)
)
BEGIN
    SELECT RAISE(ABORT, 'cost meter rate does not match event');
END;
CREATE TRIGGER cost_meter_lines_validate_amount
BEFORE INSERT ON cost_meter_lines
WHEN
    (
        NEW.rate_id IS NOT NULL
        AND (
            NEW.pricing_status != 'priced'
            OR NEW.quantity_unit != CASE
                WHEN NEW.meter_kind='tool_call' THEN 'call'
                ELSE 'token'
            END
            OR NEW.amount_micros != (
                SELECT (
                    NEW.quantity * r.price_micros + r.unit_size - 1
                ) / r.unit_size
                FROM price_rates r WHERE r.id=NEW.rate_id
            )
        )
    )
    OR
    (
        NEW.rate_id IS NULL
        AND (SELECT local_profile_id FROM cost_events WHERE id=NEW.event_id)
            IS NULL
        AND (
            NEW.pricing_status != 'unpriced'
            OR NEW.amount_micros != 0
        )
    )
    OR
    (
        NEW.rate_id IS NULL
        AND (SELECT local_profile_id FROM cost_events WHERE id=NEW.event_id)
            IS NOT NULL
        AND (
            NEW.pricing_status != 'local_estimate'
            OR NEW.quantity_unit != 'millisecond'
            OR NEW.meter_kind NOT IN ('electricity', 'hardware')
            OR NOT EXISTS (
                SELECT 1
                FROM local_cost_profiles p
                JOIN cost_events e ON e.local_profile_id=p.id
                WHERE e.id=NEW.event_id
                  AND e.source_kind='local'
                  AND e.provider=p.provider
                  AND e.sku=p.sku
                  AND e.currency_code=p.currency_code
                  AND p.enabled=1
                  AND p.effective_from <= e.occurred_at
                  AND (
                      p.effective_until IS NULL
                      OR p.effective_until > e.occurred_at
                  )
                  AND NEW.amount_micros = CASE NEW.meter_kind
                      WHEN 'hardware' THEN CAST(CEIL(
                          NEW.quantity * 1.0
                          * p.hardware_micros_per_hour / 3600000.0
                      ) AS INTEGER)
                      ELSE CAST(CEIL(
                          p.power_milliwatts * 1.0 * NEW.quantity
                          * p.electricity_micros_per_kwh
                          / 3600000000000.0
                      ) AS INTEGER)
                  END
            )
        )
    )
BEGIN
    SELECT RAISE(ABORT, 'cost meter amount is not reproducible');
END;
CREATE TRIGGER cost_meter_lines_no_delete
BEFORE DELETE ON cost_meter_lines
BEGIN
    SELECT RAISE(ABORT, 'cost meter lines are retained');
END;
CREATE TRIGGER cost_skill_allocations_guard_insert
BEFORE INSERT ON cost_skill_allocations
WHEN EXISTS (
    SELECT 1 FROM cost_event_seals
    WHERE event_id=NEW.event_id
)
OR (
    SELECT COUNT(*) FROM cost_skill_allocations
    WHERE event_id=NEW.event_id
) >= (
    SELECT expected_skill_allocations FROM cost_events
    WHERE id=NEW.event_id
)
BEGIN
    SELECT RAISE(ABORT, 'cost event allocations are sealed');
END;
CREATE TRIGGER cost_event_seals_validate
BEFORE INSERT ON cost_event_seals
WHEN
    (SELECT COUNT(*) FROM cost_meter_lines WHERE event_id=NEW.event_id)
        != (SELECT expected_meter_lines FROM cost_events WHERE id=NEW.event_id)
    OR
    (SELECT COUNT(*) FROM cost_skill_allocations WHERE event_id=NEW.event_id)
        != (SELECT expected_skill_allocations FROM cost_events WHERE id=NEW.event_id)
    OR
    NEW.total_micros != COALESCE((
        SELECT SUM(amount_micros) FROM cost_meter_lines
        WHERE event_id=NEW.event_id
    ), 0)
    OR
    (
        (SELECT expected_skill_allocations FROM cost_events WHERE id=NEW.event_id) > 0
        AND (
            COALESCE((
                SELECT SUM(weight_millionths) FROM cost_skill_allocations
                WHERE event_id=NEW.event_id
            ), 0) != 1000000
            OR NEW.allocated_micros != NEW.total_micros
            OR NEW.allocated_micros != COALESCE((
                SELECT SUM(allocated_micros) FROM cost_skill_allocations
                WHERE event_id=NEW.event_id
            ), 0)
        )
    )
    OR
    (
        (SELECT expected_skill_allocations FROM cost_events WHERE id=NEW.event_id) = 0
        AND NEW.allocated_micros != 0
    )
BEGIN
    SELECT RAISE(ABORT, 'cost event cannot be sealed');
END;
CREATE TRIGGER cost_event_seals_no_update
BEFORE UPDATE ON cost_event_seals
BEGIN
    SELECT RAISE(ABORT, 'cost event seals are immutable');
END;
CREATE TRIGGER cost_event_seals_no_delete
BEFORE DELETE ON cost_event_seals
BEGIN
    SELECT RAISE(ABORT, 'cost event seals are retained');
END;
CREATE TRIGGER cost_skill_allocations_no_update
BEFORE UPDATE ON cost_skill_allocations
BEGIN
    SELECT RAISE(ABORT, 'cost allocations are append-only');
END;
CREATE TRIGGER cost_skill_allocations_no_delete
BEFORE DELETE ON cost_skill_allocations
BEGIN
    SELECT RAISE(ABORT, 'cost allocations are retained');
END;
CREATE TRIGGER price_rates_no_overlap
BEFORE INSERT ON price_rates
WHEN EXISTS (
    SELECT 1 FROM price_rates r
    WHERE r.service_kind=NEW.service_kind
      AND r.provider=NEW.provider
      AND r.sku=NEW.sku
      AND r.operation=NEW.operation
      AND r.meter_kind=NEW.meter_kind
      AND (r.effective_until IS NULL OR r.effective_until > NEW.effective_from)
      AND (NEW.effective_until IS NULL OR r.effective_from < NEW.effective_until)
)
BEGIN
    SELECT RAISE(ABORT, 'price rate interval overlaps an existing rate');
END;
CREATE TRIGGER local_cost_profiles_no_overlap
BEFORE INSERT ON local_cost_profiles
WHEN EXISTS (
    SELECT 1 FROM local_cost_profiles p
    WHERE p.provider=NEW.provider
      AND p.sku=NEW.sku
      AND (p.effective_until IS NULL OR p.effective_until > NEW.effective_from)
      AND (NEW.effective_until IS NULL OR p.effective_from < NEW.effective_until)
)
BEGIN
    SELECT RAISE(ABORT, 'local profile interval overlaps an existing profile');
END;
"""

MIGRATION_52_SQL = """
CREATE TABLE token_waste_runs (
    id TEXT PRIMARY KEY,
    scope_hash TEXT NOT NULL CHECK (
        length(scope_hash) = 64
        AND scope_hash NOT GLOB '*[^0-9a-f]*'
    ),
    analyzer_version TEXT NOT NULL,
    policy_json TEXT NOT NULL CHECK (json_valid(policy_json)),
    evidence_revision TEXT NOT NULL CHECK (
        length(evidence_revision) = 64
        AND evidence_revision NOT GLOB '*[^0-9a-f]*'
    ),
    expected_findings INTEGER NOT NULL CHECK (expected_findings = 9),
    status TEXT NOT NULL CHECK (status IN ('running', 'completed')),
    created_at TEXT NOT NULL,
    completed_at TEXT,
    UNIQUE(analyzer_version, scope_hash, evidence_revision)
);

CREATE TABLE token_waste_findings (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES token_waste_runs(id),
    sequence INTEGER NOT NULL CHECK (sequence BETWEEN 1 AND 9),
    category TEXT NOT NULL CHECK (
        category IN (
            'large_retrieved_blocks_never_used',
            'repeated_instructions', 'duplicate_memories',
            'unnecessary_skill_text', 'oversized_tool_descriptions',
            'full_files_when_symbols_sufficient', 'excessive_reflection',
            'too_many_agents', 'unnecessary_model_escalation'
        )
    ),
    verdict TEXT NOT NULL CHECK (
        verdict IN (
            'observed_overhead', 'candidate_waste',
            'counterfactually_avoidable', 'protected', 'confounded',
            'insufficient_evidence'
        )
    ),
    subject_count INTEGER NOT NULL CHECK (subject_count >= 0),
    observed_tokens INTEGER NOT NULL CHECK (observed_tokens >= 0),
    token_quality TEXT NOT NULL CHECK (
        token_quality IN (
            'provider_reported', 'locally_measured', 'estimated', 'unknown'
        )
    ),
    evidence_method TEXT NOT NULL CHECK (
        evidence_method IN ('associated', 'derived', 'controlled', 'none')
    ),
    savings_low INTEGER,
    savings_base INTEGER,
    savings_high INTEGER,
    evidence_json TEXT NOT NULL CHECK (
        json_valid(evidence_json) AND json_type(evidence_json) = 'object'
    ),
    recommendation TEXT NOT NULL CHECK (length(recommendation) BETWEEN 1 AND 100),
    automatic_action_allowed INTEGER NOT NULL DEFAULT 0 CHECK (
        automatic_action_allowed = 0
    ),
    created_at TEXT NOT NULL,
    UNIQUE(run_id, sequence),
    UNIQUE(run_id, category),
    CHECK (
        verdict <> 'counterfactually_avoidable'
        AND savings_low IS NULL
        AND savings_base IS NULL
        AND savings_high IS NULL
    )
);

CREATE INDEX token_waste_findings_run
ON token_waste_findings(run_id, sequence);

CREATE TRIGGER token_waste_runs_start_guard
BEFORE INSERT ON token_waste_runs
WHEN NEW.status <> 'running' OR NEW.completed_at IS NOT NULL
BEGIN
    SELECT RAISE(ABORT, 'token-waste runs must start unsealed');
END;
CREATE TRIGGER token_waste_runs_terminal_guard
BEFORE UPDATE ON token_waste_runs
WHEN NOT (
    OLD.status = 'running'
    AND NEW.status = 'completed'
    AND NEW.id = OLD.id
    AND NEW.scope_hash = OLD.scope_hash
    AND NEW.analyzer_version = OLD.analyzer_version
    AND NEW.policy_json = OLD.policy_json
    AND NEW.evidence_revision = OLD.evidence_revision
    AND NEW.expected_findings = OLD.expected_findings
    AND NEW.created_at = OLD.created_at
    AND NEW.completed_at IS NOT NULL
    AND (
        SELECT COUNT(*) FROM token_waste_findings
        WHERE run_id = OLD.id
    ) = OLD.expected_findings
)
BEGIN
    SELECT RAISE(ABORT, 'token-waste run cannot be completed');
END;
CREATE TRIGGER token_waste_runs_no_delete
BEFORE DELETE ON token_waste_runs
BEGIN
    SELECT RAISE(ABORT, 'token-waste runs are retained');
END;
CREATE TRIGGER token_waste_findings_running_insert
BEFORE INSERT ON token_waste_findings
WHEN NOT EXISTS (
    SELECT 1 FROM token_waste_runs
    WHERE id = NEW.run_id AND status = 'running'
)
OR (
    SELECT COUNT(*) FROM token_waste_findings
    WHERE run_id = NEW.run_id
) >= (
    SELECT expected_findings FROM token_waste_runs
    WHERE id = NEW.run_id
)
BEGIN
    SELECT RAISE(ABORT, 'token-waste run is sealed');
END;
CREATE TRIGGER token_waste_findings_no_update
BEFORE UPDATE ON token_waste_findings
BEGIN
    SELECT RAISE(ABORT, 'token-waste findings are immutable');
END;
CREATE TRIGGER token_waste_findings_no_delete
BEFORE DELETE ON token_waste_findings
BEGIN
    SELECT RAISE(ABORT, 'token-waste findings are retained');
END;
"""

MIGRATION_53_SQL = """
CREATE TABLE tool_exposure_projections (
    id TEXT PRIMARY KEY,
    route_id TEXT NOT NULL REFERENCES tool_routes(id),
    agent_spec_id TEXT NOT NULL REFERENCES agent_specs(id),
    task_class TEXT NOT NULL CHECK (
        length(task_class) BETWEEN 1 AND 128
        AND task_class NOT GLOB '*[^A-Za-z0-9._:/-]*'
    ),
    agent_spec_hash TEXT NOT NULL CHECK (
        length(agent_spec_hash) = 64
        AND agent_spec_hash NOT GLOB '*[^0-9a-f]*'
    ),
    catalog_hash TEXT NOT NULL CHECK (
        length(catalog_hash) = 64
        AND catalog_hash NOT GLOB '*[^0-9a-f]*'
    ),
    selector_hash TEXT NOT NULL CHECK (
        length(selector_hash) = 64
        AND selector_hash NOT GLOB '*[^0-9a-f]*'
    ),
    mode TEXT NOT NULL CHECK (mode = 'direct_filtered'),
    baseline_tools_json TEXT NOT NULL CHECK (
        json_valid(baseline_tools_json)
        AND json_type(baseline_tools_json) = 'array'
        AND json_array_length(baseline_tools_json) <= 64
    ),
    exposed_tools_json TEXT NOT NULL CHECK (
        json_valid(exposed_tools_json)
        AND json_type(exposed_tools_json) = 'array'
        AND json_array_length(exposed_tools_json) <= 8
    ),
    definition_hashes_json TEXT NOT NULL CHECK (
        json_valid(definition_hashes_json)
        AND json_type(definition_hashes_json) = 'object'
    ),
    baseline_tool_count INTEGER NOT NULL CHECK (
        baseline_tool_count BETWEEN 0 AND 64
    ),
    exposed_tool_count INTEGER NOT NULL CHECK (
        exposed_tool_count BETWEEN 0 AND 8
    ),
    baseline_estimated_tokens INTEGER NOT NULL CHECK (
        baseline_estimated_tokens >= 0
    ),
    exposed_estimated_tokens INTEGER NOT NULL CHECK (
        exposed_estimated_tokens >= 0
    ),
    estimate_version TEXT NOT NULL CHECK (
        estimate_version = 'acr-json-char-estimate-v1.0.0'
    ),
    status TEXT NOT NULL CHECK (
        status IN ('available', 'unavailable')
    ),
    reasons_json TEXT NOT NULL CHECK (
        json_valid(reasons_json) AND json_type(reasons_json) = 'array'
    ),
    created_at TEXT NOT NULL,
    CHECK (
        json_array_length(baseline_tools_json) = baseline_tool_count
        AND json_array_length(exposed_tools_json) = exposed_tool_count
    ),
    CHECK (
        status = 'unavailable'
        OR (
            exposed_tool_count <= baseline_tool_count
            AND exposed_estimated_tokens <= baseline_estimated_tokens
        )
    ),
    CHECK (
        (status='available'
         AND json_array_length(reasons_json)=0)
        OR (status='unavailable'
            AND exposed_tool_count=0
            AND json_array_length(reasons_json) >= 1)
    ),
    UNIQUE(
        route_id, agent_spec_id, agent_spec_hash,
        catalog_hash, selector_hash
    )
);

CREATE INDEX tool_exposure_projections_task
ON tool_exposure_projections(task_class, agent_spec_hash, created_at);

CREATE TABLE tool_exposure_benchmark_runs (
    id TEXT PRIMARY KEY,
    task_class TEXT NOT NULL CHECK (
        length(task_class) BETWEEN 1 AND 128
        AND task_class NOT GLOB '*[^A-Za-z0-9._:/-]*'
    ),
    agent_spec_hash TEXT NOT NULL CHECK (
        length(agent_spec_hash) = 64
        AND agent_spec_hash NOT GLOB '*[^0-9a-f]*'
    ),
    catalog_hash TEXT NOT NULL CHECK (
        length(catalog_hash) = 64
        AND catalog_hash NOT GLOB '*[^0-9a-f]*'
    ),
    selector_hash TEXT NOT NULL CHECK (
        length(selector_hash) = 64
        AND selector_hash NOT GLOB '*[^0-9a-f]*'
    ),
    dataset_hash TEXT NOT NULL CHECK (
        length(dataset_hash) = 64
        AND dataset_hash NOT GLOB '*[^0-9a-f]*'
    ),
    model_hash TEXT NOT NULL CHECK (
        length(model_hash) = 64
        AND model_hash NOT GLOB '*[^0-9a-f]*'
    ),
    settings_hash TEXT NOT NULL CHECK (
        length(settings_hash) = 64
        AND settings_hash NOT GLOB '*[^0-9a-f]*'
    ),
    evaluator_hash TEXT NOT NULL CHECK (
        length(evaluator_hash) = 64
        AND evaluator_hash NOT GLOB '*[^0-9a-f]*'
    ),
    seed INTEGER NOT NULL,
    expected_cases INTEGER NOT NULL CHECK (
        expected_cases BETWEEN 5 AND 1000
    ),
    quality_margin_micros INTEGER NOT NULL CHECK (
        quality_margin_micros BETWEEN 0 AND 100000
    ),
    status TEXT NOT NULL CHECK (
        status IN ('running', 'rejected', 'insufficient_evidence')
    ),
    recommendation TEXT CHECK (
        recommendation IS NULL OR recommendation IN (
            'reject_dynamic_exposure', 'collect_verified_receipts'
        )
    ),
    summary_json TEXT CHECK (
        summary_json IS NULL OR (
            json_valid(summary_json) AND json_type(summary_json) = 'object'
        )
    ),
    created_at TEXT NOT NULL,
    completed_at TEXT,
    UNIQUE(
        task_class, agent_spec_hash, catalog_hash, selector_hash,
        dataset_hash, model_hash, settings_hash, evaluator_hash, seed,
        quality_margin_micros
    )
);

CREATE TABLE tool_exposure_benchmark_cases (
    run_id TEXT NOT NULL REFERENCES tool_exposure_benchmark_runs(id),
    sequence INTEGER NOT NULL CHECK (sequence >= 1),
    case_hash TEXT NOT NULL CHECK (
        length(case_hash) = 64
        AND case_hash NOT GLOB '*[^0-9a-f]*'
    ),
    PRIMARY KEY(run_id, case_hash),
    UNIQUE(run_id, sequence)
);

CREATE TABLE tool_exposure_benchmark_trials (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES tool_exposure_benchmark_runs(id),
    sequence INTEGER NOT NULL CHECK (sequence >= 1),
    case_hash TEXT NOT NULL CHECK (
        length(case_hash) = 64
        AND case_hash NOT GLOB '*[^0-9a-f]*'
    ),
    projection_id TEXT NOT NULL REFERENCES tool_exposure_projections(id),
    arm TEXT NOT NULL CHECK (arm IN ('full_authorized', 'dynamic')),
    attempt_id TEXT NOT NULL UNIQUE CHECK (
        length(attempt_id) BETWEEN 1 AND 200
    ),
    success INTEGER NOT NULL CHECK (success IN (0, 1)),
    quality_micros INTEGER NOT NULL CHECK (
        quality_micros BETWEEN 0 AND 1000000
    ),
    required_tool_recall_micros INTEGER NOT NULL CHECK (
        required_tool_recall_micros BETWEEN 0 AND 1000000
    ),
    hard_violation INTEGER NOT NULL CHECK (hard_violation IN (0, 1)),
    unauthorized_exposure_count INTEGER NOT NULL CHECK (
        unauthorized_exposure_count >= 0
    ),
    invalid_call_count INTEGER NOT NULL CHECK (invalid_call_count >= 0),
    input_tokens INTEGER NOT NULL CHECK (input_tokens >= 0),
    output_tokens INTEGER NOT NULL CHECK (output_tokens >= 0),
    cached_tokens INTEGER NOT NULL CHECK (
        cached_tokens BETWEEN 0 AND input_tokens
    ),
    token_quality TEXT NOT NULL CHECK (
        token_quality IN (
            'provider_reported', 'locally_measured', 'estimated', 'unknown'
        )
    ),
    latency_ms INTEGER NOT NULL CHECK (latency_ms >= 0),
    evidence_hash TEXT NOT NULL CHECK (
        length(evidence_hash) = 64
        AND evidence_hash NOT GLOB '*[^0-9a-f]*'
    ),
    created_at TEXT NOT NULL,
    UNIQUE(run_id, sequence),
    UNIQUE(run_id, case_hash, arm),
    FOREIGN KEY(run_id, case_hash)
        REFERENCES tool_exposure_benchmark_cases(run_id, case_hash)
);

CREATE INDEX tool_exposure_trials_run
ON tool_exposure_benchmark_trials(run_id, case_hash, arm);

CREATE TRIGGER tool_definitions_no_update
BEFORE UPDATE ON tool_definitions
BEGIN
    SELECT RAISE(ABORT, 'canonical tool definitions are immutable');
END;
CREATE TRIGGER tool_definitions_no_delete
BEFORE DELETE ON tool_definitions
BEGIN
    SELECT RAISE(ABORT, 'canonical tool definitions are retained');
END;
CREATE TRIGGER tool_routes_no_update
BEFORE UPDATE ON tool_routes
BEGIN
    SELECT RAISE(ABORT, 'tool routes are immutable');
END;
CREATE TRIGGER tool_routes_no_delete
BEFORE DELETE ON tool_routes
BEGIN
    SELECT RAISE(ABORT, 'tool routes are retained');
END;
CREATE TRIGGER tool_route_candidates_no_update
BEFORE UPDATE ON tool_route_candidates
BEGIN
    SELECT RAISE(ABORT, 'tool route candidates are immutable');
END;
CREATE TRIGGER tool_route_candidates_no_delete
BEFORE DELETE ON tool_route_candidates
BEGIN
    SELECT RAISE(ABORT, 'tool route candidates are retained');
END;
CREATE TRIGGER tool_exposure_projections_no_update
BEFORE UPDATE ON tool_exposure_projections
BEGIN
    SELECT RAISE(ABORT, 'tool exposure projections are immutable');
END;
CREATE TRIGGER tool_exposure_projections_no_delete
BEFORE DELETE ON tool_exposure_projections
BEGIN
    SELECT RAISE(ABORT, 'tool exposure projections are retained');
END;
CREATE TRIGGER tool_exposure_projections_integrity
BEFORE INSERT ON tool_exposure_projections
WHEN NOT EXISTS (
    SELECT 1
    FROM tool_routes AS route
    JOIN agent_specs AS spec ON spec.id=NEW.agent_spec_id
    WHERE route.id=NEW.route_id
      AND route.task_class=NEW.task_class
      AND json_extract(route.request_json, '$.subject_type')='agent'
      AND json_extract(route.request_json, '$.subject_id')=NEW.agent_spec_id
      AND spec.content_hash=NEW.agent_spec_hash
      AND EXISTS (
          SELECT 1 FROM json_each(spec.task_scope_json)
          WHERE value=NEW.task_class
      )
)
OR (
    NEW.status='available'
    AND NOT EXISTS (
        SELECT 1
        FROM tool_routes AS route
        JOIN agent_specs AS spec ON spec.id=NEW.agent_spec_id
        WHERE route.id=NEW.route_id
          AND json_extract(route.request_json, '$.exposure_selector')
              ='agent-allowlist-v1.0.0'
          AND json_extract(route.request_json, '$.agent_allowlist_count')
              =json_array_length(spec.tools_json)
    )
)
OR EXISTS (
    SELECT 1 FROM json_each(NEW.baseline_tools_json)
    WHERE type <> 'text'
)
OR EXISTS (
    SELECT 1 FROM json_each(NEW.exposed_tools_json)
    WHERE type <> 'text'
)
OR EXISTS (
    SELECT value FROM json_each(NEW.baseline_tools_json)
    GROUP BY value HAVING COUNT(*) <> 1
)
OR EXISTS (
    SELECT value FROM json_each(NEW.exposed_tools_json)
    GROUP BY value HAVING COUNT(*) <> 1
)
OR EXISTS (
    SELECT value FROM json_each(NEW.exposed_tools_json)
    EXCEPT SELECT value FROM json_each(NEW.baseline_tools_json)
)
OR (NEW.status='available' AND (
    SELECT COUNT(*) FROM json_each(NEW.exposed_tools_json)
) <> (
    SELECT COUNT(*) FROM json_each(
        (SELECT selected_tools_json FROM tool_routes WHERE id=NEW.route_id)
    )
))
OR (NEW.status='available' AND EXISTS (
    SELECT value FROM json_each(NEW.exposed_tools_json)
    EXCEPT
    SELECT value FROM json_each(
        (SELECT selected_tools_json FROM tool_routes WHERE id=NEW.route_id)
    )
))
OR (NEW.status='available' AND EXISTS (
    SELECT value FROM json_each(
        (SELECT selected_tools_json FROM tool_routes WHERE id=NEW.route_id)
    )
    EXCEPT SELECT value FROM json_each(NEW.exposed_tools_json)
))
OR EXISTS (
    SELECT base.value
    FROM json_each(NEW.baseline_tools_json) AS base
    LEFT JOIN tool_definitions AS tool ON tool.name=base.value
    WHERE tool.name IS NULL
       OR json_extract(NEW.definition_hashes_json, '$."' || base.value || '"')
          IS NOT tool.definition_hash
       OR NOT EXISTS (
           SELECT 1
           FROM agent_specs AS spec, json_each(spec.tools_json) AS allowed
           WHERE spec.id=NEW.agent_spec_id AND allowed.value=base.value
       )
)
OR (
    SELECT COUNT(*) FROM json_each(NEW.definition_hashes_json)
) <> NEW.baseline_tool_count
OR EXISTS (
    SELECT 1 FROM json_each(NEW.reasons_json)
    WHERE type <> 'text'
       OR value NOT IN (
           'agent_tool_missing', 'exposure_limit_exceeded',
           'agent_allowlist_changed',
           'required_tool_missing', 'route_agent_mismatch',
           'route_catalog_incomplete',
           'route_not_agent_filtered',
           'selected_tool_not_currently_authorized',
           'task_class_outside_agent_scope'
       )
)
BEGIN
    SELECT RAISE(ABORT, 'tool exposure projection integrity mismatch');
END;
CREATE TRIGGER tool_exposure_runs_start_guard
BEFORE INSERT ON tool_exposure_benchmark_runs
WHEN NEW.status <> 'running'
  OR NEW.recommendation IS NOT NULL
  OR NEW.summary_json IS NOT NULL
  OR NEW.completed_at IS NOT NULL
BEGIN
    SELECT RAISE(ABORT, 'tool exposure benchmark must start unsealed');
END;
CREATE TRIGGER tool_exposure_runs_terminal_guard
BEFORE UPDATE ON tool_exposure_benchmark_runs
WHEN NOT (
    OLD.status = 'running'
    AND NEW.status IN (
        'rejected', 'insufficient_evidence'
    )
    AND NEW.id = OLD.id
    AND NEW.task_class = OLD.task_class
    AND NEW.agent_spec_hash = OLD.agent_spec_hash
    AND NEW.catalog_hash = OLD.catalog_hash
    AND NEW.selector_hash = OLD.selector_hash
    AND NEW.dataset_hash = OLD.dataset_hash
    AND NEW.model_hash = OLD.model_hash
    AND NEW.settings_hash = OLD.settings_hash
    AND NEW.evaluator_hash = OLD.evaluator_hash
    AND NEW.seed = OLD.seed
    AND NEW.expected_cases = OLD.expected_cases
    AND NEW.quality_margin_micros = OLD.quality_margin_micros
    AND NEW.created_at = OLD.created_at
    AND NEW.recommendation IS NOT NULL
    AND NEW.summary_json IS NOT NULL
    AND NEW.completed_at IS NOT NULL
    AND (
        SELECT COUNT(*) FROM tool_exposure_benchmark_trials
        WHERE run_id = OLD.id
    ) = OLD.expected_cases * 2
    AND (
        SELECT COUNT(DISTINCT case_hash)
        FROM tool_exposure_benchmark_trials
        WHERE run_id = OLD.id
    ) = OLD.expected_cases
    AND NOT EXISTS (
        SELECT case_hash
        FROM tool_exposure_benchmark_trials
        WHERE run_id = OLD.id
        GROUP BY case_hash
        HAVING COUNT(*) <> 2
           OR COUNT(DISTINCT arm) <> 2
           OR COUNT(DISTINCT projection_id) <> 1
    )
    AND (
        (NEW.status='rejected'
            AND NEW.recommendation='reject_dynamic_exposure')
        OR (NEW.status='insufficient_evidence'
            AND NEW.recommendation='collect_verified_receipts')
    )
)
BEGIN
    SELECT RAISE(ABORT, 'tool exposure benchmark cannot be sealed');
END;
CREATE TRIGGER tool_exposure_runs_no_delete
BEFORE DELETE ON tool_exposure_benchmark_runs
BEGIN
    SELECT RAISE(ABORT, 'tool exposure benchmark runs are retained');
END;
CREATE TRIGGER tool_exposure_cases_running_insert
BEFORE INSERT ON tool_exposure_benchmark_cases
WHEN NOT EXISTS (
    SELECT 1 FROM tool_exposure_benchmark_runs
    WHERE id=NEW.run_id AND status='running'
)
OR NEW.sequence > (
    SELECT expected_cases FROM tool_exposure_benchmark_runs WHERE id=NEW.run_id
)
BEGIN
    SELECT RAISE(ABORT, 'tool exposure benchmark cases are sealed');
END;
CREATE TRIGGER tool_exposure_cases_no_update
BEFORE UPDATE ON tool_exposure_benchmark_cases
BEGIN
    SELECT RAISE(ABORT, 'tool exposure benchmark cases are immutable');
END;
CREATE TRIGGER tool_exposure_cases_no_delete
BEFORE DELETE ON tool_exposure_benchmark_cases
BEGIN
    SELECT RAISE(ABORT, 'tool exposure benchmark cases are retained');
END;
CREATE TRIGGER tool_exposure_trials_running_insert
BEFORE INSERT ON tool_exposure_benchmark_trials
WHEN NOT EXISTS (
    SELECT 1 FROM tool_exposure_benchmark_runs
WHERE id=NEW.run_id AND status='running'
)
OR NOT EXISTS (
    SELECT 1
    FROM tool_exposure_benchmark_runs AS run
    JOIN tool_exposure_projections AS projection
      ON projection.id=NEW.projection_id
    WHERE run.id=NEW.run_id
      AND projection.status='available'
      AND projection.task_class=run.task_class
      AND projection.agent_spec_hash=run.agent_spec_hash
      AND projection.catalog_hash=run.catalog_hash
      AND projection.selector_hash=run.selector_hash
)
OR (
    SELECT COUNT(*) FROM tool_exposure_benchmark_trials
    WHERE run_id=NEW.run_id
) >= (
    SELECT expected_cases * 2 FROM tool_exposure_benchmark_runs
    WHERE id=NEW.run_id
)
BEGIN
    SELECT RAISE(ABORT, 'tool exposure benchmark is sealed');
END;
CREATE TRIGGER tool_exposure_trials_no_update
BEFORE UPDATE ON tool_exposure_benchmark_trials
BEGIN
    SELECT RAISE(ABORT, 'tool exposure trials are immutable');
END;
CREATE TRIGGER tool_exposure_trials_no_delete
BEFORE DELETE ON tool_exposure_benchmark_trials
BEGIN
    SELECT RAISE(ABORT, 'tool exposure trials are retained');
END;
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

    @staticmethod
    def _apply_migration_28(connection: sqlite3.Connection) -> None:
        try:
            connection.execute("BEGIN IMMEDIATE")
            for statement in MIGRATION_28_SQL.split(";"):
                if statement.strip():
                    connection.execute(statement)
            connection.execute(
                """
                INSERT INTO schema_migrations(version, applied_at)
                VALUES (28, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
                """
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise

    @staticmethod
    def _apply_migration_29(connection: sqlite3.Connection) -> None:
        try:
            connection.execute("BEGIN IMMEDIATE")
            for statement in MIGRATION_29_SQL.split(";"):
                if statement.strip():
                    connection.execute(statement)
            connection.execute(
                """
                INSERT INTO schema_migrations(version, applied_at)
                VALUES (29, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
                """
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise

    @staticmethod
    def _apply_migration_30(connection: sqlite3.Connection) -> None:
        try:
            connection.execute("BEGIN IMMEDIATE")
            for statement in MIGRATION_30_SQL.split(";"):
                if statement.strip():
                    connection.execute(statement)
            connection.execute(
                """
                INSERT INTO schema_migrations(version, applied_at)
                VALUES (30, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
                """
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise

    @staticmethod
    def _apply_migration_31(connection: sqlite3.Connection) -> None:
        try:
            connection.execute("BEGIN IMMEDIATE")
            for statement in MIGRATION_31_SQL.split(";"):
                if statement.strip():
                    connection.execute(statement)
            connection.execute(
                """
                INSERT INTO schema_migrations(version, applied_at)
                VALUES (31, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
                """
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise

    @staticmethod
    def _apply_migration_32(connection: sqlite3.Connection) -> None:
        try:
            connection.execute("BEGIN IMMEDIATE")
            for statement in MIGRATION_32_SQL.split(";"):
                if statement.strip():
                    connection.execute(statement)
            connection.execute(
                """
                INSERT INTO schema_migrations(version, applied_at)
                VALUES (32, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
                """
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise

    @staticmethod
    def _apply_migration_33(connection: sqlite3.Connection) -> None:
        try:
            connection.execute("BEGIN IMMEDIATE")
            for statement in MIGRATION_33_SQL.split(";"):
                if statement.strip():
                    connection.execute(statement)
            connection.execute(
                """
                INSERT INTO schema_migrations(version, applied_at)
                VALUES (33, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
                """
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise

    @staticmethod
    def _apply_migration_34(connection: sqlite3.Connection) -> None:
        try:
            connection.execute("BEGIN IMMEDIATE")
            for statement in MIGRATION_34_SQL.split(";"):
                if statement.strip():
                    connection.execute(statement)
            connection.execute(
                """
                INSERT INTO schema_migrations(version, applied_at)
                VALUES (34, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
                """
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise

    @staticmethod
    def _apply_migration_35(connection: sqlite3.Connection) -> None:
        try:
            connection.execute("BEGIN IMMEDIATE")
            for statement in MIGRATION_35_SQL.split(";"):
                if statement.strip():
                    connection.execute(statement)
            connection.execute(
                """
                INSERT INTO schema_migrations(version, applied_at)
                VALUES (35, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
                """
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise

    @staticmethod
    def _apply_migration_36(connection: sqlite3.Connection) -> None:
        try:
            connection.execute("BEGIN IMMEDIATE")
            for statement in MIGRATION_36_SQL.split(";"):
                if statement.strip():
                    connection.execute(statement)
            connection.execute(
                """
                INSERT INTO schema_migrations(version, applied_at)
                VALUES (36, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
                """
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise

    @staticmethod
    def _apply_migration_37(connection: sqlite3.Connection) -> None:
        try:
            connection.execute("BEGIN IMMEDIATE")
            for statement in MIGRATION_37_SQL.split(";"):
                if statement.strip():
                    connection.execute(statement)
            connection.execute(
                """
                INSERT INTO schema_migrations(version, applied_at)
                VALUES (37, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
                """
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise

    @staticmethod
    def _apply_migration_38(connection: sqlite3.Connection) -> None:
        try:
            connection.execute("BEGIN IMMEDIATE")
            for statement in MIGRATION_38_SQL.split(";"):
                if statement.strip():
                    connection.execute(statement)
            connection.execute(
                """
                INSERT INTO schema_migrations(version, applied_at)
                VALUES (38, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
                """
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise

    @staticmethod
    def _apply_migration_39(connection: sqlite3.Connection) -> None:
        try:
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("BEGIN IMMEDIATE")
            for statement in MIGRATION_39_SQL.split(";"):
                if statement.strip():
                    connection.execute(statement)
            connection.execute(
                """
                INSERT INTO schema_migrations(version, applied_at)
                VALUES (39, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
                """
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise

    @staticmethod
    def _apply_migration_40(connection: sqlite3.Connection) -> None:
        try:
            connection.execute("PRAGMA foreign_keys = ON")
            connection.executescript(
                "BEGIN IMMEDIATE;\n"
                + MIGRATION_40_SQL
                + """
                INSERT INTO schema_migrations(version, applied_at)
                VALUES (40, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));
                COMMIT;
                """
            )
        except Exception:
            connection.rollback()
            raise

    @staticmethod
    def _apply_migration_41(connection: sqlite3.Connection) -> None:
        try:
            connection.execute("PRAGMA foreign_keys = ON")
            connection.executescript(
                "BEGIN IMMEDIATE;\n"
                + MIGRATION_41_SQL
                + """
                INSERT INTO schema_migrations(version, applied_at)
                VALUES (41, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));
                COMMIT;
                """
            )
        except Exception:
            connection.rollback()
            raise

    @staticmethod
    def _apply_migration_42(connection: sqlite3.Connection) -> None:
        try:
            connection.execute("PRAGMA foreign_keys = ON")
            connection.executescript(
                "BEGIN IMMEDIATE;\n"
                + MIGRATION_42_SQL
                + """
                INSERT INTO schema_migrations(version, applied_at)
                VALUES (42, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));
                COMMIT;
                """
            )
        except Exception:
            connection.rollback()
            raise

    @staticmethod
    def _apply_migration_43(connection: sqlite3.Connection) -> None:
        try:
            connection.execute("PRAGMA foreign_keys = ON")
            connection.executescript(
                "BEGIN IMMEDIATE;\n"
                + MIGRATION_43_SQL
                + """
                INSERT INTO schema_migrations(version, applied_at)
                VALUES (43, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));
                COMMIT;
                """
            )
        except Exception:
            connection.rollback()
            raise

    @staticmethod
    def _apply_migration_44(connection: sqlite3.Connection) -> None:
        try:
            connection.execute("PRAGMA foreign_keys = ON")
            connection.executescript(
                "BEGIN IMMEDIATE;\n"
                + MIGRATION_44_SQL
                + """
                INSERT INTO schema_migrations(version, applied_at)
                VALUES (44, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));
                COMMIT;
                """
            )
        except Exception:
            connection.rollback()
            raise

    @staticmethod
    def _apply_migration_45(connection: sqlite3.Connection) -> None:
        try:
            connection.execute("PRAGMA foreign_keys = ON")
            connection.executescript(
                "BEGIN IMMEDIATE;\n"
                + MIGRATION_45_SQL
                + """
                INSERT INTO schema_migrations(version, applied_at)
                VALUES (45, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));
                COMMIT;
                """
            )
        except Exception:
            connection.rollback()
            raise

    @staticmethod
    def _apply_migration_46(connection: sqlite3.Connection) -> None:
        try:
            connection.execute("PRAGMA foreign_keys = ON")
            connection.executescript(
                "BEGIN IMMEDIATE;\n"
                + MIGRATION_46_SQL
                + """
                INSERT INTO schema_migrations(version, applied_at)
                VALUES (46, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));
                COMMIT;
                """
            )
        except Exception:
            connection.rollback()
            raise

    @staticmethod
    def _apply_migration_47(connection: sqlite3.Connection) -> None:
        try:
            connection.execute("PRAGMA foreign_keys = ON")
            connection.executescript(
                "BEGIN IMMEDIATE;\n"
                + MIGRATION_47_SQL
                + """
                INSERT INTO schema_migrations(version, applied_at)
                VALUES (47, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));
                COMMIT;
                """
            )
        except Exception:
            connection.rollback()
            raise

    @staticmethod
    def _apply_migration_48(connection: sqlite3.Connection) -> None:
        try:
            connection.execute("PRAGMA foreign_keys = ON")
            connection.executescript(
                "BEGIN IMMEDIATE;\n"
                + MIGRATION_48_SQL
                + """
                INSERT INTO schema_migrations(version, applied_at)
                VALUES (48, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));
                COMMIT;
                """
            )
        except Exception:
            connection.rollback()
            raise

    @staticmethod
    def _apply_migration_49(connection: sqlite3.Connection) -> None:
        try:
            connection.execute("PRAGMA foreign_keys = ON")
            connection.executescript(
                "BEGIN IMMEDIATE;\n"
                + MIGRATION_49_SQL
                + """
                INSERT INTO schema_migrations(version, applied_at)
                VALUES (49, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));
                COMMIT;
                """
            )
        except Exception:
            connection.rollback()
            raise

    @staticmethod
    def _apply_migration_50(connection: sqlite3.Connection) -> None:
        try:
            connection.execute("PRAGMA foreign_keys = ON")
            connection.executescript(
                "BEGIN IMMEDIATE;\n"
                + MIGRATION_50_SQL
                + """
                INSERT INTO schema_migrations(version, applied_at)
                VALUES (50, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));
                COMMIT;
                """
            )
        except Exception:
            connection.rollback()
            raise

    @staticmethod
    def _apply_migration_51(connection: sqlite3.Connection) -> None:
        try:
            connection.execute("PRAGMA foreign_keys = ON")
            connection.executescript(
                "BEGIN IMMEDIATE;\n"
                + MIGRATION_51_SQL
                + """
                INSERT INTO schema_migrations(version, applied_at)
                VALUES (51, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));
                COMMIT;
                """
            )
        except Exception:
            connection.rollback()
            raise

    @staticmethod
    def _apply_migration_52(connection: sqlite3.Connection) -> None:
        try:
            connection.execute("PRAGMA foreign_keys = ON")
            connection.executescript(
                "BEGIN IMMEDIATE;\n"
                + MIGRATION_52_SQL
                + """
                INSERT INTO schema_migrations(version, applied_at)
                VALUES (52, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));
                COMMIT;
                """
            )
        except Exception:
            connection.rollback()
            raise

    @staticmethod
    def _apply_migration_53(connection: sqlite3.Connection) -> None:
        try:
            connection.execute("PRAGMA foreign_keys = ON")
            connection.executescript(
                "BEGIN IMMEDIATE;\n"
                + MIGRATION_53_SQL
                + """
                INSERT INTO schema_migrations(version, applied_at)
                VALUES (53, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));
                COMMIT;
                """
            )
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
            if 28 in status.pending_versions:
                self._apply_migration_28(connection)
            if 29 in status.pending_versions:
                self._apply_migration_29(connection)
            if 30 in status.pending_versions:
                self._apply_migration_30(connection)
            if 31 in status.pending_versions:
                self._apply_migration_31(connection)
            if 32 in status.pending_versions:
                self._apply_migration_32(connection)
            if 33 in status.pending_versions:
                self._apply_migration_33(connection)
            if 34 in status.pending_versions:
                self._apply_migration_34(connection)
            if 35 in status.pending_versions:
                self._apply_migration_35(connection)
            if 36 in status.pending_versions:
                self._apply_migration_36(connection)
            if 37 in status.pending_versions:
                self._apply_migration_37(connection)
            if 38 in status.pending_versions:
                self._apply_migration_38(connection)
            if 39 in status.pending_versions:
                self._apply_migration_39(connection)
            if 40 in status.pending_versions:
                self._apply_migration_40(connection)
            if 41 in status.pending_versions:
                self._apply_migration_41(connection)
            if 42 in status.pending_versions:
                self._apply_migration_42(connection)
            if 43 in status.pending_versions:
                self._apply_migration_43(connection)
            if 44 in status.pending_versions:
                self._apply_migration_44(connection)
            if 45 in status.pending_versions:
                self._apply_migration_45(connection)
            if 46 in status.pending_versions:
                self._apply_migration_46(connection)
            if 47 in status.pending_versions:
                self._apply_migration_47(connection)
            if 48 in status.pending_versions:
                self._apply_migration_48(connection)
            if 49 in status.pending_versions:
                self._apply_migration_49(connection)
            if 50 in status.pending_versions:
                self._apply_migration_50(connection)
            if 51 in status.pending_versions:
                self._apply_migration_51(connection)
            if 52 in status.pending_versions:
                self._apply_migration_52(connection)
            if 53 in status.pending_versions:
                self._apply_migration_53(connection)
        finally:
            connection.close()
        return self.status()
