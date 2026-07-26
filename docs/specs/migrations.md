# Prompt 81: migration system

Prompt 81 was moved ahead of the expanded memory schema because live local
databases already exist.

## Implemented

- Persistent schema version history.
- Fresh databases bootstrap at the current schema.
- Existing outdated databases fail closed instead of upgrading when opened.
- `acr migrate` is the explicit upgrade action.
- Upgrade-path regression test from schema 1 to schema 2.
- Newer-than-runtime schemas are rejected.

Destructive or structurally complex future migrations must add backup guidance
and dedicated fixture-based upgrade tests before release.

