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
- structural-metadata-only repository indexing and hash-verified code retrieval.

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

The equivalent classified gate and focused tiers are:

```powershell
python -m acr_runtime.architecture_guard check
python -m acr_runtime.test_architecture validate
python -m acr_runtime.test_architecture run deterministic
python -m acr_runtime.test_architecture run unit
python -m acr_runtime.test_architecture run integration
python -m acr_runtime.test_architecture run scenario
python -m acr_runtime.test_architecture run benchmark
python -m acr_runtime.test_architecture run security
python -m acr_runtime.test_architecture run regression
```

Real-model probabilistic quality benchmarks are kept separately under
`quality_benchmarks/`; they are never part of the default gate and require no
paid API.

Validate Prompt 106's current networked-production readiness assessment:

```powershell
python -m acr_runtime.production_readiness `
  docs/audits/prompt-106-readiness.json
```

A valid not-ready report exits `1`; only complete contiguous production
evidence across all fourteen dimensions can exit `0`.

Run the deterministic core in the explicit zero-cloud profile:

```powershell
$env:ACR_DEPLOYMENT_PROFILE = "zero-cloud"
$env:ACR_PROVIDER = "ollama" # optional; omit for the model-free core
$env:ACR_OLLAMA_URL = "http://127.0.0.1:11434"
python -m acr_runtime.cli config show
python -m acr_runtime.cli --json doctor
```

Zero-cloud mode requires SQLite, filesystem skills, SQLite-only telemetry, and
a root loopback Ollama endpoint. It rejects cloud providers and filters Ollama
model names ending in `:cloud` or `-cloud`. For defense in depth, also set
Ollama's official `OLLAMA_NO_CLOUD=1` setting and restart Ollama.

Run the optional desktop daemon:

```powershell
python -m acr_runtime.cli --db .acr/acr.db daemon start
python -m acr_runtime.cli --db .acr/acr.db daemon status
python -m acr_runtime.cli --db .acr/acr.db daemon stop
```

It binds `127.0.0.1:8000` by default. Non-loopback binding requires both
`--allow-network` and `ACR_API_TOKEN`; zero-cloud mode never permits it.

Run the local API, Prompt 50 memory inspector, and Prompt 51 Skill Lab:

```powershell
pip install -e ".[api]"
python -m acr_runtime.cli --db .acr/demo.db serve --port 8011
cd apps/control-center
npm install
npm run dev -- --port 4173
```

Open `http://127.0.0.1:4173/memory` and enter the memory's exact scope. Reads
show only public/internal records. Guarded actions additionally require
`ACR_API_TOKEN`, `ACR_API_OPERATOR_ID`, and an active exact-scope
`memory.write` grant as documented in `docs/specs/memory-inspector.md`.

Open `http://127.0.0.1:4173/skills` to inspect exact skill versions, compare
their visible changes, review validation and benchmark evidence, and use
guarded lifecycle controls. Skill writes require the same server-bound token
and operator plus the exact grants documented in `docs/specs/skill-lab.md`.

Open `http://127.0.0.1:4173/learning` for the Prompt 52 audit timeline. It
separates approved changes, proposal-only recommendations, advisory
discoveries, historically unattributed workflows, and automatic measurements
inside requested runs without exposing raw memory, task, evidence, or package
content. See `docs/specs/learning-dashboard.md`.

Build Prompt 53's bounded repository index and retrieve one qualified symbol:

```powershell
python -m acr_runtime.cli --db .acr/acr.db code index .
python -m acr_runtime.cli --db .acr/acr.db code retrieve `
  "SkillRegistry.activate" --repository . --budget 4000
python -m acr_runtime.cli --db .acr/acr.db code slice `
  "SkillRegistry.activate" --repository . --budget 4000
```

The index retains relative paths, hashes, spans, imports, dependency names, and
structural relations, never source bodies or embeddings. Retrieval refuses
stale generations and returns only the requested definition plus useful
one-hop context. See `docs/specs/codebase-context-indexer.md`.

Prompt 54's `code slice` command reparses the verified Python file in a
killable worker and returns the exact target source unit plus bounded,
transitive same-file imports, definitions, and constants. It reports raw
whole-file versus slice token cost and labels unresolved or dynamic behavior
as partial rather than claiming runtime closure. See
`docs/specs/ast-aware-code-retrieval.md`.

