# ACR architecture

ACR is a local-first cognitive runtime. The current foundation intentionally
uses Python's standard library and SQLite so the behavior remains inspectable.
Dependencies are introduced only when a measured requirement justifies them.

## Dependency direction

```text
CLI / future API
      |
      v
application service
      |
      +--> context compiler --> scoring
      |
      +--> storage port (current adapter: SQLite)
      |
      +--> execution engine --> planner/executor/verifier/evaluator protocols
                              --> future provider adapters
```

Core scoring and data models do not import the CLI, a web framework, a model
provider, or provider credentials. Future provider implementations will depend
on provider protocols defined by the core; core modules will not import concrete
providers.

## Current domains

- `config.py`: typed, secret-safe local settings
- `models.py`: immutable context-domain values
- `db.py`: SQLite storage adapter and schema migration
- `compiler.py`: retrieval, ranking, and hard-budget assembly
- `scoring.py`: deterministic token and utility heuristics
- `service.py`: application-facing orchestration boundary
- `diagnostics.py`: operational health and local-model discovery
- `execution.py`: validated task lifecycle, deterministic runner, and event bus
- `telemetry.py`: secret-safe event subscriber and run persistence adapter
- `providers/`: model-independent contracts, mock/Ollama adapters, and task adapter
- `benchmark.py`: versioned datasets, reproducible runs, and baseline metrics
- `evaluation.py`: deterministic and guarded model-assisted evaluation panels
- `cli.py`: command-line application

Domains without executable behavior are documented but are not represented by
empty packages. Routing, agents, tools, learning, security enforcement, and HTTP
API packages will be introduced with their first tested vertical slice.

See [docs/architecture.md](docs/architecture.md) for the v0.1 context loop.
