# Prompt 121: synthetic benchmark generator

Prompt 121 adds deterministic generation and explicit review of synthetic-only
evaluation suites. It does not run a model, infer ground truth, import
historical tasks, or authorize optimization.

## Generation contract

One versioned request declares a suite name, typed generator reference, exact
SHA-256 implementation identity, seed, and two to sixteen capability classes.
Each class supplies a description, one objective template containing exactly
one `{variant}` field, two to sixteen unique variants spanning at least two
difficulty levels, and one bounded evaluation specification.

Generation substitutes variants and orders the resulting 4 to 128 unique cases
by a seed-bound hash. Requests, objectives, evaluators, suites, and cases have
stable hashes. Repeating an exact request returns the retained suite.

Capability classes are explicit request data because ACR has no authoritative
global task taxonomy. The generator does not infer classes from prose,
telemetry, memory, historical tasks, or a model.

## Separation and authority

Schema 71 stores suites, cases, and reviews only in
`synthetic_benchmark_*` tables. Generated cases have `origin=synthetic`, report
that zero historical task rows were used, and never create tasks, replay cases,
memories, skills, or project items. They remain untrusted evaluation data with
no promotion or deployment authority.

Generated objectives are visible for review. All text and JSON are bounded and
secret-scanned before storage.

## Mandatory review

Every suite permits one immutable explicit human review over:

1. `leakage`: cases or close variants were not copied from the bounded
   evaluation inventory;
2. `triviality`: formatting, label leakage, duplicates, or vacuous criteria do
   not make cases superficial;
3. `coverage`: declared classes, variants, difficulties, and evaluation intent
   are represented.

Each assessment is `passed` or `failed` and retains rationale and evidence.
Acceptance for synthetic evaluation requires all three passes plus at least one
separate real-task evidence reference. A failed or incomplete review is
immutable; a corrected suite must be created.

Even an accepted suite remains synthetic-only. The real-task reference is an
anti-exclusivity gate, not proof that synthetic cases predict production
quality. Promotion requires separate governed real-task outcomes.

## CLI

```powershell
python -m acr_runtime.cli --db .acr/acr.db benchmark synthetic-generate REQUEST.json
python -m acr_runtime.cli --db .acr/acr.db benchmark synthetic-report SUITE_ID
python -m acr_runtime.cli --db .acr/acr.db benchmark synthetic-review REVIEW.json
python -m acr_runtime.cli --db .acr/acr.db benchmark synthetic-review-report REVIEW_ID
```

The commands manage data only. Safe Mode blocks generation and review while
reports remain available. Tables are immutable through database triggers.
There is no execution, training, optimization, provider, network, promotion,
release, or deployment command.

Version 1 does not automatically detect semantic contamination, authenticate
review truth, or calibrate synthetic-to-real transfer. Those require separately
evidenced changes.
