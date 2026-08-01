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
  -> bounded parallel research + shared references + central synthesis
  -> relational claim-to-skill evidence graph + bounded recursive traversal
  -> read-only evidence-derived runtime explanations + explicit knowledge limits
  -> append-only human overrides + constrained runtime enforcement
  -> persistent Safe Mode containment + append-only blocked-action audit
  -> fixed-scope hash-manifested backup + verify-before-restore staging
  -> progressive hierarchical plan + immutable editable revisions
  -> independent multi-judge evaluation + durable criterion disagreement
  -> bounded evidence-driven reflection + nine structured findings
  -> atomic ten-stage post-task learning + rollback isolation
  -> adaptive input budget + output/reasoning headroom
  -> immutable risk-floored reasoning-depth policy bundle
  -> exact utility-maximizing hard-budget compilation
  -> context bundle + attribution rows
  -> deterministic or local-model executor
  -> model, trace, dependency, and evaluator attribution fusion
  -> role-tiered multi-model workflow + paired baseline measurement
  -> contributed, ignored, misled, or uncertain context outcomes
  -> memory/skill statistics and wasted-token telemetry
  -> exact-grant provider projection
  -> explicit ancestor-only cross-agent memory scopes
  -> assumption-aware architecture decision preflight
  -> explicit read-only knowledge-conflict classification
  -> pinned local MCP stdio transport or reviewed external MCP adapter
  -> thin Codex / Claude Code host instructions and hooks