Build Prompt 55's semantic Markdown index and retrieve document context:

```powershell
python -m acr_runtime.cli --db .acr/acr.db docs index .
python -m acr_runtime.cli --db .acr/acr.db docs retrieve `
  "Privacy and audit contract" --repository .
python -m acr_runtime.cli --db .acr/acr.db docs retrieve `
  "exact quoted text" --mode exact --repository .
```

The database retains headings, hierarchy, character/byte/line spans, hashes,
chunk strategy, and explicit relationships—not document prose. Every retrieval
rechecks the Prompt 53 snapshot and file hash before reading exact source. See
`docs/specs/document-context-engine.md`.

Run Prompt 56's local MCP provider after granting its server-bound identity the
exact scopes it needs:

```powershell
python -m acr_runtime.cli --db .acr/acr.db mcp serve `
  --subject-type agent --subject-id local-mcp-agent
```

The static catalog exposes memory/context retrieval, active-skill discovery,
content-minimized task history, and failure lookup. `execute_skill` is listed
but fails closed because ACR has neither a production skill executor nor a
`skill.execute` capability. Stdio is not treated as authorization. See
`docs/specs/mcp-integration.md`.

Prompt 57 connects that provider to Codex through the trusted-project
`.codex/config.toml` and a compact root `AGENTS.md`. Start a new Codex session,
confirm `codex mcp get acr`, and add only the exact expiring grants justified
for `agent:codex-local`. The bounded pre-task retrieval and evidence-backed
post-task learning workflow is in `docs/integrations/codex.md`.

Prompt 58 provides the Claude Code equivalent through `CLAUDE.md`, `.mcp.json`,
and two small hooks in `.claude/settings.json`. Confirm `claude mcp get acr`,
then grant only the required exact scopes to `agent:claude-code-local`. Claude
auto-memory is disabled for this project so governed ACR memory remains the
external persistent-intelligence layer. See
`docs/integrations/claude-code.md`.

Prompt 63 checks whether retained confidence forecasts correspond to real
outcomes:

```powershell
python -m acr_runtime.cli --db .acr/acr.db calibration report memory
python -m acr_runtime.cli --db .acr/acr.db calibration interpret routing 0.9 `
  --group coding --minimum-samples 20
```

Memory, routing, and explicitly forecast evaluation outcomes remain separate
cohorts. Reports expose sample counts, reliability bins, uncertainty intervals,
ECE, MCE, and Brier score. Interpretation never rewrites confidence or policy.
See `docs/specs/confidence-calibration.md`.

Prompt 64 adds task-level hard resource budgets with atomic reservations:

```powershell
python -m acr_runtime.cli --db .acr/acr.db resources create TASK_ID budget.json
python -m acr_runtime.cli --db .acr/acr.db resources status TASK_ID
```

Soft limits require an exact, expiring manual approval; hard limits cannot be
raised. Local model and external MCP calls reserve capacity before dispatch,
and uncertain failed calls remain held. See
`docs/specs/resource-governor.md`.

Prompt 65 adds opt-in, exact retrieval caching:

```powershell
python -m acr_runtime.cli --db .acr/acr.db memory retrieve "SQLite FTS5" `
  --task "Inspect the database" --scope project:a --cache-max-age 30
python -m acr_runtime.cli --db .acr/acr.db cache status
```

Cache keys include scope, temporal, privacy, algorithm, and ranking inputs.
Memory, scope, and privacy-policy changes invalidate all entries. Stored
payloads contain opaque IDs and scoring metadata, never tasks, queries, prompts,
or memory content. See `docs/specs/safe-cache.md`.

Prompt 66 adds bounded, advisory duplicate detection:

```powershell
python -m acr_runtime.cli --db .acr/acr.db dedup scan `
  --kind memory --scope project:a --limit 100
python -m acr_runtime.cli --db .acr/acr.db dedup report <RUN_ID> `
  --scope project:a
```

The scanner compares only compatible artifact kinds, scopes, privacy
partitions, and behavior contracts. Exact canonical hashes are checked before
near-duplicate lexical evidence; semantic comparison is optional and requires
an explicitly trusted, local, version-identified adapter. Reports retain
content-minimized provenance and recommendations, but
`automatic_action_allowed` is always false. Exact context coalescing preserves
all provenance, dependencies, required status, and authority partitions. See
`docs/specs/deduplication.md`.

Run a bounded task through an installed local Ollama model:

```powershell
python -m acr_runtime.cli --db .acr/acr.db run `
  "Return exactly: ACR local execution works." `
  --model qwen2.5-coder:1.5b `
  --environment '{"platform":"windows","purpose":"local-smoke-test"}'
```

