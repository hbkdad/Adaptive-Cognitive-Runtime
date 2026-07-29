# Prompt 76: evidence graph

Prompt 76 adds an ordinary relational provenance graph over canonical ACR
records:

`claim -> evidence -> source -> task -> decision -> skill`

The implementation follows the useful core of W3C PROV: entities and
activities remain identifiable, derivation is explicit, and qualified
assertions retain their own provenance. It does not claim full PROV-O
conformance.

## Canonical nodes

The graph does not copy source text, claims, task objectives, decisions, or
skill instructions. Nodes retain only a type, native record identity, canonical
content hash, and timestamp:

- claim: `research_findings`;
- evidence: a deterministic finding/reference citation;
- source: `research_references`;
- task: a succeeded `tasks` record;
- decision: a live confirmed decision memory; and
- skill: an active immutable skill version.

Creation fails closed if any canonical record is missing or not in the required
terminal/lifecycle state. Repeating the same import is idempotent.

## Edges and truth boundary

SQL permits exactly five directed type pairs: `supported_by`, `derived_from`,
`used_by`, `informed`, and `applied`. A database trigger rejects all other
source/target combinations. Nodes, edges, bundles, and membership rows are
append-only and retained.

The CLI can only create `caller_asserted_unverified` bundles. Its bounded
assertion evidence is secret-checked and retained only as a hash. The graph is
therefore inspectable lineage, not causal proof and not positive learning
evidence.

## Traversal

SQLite indexes both edge directions. A recursive CTE performs forward or
backward traversal within one bundle, with a maximum depth of five, a maximum
of 100 returned nodes, and cycle avoidance. SQLite officially supports
recursive CTE graph walks, so a separate graph database is not justified by
the current scale or query needs.

```powershell
python -m acr_runtime.cli --db .acr/acr.db evidence-graph create graph.json
python -m acr_runtime.cli --db .acr/acr.db evidence-graph inspect BUNDLE_ID
python -m acr_runtime.cli --db .acr/acr.db evidence-graph traverse `
  BUNDLE_ID NODE_ID --direction forward --max-depth 5
```

Future graph storage is warranted only if measured traversal latency, graph
size, or required algorithms exceed the bounded relational implementation.
