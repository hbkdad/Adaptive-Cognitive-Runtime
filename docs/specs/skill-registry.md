# Prompt 17: skill registry

The local SQLite registry admits validated Skill Format v1 packages, keeps them
quarantined by default, indexes bounded metadata, records lifecycle history, and
tracks realized performance.

## Commands

```powershell
acr skills list
acr skills inspect <skill>
acr skills search <query>
acr skills test <skill>
acr skills activate <skill>
acr skills quarantine <skill>
acr skills retire <skill>
acr skills history <skill>
```

`acr skills install <directory>` is the explicit admission command and
`acr skills validate <directory>` remains a non-mutating format check.

Admission recomputes the package digest and always assigns the runtime state
`quarantined`, regardless of the manifest's descriptive status. Re-admitting an
identical package is idempotent. Different content with the same skill ID and
version is rejected because released Semantic Versions are immutable.

## Search boundary

SQLite FTS5 indexes only skill ID, name, description, task classes, and
applicability. Instructions, scripts, examples, and assets are not loaded for
keyword search. Results return metadata and reasons, never instruction bodies.

An optional semantic index accepts only a query and limit and returns
`skill_id -> score` values. This allows a local embedding store to retrieve IDs
without loading every skill into model context. Scores must be bounded from zero
to one, and the default runtime clearly reports semantic search as unavailable.

## Verification and lifecycle

`skills test` revalidates format, layout, package identity, and verification
declarations. It deliberately does not execute package commands or scripts.
Activation is an explicit operator action and requires a successful static test
plus a fresh digest check, preventing activation after package mutation.
Retirement is terminal. Every admission, test, and state transition produces a
content-free history record.

This boundary is deliberate: a Python virtual environment isolates packages,
not operating-system permissions or network access. Executing untrusted test
code requires a later sandbox and permission broker.

## Performance

Conclusive context attribution updates:

- uses, successful uses, and failures;
- average selected skill tokens;
- average allocated model cost;
- average task latency;
- last-used time;
- task-class performance;
- model-specific performance.

Uncertain attribution does not change skill history. Cost is allocated
proportionally across selected skill-token overhead. Schema v13 stores registry
metadata, history, performance dimensions, and the FTS5 index.
