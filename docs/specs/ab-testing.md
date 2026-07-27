# Prompt 42: reproducible A/B testing

ACR experiments compare one controlled strategy domain at a time: retrieval
algorithm, context budget, skill version, model router, or planner strategy.

An immutable draft records a bounded hypothesis, randomization unit, explicit
seed, primary metric, and 2–10 variants. Allocations are integer basis points
that must total 10,000, and exactly one variant is the baseline. Variant
configuration is strict JSON, size-bounded, and secret-checked.

## Assignment

Starting an experiment is explicit. A SHA-256 mapping of experiment ID, seed,
and caller-supplied unit creates a stable bucket from 0–9,999. The same unit in
the same experiment always receives the retained assignment. SQLite stores only
the experiment-salted unit hash; it does not retain the raw task, agent, or
other unit identifier.

Assignment returns the selected configuration to the opted-in caller. It does
not mutate a router, skill, planner, retrieval setting, context budget, or
production default.

## Outcomes and report

Each assignment accepts at most one evidenced outcome with quality, tokens,
cost, latency, and failure. The report shows assignment/outcome counts, expected
and observed allocation, means, and raw deltas from the baseline. A conservative
allocation diagnostic flags a large observed split deviation.

Results are deliberately
`descriptive_only_replicate_before_production_decision`: the controller does not
claim statistical significance, recommend shipping, or apply a default. This
separation prevents early peeking, a noisy mean, or an accidental API call from
turning experimental behavior into production policy.

## CLI

```powershell
python -m acr_runtime.cli --db .acr/acr.db experiments create experiment.json
python -m acr_runtime.cli --db .acr/acr.db experiments start <EXPERIMENT_ID>
python -m acr_runtime.cli --db .acr/acr.db experiments assign `
  <EXPERIMENT_ID> <UNIT_ID>
python -m acr_runtime.cli --db .acr/acr.db experiments outcome `
  <EXPERIMENT_ID> outcome.json
python -m acr_runtime.cli --db .acr/acr.db experiments report <EXPERIMENT_ID>
python -m acr_runtime.cli --db .acr/acr.db experiments finish <EXPERIMENT_ID>
```

`inspect`, `cancel`, and all lifecycle responses include
`production_default_changed: false`.
