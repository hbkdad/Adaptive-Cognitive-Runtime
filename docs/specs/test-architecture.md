# Test architecture

Prompt 86 makes the repository's test taxonomy executable while keeping
probabilistic AI quality measurement outside the deterministic release gate.

## Deterministic gate

`tests/suites.json` classifies every `tests/test_*.py` module into exactly one
of six tiers:

| Tier | Purpose |
| --- | --- |
| `unit` | bounded component contracts and pure logic |
| `integration` | runtime, CLI, API, provider, MCP, and storage boundaries |
| `scenario` | end-to-end lifecycle and multi-component workflows |
| `benchmark` | deterministic benchmark math, datasets, and profiler behavior |
| `security` | permissions, secrets, privacy, sandbox, and safe-mode boundaries |
| `regression` | migrations and previously protected failure conditions |

The manifest validator fails on an unclassified, duplicated, nonexistent, or
path-escaping test file. This prevents new tests from silently falling outside
CI.

Run the complete deterministic gate or one tier:

```powershell
python -m acr_runtime.test_architecture validate
python -m acr_runtime.test_architecture run deterministic
python -m acr_runtime.test_architecture run security
```

GitHub Actions runs all six tiers independently with no credentials. Existing
model-dependent software assertions use `MockProvider`, fake Ollama transports,
stubs, or fixed evaluator adapters. They test contracts deterministically and
make no paid API call.

## Probabilistic quality boundary

`quality_benchmarks/` is outside `tests/` and default `unittest` discovery.
`acr_runtime.quality_benchmark` requires an explicitly supplied provider and
evaluator, repeats each case at least three times, and reports descriptive
quality evidence:

- mean and standard deviation;
- minimum and maximum;
- threshold pass counts;
- model-adapter and evaluator identity;
- seed and sample count.

This output is benchmark evidence, not a deterministic assertion. The runtime
ships `MockQualityProvider` and `KeywordQualityEvaluator` to validate the
harness offline. A real-model run is separate, opt-in, and can use a governed
local provider; no paid API is required by the architecture.

## Design references

- [Python `unittest` discovery](https://docs.python.org/3.11/library/unittest.html#test-discovery)
  provides the standard-library discovery mechanism used by each manifest
  tier.
- [Google production ML deployment testing](https://developers.google.com/machine-learning/crash-course/production-ml-systems/deployment-testing)
  recommends deterministic infrastructure tests, integration coverage, fixed
  thresholds, reproducibility controls, and averaging repeated model runs.
- [NIST AI 800-3](https://www.nist.gov/publications/expanding-ai-evaluation-toolbox-statistical-models)
  distinguishes fixed-benchmark measurements from broader performance claims
  and motivates reporting uncertainty rather than treating one stochastic
  output as a software assertion.
