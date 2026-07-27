# Regression detection

Prompt 43 adds a retained, local-first detector for six runtime health signals:
token consumption, quality, latency, model escalation, memory retrieval, and
skill failure.

## Contract

`acr regressions analyze request.json` compares two non-overlapping windows for
the same explicit `scope` and `task_class`. The caller supplies one aggregate
for each required metric, including baseline and candidate sample counts and an
optional baseline standard deviation. Raw prompts, retrieved memory, and user
identifiers are neither required nor retained.

Each metric has a direction, a practical absolute floor, a relative floor, a
minimum of 30 samples in both windows, and a three-sigma standard-error floor.
An alert is emitted only when the adverse shift clears the largest applicable
floor. This follows the control-chart principle of comparing a current rational
subgroup with an established historical center and variation, while adding a
practical-effect guard against noisy but immaterial alerts.

The initial thresholds are deliberately conservative and versioned in code:

| Metric | Bad direction | Relative | Absolute |
| --- | --- | ---: | ---: |
| token consumption | higher | 20% | 100 tokens |
| quality | lower | 5% | 0.03 |
| latency | higher | 20% | 50 ms |
| model escalation | higher | 25% | 0.05 |
| memory retrieval | lower | 10% | 0.05 |
| skill failure | higher | 25% | 0.05 |

## Change attribution

A change may be named as *likely responsible* only when all of these hold:

1. its timestamp is between the baseline end and candidate start;
2. its declared domain can affect the regressed metric;
3. its evidence explicitly lists that metric; and
4. it is the unique most recent eligible change.

Otherwise the alert is retained as unattributed or ambiguous. Even a matched
change is labelled a temporal/domain/metric hypothesis, not causal proof.

## Rollback safety

If an attributed change has an explicit `rollback_ref`, the detector creates a
`proposed` rollback recommendation. It does not invoke Git, mutate a production
default, activate a skill version, change model routing, or execute any rollback.
Operator review remains mandatory.

## Evidence basis

- NIST describes control charts as a historical center plus control limits and
  says an out-of-control point warrants investigation for an assignable cause:
  <https://www.itl.nist.gov/div898/handbook/pmc/section3/pmc31.htm>
- NIST distinguishes statistical control limits from product specification
  limits and documents the conventional three-sigma limit:
  <https://www.itl.nist.gov/div898/handbook/pmc/section3/pmc32.htm>
- Azure Monitor's current dynamic-threshold guidance requires at least 30
  samples before alerting and uses minimum violation duration to reduce brief
  false positives:
  <https://learn.microsoft.com/en-us/azure/azure-monitor/alerts/alerts-dynamic-thresholds>

These references motivate the safeguards; ACR's fixed thresholds are product
policy, not claims copied from those systems.
