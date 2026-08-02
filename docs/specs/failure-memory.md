# Failure intelligence (Prompt 10)

Failure memory is structured evidence used before planning. It is not a
blacklist and does not turn one bad outcome into a permanent prohibition.
Prompt 114 adds an authority-free negative-procedure projection for narrowly
scoped, repeated deterministic evidence; see
`docs/specs/negative-procedures.md`.

## Record shape

Each first-class failure record links to an underlying `failure` memory and
stores:

- task class and strategy attempted;
- structured environment and symptoms;
- root cause when known;
- failed action;
- bounded error type and message;
- avoidance rule;
- confidence and evidence through the linked memory;
- occurrence count and first/last observation times;
- unresolved/resolved state;
- successful remediation memory and resolution when resolved.

Pre-schema-7 free-form failure memories are not silently converted because they
do not contain enough evidence to reconstruct strategy, environment, failed
action, or root cause accurately. They remain available through ordinary memory
retrieval until an operator records a verified structured failure.

Exception messages are limited to 4 KB and raw stack traces are not copied into
default planning context. Evidence is mandatory. Exact repeats reinforce one
record, merge evidence, and increase the repetition component. A recurrence
reopens a previously resolved record while preserving its remediation link as
historical evidence.

## Operator workflow

```powershell
python -m acr_runtime.cli --db .acr/acr.db failure record `
  --task-class "sqlite migration" `
  --strategy "rebuild FTS while writers are active" `
  --environment '{"platform":"windows","database":"sqlite"}' `
  --symptom "database locked" `
  --failed-action "rebuild memories_fts" `
  --error-type "sqlite3.OperationalError" `
  --root-cause "an active writer held the lock" `
  --avoidance-rule "quiesce writers before rebuilding FTS" `
  --confidence 0.95 --evidence run-123 --scope my-project

python -m acr_runtime.cli --db .acr/acr.db failure query `
  "migrate the SQLite FTS index" --task-class "sqlite migration" `
  --strategy "rebuild FTS" --scope my-project

python -m acr_runtime.cli --db .acr/acr.db failure negatives `
  --scope my-project --task-class "sqlite migration"

python -m acr_runtime.cli --db .acr/acr.db failure resolve <FAILURE_ID> `
  --resolution "Stopped writers, migrated, verified, then restarted." `
  --remediation-memory <CONFIRMED_EVIDENCE_BACKED_MEMORY_ID>
```

Use `failure show <FAILURE_ID>` to inspect the complete structured record.

## Planning influence

The runtime `run` path queries failure intelligence before calling the planner.
The task supplies scope, task class, optional strategy, and a JSON environment:

```powershell
python -m acr_runtime.cli --db .acr/acr.db run "Migrate the FTS index" `
  --model qwen2.5-coder:1.5b --scope my-project `
  --task-class "sqlite migration" --strategy "rebuild FTS" `
  --environment '{"platform":"windows","database":"sqlite"}'
```

Matching is deterministic and explained. It combines task-class similarity,
objective overlap, strategy similarity, environment overlap, confidence, and a
bounded repetition factor. Resolved failures retain historical value but have a
lower avoidance weight and point planning toward the remediation.

Normal failures add weighted planning constraints. Absolute blocking requires
all of the following:

- the record was explicitly marked deterministic;
- it remains unresolved;
- confidence is at least 0.95;
- at least three occurrences and three distinct evidence references exist;
- analogy similarity is at least 0.75;
- an avoidance rule is present.

Planning-advice telemetry contains only record IDs, weights, counts, and the
blocking flag. It does not copy failure text into telemetry.
