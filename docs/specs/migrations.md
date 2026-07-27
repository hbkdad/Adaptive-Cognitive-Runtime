# Prompt 81: migration system

Prompt 81 was moved ahead of the expanded memory schema because live local
databases already exist.

## Implemented

- Persistent schema version history.
- Fresh databases bootstrap at the current schema.
- Existing outdated databases fail closed instead of upgrading when opened.
- `acr migrate` is the explicit upgrade action.
- Fixture-based upgrade regression from schema 2 through current schema 18.
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
- Newer-than-runtime schemas are rejected.

Destructive or structurally complex future migrations must continue to add
dedicated fixture-based upgrade and rollback tests before release.
