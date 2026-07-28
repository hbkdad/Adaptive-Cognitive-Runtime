# Autonomous improvement loop

Prompt 67 adds an operator-started, bounded policy-improvement loop. It does
not add a scheduler, a recursive source-code editor, or authority to change
security controls.

## Safety boundary

The numeric policy ledger accepts exactly three targets:

- `retrieval_weights`: ten integer basis-point weights that sum to 10,000;
- `context_thresholds`: `minimum_optional_utility_bps`;
- `skill_routing_thresholds`: skill selection's minimum-benefit and overlap
  thresholds.

Unknown keys, nested values, booleans, floating-point values, values outside
0..10,000, and excessive per-field changes are rejected. Model/tool routing,
security policy, permissions, secret handling, privacy, retention, and deletion
are outside this allowlist at both the Python and SQLite boundaries.

`skill_instructions` is named by Prompt 67 but is not automatically promoted
by the numeric policy ledger. Readiness fails closed until a governed adapter
can reuse the existing Skill Lab validation, unchanged capability manifest,
benchmark, atomic activation, and rollback path.

## Version and evaluation contract

Policy versions are immutable canonical JSON records with SHA-256 digests.
Each target has a mutable head pointing to one immutable version. Runtime
retrieval, context filtering, and skill routing resolve the current head for
each operation, and compiled tasks attribute the exact versions and hashes
used.

One improvement iteration has one incumbent and one candidate. A one-use,
expiring authorization binds the target, hashed scope, incumbent and candidate
hashes, controlled benchmark identity, and maximum case count.

The benchmark adapter runs the incumbent and candidate on paired cases. It
returns fixed-point, content-minimized evidence; caller-supplied aggregate
metrics are not an activation interface. Promotion requires all gates:

- complete benchmark and minimum case count;
- zero hard violations;
- zero protected regressions;
- minimum practical utility improvement;
- incumbent still equals the benchmarked head.

A rejected candidate never changes the head. Promotion and rollback use
`BEGIN IMMEDIATE` compare-and-swap transactions. Rollback can only replace the
exact still-current head, so it cannot clobber a newer revision.

## Current readiness

The framework is testable with sealed fixtures, but a live target remains
blocked until it has at least 30 successful tasks attributed to its exact
active version. This prevents pre-Prompt-67 demo tasks from being
misrepresented as causal production evidence.

```powershell
python -m acr_runtime.cli --db .acr/acr.db improvements status
python -m acr_runtime.cli --db .acr/acr.db improvements readiness retrieval_weights
python -m acr_runtime.cli --db .acr/acr.db improvements readiness context_thresholds
python -m acr_runtime.cli --db .acr/acr.db improvements readiness skill_routing_thresholds
```

Run creation remains an application/API integration point for a trusted
`ControlledBenchmarkAdapter`. The CLI intentionally does not accept an
arbitrary metrics file as proof. Reports and guarded rollback are available:

```powershell
python -m acr_runtime.cli --db .acr/acr.db improvements report RUN_ID
python -m acr_runtime.cli --db .acr/acr.db improvements rollback TARGET --expected-head VERSION_ID
```

## Research basis

- [NIST AI RMF Govern function](https://airc.nist.gov/airmf-resources/airmf/5-sec-core/)
- [OpenAI evaluation best practices](https://developers.openai.com/api/docs/guides/evaluation-best-practices)
- [FDA adaptive-design guidance](https://www.fda.gov/media/78495/download)
- [NIST multiple-comparison guidance](https://www.itl.nist.gov/div898/handbook/prc/section4/prc47.htm)
- [Google SRE canarying releases](https://sre.google/workbook/canarying-releases/)
- [SQLite transaction semantics](https://www.sqlite.org/lang_transaction.html)
