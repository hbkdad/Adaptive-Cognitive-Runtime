# Experience distillation (Prompt 11)

Experience distillation converts a significant, structured raw trajectory into
small governed candidates. It does not replace or delete the raw trajectory.

## Raw trace boundary

Raw traces are stored in `experience_traces`, not in the memory table, so they
cannot enter default context retrieval. Input is limited to 5 MB before JSON
parsing and each event is limited to 100 KB. A trace records scope, task class,
outcome, significance, ordered events, token estimate, and optional task ID.

The event vocabulary is:

- `fact`
- `decision`
- `procedure`
- `failure`
- `environment`
- `tool_sequence`
- `candidate_skill`
- `observation`

Each event carries content, confidence, importance, durability, evidence, and
optional metadata. Observations remain raw-only. Procedures are classified as
successful only when the trace outcome is `succeeded`.

## Workflow

Prepare a bounded JSON file:

```json
{
  "events": [
    {
      "kind": "fact",
      "content": "The service uses SQLite FTS5.",
      "evidence": ["schema.sql"],
      "confidence": 0.95,
      "importance": 0.8
    }
  ]
}
```

Then capture, inspect, plan, and explicitly approve:

```powershell
python -m acr_runtime.cli --db .acr/acr.db experience capture trace.json `
  --scope my-project --task-class "database diagnosis" `
  --outcome succeeded --significance 0.9

python -m acr_runtime.cli --db .acr/acr.db experience show <TRACE_ID>
python -m acr_runtime.cli --db .acr/acr.db experience distill `
  --dry-run <TRACE_ID>
python -m acr_runtime.cli --db .acr/acr.db experience show <RUN_ID> --plan
python -m acr_runtime.cli --db .acr/acr.db experience distill `
  --approve <RUN_ID>
```

## Significance and extraction

The default significance threshold is 0.60. Durable events must also meet
confidence and importance thresholds. Exact normalized duplicates within a
trace merge their evidence and source indexes.

The seven distilled categories are durable facts, decisions, successful
procedures, failure patterns, environment discoveries, reusable tool
sequences, and candidate skills.

Compression is reported as:

```text
compression ratio = raw estimated tokens / distilled estimated tokens
reduction ratio = 1 - distilled estimated tokens / raw estimated tokens
```

The raw estimate covers the complete stored JSON trajectory. The distilled
estimate covers the compact candidate content.

## Approval and safety

Planning persists candidates but changes neither memory nor skills. Approval:

- sends memory candidates through the governed write controller;
- preserves trace and distillation attribution in structured payloads;
- lets risky or low-value candidates be skipped by policy;
- registers candidate skills in quarantine;
- never deletes the raw trace.

The first extractor is deterministic `structured-v1`. A future model-backed
extractor must preserve the same schema, significance gate, approval boundary,
size limits, evidence requirements, and evaluations.
