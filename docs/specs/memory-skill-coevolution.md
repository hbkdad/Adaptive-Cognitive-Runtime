# Memory-skill co-evolution

Prompt 69 connects repeated successful experience to generated skills without
allowing raw traces or caller claims to mint trusted capabilities.

## Governed lifecycle

The experimental path is:

`successful execution -> successful-procedure distillation -> governed
procedural memory -> repeated grounded support -> quarantined candidate skill
-> mandatory validation -> explicitly promoted active skill`

Generation still requires at least three distinct successful traces in one
scope and task class. A managed lineage link is created only when each trace
has an applied successful-procedure item bound to procedural memory. Activation
additionally requires three distinct task roots with terminal execution
verification, a passed evaluation, and current confirmed memory.

Legacy skills without managed links are reported as `unassessed`. They are not
silently assigned co-evolution reliability. Direct `candidate_skill`
distillation remains quarantined and does not satisfy this lifecycle.

## Evidence and trust

Support links, invalidations, reliability snapshots, and lifecycle events are
append-only. Reports contain identifiers, hashes, timestamps, counts, and
closed status values rather than trace bodies, procedures, prompts, or
credentials.

Support becomes invalid when its trace, applied distillation/item, current
procedural memory, or immutable package hash no longer matches. Explicit
invalidations use closed reason codes and are idempotent. An active generated
skill that loses activation eligibility is immediately quarantined before
future routing.

Execution reliability is recomputed only from a selected skill's conclusive
context attribution when the same task has terminal execution verification,
an evaluation run, and an execution attribution score. It uses the 95 percent
Wilson lower confidence bound and is multiplied by the valid-support ratio.
Caller aggregate success claims do not enter the calculation.

## Operator inspection

```powershell
python -m acr_runtime.cli --db .acr/acr.db skills evidence SKILL_ID
python -m acr_runtime.cli --db .acr/acr.db skills reconcile-evidence SKILL_ID
python -m acr_runtime.cli --db .acr/acr.db skills invalidate-support LINK_ID `
  --reason operator_rejected --actor OPERATOR_ID
```

Invalidation is an explicit state-changing operation. Reconciliation is
idempotent and may only preserve or reduce trust when support has become
invalid; it cannot repair evidence by rewriting history.

## Research basis

- [W3C PROV-O](https://www.w3.org/TR/prov-o/) for explicit provenance entities,
  activities, agents, and derivation links.
- [SLSA provenance](https://slsa.dev/spec/v1.1/provenance) and
  [artifact verification](https://slsa.dev/spec/v1.2/verifying-artifacts) for
  immutable subject identity and verification against expected provenance.
- [NIST AI RMF Measure playbook](https://airc.nist.gov/airmf-resources/playbook/measure/)
  for independent measurement, monitoring, and documented evidence.
- [NIST AI 800-4](https://doi.org/10.6028/NIST.AI.800-4) for measuring and
  managing AI system risk throughout the lifecycle.
