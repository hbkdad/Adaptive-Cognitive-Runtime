# ACR — Adaptive Cognitive Runtime

ACR v0.1 is a local-first prototype of a context-economy layer for AI agents. It
has a deterministic core plus an explicitly configured local Ollama adapter. It
proves the governed lower layer that should exist before self-modification:

- eight evidence-backed memory types with governed lifecycle states;
- temporal supersession instead of destructive overwrites;
- scoped SQLite FTS5 plus optional semantic retrieval;
- configurable, explained ranking and hard context budgets;
- quarantined versus trusted skills;
- task, context-use, success, and wasted-token telemetry.

The first goal is measurable: select a smaller, more relevant context bundle and
record whether each selected block earned its token cost.

## Quick start

No third-party package is required.

```powershell
python -m unittest discover -s tests
python -m acr_runtime.cli doctor
python -m acr_runtime.cli --db .acr/acr.db migrate
python -m acr_runtime.cli --db .acr/demo.db status
python -m acr_runtime.cli --db .acr/demo.db demo
```

Run a bounded task through an installed local Ollama model:

```powershell
python -m acr_runtime.cli --db .acr/acr.db run `
  "Return exactly: ACR local execution works." `
  --model qwen2.5-coder:1.5b
```

Validate and run the first local benchmark:

```powershell
python -m acr_runtime.cli benchmark validate benchmarks/v1/core.jsonl
python -m acr_runtime.cli benchmark run benchmarks/v1/core.jsonl `
  --model qwen2.5-coder:1.5b --seed 0
```

Store a memory and compile context:

```powershell
python -m acr_runtime.cli remember semantic `
  "This project uses SQLite FTS5." --scope my-project `
  --confidence 0.98 --importance 0.9 --evidence pyproject.toml

python -m acr_runtime.cli compile `
  "Diagnose the SQLite memory index" --scope my-project --budget 500

python -m acr_runtime.cli memory retrieve "SQLite migration" `
  --task "Diagnose a failed database migration" `
  --scope my-project --budget 500 --limit 8

python -m acr_runtime.cli memory current database --scope my-project
python -m acr_runtime.cli memory at database "2026-03-01T00:00:00Z" `
  --scope my-project
python -m acr_runtime.cli memory history database --scope my-project

python -m acr_runtime.cli memory consider decision `
  "Use SQLite for local state" --scope my-project --subject database `
  --confidence 0.98 --usefulness 0.95 --stability 0.95 `
  --evidence architecture.md --trusted-source

python -m acr_runtime.cli memory decisions --limit 20

python -m acr_runtime.cli memory consolidate --dry-run --scope my-project
python -m acr_runtime.cli memory consolidate --approve <RUN_ID>

python -m acr_runtime.cli memory gc --dry-run --scope my-project
python -m acr_runtime.cli memory gc --approve <RUN_ID>
python -m acr_runtime.cli memory pin <ID> --reason "operator hold"
python -m acr_runtime.cli memory archive <ID>
python -m acr_runtime.cli memory restore <ID>

python -m acr_runtime.cli failure record `
  --task-class "sqlite migration" --strategy "rebuild FTS" `
  --symptom "database locked" --failed-action "rebuild index" `
  --error-type "sqlite3.OperationalError" `
  --avoidance-rule "stop writers before rebuilding" `
  --evidence run-123 --scope my-project

python -m acr_runtime.cli failure query "migrate SQLite FTS" `
  --task-class "sqlite migration" --strategy "rebuild FTS" `
  --scope my-project

python -m acr_runtime.cli experience capture trace.json `
  --scope my-project --task-class "database diagnosis" `
  --outcome succeeded --significance 0.9
python -m acr_runtime.cli experience distill --dry-run <TRACE_ID>
python -m acr_runtime.cli experience distill --approve <RUN_ID>

python -m acr_runtime.cli --db .acr/acr.db telemetry economy
python -m acr_runtime.cli --db .acr/acr.db telemetry attribution <TASK_ID>
python -m acr_runtime.cli --db .acr/acr.db telemetry compression
python -m acr_runtime.cli skills validate `
  examples/skill-v1/sqlite-diagnostics

python -m acr_runtime.cli --db .acr/acr.db skills install `
  examples/skill-v1/sqlite-diagnostics
