# Prompt 81: migration system

Prompt 81 was moved ahead of the expanded memory schema because live local
databases already exist.

## Implemented

- Persistent schema version history.
- Fresh databases bootstrap at the current schema.
- Existing outdated databases fail closed instead of upgrading when opened.
- `acr migrate` is the explicit upgrade action.
- Fixture-based upgrade regression from schema 2 through current schema 9.
- Coherent SQLite backups before every pending migration batch.
- Transactional rollback tests for the memory rebuild, retention/audit upgrade,
  consolidation-audit upgrade, lifecycle/GC upgrade, and failure-intelligence
  upgrade, and experience-distillation upgrade.
  Schema v9 transactionally expands context attribution source types.
- Newer-than-runtime schemas are rejected.

Destructive or structurally complex future migrations must continue to add
dedicated fixture-based upgrade and rollback tests before release.
