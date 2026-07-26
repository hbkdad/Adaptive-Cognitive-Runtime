# ACR v0.1 architecture

## Vertical slice

```text
task
  -> scoped FTS5 memory retrieval
  -> active-skill retrieval
  -> relevance/confidence/importance/recency/history scoring
  -> utility-per-token ranking
  -> greedy hard-budget compilation
  -> context bundle + attribution rows
  -> external executor or human
  -> success, critic score, useful-block feedback
  -> memory/skill statistics and wasted-token telemetry
```

The storage model keeps raw claims separate from task telemetry. Superseding a
claim closes its validity window and preserves history. Skills have a lifecycle
state; only `active` skills are selectable.

## Scoring

The first transparent heuristic is:

```text
utility =
  0.45 * lexical_relevance
  + 0.20 * confidence
  + 0.15 * importance
  + 0.10 * recency
  + 0.10 * historical_success

token_roi = utility / estimated_tokens
```

Weights are configuration candidates, not learned truth. Telemetry exists so a
later evaluator can compare scoring variants on a fixed task suite.

## Deliberately deferred

- Model calls and provider routing
- Embeddings and graph storage
- Automatic trace distillation
- Candidate-memory promotion rules
- Sandboxed skill execution and signing
- Multi-agent topology generation
- Learned scoring weights

These features should be added only behind repeatable evals. The next milestone
is an executor adapter plus a small benchmark that compares ACR-selected context
against full-context and no-memory baselines.

