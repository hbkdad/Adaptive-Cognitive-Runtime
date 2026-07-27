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
      |                    --> active skill router --> skill registry
      |
      +--> memory reader/store protocols --> SQLite memory adapter
      |
      +--> runtime persistence adapter (current adapter: SQLite)
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
- `memory.py`: storage-independent memory values, lifecycle, and ports
- `db.py`: runtime persistence adapter and fresh-database bootstrap
- `migrations.py`: explicit, backed-up schema upgrades
- `compiler.py`: retrieval, ranking, and hard-budget assembly
- `skill_router.py`: metadata-only applicability, benefit, overlap, dependency,
  and hard-budget optimization
- `skill_registry.py`: governed skill admission, lifecycle, retrieval, and
  performance history
- `skill_generator.py`: deterministic repeated-success detection, evidence-backed
  v1 package generation, and quarantine-only registry admission
- `skill_validator.py`: fail-closed ten-stage validation, real-sandbox adapter
  boundary, benchmark regression policy, and digest-bound promotion
- `retrieval.py`: hybrid candidates, configurable scoring, conflicts, and selection
- `temporal.py`: current, point-in-time, and historical truth resolution
- `write_controller.py`: deterministic retention policy and hash-only decision audit
- `consolidation.py`: dry-run planning and explicitly approved memory maintenance
- `scoring.py`: deterministic token and utility heuristics
- `service.py`: application-facing orchestration boundary
- `diagnostics.py`: operational health and local-model discovery
- `execution.py`: validated task lifecycle, deterministic runner, and event bus
- `telemetry.py`: secret-safe event subscriber and run persistence adapter
- `providers/`: model-independent contracts, mock/Ollama adapters, and task adapter
- `benchmark.py`: versioned datasets, reproducible runs, and baseline metrics
- `evaluation.py`: deterministic and guarded model-assisted evaluation panels
- `capability_vocab.py`: closed shared permission vocabulary
- `permissions.py`: exact grants, default-deny decisions, bounded delegation,
  expiry, and transitive revocation
- `content_security.py`: instruction/data authority, hash-only provenance,
  injection signals, data framing, and one-shot sensitive-action approvals
- `provider_tools.py`: protocol-neutral, identity-bound, content-minimized
  provider operations and the unavailable-by-default skill execution boundary
- `mcp_stdio.py`: pinned local JSON-RPC/MCP lifecycle and six-tool stdio catalog
- `mcp_bridge.py`: strict versioned adapter for reviewed external MCP tools
- `skill_validator.py`: mandatory validation plus the generated-skill Docker
  isolation policy, boundary self-test, timeout cleanup, and retained audit
- `cli.py`: command-line application

The future control center is a separate client of a loopback API. Its operations
dashboard and cinematic visualization are separate UI layers; neither is part
of the runtime process or an authority over memory writes. Routing, agents,
tools, learning, security enforcement, HTTP APIs, and UI packages are introduced
only with their first tested vertical slice.

See [docs/architecture.md](docs/architecture.md) for the v0.1 context loop.
