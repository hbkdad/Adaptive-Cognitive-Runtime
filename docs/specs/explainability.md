# Prompt 77: runtime explainability

Prompt 77 adds a read-only inspection surface for six runtime questions:

- why a model was chosen;
- why a skill was loaded;
- why a memory was retrieved;
- why an agent was spawned;
- why a context bundle consumed its tokens; and
- why a memory was forgotten.

The inspector renders retained facts and source-row identities. It does not
call a model, generate a causal narrative, rescore historical candidates, or
mutate runtime state. Every response includes a status, exact facts, source
rows, limitations, and `narrative_generated: false`.

## Evidence contract

Model answers replay the immutable candidate snapshot in `model_routes`.
Skill answers use the routing run and compiler selection rows. Memory and token
answers use the exact `context_uses`, attribution, task, and optional budget
plan rows. Forgetting answers use lifecycle state, supersession, garbage
collection actions, and verified deletion requests.

The inspector says `unavailable` when the relevant selection evidence was not
retained. It says `partial` when a terminal state exists but the retained rows
cannot establish the full cause. External model allow/prefer constraints from
older route calls were not persisted, so a model selection that differs from
the cheapest stored eligible candidate is explicitly partial.

Agent Factory currently produces proposals only. Its inspection response is
therefore `not_executed`, even when a plan contains worker specifications.
The response exposes bounded plan metadata but omits stored objectives and
never claims that a worker was spawned without an execution receipt.

## CLI

```powershell
python -m acr_runtime.cli --db .acr/acr.db explain model ROUTE_ID
python -m acr_runtime.cli --db .acr/acr.db explain skill TASK_ID SKILL_ID
python -m acr_runtime.cli --db .acr/acr.db explain memory TASK_ID MEMORY_ID
python -m acr_runtime.cli --db .acr/acr.db explain agent PLAN_ID
python -m acr_runtime.cli --db .acr/acr.db explain agent PLAN_ID `
  --worker-id WORKER_ID
python -m acr_runtime.cli --db .acr/acr.db explain context TASK_ID
python -m acr_runtime.cli --db .acr/acr.db explain forgotten MEMORY_ID
```

No schema migration is required: Prompt 77 deliberately reuses the decision,
scoring, attribution, and lifecycle evidence already retained through schema
56.
