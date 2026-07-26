# Adapted prompt roadmap

The 131-prompt build pack is a specification catalog. Only one bounded prompt is
executed at a time, and completed behavior is audited before a later prompt is
used.

## Prompt 0A audit result

| Prompt area | Status | Evidence |
| --- | --- | --- |
| 0 master principles | Partial | Local-first, measurable context loop exists |
| 1 repository foundation | Partial | Packaging exists; diagnostics and governance were missing |
| 2 task engine | Complete | Validated lifecycle, immutable run record, deterministic runner, and event bus |
| 3 telemetry | Complete | Secret-safe event/run persistence and evidence-backed CLI metrics |
| 4 memory model | Complete (4A) | Eight types, six-state lifecycle, provenance, storage port, schema v3 |
| 5 retrieval | Partial | Scoped FTS5 and transparent scoring exist |
| 6 temporal memory | Partial | Supersession exists; point-in-time API does not |
| 7 write controller | Missing | Callers currently choose storage directly |
| 8–11 memory learning | Missing | Deferred until evaluation boundaries exist |
| 12–14 context economy | Partial | Budgeting, ROI, and attribution exist |
| 15 compression | Missing | Deferred until exactness policies exist |
| 16 skill format | Missing | SQLite skill rows are not ACR Skill Format v1 |
| 17 registry | Partial | Quarantine and usage statistics exist |
| 18 router | Partial | Keyword/ROI selection exists without rejected alternatives |

## Dependency-corrected near-term order

1. Prompt 1: foundation completion
2. Prompt 2: deterministic task lifecycle and event boundary
3. Prompt 3: telemetry expansion
4. Prompt 31: provider protocol and mock adapter — complete
5. Prompt 33A: local Ollama adapter — complete
6. Prompt 41: benchmark framework — complete
7. Prompt 28: evaluator/critic — complete
8. Prompt 4A: canonical memory domain and SQLite adapter — complete

Next, continue with Prompt 5 retrieval, Prompt 6 temporal reasoning, and Prompt
7 governed writes before exposing memory mutations through an API.

Prompt 81 was pulled forward and completed before expanding Prompt 4 because
persistent databases now exist and must not be altered implicitly.

The two-layer control-center proposal is accepted as an architecture constraint.
The operations dashboard precedes the separately loaded cinematic layer; both
wait for a sanitized, replayable API contract.
