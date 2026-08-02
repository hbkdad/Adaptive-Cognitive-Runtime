# Prompt 123: development prioritizer

Prompt 123 adds an immutable advisory ranking over one explicitly declared
development-work inventory. It does not scrape unrelated stores, infer that an
inventory is complete, create work, or authorize implementation.

## Input contract

A version-1 request contains 1 to 256 unique candidates in these closed kinds:

- bug;
- technical debt;
- feature request;
- benchmark failure;
- security finding;
- token waste.

Each candidate has a bounded title, source references, and evidence-backed
integer estimates for expected value, confidence in basis points, observed
frequency, effort, and delivery risk. Effort and delivery risk must be
positive, so the denominator cannot be zero.

Security impact belongs in expected value. `delivery_risk_points` describes the
risk of implementing the proposed work; it is not vulnerability severity or
probability of exploitation.

The caller labels the inventory `complete` or `partial` and supplies an
inventory reference. A complete label is retained as
`caller_asserted_complete`, not independently verified truth.

## Ranking

The score uses fixed-point integer arithmetic:

```text
priority_micros =
floor(
  expected_value_points * confidence_bps * frequency_count * 1,000,000
  / (effort_points * delivery_risk_points * 10,000)
)
```

Higher scores rank first. Exact ties use lower effort, lower delivery risk, and
then stable candidate identifier order. Reports expose every input, evidence
reference, score, and formula.

Schema 73 stores immutable runs and candidates. Exact repeated requests return
the retained run. Safe Mode blocks new rankings while reports remain readable.

## CLI

```powershell
python -m acr_runtime.cli --db .acr/acr.db prioritize create REQUEST.json
python -m acr_runtime.cli --db .acr/acr.db prioritize report RUN_ID
```

Every report is advisory-only, denies implementation authority, and records
that no automatic action occurred. Prompt 123 does not estimate values from
prose, resolve dependencies, modify project priorities, import issue trackers,
or claim that this heuristic is empirically optimal.
