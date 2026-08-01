# Prompt 112: long-horizon project state

Prompt 112 adds a structured local project ledger that survives process and
conversation boundaries. It does not synthesize project history and does not
write semantic memory.

## Ownership boundary

SQLite tables prefixed `project_state_` own explicit operator-authored project
state. They are separate from:

- execution `tasks`, which describe individual runtime tasks;
- semantic and decision memory, which are retrieval evidence;
- telemetry, which measures runs rather than project intent;
- conversational summaries, which are not authoritative state.

The ledger stores project name, objective, scope, lifecycle, and revision plus
bounded typed items:

- milestone;
- completed work;
- decision;
- blocker;
- dependency;
- technical debt;
- benchmark;
- next recommended work.

Every item has a stable UUID, status, priority, evidence references, revision,
and explicit same-project dependency edges. The schema caps text and JSON
sizes, while the controller caps each project at 512 items, 32 dependencies per
item, and 16 evidence references per item.

## Mutation contract

All writes require an actor and an expected project revision. Item updates also
require the expected item revision. A stale writer fails instead of silently
overwriting another session.

Project item state changes are accepted only while the project is `active`.
Projects may move through:

- `active` to `paused`, `completed`, or `archived`;
- `paused` to `active`, `completed`, or `archived`;
- `completed` to `active` or `archived`;
- `archived` to no other state.

Kinds carry additional invariants. Completed-work and decision items are
completed facts, active blockers are `blocked`, and completed benchmarks need
an evidence reference. Dependencies must resolve inside the same project and
must remain acyclic.

Safe Mode blocks `project_state_write` while retaining project inspection.
Inputs are secret-scanned and treated as untrusted operator data. No stored
text receives instruction authority.

## History and privacy

Current project and item state is mutable only through the controller.
`project_state_events` is an append-only audit stream protected by SQLite
triggers. Events contain the changed field names or bounded state values, not
objectives, item prose, prompts, or source bodies. The actor is retained only
as a SHA-256 hash.

Snapshots return structured fields and at most 50 recent events. They do not
generate or retain narrative project summaries.

## Deterministic next work

`next_work` is an explicit item kind, not a model inference. Ranking is
deterministic:

1. in-progress before planned before blocked;
2. higher priority first;
3. stable creation and identifier order.

A candidate is ready only when it is not explicitly blocked and every
dependency is completed. Unready candidates remain visible with structured
`blocked_by` records.

## CLI

```powershell
python -m acr_runtime.cli --db .acr/acr.db project create `
  examples/project-state/project.json --actor operator:miche
python -m acr_runtime.cli --db .acr/acr.db project item-add `
  adaptive-cognitive-runtime examples/project-state/next-work.json `
  --expected-project-revision 1 --actor operator:miche
python -m acr_runtime.cli --db .acr/acr.db project show `
  adaptive-cognitive-runtime
python -m acr_runtime.cli --db .acr/acr.db project next `
  adaptive-cognitive-runtime
```

`item-update` consumes the full bounded update schema so omitted fields cannot
silently erase state. `status` changes project lifecycle with an expected
revision. `list` returns project headers only.

## Non-goals

Prompt 112 does not:

- infer project truth from a chat or repository;
- copy tasks, memories, benchmark bodies, or decision prose automatically;
- grant project text model, tool, or instruction authority;
- execute next work;
- send state to a network service;
- delete retained events;
- replace source-control history or issue tracking.

Future importers or model-facing summaries must preserve the untrusted-data
label and require separately authorized, evidence-backed writes.
