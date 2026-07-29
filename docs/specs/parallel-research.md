# Prompt 75: parallel research engine

Prompt 75 adds a bounded manager-worker research runtime. It is deliberately
separate from Agent Factory: the factory proposes a topology, while this engine
can execute an explicitly supplied Python adapter. The CLI can create
references and plans and inspect retained results, but cannot load or execute
arbitrary code.

## Contract

A research plan contains two to six caller-declared independent questions, a
two-to-six worker cap, a finite deadline, and optional linkage to an Agent
Factory plan whose selected topology is `researchers_synthesizer`. Duplicate
questions and unknown references fail closed. Declaring independence is an
auditable assertion; it is not treated as semantic proof.

References are immutable, content-addressed records. Each worker receives only
its question, scoped reference IDs, and a monotonic deadline. It resolves
content through a read-only in-memory snapshot. Workers receive neither other
workers' histories nor the runtime SQLite connection. The coordinator alone:

1. submits bounded work;
2. validates evidence references;
3. exactly deduplicates normalized claims;
4. ranks retained findings;
5. calls the central synthesizer; and
6. writes the immutable run.

The score is advisory and transparent:

`0.70 * mean source authority + 0.20 * corroboration + 0.10 * worker confidence`

Authority and worker confidence are inputs, not verified truth. Exact
deduplication merges evidence for the same normalized claim but never performs
semantic merging. Prompt 76 can extend these relational records into explicit
claim-to-evidence provenance without requiring a graph database.

Every reference first passes through the existing content-security controller
as untrusted web or document data. Its assessment ID is retained with the
reference. Suspicious instructions and secret-bearing material are quarantined
before raw content reaches research storage; ordinary evidence remains
data-only and never acquires instruction authority.

## Execution safety

The engine uses a capped `ThreadPoolExecutor` for bounded I/O-oriented adapters.
Workers never submit nested futures or wait on other workers. Python cannot
forcibly terminate a running thread, so adapters must cooperate with the
deadline and this is not a sandbox for untrusted or long-running code. On
timeout, pending work is cancelled, the failure is retained, and no partial
findings are committed.

SQLite writes remain centralized. This avoids sharing the runtime connection
across worker threads and preserves a single auditable transaction boundary.
The current design does not depend on WAL mode for correctness.

## Measurement

`ParallelResearchEngine.benchmark` runs the same plan and adapter once serially
and once in parallel. It retains both full run receipts, wall-clock latency,
and optional quality scores from an explicitly identified evaluator. Parallel
execution is supported only when this paired evidence improves latency or
quality. Without quality evidence, the report says so rather than treating
worker confidence or topology estimates as measured quality.

This paired benchmark is advisory. Production adoption still needs
representative repeated cases, cost/token accounting, and no-regression
evidence.

## CLI

```powershell
python -m acr_runtime.cli --db .acr/acr.db research reference-add reference.json
python -m acr_runtime.cli --db .acr/acr.db research plan research-plan.json
python -m acr_runtime.cli --db .acr/acr.db research plan-inspect PLAN_ID
python -m acr_runtime.cli --db .acr/acr.db research run-inspect RUN_ID
python -m acr_runtime.cli --db .acr/acr.db research benchmark-inspect BENCHMARK_ID
```

Execution remains a Python integration API so the CLI never becomes an
arbitrary adapter loader.
