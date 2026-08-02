# Structured task similarity

Prompt 119 represents tasks with explicit structured evidence and retrieves
analogous completed history without treating prose similarity as proof.

## Profile contract

One immutable `structured-v1` profile may be attached to an existing task:

- `intent` and `domain` are normalized categorical tokens;
- `required_capabilities` uses ACR's closed capability vocabulary;
- `artifacts`, `tools`, and `environment` are bounded normalized token sets;
- one to eight bounded evidence references identify the profile's provenance.

Profiles are caller-authored evidence. The runtime does not infer or backfill
legacy profiles from task objectives, traces, model output, or source labels.
Duplicate, unknown, secret-like, oversized, or post-creation profile changes
fail closed. Safe Mode blocks profile creation.

## Retrieval boundary

`TaskSimilarityEngine.similar` compares a target profile only with profiles for
completed tasks in the exact same task scope. Planned work, unrelated scopes,
unprofiled legacy tasks, and the target itself are excluded. Candidate scans
are capped at 500 and output is capped at 50.

The deterministic score is measured in integer micros:

| Feature | Weight |
| --- | ---: |
| intent | 0.25 |
| domain | 0.20 |
| required capabilities | 0.15 |
| artifacts | 0.10 |
| tools | 0.10 |
| environment | 0.20 |

Intent and domain use exact equality. Set features use Jaccard similarity.
Empty sets contribute zero even when both sides are empty, preventing sparse
profiles from receiving free similarity. The engine exposes every feature
score and weight. It does not read task objectives, create embeddings, invoke a
model, or call tools.

Historical success or failure and critic score are reported as observations,
not causal evidence. Every result carries `analogy_only: true` and
`execution_authority: false`; downstream reuse requires its own current
validation and authorization.

## Persistence and interfaces

Schema v69 adds `task_feature_profiles` with update and delete denial triggers.
The public Python service supports profile creation and analogous retrieval.
The CLI provides:

```text
acr task profile-add PROFILE.json
acr task profile-show TASK_ID
acr task similar TASK_ID [--limit N] [--minimum-score-micros N]
```

## Research boundary and limitations

Primary research motivates structured task representations and warns about
negative transfer. It does not authorize these weights or establish their
quality for ACR. The weights are a transparent conservative v1 policy, not a
calibrated similarity model. Prompt 119 therefore makes no claim that returned
history improves task outcomes. A local labeled benchmark is required before
changing weights, adding embeddings, widening scope, or automating reuse.
