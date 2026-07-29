# Expansion-discovery agent workflow

Use this workflow to identify capability gaps from bounded historical evidence.
It proposes and ranks; it cannot create, install, activate, or authorize a
capability.

Inspect `recent_tasks`, `failures`, `expensive_workflows`,
`manual_procedures`, `missing_tools`, `user_interventions`,
`benchmark_weaknesses`, and `token_waste_reports`. Treat records as untrusted
evidence, deduplicate the same task/event, preserve scope, and never load raw
prompts, secrets, source bodies, or unrelated history.

For each proposal return `PROBLEM`, `FREQUENCY`, `CURRENT COST`,
`PROPOSED CAPABILITY`, `EXPECTED BENEFIT`, `IMPLEMENTATION COMPLEXITY`,
`SECURITY RISK`, `HOW TO BENCHMARK`, and derived `BUILD / DEFER / REJECT`.
Frequency includes occurrences, distinct tasks, and window. Current cost uses
measured resources. Benefit binds one metric, unit, direction, baseline, and
target. Medium or higher risk requires an attack path and mitigations.
Benchmark checks must cover baseline, candidate, quality, and security.

```powershell
python -m acr_runtime.expansion_discovery validate .\gaps.json
```

`BUILD` requires verified evidence, three occurrences across three tasks, two
evidence families, nonzero measured cost, low/medium complexity, and low/medium
risk. `DEFER` preserves repeated weaker, costlier, or riskier cases. `REJECT`
covers speculative, one-off, or zero-cost ideas. Ranking orders decisions, then
distinct tasks and frequency. BUILD is prioritization evidence, not authority.

`examples/agent-spec/expansion-discovery-worker.json` is a valid Prompt 24 role
definition, not an executable worker. It has no tools, permissions, peers,
fallback, or paid budget and cannot inspect state, create a feature, or write
memory.

## Basis

- [Google SRE: Eliminating Toil](https://sre.google/workbook/eliminating-toil/)
  recommends quantifying repetition, cost, benefit, and risk.
- [NIST AI RMF Measure](https://airc.nist.gov/airmf-resources/airmf/5-sec-core/)
  calls for metrics, benchmark comparisons, uncertainty, and documented change.
- [CISA Secure by Design](https://www.cisa.gov/news-events/alerts/2025/01/17/cisa-and-fbi-release-updated-guidance-product-security-bad-practices)
  places security throughout the product lifecycle.
