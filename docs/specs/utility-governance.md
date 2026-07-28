# Utility governance

Prompt 70 adds shared lifecycle economics for memory, skills, models, tools,
agent topologies, and context strategies. Utility is a derived estimate over
append-only actual-use evidence, not a caller-editable truth field.

## Evidence boundaries

Each asset is registered by kind, an opaque external-identity hash, an exact
revision hash, and a scope hash. A revision change creates a new asset
generation rather than silently inheriting incomparable results.

Canonical observations come only from:

- memory and skill selections bound before execution, then resolved through a
  terminal task, execution verification, evaluation, and context attribution;
- routed model attempts, never standalone caller-submitted model outcomes;
- tools selected by a retained route and their one retained outcome;
- plan-bound agent-topology outcomes, with unverified outcomes retained only
  as uncertain exposure;
- the exact context-strategy configuration bound before a task. Until
  production paired attribution exists, strategy use remains uncertain and
  receives no positive credit.

Retrieval, search rank, route candidacy, selection frequency, and offline
benchmarks cannot enter the positive-utility numerator. Uncertain exposure is
visible but changes neither utility nor confidence. A verified misleading
attribution carries negative signed benefit even when the asset was retrieved
frequently.

## Conservative snapshots

Snapshots are rebuildable materializations of immutable observations. They
separate observed uses from evidenced uses and retain positive, ignored,
misled, and failed counts. Reported utility is the 95 percent Wilson lower
confidence bound over evidenced positive outcomes. Signed benefit, evidence
coverage, the exact estimator revision, and last-use time remain separate.

The lifecycle assessment is advisory:

- `unassessed`: no evidenced use; collect evidence;
- `probation`: fewer than three evidenced uses; review;
- `productive`: adequate evidence and conservative utility; retain;
- `degrading`: misleading evidence or a low conservative bound; lifecycle
  review.

Utility cannot grant permissions, activate skills, weaken security, promote a
context strategy, delete data, or directly retire an asset. Existing
domain-specific governance remains authoritative.

## Operator inspection

```powershell
python -m acr_runtime.cli --db .acr/acr.db utility list
python -m acr_runtime.cli --db .acr/acr.db utility list --kind memory
python -m acr_runtime.cli --db .acr/acr.db utility show memory MEMORY_ID
```

Outputs contain hashes, IDs internal to the ledger, bounded metrics, counts,
and closed states. They do not contain memory bodies, prompts, tool results,
model responses, or raw evidence.

## Research basis

- [NIST Wilson confidence interval guidance](https://www.itl.nist.gov/div898/handbook/prc/section2/prc241.htm)
- [NIST AI RMF Core](https://airc.nist.gov/airmf-resources/airmf/5-sec-core/)
- [NIST AI RMF Manage playbook](https://airc.nist.gov/airmf-resources/playbook/manage/)
- [NIST life-cycle cost analysis](https://nvlpubs.nist.gov/nistpubs/hb/2025/NIST.HB.135e2025.pdf)
- [W3C PROV-O](https://www.w3.org/TR/prov-o/)
- [Doubly robust policy evaluation](https://www.microsoft.com/en-us/research/publication/doubly-robust-policy-evaluation-and-learning-2/)
