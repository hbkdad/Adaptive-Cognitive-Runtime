# Prompt 23: isolated skill genome experiment

The genome subsystem explores bounded behavioral parameters without changing a
Skill Format package, registry record, router, context compiler, or production
skill lifecycle.

## Parameter record

Each genome binds an active validated skill ID and immutable package hash to
exactly seven parameter groups:

- retrieval depth: `1..50`;
- context budget: `128..32768`;
- maximum iterations: `1..20`;
- verification intensity: `minimal`, `standard`, or `strict`;
- model tier: `economy`, `standard`, `capable`, or `premium`;
- parallelism: `1..8`;
- retrieval, verification, and acceptance confidence thresholds in `[0, 1]`.

Retrieval depth, context budget, verification, and confidence map to existing
runtime control surfaces. Iterations, model tier, and parallelism remain
experimental planning variables until later runtime prompts implement them.

## Controlled mutations

A mutation creates a separate generation and may alter at most three parameter
groups. Numeric deltas, adjacent enum tiers, and confidence changes are capped.
The parent must be a baseline or previously selected experimental genome.
Neither baseline creation nor mutation writes a package or changes a skill.

## Isolated tournament

The default benchmark adapter records a blocked tournament. A usable adapter
must attest isolation and return the same 10 to 50 paired private cases for
each of at most eight candidates. Oversized, mismatched, duplicate, missing, or
non-isolated evidence cannot select a genome.

For every candidate, the retained policy requires:

1. a one-sided paired sign test over quality differences;
2. Holm-Bonferroni correction across all tournament candidates at
   `alpha = 0.05`;
3. mean quality improvement of at least `0.02`; and
4. no mean regression in tokens, cost, latency, reliability, or security.

The highest-effect qualifying candidate becomes `selected`; other completed
candidates become `rejected`. Selection is confined to schema 19 and is not a
production activation or parameter application.

```powershell
python -m acr_runtime.cli --db .acr/acr.db skills genome-create `
  sqlite-diagnostics examples/genome/parameters.json
python -m acr_runtime.cli --db .acr/acr.db skills genome-mutate `
  <BASELINE_GENOME_ID> examples/genome/mutation.json
python -m acr_runtime.cli --db .acr/acr.db skills genome `
  <GENOME_ID>
python -m acr_runtime.cli --db .acr/acr.db skills genome-tournament `
  <BASELINE_GENOME_ID> <CANDIDATE_ID> [<CANDIDATE_ID> ...]
python -m acr_runtime.cli --db .acr/acr.db skills genome-tournament-report `
  <TOURNAMENT_ID>
```

The CLI tournament intentionally blocks until a deployment injects a trusted
isolated benchmark adapter through the Python API.