```

The storage model keeps raw claims separate from task telemetry. Superseding a
claim closes its validity window and preserves history. Skills have a lifecycle
state; only `active` skills are selectable. Skill versions are immutable:
evolution creates a quarantined candidate, promotion keeps the prior validated
version available for explicit rollback, and a benchmark score alone cannot
authorize replacement. Pairwise merger analysis is bounded and produces
recommendations only; schema constraints prohibit automatic actions.
Memory sharing is governed by an immutable registered scope tree. Retrieval
filters to the queried leaf and its ancestors before any lexical or semantic
ranking, so shared repository/project knowledge flows down to agents while
sibling projects, tasks, and agents remain isolated.
Decision memories retain structured context, alternatives, rationale,
consequences, date, evidence, and named assumptions on the existing temporal
memory chain. Architecture preflight checks current assumptions and labels old
decisions stale or unverified instead of silently treating historical choices
as current instructions.
The knowledge-conflict engine compares evidence, timestamps, reliability,
confidence, and scope for disagreeing claims. It recognizes explicit
supersession and scope/time separation, but leaves overlapping unlinked claims
unresolved and never mutates memory or invents a preferred record.
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
The multi-model coordinator composes those routes into finite role-specialized
stage graphs. Model tiers are operator-declared, every stage retains its own
evidence-qualified route, and no provider is invoked. Repeated paired outcomes
compare verified specialized stages with one single-model baseline before any
benefit is reported; the report cannot mutate model profiles or routing policy.
The local model router composes, rather than replaces, that boundary. Ollama
discovery supplies local capability profiles, a five-class benchmark supplies
verified histories, and a content-free sensitivity policy filters cloud
candidates. Sensitive context cannot produce a cloud recommendation without
both configured escalation and an explicit permission reference; only the
reference hash is stored.
The tool registry is a separate immutable metadata boundary. Closed input and
output schemas, permissions, cost, latency, side effects, network/filesystem
access, and credential identifiers are inspectable before selection. Its
authorization check fails closed and requires explicit approval evidence for
destructive actions; it has no execution adapter.
The tool router consumes that registry boundary and retains relevance,
reliability, latency, cost, risk, grant checks, and rejected alternatives.
Known deterministic intents produce an explicit no-model-simulation signal.
Only evidenced append-only outcomes update task-class reliability, and routing
remains non-executable.
The plugin registry composes this boundary without introducing dynamic imports
or arbitrary entrypoints. Strict versioned manifests map namespaced plugin
capabilities only to immutable registered tool names. Exact dependency and
permission-union compatibility is retained before registration, while every
entrypoint route still passes through the central tool and capability checks.
Manifest permissions are requirements, never grants, and plugin routing remains
non-executable.
The capability controller is the authorization source for governed routes.
Tasks, agents, and skills receive exact scoped, expiring grants under default
deny. Task and agent delegation cannot expand capability, scope, delegation
authority, or lifetime; skills cannot grant. Parent-chain validation and
transitive revocation prevent orphaned delegated authority. Every authorization
decision is append-only and content-minimized.
The content-security controller assigns authority from provenance rather than
from content. Retrieved memory, web content, documents, and tool output are
always data-only; suspicious external items are excluded from compiled context,
while clean items are escaped inside a budgeted untrusted-data frame. Security
assessments retain hashes and provenance, not raw text. Exact one-shot trusted
workflow approvals are required before external content can derive memory or a
permission grant. Skill creation remains explicitly approved and quarantined,
and Agent Factory output remains a non-executing proposal.
Generated skill execution is a separate boundary. The Docker adapter resolves a
preinstalled image to its immutable local ID and runs with no network, no
writable host mounts, read-only package/root filesystems, a bounded tmpfs
workspace, non-root identity, dropped capabilities, built-in seccomp, private
namespaces, filtered environment, hard resource/time limits, and forced timeout
cleanup. Its self-test and content-minimized policy evidence are retained in the
existing skill-validation result log.
Secret resolution is another separate boundary. Runtime objects carry opaque
provider references, while exact `credential.use` scopes are derived from a
reference hash. Only after a permitted capability decision may the environment,
OS keyring, or configured external adapter be queried. A one-use lease minimizes
the plaintext lifetime, and schema 33 stores value-free access outcomes linked
to capability decisions. Boundary detectors reject secrets from durable memory,
skills, prompts, embeddings, traces, and failure context; imported material is
quarantined and telemetry is redacted before serialization.
The privacy engine attaches a closed sensitivity class and policy version to
every memory. Policies permit only exact providers, compute retention deadlines,
and make export an all-or-nothing operation. Memory-bearing local-model routes
are intersected with these provider decisions. Erasure is a stale-plan-safe
two-step workflow that scrubs content fields and the FTS index, preserves only
a content-free foreign-key tombstone, and records the active-database versus
backup-cleanup boundary.
The experiment controller is an opt-in control plane, not a production feature
flag. Immutable definitions and explicit starts precede deterministic
experiment-salted unit assignment. Only unit hashes, selected variants, and
evidenced numeric outcomes are retained. Reports expose allocation diagnostics
and baseline deltas but have no path to mutate production router, retrieval,
budget, planner, or skill defaults.

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
Research execution accepts only bounded integration adapters, keeps SQLite
writes in the coordinator, and treats paired serial/parallel results as
advisory evidence rather than automatic topology policy.
Safe Mode is evaluated by the mutation-owning controllers rather than only by
CLI dispatch. A persistent schema-58 state and an environment emergency latch
feed the same guard used by skill generation/evolution, genome mutation, Agent
Factory, privacy erasure, autonomous policy mutation, and write-capability
authorization. Recovery rollback stays outside the blocked optimization path.
Backup operates outside the live runtime facade so it can preserve incident
state without booting model or learning services. SQLite's backup API produces
the coherent database component; fixed skills, public-configuration, and
benchmark roots are then bound to a closed manifest. Restore never extracts
arbitrary archive paths or overwrites live state: it verifies into a new
same-parent staging tree and atomically publishes only a complete result.
Prompt 82 adds schema-60 immutable declarative plugin manifests, retained
compatibility validation, exact dependency/tool hashes, and entrypoint routing
through the existing default-deny permission controller. No plugin code is
loaded or executed by this layer.
Prompt 83 adds schema-61 durable recovery plans and per-step checkpoints.
Idempotent and known-failed retryable actions can resume within fixed attempt
limits; unknown outcomes for retryable, non-retryable, or destructive actions
fail closed for retained human review. A running checkpoint cannot be stolen
until an operator confirms the prior worker stopped, and completed steps are
never replayed.
Prompt 84 adds a schema-62 immutable audit projection for eight high-value
mutation events. Permanent source-table triggers make audit emission atomic
with each mutation, while minimal event details avoid copying memory content,
skill instructions, or credentials. Ordinary application state remains in its
authoritative tables and is not event sourced. A bounded read-only runtime and
CLI viewer support event inspection and summaries.
Prompt 85 adds a schema-63 opt-in local performance profiler. A monotonic
nanosecond clock measures database calls, retrieval, embeddings, model waits,
governed external tools, context compilation, and explicit serialization.
Profiles retain only hashed run identity, low-cardinality operation names,
durations, and exception types. Repeated measured evidence is required before a
category is labeled a bottleneck; the profiler neither changes runtime policy
nor introduces distributed infrastructure.
Prompt 86 adds an executable six-tier deterministic test manifest and CI
matrix. Every default test must be classified exactly once as unit,
integration, scenario, benchmark, security, or regression. Provider contracts
remain offline through mocks and fake transports. Repeated probabilistic model
quality measurement lives outside default unittest discovery and reports
descriptive evidence rather than becoming a flaky software assertion.
Prompt 87 adds a deterministic adversarial-memory security suite covering
prompt injection, false and contradictory claims, repetition, scope confusion,
malicious web text, obsolete facts, and oversized junk. Each case crosses the
public runtime boundary and the fixture independently asserts that no
attack-tagged source becomes confirmed memory.
Prompt 88 replaces the generated-skill adversarial stage's placeholder with a
closed six-attack Docker probe. The probe exercises credential, filesystem,
privilege, test, telemetry, and network boundaries without accepting generated
commands; manifest permission analysis remains a separate pre-execution gate.
Prompt 89 adds a local deterministic chaos suite over provider, model,
database, tool, memory, skill, transaction, and agent boundaries. Transient
connection, timeout, provider-unavailable, and SQLite lock faults are retained
as retryable task failures; all experiments assert bounded blast radius and
post-fault recovery.
Prompt 90 adds a deterministic documentation agent with a proposal, freshness
review, and exact-hash publication gate. Seven generated references derive from
the Python module graph, FastAPI route declarations, live CLI parser, skill
format constants, fresh SQLite schema, provider contracts, and diagnostics.
Prompt 91 adds an executable architecture guard over the explicitly declared
dependency-free core domain. Static AST inspection rejects direct and transitive
paths from core models, capability vocabulary, or scoring into web/presentation
code, concrete provider adapters, or SQLite implementation details. The policy
is dependency-free, machine-readable, tested against the real repository, and
enforced in CI before every deterministic tier.
Prompt 92 strengthens the compact root `AGENTS.md` contract into an ordered
feature-development checklist: inspect subsystem, interfaces, tests, and
architecture constraints; implement the minimum complete change; test narrowly
then broadly; inspect the diff; update documentation; and report real metrics.
Unrelated refactors remain explicitly out of scope.
Prompt 93 adds an evidence-first bug-fix role and host workflow. Reproduction,
exact bounded errors, boundary reduction, recent-change inspection, explicit
hypotheses, discriminating tests, root-cause repair, regression coverage, and
broader verification are ordered gates. The role is least-privilege by default;
failure memory still requires explicit authorization and verified evidence.
Prompt 94 adds a least-privilege security-review role and a strict,
secret-rejecting report validator. Reports cover eleven fixed threat categories
and require bounded evidence plus a multi-step attack path for every finding.
Blocking status is derived rather than author-controlled: only verified high or
critical findings block, while supported or speculative findings remain visible
for follow-up without stopping normal changes.
Prompt 95 adds a least-privilege performance-review role and a strict report
validator over token usage, model calls, retrieval volume, database queries,
tool calls, and latency. Existing profiler, telemetry, and token-waste systems
remain the evidence producers. Only repeated paired reductions with passing
quality and security gates become ranked opportunities; observed overhead and
missing measurements cannot be mislabeled as optimization wins.
Prompt 96 adds a least-privilege architecture-review role and a strict evidence
report over cohesion, coupling, interfaces, data ownership, testability,
failure modes, provider independence, and future replacement. The report
complements the AST guard. It derives rejection only from verified high or
critical design concerns or a verified needless abstraction with documented
complexity cost and a simpler removal path.
Prompt 97 adds a least-privilege release-engineer role and a strict,
content-minimized evidence manifest for nine ordered release gates. Every
completed gate is bound to a command, exit code, run reference, artifact hash,
and fresh timestamp. Readiness additionally requires an absent version tag and
enabled GitHub immutable releases. Validation never runs a gate, creates a tag,
or publishes a release.
Prompt 98 adds a least-privilege capability-gap analyst and strict proposal
validator over eight historical evidence families. BUILD, DEFER, and REJECT are
derived from certainty, repeated demand, measured cost, complexity, security
risk, and a complete benchmark plan. Discovery cannot create, install,
activate, or authorize a capability.
Prompt 99 adds a least-privilege research-scout role and a strict, source-bound
report validator over thirteen fixed research topics. Every source carries a
retrieval date and content digest. Findings keep novelty, claim maturity, code
availability, license assessment, ACR comparison, expected improvement, and
integration cost separate. Only an ACR reproduction reference can label an
external result reproduced; validation never browses, fetches, executes,
copies, installs, or authorizes an external artifact.
Prompt 100 remains deferred until meaningful varied operational usage exists;
fixture-scale telemetry cannot justify an optimization experiment. Prompt 101
adds a stateless deterministic capability designer. Closed traits select the
smallest sufficient code, tool, skill, strategy, workflow, agent, or subsystem
form. Cross-boundary requests fail closed, agent selection requires explicit
adaptive-goal evidence, and generated specifications never execute or authorize
their own implementation. Prompt 102 adds a conservative architectural
simplification checkpoint. Seven governance modules now reuse one secret-safe
bounded text primitive and five reuse its identical bounded-list contract.
Variant list semantics remain local, public and dynamic extension points remain
intact, and deletion candidates require compatibility or runtime evidence
beyond static non-reference.