`--environment` is an optional bounded JSON object used for content-minimized
failure comparison. Omit it to use `{}`; plain text is rejected at the CLI
boundary without starting a model call.

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
python -m acr_runtime.cli --db .acr/acr.db evaluate run evaluation-case.json
python -m acr_runtime.cli --db .acr/acr.db evaluate report <RUN_ID>
python -m acr_runtime.cli --db .acr/acr.db reflect run `
  examples/reflection/complete-request.json
python -m acr_runtime.cli --db .acr/acr.db reflect report <RUN_ID>
python -m acr_runtime.cli --db .acr/acr.db learn plan <TASK_ID>
python -m acr_runtime.cli --db .acr/acr.db learn run learning-request.json
python -m acr_runtime.cli --db .acr/acr.db learn report <RUN_ID>
python -m acr_runtime.cli --db .acr/acr.db capabilities grant `
  examples/capabilities/database-read-grant.json
python -m acr_runtime.cli --db .acr/acr.db capabilities check `
  examples/capabilities/database-read-check.json
python -m acr_runtime.cli --db .acr/acr.db tools route `
  examples/capabilities/database-tool-route.json
python -m acr_runtime.cli --db .acr/acr.db security assess `
  examples/security/injected-document-assessment.json
python -m acr_runtime.cli design capability `
  examples/capability-design/tool-request.json
python -m acr_runtime.cli --db .acr/acr.db skills certify <SKILL_ID> `
  --docker-sandbox --sandbox-image python:3.11-slim
python -m acr_runtime.cli --db .acr/acr.db skills import-external `
  C:\path\to\agent-skill --source-label local `
  --docker-sandbox --sandbox-image python:3.11-slim
python -m acr_runtime.cli --db .acr/acr.db coding run `
  examples/coding-experiment/request.json
python -m acr_runtime.cli --db .acr/acr.db coding report <RUN_ID>
python -m acr_runtime.cli --db .acr/acr.db project create `
  examples/project-state/project.json --actor operator:miche
python -m acr_runtime.cli --db .acr/acr.db project item-add `
  adaptive-cognitive-runtime examples/project-state/next-work.json `
  --expected-project-revision 1 --actor operator:miche
python -m acr_runtime.cli --db .acr/acr.db project show `
  adaptive-cognitive-runtime
python -m acr_runtime.cli --db .acr/acr.db project next `
  adaptive-cognitive-runtime
python -m acr_runtime.cli secrets scan-staged --repository .
python -m acr_runtime.cli --db .acr/acr.db secrets inspect <ACCESS_EVENT_ID>
python -m acr_runtime.cli --db .acr/acr.db privacy policies
python -m acr_runtime.cli --db .acr/acr.db privacy retention-due
python -m acr_runtime.cli --db .acr/acr.db dedup scan --limit 100
python -m acr_runtime.cli --db .acr/acr.db dedup report <RUN_ID>
python -m acr_runtime.cli --db .acr/acr.db experiments report <EXPERIMENT_ID>
python -m acr_runtime.cli --db .acr/acr.db cost rates
python -m acr_runtime.cli --db .acr/acr.db cost local-status
python -m acr_runtime.cli --db .acr/acr.db cost report
python -m acr_runtime.cli --db .acr/acr.db explain model <ROUTE_ID>
python -m acr_runtime.cli --db .acr/acr.db explain skill <TASK_ID> <SKILL_ID>
python -m acr_runtime.cli --db .acr/acr.db explain memory <TASK_ID> <MEMORY_ID>
python -m acr_runtime.cli --db .acr/acr.db explain agent <PLAN_ID>
python -m acr_runtime.cli --db .acr/acr.db explain context <TASK_ID>
python -m acr_runtime.cli --db .acr/acr.db explain forgotten <MEMORY_ID>
python -m acr_runtime.cli --db .acr/acr.db overrides apply override.json
python -m acr_runtime.cli --db .acr/acr.db overrides list --active
python -m acr_runtime.cli --db .acr/acr.db overrides show <OVERRIDE_ID>
python -m acr_runtime.cli --db .acr/acr.db overrides revoke <OVERRIDE_ID> `
  --actor operator:miche --reason "Return control to runtime policy."
