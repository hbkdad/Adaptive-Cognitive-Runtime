# ACR v0.1 architecture

## Vertical slice

```text
task
  -> over-fetched scoped FTS5 + broad memory candidates
  -> optional semantic similarity adapter
  -> duplicate and contradiction analysis
  -> effective-time and point-in-time truth resolution
  -> deterministic governed memory-write decisions
  -> dry-run and explicitly approved consolidation
  -> scored, pinned, and explicitly approved memory lifecycle transitions
  -> analogous-failure advice before planning
  -> isolated raw trajectories and approved experience distillation
  -> seven-source dependency-aware context compilation
  -> exactness-aware layered compression and artifact references
  -> configurable explained memory scoring
  -> active-skill retrieval
  -> validated composable Skill Format v1 packages
  -> metadata-only FTS5/semantic skill registry retrieval
  -> immutable candidate evolution + retained multi-objective comparison
  -> advisory redundancy, deprecation, merge, and composition evidence
  -> isolated parameter genomes + corrected paired benchmark tournaments
  -> adaptive input budget + output/reasoning headroom
  -> exact utility-maximizing hard-budget compilation
  -> context bundle + attribution rows
  -> deterministic or local-model executor
  -> model, trace, dependency, and evaluator attribution fusion
  -> contributed, ignored, misled, or uncertain context outcomes
  -> memory/skill statistics and wasted-token telemetry
```

The storage model keeps raw claims separate from task telemetry. Superseding a
claim closes its validity window and preserves history. Skills have a lifecycle
state; only `active` skills are selectable. Skill versions are immutable:
evolution creates a quarantined candidate, promotion keeps the prior validated
version available for explicit rollback, and a benchmark score alone cannot
authorize replacement. Pairwise merger analysis is bounded and produces
recommendations only; schema constraints prohibit automatic actions.
Genome parameters and tournament winners live in separate experimental tables;
they have no write path into production packages, registry state, or routing.

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
- Learned or autonomous promotion rules
- Sandboxed skill execution and signing
- Multi-agent topology generation
- Learned scoring weights

These features should be added only behind repeatable evaluations. Point-in-time
truth, governed memory writes, and explicitly approved consolidation are
implemented, along with reversible lifecycle garbage collection. Failure-memory
intelligence, experience distillation, the expanded context compiler, the
deterministic Token Economist, and conservative context attribution are
implemented, along with exactness-aware context compression and versioned skill
evolution. Learned budgeting remains deferred; skill evolution uses a fixed
no-regression policy with retained benchmark evidence and rollback.
