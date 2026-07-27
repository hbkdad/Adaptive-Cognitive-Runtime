# Prompt 81: migration system

Prompt 81 was moved ahead of the expanded memory schema because live local
databases already exist.

## Implemented

- Persistent schema version history.
- Fresh databases bootstrap at the current schema.
- Existing outdated databases fail closed instead of upgrading when opened.
- `acr migrate` is the explicit upgrade action.
- Fixture-based upgrade regression from schema 2 through current schema 34.
- Coherent SQLite backups before every pending migration batch.
- Transactional rollback tests for the memory rebuild, retention/audit upgrade,
  consolidation-audit upgrade, lifecycle/GC upgrade, and failure-intelligence
  upgrade, and experience-distillation upgrade.
  Schema v9 transactionally expands context attribution source types.
  Schema v10 adds persisted Token Economist budget plans and telemetry.
  Schema v11 adds evidence-fused context attribution records.
  Schema v12 adds per-block compression strategy and token-savings telemetry.
  Schema v13 adds registry metadata, lifecycle history, dimensional performance,
  and the metadata-only skill FTS5 index.
  Schema v14 persists router runs, selected and rejected candidates, compiler
  selections, score evidence, and conservative attribution outcomes.
  Schema v15 persists repeated-success generation plans, complete candidate
  specifications, evidence references, generated packages, and admission results.
  Schema v16 retains digest-bound validation runs and all ten ordered stage
  results, including policy, incumbent, evidence metrics, and promotion state.
  It quarantines previously active skills so no pre-Prompt-20 activation is
  silently grandfathered past the mandatory gate.
  Schema v17 retains immutable source/candidate skill evolution runs,
  multi-objective comparisons, promotion decisions, and explicit rollbacks.
  Schema v18 retains bounded skill-pair analysis, comparison evidence, advisory
  recommendations, and a database-level prohibition on automatic actions.
  Schema v19 isolates parameterized skill genomes, controlled mutations,
  paired tournament evidence, statistical decisions, and experimental winners
  from production skill behavior.
  Schema v20 retains immutable AgentSpecs, canonical hashes, scoped worker
  responsibilities, and exact resolved skill-version evidence.
  Schema v21 retains Agent Factory requests, all costed topology candidates,
  rejection evidence, and proposed temporary worker specifications.
  Schema v22 retains reusable successful topology recipes and every reported
  run's derived structure, verification state, tokens, latency, and quality.
  Schema v23 retains progressive hierarchical plans, current phase pointers,
  canonical immutable revision snapshots, reasons, and parent revisions.
  Schema v24 retains minimized evaluation runs, ordered judge results, and
  criterion-level grounding and disagreement.
  Schema v25 retains one-pass reflections and exactly nine ordered structured
  findings under hard depth and count constraints.
  Schema v26 retains atomic learning runs, ten ordered stages, proposed memory
  and routing candidates, and review-only regression evidence.
- Newer-than-runtime schemas are rejected.

Destructive or structurally complex future migrations must continue to add
dedicated fixture-based upgrade and rollback tests before release.
