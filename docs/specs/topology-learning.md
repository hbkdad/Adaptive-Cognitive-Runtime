# Prompt 26: evidence-backed agent topology learning

Prompt 26 records agent-factory outcomes and turns repeatedly successful
structures into reusable recipes. It does not execute a recipe or override the
Prompt 25 factory. Recommendations are advisory.

## Outcome evidence

One outcome may be reported for each retained factory plan. The runtime
derives—not trusts from the reporter—the task class, topology, worker count,
topology, worker count, and parallelism from that plan. The report supplies the
actual models and skills used, tokens, latency, quality, success, verification
status, and non-empty evidence references. Reported models and skills must be
subsets of the plan's allowlists.

All outcomes remain visible, including failures and unverified reports. Only
successful, verified outcomes create a reusable recipe. Recommendation
statistics use verified outcomes only, preventing unverified self-reports from
inflating historical performance.

## Reusable recipe

The canonical recipe retains ordered roles, normalized responsibilities, model
policies, exact skills, and bounded communication shape. Temporary agent IDs,
task-specific objectives, and workstream names are excluded. This lets the same
structure match later work in the same task class without replaying stale task
content.

## Conservative recommendation gate

A compatible recipe is eligible only after:

- at least three verified trials;
- at least two verified successes;
- success rate of at least two thirds;
- average quality of at least `0.70`;
- worker count, average tokens, and average latency within the new request;
- compatible model allowlists; and
- no skills absent from the new request.

Eligible recipes are ranked by quality, success rate, token efficiency, and
latency efficiency. The complete candidate set and rejection reasons are
returned. Missing evidence produces `insufficient_compatible_evidence`, never a
guessed historical recommendation.

This fixed policy avoids online self-modification. Future learning may revise
the policy only through versioned evaluation and migration, not by rewriting
past recipes or outcomes.

## CLI

```powershell
python -m acr_runtime.cli --db .acr/acr.db agents topology-record outcome.json
python -m acr_runtime.cli --db .acr/acr.db agents topology-outcome <OUTCOME_ID>
python -m acr_runtime.cli --db .acr/acr.db agents topology-recipes `
  --task-class competitor-research
python -m acr_runtime.cli --db .acr/acr.db agents topology-recommend `
  examples/agent-factory/research-plan.json
```

Example outcome shape:

```json
{
  "plan_id": "<FACTORY_PLAN_ID>",
  "models_used": ["qwen2.5-coder:7b"],
  "skills_used": [],
  "tokens": 6400,
  "latency_ms": 420000,
  "quality": 0.91,
  "success": true,
  "verification_passed": true,
  "verification_evidence": ["benchmark:competitor-research-v1"]
}
```
