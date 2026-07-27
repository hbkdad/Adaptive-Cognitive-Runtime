# Prompt 60: multi-model coordination

## Outcome

ACR can plan a bounded advisory workflow in which independently verified model
routes handle different responsibilities. It does not call providers, pass
stage output between models, or treat a model response as verified quality.

Model profiles now carry an operator-declared `tier`: `small`, `medium`, or
`strong`. The runtime never guesses tier from a model name, price, parameter
count, or provider. Locality remains a separate profile property.

## Closed roles

Roles map deterministically to required tiers:

| Tier | Roles |
| --- | --- |
| small | classification, memory extraction, routing |
| medium | implementation, summarization |
| strong | architecture, complex debugging, critique |

For a small-tier stage, an eligible local profile is preferred. Every stage
still uses the Prompt 32 router and therefore needs enough verified outcomes
for its exact task class plus the requested quality, success, context, and tool
thresholds. A profile in the wrong tier never enters that stage's candidate
pool.

Requests contain 2–12 ordered stages. Dependencies may reference earlier stages
only, giving a finite acyclic workflow. Planning is atomic and retains each
stage's model-route ID, selected model, role, tier, and dependencies. If any
stage has no eligible model, or fewer than two distinct models are selected,
the workflow is retained as `unavailable`. Planning never executes a route.

## Measured benefit

The caller separately executes and verifies each selected route through the
existing model-attempt boundary. A paired outcome can be recorded only after
every specialized stage reaches `completed`.

Specialized metrics are derived from those retained final attempts:

- success;
- mean stage quality;
- total latency;
- input, output, and total tokens;
- actual input/output cost;
- model IDs and route IDs.

The comparison arm is one predeclared baseline model run with non-empty
evaluation evidence. ACR records quality and success deltas plus latency,
token, and cost savings. It does not copy prompts or responses into the
coordination tables.

One pair never proves specialization. The default report requires three
comparable pairs for the exact workflow class. It reports `beneficial` only
when average quality improves by at least 0.02 or success rate by at least
0.05, with no average latency, token, or cost regression. Otherwise the result
is `insufficient_evidence` or `not_beneficial`. This is measurement, not an
automatic routing-policy update.

## CLI

```powershell
python -m acr_runtime.cli --db .acr/acr.db models register profile.json
python -m acr_runtime.cli --db .acr/acr.db models workflow-plan workflow.json
python -m acr_runtime.cli --db .acr/acr.db models attempt ROUTE_ID attempt.json
python -m acr_runtime.cli --db .acr/acr.db models workflow-outcome `
  WORKFLOW_ID baseline.json
python -m acr_runtime.cli --db .acr/acr.db models workflow-report WORKFLOW_ID
python -m acr_runtime.cli --db .acr/acr.db models workflow-benefit feature-build
```

Schema 42 adds the tier field and the workflow, stage, and paired-outcome
tables. Existing model profiles migrate to `medium`; operators must explicitly
reclassify profiles before assigning them to small or strong roles.
