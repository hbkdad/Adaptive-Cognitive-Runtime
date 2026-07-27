# Skill benchmark

Prompt 45 adds a retained three-arm comparison for an exact skill family:

1. `without_skill`
2. `existing_skill`
3. `candidate_skill`

Every case must contain all three arms with the same case ID and task class.
Each trial records quality, total tokens including skill-instruction overhead,
end-to-end latency, cost, operational failure, and bounded evidence. The runtime
reports each metric separately, both overall and by task class.

Run a comparison or inspect retained evidence with:

```powershell
python -m acr_runtime.cli --db .acr/acr.db benchmark skill request.json
python -m acr_runtime.cli --db .acr/acr.db benchmark skill-report <RUN_ID>
```

The JSON request contains `skill_name`, exact `existing_ref` and
`candidate_ref`, and a `trials` array. A trial contains:

```json
{
  "case_id": "locked-database-1",
  "task_class": "database-diagnostics",
  "arm": "existing_skill",
  "quality": 0.9,
  "tokens": 140,
  "latency_ms": 90,
  "cost": 0.0014,
  "failed": false,
  "evidence": ["evaluation:locked-database-1"]
}
```

## Recommendation policy

At least five paired cases are required for lifecycle advice. Below that, both
versions receive `insufficient_evidence`.

An incumbent earns continued existence when it delivers at least a two-point
quality gain or two-point failure-rate reduction in any measured task class, or
when it has no material resource overhead. If it earns no such value and adds
material tokens, latency, or cost, the benchmark proposes `deprecate`.

A candidate receives `consider_candidate` only when it is weakly better than
the incumbent on quality, failure rate, tokens, latency, and cost; strictly
better on at least one; and non-regressing on quality and failure rate in every
task class. Otherwise it is rejected or, when it adds overhead without value,
proposed for deprecation.

All recommendations remain `proposed`. Benchmarking never invokes skill
activation, quarantine, promotion, deprecation, retirement, or rollback.
Validation, exact package hashes, security stages, and operator approval remain
separate prerequisites for any lifecycle action.

## Research basis

- [SWE-Skills-Bench](https://arxiv.org/abs/2603.15401) uses controlled paired
  with-skill/no-skill execution tests and demonstrates why standalone skill
  success does not establish marginal value.
- [Microsoft Waza](https://github.com/microsoft/waza) supports baseline/trial
  skill evaluation with token, duration, behavior, and repeated-run controls.
- [Berkeley Function Calling Leaderboard methodology](https://gorilla.cs.berkeley.edu/blogs/8_berkeley_function_calling_leaderboard.html)
  reports accuracy, cost, and latency separately and favors executable checks.
- [NIST McNemar reference](https://www.itl.nist.gov/div898/software/dataplot/refman1/auxillar/mcnemar.htm)
  documents paired binary comparison; future nondeterministic live trials can
  add paired significance and confidence intervals without changing this
  deterministic retained schema.

This first implementation analyzes supplied measured trials. It does not claim
to execute a candidate package or validate its sandbox/security posture.
