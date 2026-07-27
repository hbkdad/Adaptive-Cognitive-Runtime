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
| 5 retrieval | Complete | Hybrid boundary, configurable scoring, dedupe, conflicts, explained budget selection |
| 6 temporal memory | Complete | Effective intervals, scheduled changes, current/at/history APIs |
| 7 write controller | Complete | Eight outcomes, deterministic policy, retention reasons, hash-only audit |
| 8 consolidation | Complete | Dry-run plans, explicit approval, provenance-preserving actions |
| 9 lifecycle GC | Complete | Scored dry runs, approval, pin/archive/restore, protected classes |
| 10 failure intelligence | Complete | Structured repeats, analogy weights, remediation links, planning advice |
| 11 experience distillation | Complete | Raw trace isolation, significance gate, seven categories, compression, approval |
| 12 context compiler | Complete | Seven sources, full pipeline, dependencies, rejections, hard budgets |
| 13 context economy | Complete | Adaptive headroom, exact knapsack selection, and budget/outcome telemetry |
| 14 context attribution | Complete | Four-channel evidence fusion, uncertain state, approximate realized ROI |
| 15 compression | Complete | Layered exact extraction, AST symbols, references, dedupe, protected classes |
| 16 skill format | Complete | Strict v1 manifest, layout, integrity hash, lifecycle vocabulary |
| 17 registry | Complete | Full CLI, admission, lifecycle, FTS/semantic boundary, dimensional metrics |
| 18 router | Complete | Active-only bounded routing, exact minimal-set optimization, rejected alternatives, outcome loop |

## Dependency-corrected near-term order

1. Prompt 1: foundation completion
2. Prompt 2: deterministic task lifecycle and event boundary
3. Prompt 3: telemetry expansion
4. Prompt 31: provider protocol and mock adapter — complete
5. Prompt 33A: local Ollama adapter — complete
6. Prompt 41: benchmark framework — complete
7. Prompt 28: evaluator/critic — complete
8. Prompt 4A: canonical memory domain and SQLite adapter — complete
9. Prompt 5: hybrid memory retrieval engine — complete
10. Prompt 6: temporal memory and point-in-time truth — complete
11. Prompt 7: governed memory write controller — complete
12. Prompt 8: memory consolidation service — complete
13. Prompt 9: conservative memory lifecycle garbage collector — complete
14. Prompt 10: first-class failure intelligence and planning advice — complete
15. Prompt 11: governed experience distillation pipeline — complete
16. Prompt 12: expanded deterministic context compiler — complete
17. Prompt 13: adaptive Token Economist and constrained optimization — complete
18. Prompt 14: conservative multi-signal context attribution — complete
19. Prompt 15: exactness-aware layered context compression — complete
20. Prompt 16: ACR Skill Format v1 package contract — complete
21. Prompt 17: governed local skill registry and retrieval — complete
22. Prompt 18: minimal-set task-to-skill router and attribution loop — complete
23. Prompt 19: repeated-success skill generator with quarantined v1 packages — complete
24. Prompt 20: retained ten-stage validation and mandatory promotion gate — complete
25. Prompt 21: immutable versioned skill evolution, Pareto comparison, and rollback — complete
26. Prompt 22: advisory evidence-backed skill merger and composition analysis — complete

Next, continue with Prompt 23. Network-facing
memory mutations remain deferred until authorization and scope enforcement
exist.

Prompt 81 was pulled forward and completed before expanding Prompt 4 because
persistent databases now exist and must not be altered implicitly.

The two-layer control-center proposal is accepted as an architecture constraint.
The operations dashboard precedes the separately loaded cinematic layer; both
wait for a sanitized, replayable API contract.
