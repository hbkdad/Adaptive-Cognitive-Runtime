# Hybrid memory retrieval (Prompt 5)

## Contract

`HybridMemoryRetriever` accepts a task, query, scope, optional memory-type
restrictions, token budget, validity timestamp, confidence floor, and target
count. It returns every ranked candidate with a component breakdown, explanation,
conflict identifiers, selection state, and rejection reason.

## Pipeline

1. retrieve a bounded pool significantly larger than the target;
2. merge keyword FTS5 hits with broad in-scope candidates;
3. apply temporal, lifecycle, scope, type, and confidence filters in storage;
4. request optional semantic similarity for the bounded pool;
5. calculate configurable component scores;
6. rank deterministically;
7. flag subject-level contradictions;
8. reject exact normalized duplicates;
9. reject candidates with no keyword, task, or semantic relevance signal;
10. select within both target-count and token limits.

The broad candidate pass allows an embedding adapter to recover records that
share no query keywords. The current SQLite adapter remains fully functional
without embeddings. If a semantic provider fails, retrieval visibly reports the
failure and renormalizes over the available components.

## Default scoring components

- keyword match
- optional semantic similarity
- exact/global scope match
- type-aware knowledge recency
- temporal validity
- confidence
- historical usefulness
- importance
- task similarity
- configured source reliability

Weights, candidate multiplier, maximum candidates, minimum score, semantic
recency baseline, and source reliability are configuration values rather than
hidden constants. Prompt 115 scales that baseline through closed per-memory-type
profiles and preserves decisions until supersession. Score components remain
measurable so later benchmarks can compare weight sets. See
`docs/specs/knowledge-half-life.md`.

## Conflict boundary

Prompt 5 detects unresolved contradictions between currently valid records with
the same subject and scope. It does not silently discard either claim. Prompt 6
will add richer point-in-time truth, and the later knowledge-conflict engine can
compare evidence and resolve competing claims.

## CLI

```powershell
python -m acr_runtime.cli --db .acr/acr.db memory retrieve `
  "SQLite migration" --task "Diagnose a failed migration" `
  --scope project --type failure --budget 500 --limit 8
```
