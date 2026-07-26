# ACR — Adaptive Cognitive Runtime

ACR v0.1 is a local-first prototype of a context-economy layer for AI agents. It
does not call a model yet. It proves the lower layer that should exist before
self-modification is allowed:

- evidence-backed semantic, episodic, procedural, and failure memory;
- temporal supersession instead of destructive overwrites;
- scoped SQLite FTS5 retrieval;
- utility-per-token ranking and hard context budgets;
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
```

## Safety boundary

Generated skills default to `quarantine`. A caller must explicitly register a
skill as trusted before the context compiler can select it. v0.1 intentionally
does not execute generated code, install packages, mutate its own policy, or
write memories from untrusted web content.

See [docs/architecture.md](docs/architecture.md) and
[docs/research.md](docs/research.md) for the build rationale and next steps.

Repository-wide boundaries are documented in [ARCHITECTURE.md](ARCHITECTURE.md).
The adapted modular build sequence is tracked in
[docs/specs/prompt-roadmap.md](docs/specs/prompt-roadmap.md).
