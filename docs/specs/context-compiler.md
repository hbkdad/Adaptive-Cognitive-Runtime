# Context compiler (Prompt 12)

The compiler accepts a `ContextRequest` containing the task, hard token budget,
scope, and explicit candidates for system rules, relevant files, tool
definitions, agent state, and previous observations. Governed memory and active
skills are discovered internally.

The deterministic pipeline is:

```text
DISCOVER
FILTER
RANK
DEDUPLICATE
RESOLVE_TEMPORAL_CONFLICTS
DEPENDENCY EXPAND
COMPRESS
TOKEN PRICE
OPTIMIZE
ASSEMBLE
```

Temporal conflict handling is delegated to the point-in-time memory retriever.
Compression is exact whitespace normalization only; lossy semantic compression
remains deferred to Prompt 15.

Every selected `ContextBlock` exposes source type/ID, token cost, relevance,
confidence, expected utility, required status, selection reason, ROI, and
dependencies. Rejected candidates retain a machine-readable reason such as
`low_marginal_value`, `duplicate`, `missing_dependency`, or `token_budget`.

Required items and expanded dependencies are priced first. Compilation fails
closed when they exceed the remaining budget. Optional items are ordered by
utility per token with deterministic tie breakers. Zero-relevance and
low-marginal-value items are rejected even when budget remains.

The original `compile_context(task, scope, token_budget)` API remains available.
Advanced callers use `compile_context_request(ContextRequest(...))`.

Schema v9 expands context attribution to:

- `system_rule`
- `memory`
- `skill`
- `file`
- `tool`
- `agent_state`
- `observation`
