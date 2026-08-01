# Prompt 103: project memory bootstrap

## Scope

Prompt 103 bootstraps only durable repository knowledge into the explicit
`project:acr` memory scope under `global`. It does not import session history,
copy source bodies, infer undocumented deployment facts, or store every README
sentence.

## Confirmed foundation

Eight evidence-backed memories were selected:

| Subject | Type | Primary evidence |
| --- | --- | --- |
| Technology stack | semantic | `pyproject.toml`, `apps/control-center/package.json` |
| Project structure | semantic | `pyproject.toml`, `README.md` |
| Verification commands | procedural | `.github/workflows/deterministic-tests.yml`, `tests/suites.json` |
| Build and startup | procedural | `pyproject.toml`, `README.md` |
| Deployment environment | environment | `docs/adr/0002-two-layer-control-center.md`, `README.md` |
| Database foundation | semantic | `README.md`, `acr_runtime/migrations.py` |
| Architecture boundaries | decision | `architecture-boundaries.toml`, `docs/architecture.md` |
| Authority boundary | decision | `AGENTS.md`, `docs/specs/capability-designer.md` |

The memories bind their evidence to verified repository commit `551c140` and
use high stability only for statements intended to remain foundational. No
current schema version, temporary Ollama process state, task transcript, raw
prompt, credential, or absolute attachment path is retained.

## Security workflow

Repository documents are still external-content derivations at the memory
write boundary. The initial eight writes were therefore quarantined. Each
exact candidate received a one-shot `memory.create` approval grounded in the
user-authorized Prompt 103 workflow and reviewed repository evidence.

One original authority-boundary sentence independently matched the
authority-override heuristic and remained quarantined despite approval. It was
not forced through. A neutral positive policy statement was assessed and
approved as a new exact candidate.

## Verification

A bounded lexical retrieval for verification and architecture guidance found
all eight candidates, selected five within a 320-token budget, and ranked the
verification procedure first and architecture decision second. Semantic
retrieval remains explicitly unavailable because no embedding adapter is
configured; FTS-backed retrieval is sufficient for this bootstrap.