python -m acr_runtime.cli --db .acr/acr.db skills search "SQLite FTS"
python -m acr_runtime.cli --db .acr/acr.db skills test sqlite-diagnostics
# Activation is available only after a fully passed Prompt 20 validation run.
python -m acr_runtime.cli --db .acr/acr.db skills activate sqlite-diagnostics
python -m acr_runtime.cli --db .acr/acr.db skills history sqlite-diagnostics
python -m acr_runtime.cli --db .acr/acr.db skills route `
  "Diagnose SQLite FTS5 integrity" `
  --task-class database-diagnostics --budget 300
python -m acr_runtime.cli --db .acr/acr.db telemetry routing
python -m acr_runtime.cli --db .acr/acr.db skills generate `
  --dry-run --scope my-project
python -m acr_runtime.cli --db .acr/acr.db skills generate `
  --approve <RUN_ID>
python -m acr_runtime.cli --db .acr/acr.db skills certify `
  generated-database-release-example
python -m acr_runtime.cli --db .acr/acr.db skills validation <RUN_ID>
python -m acr_runtime.cli --db .acr/acr.db skills promote <RUN_ID>
python -m acr_runtime.cli --db .acr/acr.db skills evolve `
  sqlite-diagnostics mutation.json
python -m acr_runtime.cli --db .acr/acr.db skills compare-evolution `
  <EVOLUTION_RUN_ID> comparison.json
python -m acr_runtime.cli --db .acr/acr.db skills promote-evolution `
  <EVOLUTION_RUN_ID>
python -m acr_runtime.cli --db .acr/acr.db skills rollback-evolution `
  <EVOLUTION_RUN_ID> --reason "Observed production regression"
python -m acr_runtime.cli --db .acr/acr.db skills merge-analysis `
  --skill sqlite-diagnostics --limit 25
python -m acr_runtime.cli --db .acr/acr.db skills merge-report `
  <MERGE_ANALYSIS_RUN_ID>
python -m acr_runtime.cli --db .acr/acr.db skills genome-create `
  sqlite-diagnostics examples/genome/parameters.json
python -m acr_runtime.cli --db .acr/acr.db skills genome-mutate `
  <BASELINE_GENOME_ID> examples/genome/mutation.json
python -m acr_runtime.cli --db .acr/acr.db skills genome-tournament `
  <BASELINE_GENOME_ID> <CANDIDATE_GENOME_ID>
python -m acr_runtime.cli --db .acr/acr.db skills genome-tournament-report `
  <TOURNAMENT_ID>
python -m acr_runtime.cli --db .acr/acr.db agents define `
  examples/agent-spec/database-worker.json
python -m acr_runtime.cli --db .acr/acr.db agents list
python -m acr_runtime.cli --db .acr/acr.db agents inspect database-worker
python -m acr_runtime.cli --db .acr/acr.db agents factory-plan `
  examples/agent-factory/research-plan.json
python -m acr_runtime.cli --db .acr/acr.db agents factory-report <PLAN_ID>
python -m acr_runtime.cli --db .acr/acr.db agents topology-record outcome.json
python -m acr_runtime.cli --db .acr/acr.db agents topology-recipes
python -m acr_runtime.cli --db .acr/acr.db agents topology-recommend `
  examples/agent-factory/research-plan.json
python -m acr_runtime.cli --db .acr/acr.db plans create `
  examples/planner/architecture-plan.json
python -m acr_runtime.cli --db .acr/acr.db plans inspect <PLAN_ID>
python -m acr_runtime.cli --db .acr/acr.db plans history <PLAN_ID>
```

## Safety boundary

Generated and evolved skills default to `quarantine`. Activation requires the
complete retained validation pipeline; evolved versions additionally require a
six-objective no-regression comparison and explicit promotion. Prior validated
versions are retained for reasoned rollback. v0.1 intentionally does not
install packages, mutate its own policy, automatically act on skill-merger
recommendations, apply experimental genome winners to production behavior,
spawn proposed Agent Factory workers, execute defined AgentSpecs, or write
memories from untrusted web content.

See [docs/architecture.md](docs/architecture.md) and
[docs/research.md](docs/research.md) for the build rationale and next steps.

Repository-wide boundaries are documented in [ARCHITECTURE.md](ARCHITECTURE.md).
The adapted modular build sequence is tracked in
[docs/specs/prompt-roadmap.md](docs/specs/prompt-roadmap.md).
