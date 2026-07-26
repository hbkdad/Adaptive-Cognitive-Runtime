# ACR v0.1 architecture

## Vertical slice

```text
task
  -> over-fetched scoped FTS5 + broad memory candidates
  -> optional semantic similarity adapter
  -> duplicate and contradiction analysis
  -> effective-time and point-in-time truth resolution
  -> configurable explained memory scoring
  -> active-skill retrieval
  -> utility-per-token ranking
  -> greedy hard-budget compilation
  -> context bundle + attribution rows
  -> deterministic or local-model executor
  -> success, critic score, useful-block feedback
  -> memory/skill statistics and wasted-token telemetry
```

The storage model keeps raw claims separate from task telemetry. Superseding a
claim closes its validity window and preserves history. Skills have a lifecycle
state; only `active` skills are selectable.

## Memory retrieval scoring

Prompt 5 uses a configurable weighted average of:

```text
keyword + optional semantic similarity + scope + recency + temporal validity
+ confidence + historical utility + importance + task similarity
+ source reliability
```

Unavailable semantic retrieval is reported and its weight is removed from the
normalization rather than treated as a zero score. Every ranked record includes
its component breakdown, selection explanation, conflict IDs, and any rejection
reason. Context compilation still compares selected memories against skills by
utility per estimated token.

## Deliberately deferred

- General provider routing
- Persistent embedding and graph indexes
- Automatic trace distillation
- Candidate-memory promotion rules
- Sandboxed skill execution and signing
- Multi-agent topology generation
- Learned scoring weights

These features should be added only behind repeatable evaluations. Point-in-time
truth is implemented; governed memory-write decisions are next.
