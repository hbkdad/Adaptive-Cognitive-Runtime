# Foundation milestone specification

## Acceptance criteria

- Existing memory, context compilation, skill quarantine, and telemetry tests
  continue to pass.
- Configuration is typed and can be supplied through environment variables.
- `acr doctor` reports Python, filesystem, SQLite, migration, FTS5, provider,
  local-model, and skill-directory health without exposing secrets.
- `acr status` reports schema and local store counts.
- CLI inspection commands exist for memory, skills, agents, models, and
  telemetry.
- Architecture, contribution, security, and ADR documentation exists.
- Runtime state, secrets, and generated caches are excluded from source control.
- No model provider, web framework, ORM, or autonomous behavior is added.

