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
  -> immutable least-privilege worker specifications
  -> costed minimum-team topology proposals + scoped temporary workers
  -> verified topology outcomes + advisory reusable recipes
  -> progressive hierarchical plan + immutable editable revisions
  -> independent multi-judge evaluation + durable criterion disagreement
  -> bounded evidence-driven reflection + nine structured findings
  -> atomic ten-stage post-task learning + rollback isolation
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
AgentSpecs define scoped worker contracts without creating workers. Each
definition binds explicit budgets, permissions, model policy, termination,
verification, communication, and exact validated skill hashes.
The Agent Factory evaluates five bounded topologies and retains every estimate
and rejection before emitting proposed temporary AgentSpecs. It never spawns or
executes those workers.
Topology learning derives structures from retained plans, records verified and
failed outcomes, and recommends only compatible recipes with repeated evidence.
It cannot execute a recipe or override the factory.
The hierarchical planner selects existing skills, tools, and proposed agents,
allocates bounded resources, and blocks missing prerequisites. Coarse plans can
be refined or edited during execution through optimistic-lock revisions without
rewriting history.
Evaluation is a separate retained boundary. Deterministic graders ground
criterion pass/fail, optional LLM judges require explicit content transmission,
and model confidence cannot override a deterministic failure. Case contents are
represented in storage by hashes and counts while full judge and disagreement
records remain inspectable.
Reflection consumes those results plus explicit context attribution, model-cost,
tool-necessity, missing-information, and reusable-experience signals. It runs
once, emits recommendations only, and has no path to memory, skill, routing, or
task-result mutation.
The learning controller is the sole new orchestration boundary for post-task
learning. It uses one SQLite write transaction for evaluation, attribution,
efficiency, distillation, candidate creation, utility updates, routing advice,
and regression evidence. It writes proposals rather than activating memory,
skills, or routes, and never updates the retained execution result.
The model router keeps static price/capability profiles separate from verified
task-class outcomes. It selects the cheapest conservatively qualified model,
retains rejected candidates, and permits one evidence-backed escalation to a
historically stronger model. Routing never invokes a provider or treats a raw
model call as verified quality. Both escalation attempts and whether the second
improved the outcome remain auditable.

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

- Persistent embedding and graph indexes
- Automatic trace distillation
- Learned or autonomous promotion rules
- Sandboxed skill execution and signing
- Learned scoring weights

These features should be added only behind repeatable evaluations. Point-in-time
truth, governed memory writes, and explicitly approved consolidation are
implemented, along with reversible lifecycle garbage collection. Failure-memory
intelligence, experience distillation, the expanded context compiler, the
deterministic Token Economist, and conservative context attribution are
implemented, along with exactness-aware context compression and versioned skill
evolution. Learned budgeting remains deferred; skill evolution uses a fixed
no-regression policy with retained benchmark evidence and rollback.
Agent topology generation uses fixed, inspectable heuristics. Historical
recommendation uses fixed evidence thresholds; autonomous policy learning
remains deferred.
