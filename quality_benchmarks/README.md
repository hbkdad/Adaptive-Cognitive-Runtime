# Probabilistic quality benchmarks

This directory is intentionally outside `tests/` and Python `unittest`
discovery. Real-model output quality is measured repeatedly; it is not treated
as a deterministic software assertion.

`acr_runtime.quality_benchmark` provides a strict JSONL dataset loader, a
provider/evaluator boundary, repeated seeded sampling, and descriptive results
including mean, standard deviation, range, and threshold pass counts. It does
not create a network client or require a paid API. Callers explicitly supply a
governed provider adapter.

The checked-in `v1/smoke.jsonl` dataset can be validated with the deterministic
`MockQualityProvider` and `KeywordQualityEvaluator`. That exercises the harness,
not model quality. A real provider run must be invoked separately and should
retain its model/version, evaluator/version, dataset hash, sample count, and
cost evidence outside the default test gate.