python -m acr_runtime.cli --db .acr/acr.db safe-mode
python -m acr_runtime.cli --db .acr/acr.db safe-mode enable `
  --actor operator:miche --reason "Contain suspected runtime behavior."
python -m acr_runtime.cli --db .acr/acr.db safe-mode events --limit 50
python -m acr_runtime.cli --db .acr/acr.db safe-mode disable `
  --actor operator:miche --reason "Recovery checks completed."
python -m acr_runtime.cli --db .acr/acr.db backup `
  backups/acr-checkpoint.acrb --benchmarks-dir benchmarks
python -m acr_runtime.cli verify-backup backups/acr-checkpoint.acrb
python -m acr_runtime.cli restore `
  backups/acr-checkpoint.acrb recovered/acr-checkpoint
```

Safe Mode persists across restarts and can also be latched before startup with
`ACR_SAFE_MODE=1`. It blocks skill generation/mutation, agent generation,
privacy erasure, write/shell capabilities, and autonomous optimization while
retaining retrieval, basic model inference, inspection, audit, and rollback.
See `docs/specs/safe-mode.md`.

Prompt 80 backups contain a coherent SQLite snapshot, skills, public
configuration, benchmarks, and the database-resident learning history. Every
entry is manifest-bound by size and SHA-256. Restore writes only to a new
directory and never activates or overwrites live state. Secret-like files or
values abort creation. See `docs/specs/backup-restore.md`.

## Safety boundary

Generated and evolved skills default to `quarantine`. Activation requires the
complete retained validation pipeline; evolved versions additionally require a
six-objective no-regression comparison and explicit promotion. Prior validated
versions are retained for reasoned rollback. v0.1 intentionally does not
install packages, mutate its own policy, automatically act on skill-merger
recommendations, apply experimental genome winners to production behavior,
spawn proposed Agent Factory workers, execute defined AgentSpecs, or write
memories from untrusted web content.
Task, agent, and skill authority is default-deny and exact-scoped. Skills cannot
issue capability grants, delegated authority cannot expand or outlive its
parent, and revocation propagates to descendants.
Retrieved memory, web content, documents, and tool output have no instruction
authority. Their provenance is retained by hash; suspicious items are excluded
from compiled context, clean items are escaped as untrusted data, and external
content cannot create memory or permissions without exact one-shot review.
Generated executable skill checks fail closed unless an isolation adapter is
explicitly enabled. The Docker adapter runs an immutable local image ID with no
network or writable host mount, read-only code, non-root execution, filtered
environment, bounded resources/workspace/time, forced timeout cleanup, and
retained audit evidence.
Credentials remain outside memory, telemetry, skills, and prompts. Opaque
environment, OS-keyring, or configured external-store references require exact
`credential.use` grants; successful resolution returns a one-use lease, while
schema 33 retains only hash-based access evidence. A staged Git scanner rejects
high-confidence credential formats without printing matched values.
Every memory is also tagged public, internal, personal, confidential, or
secret. Versioned schema-34 policies govern exact provider receipt, retention,
exportability, and a two-step verified SQLite/FTS erasure pathway. Credential
material remains forbidden even when the requested sensitivity is `secret`.
Opt-in schema-35 experiments can reproducibly compare retrieval, budgets,
skills, model routers, and planners. Assignment IDs and outcomes are retained,
raw randomization-unit IDs are not, and reports never change production
defaults.
The MCP v1 surface is local stdio only. It binds one configured ACR identity,
requires exact active grants, returns sensitive domain text only through
content-minimized or authority-free projections, and emits JSON-RPC alone on
stdout. External MCP definitions receive operator-supplied risk metadata and
versioned names; their descriptions, annotations, and results are untrusted.

See [docs/architecture.md](docs/architecture.md) and
[docs/research.md](docs/research.md) for the build rationale and next steps.

Repository-wide boundaries are documented in [ARCHITECTURE.md](ARCHITECTURE.md).
The adapted modular build sequence is tracked in
[docs/specs/prompt-roadmap.md](docs/specs/prompt-roadmap.md).
