# Prompt 122: continuous quality gates

Prompt 122 adds an immutable final quality assessment and explicit human
approval boundary to the numeric autonomous-improvement loop. A benchmarked
candidate cannot change an active policy head until this boundary is satisfied.

## Gate contract

Every benchmark adapter must supply retained references plus all six gate
inputs:

1. unit tests passed;
2. security checks passed;
3. benchmark quality meets its declared fixed-point threshold;
4. token regression is within its declared basis-point threshold;
5. cost regression is within its declared basis-point threshold;
6. latency regression is within its declared basis-point threshold.

Incomplete benchmarks, insufficient cases, hard benchmark violations,
protected regressions, and failure to improve incumbent utility remain
non-waivable blockers. Unit-test and security-check failures are also
non-waivable.

Benchmark quality, token, cost, and latency misses are quantitative tradeoffs.
An approval must name every and only failed quantitative gate and retain a
bounded justification and evidence references. Passing gates cannot be listed
as tradeoffs.

## Approval and storage

Schema 72 owns one immutable `continuous_quality_assessments` row per
improvement run and at most one immutable
`continuous_quality_approvals` row per assessment. The assessment binds the
candidate, metrics, thresholds, results, and evidence with a SHA-256 hash.

Approval input must use a `human:` actor reference. The database stores hashes
of the actor and justification rather than their raw values. This is content
minimization, not authentication: the caller remains responsible for binding
the reference to a real authorized operator.

A rejection ends the run without changing the active head. An approval invokes
the existing compare-and-swap promotion using the retained incumbent and
candidate. Safe Mode blocks final approval and promotion. If compare-and-swap
promotion fails after the approval is retained, an exact retry may complete the
same approval; a different decision is rejected.

## CLI

```powershell
python -m acr_runtime.cli --db .acr/acr.db improvements report RUN_ID
python -m acr_runtime.cli --db .acr/acr.db improvements approve APPROVAL.json
```

The approval file is a closed version-1 JSON object containing `run_id`,
`assessment_hash`, a `human:` actor reference, `approve` or `reject`, the exact
failed quantitative tradeoffs being accepted, a justification, and bounded
evidence references.

Prompt 122 does not run tests or security tools itself, authenticate an
operator, choose thresholds, infer evidence, or broaden the set of autonomous
targets. Skill certification and evolution retain their existing separate
manual promotion workflows.
