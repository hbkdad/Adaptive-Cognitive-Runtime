# Offline replay engine

Prompt 120 enables controlled offline evaluation of new models, skills,
routers, and context algorithms against immutable recorded cases.

## Replay-case boundary

A replay case must reference a completed source task and explicitly provide:

- one bounded JSON input object;
- one bounded JSON evaluation specification;
- `public` or `internal` privacy classification;
- optional bounded privacy approval reference;
- one to eight provenance references.

ACR never converts ordinary task objectives into replay cases. Inputs and
evaluation specifications are limited to 64 KiB each, secret scanned, hashed,
and retained immutably. Version 1 rejects personal, confidential, and secret
inputs because the immutable store does not yet implement privacy erasure.
Reports expose hashes and provenance, not raw case contents.

## Target and adapter contract

Each replay request identifies exactly one target kind:

- `model`;
- `skill`;
- `router`;
- `context_algorithm`.

The target has a bounded reference and required SHA-256 version identity.
Requests also bind an evaluator reference, deterministic seed, and evidence.

`ReplayEngine` accepts an injected adapter only when its identity declares:

```json
{
  "available": true,
  "isolation": "offline",
  "external_network": "forbidden",
  "side_effects": "none",
  "deployment": "forbidden",
  "adapter": "versioned-adapter-reference"
}
```

The runtime has no production replay adapter by default. A host supplying an
adapter remains responsible for enforcing the claimed isolation. The adapter
receives the immutable case, target identity, evaluator reference, and seed.
It returns only bounded metrics, an output hash, and evidence. Raw output,
reasoning, tool arguments, and environment mutations are not retained.

Safe Mode blocks both case registration and replay-run writes.

## Results and comparison

Schema v70 retains idempotent immutable replay runs with:

- target, adapter, input, and evaluation hashes;
- success and quality micros;
- input and output tokens;
- latency and cost micros;
- output hash and evidence.

`ReplayEngine.compare` accepts only runs with the same case input, evaluation
specification, and evaluator reference. It reports candidate-minus-baseline
quality, token, latency, and cost deltas. It deliberately returns:

- `paired_offline_observation_only: true`;
- `causal_claim: false`;
- `promotion_authority: false`;
- `deployment_authority: false`.

The CLI can register and inspect cases and reports:

```text
acr replay case-add CASE.json
acr replay case-report CASE_ID
acr replay run-report RUN_ID
acr replay compare BASELINE_RUN_ID CANDIDATE_RUN_ID
```

Replay execution is a Python adapter boundary rather than a CLI command so no
shell or provider action can be inferred from stored metadata.

## Limitations

Adapter isolation is a claimed host boundary, not an operating-system sandbox.
Caller-authored cases and evaluation specifications are not authenticated
ground truth. Paired results do not correct for distribution shift,
confounding, evaluator drift, or benchmark leakage. Prompt 120 therefore does
not promote targets or update routing, skills, memory, or policy. Those actions
require separate quality gates and authorization.
