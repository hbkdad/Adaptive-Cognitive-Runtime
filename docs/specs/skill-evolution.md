# Prompt 21: versioned skill evolution

Skill evolution is an explicit, retained lifecycle. A published package is never
edited in place:

1. select an active, Prompt-20-validated source version;
2. create a new Semantic Versioning candidate in quarantine;
3. validate the candidate through all ten Prompt 20 stages;
4. compare v1 and v2 across every required objective;
5. explicitly promote a qualifying winner; and
6. retain the validated source for operator-initiated rollback.

## Mutation contract

A candidate may change instructions, workflow, tool selection, retrieval
strategy, verification, error handling, and token budget. The mutation and
source/candidate hashes are stored in schema 17. The generated package also
retains its source version and mutation in `evolution.json` and appends a
`candidate_mutation` history event.

Candidate versions must be valid Semantic Versions newer than their source.
Existing candidate directories and registry versions are never overwritten.
Every candidate starts as `experimental` in its manifest and `quarantined` in
the registry.

## Evidence and comparison

Comparison accepts only:

- a retained passed or promoted Prompt 20 validation for the source;
- a retained passed Prompt 20 validation for the exact candidate; and
- candidate benchmark evidence containing both versions' quality, token use,
  cost, latency, reliability, and security.

The `pareto_no_regression_v1` policy selects v2 only when it is no worse on all
six objectives and strictly better on at least one. A higher benchmark score
cannot hide a cost, latency, reliability, security, or token regression.
Comparison values are derived from retained validation evidence rather than
caller-supplied metrics.

## Promotion and rollback

Promotion is explicit and rechecks Prompt 20's digest-bound proof. The candidate
becomes active and the source is quarantined, not deleted. Rollback requires an
operator reason, quarantines the candidate, reactivates the still-validated
source, and stores a durable rollback record.

```powershell
python -m acr_runtime.cli --db .acr/acr.db skills evolve `
  sqlite-diagnostics mutation.json
python -m acr_runtime.cli --db .acr/acr.db skills compare-evolution `
  <EVOLUTION_RUN_ID> comparison.json
python -m acr_runtime.cli --db .acr/acr.db skills promote-evolution `
  <EVOLUTION_RUN_ID>
python -m acr_runtime.cli --db .acr/acr.db skills rollback-evolution `
  <EVOLUTION_RUN_ID> --reason "Observed production regression"
```

`comparison.json` contains only `baseline_validation_id` and
`candidate_validation_id`; objective values are loaded from the retained
candidate validation.
