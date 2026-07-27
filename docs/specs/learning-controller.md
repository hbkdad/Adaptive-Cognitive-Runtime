# Prompt 30: transactional post-task learning controller

## Boundary

The learning controller runs only after a retained execution reaches `completed`
or `failed`. The execution record is the immutable outcome boundary: learning
never updates `execution_runs` or `tasks`.

One SQLite `BEGIN IMMEDIATE` transaction performs the complete ordered pipeline:

1. deterministic evaluation;
2. context attribution;
3. resource-efficiency calculation;
4. experience distillation, when a trace is supplied;
5. memory-candidate generation;
6. memory-utility updates;
7. skill-utility updates;
8. repeated-pattern skill-candidate identification;
9. routing-improvement identification;
10. regression detection.

Evaluation, distillation, and skill-generation stores accept a caller-managed
transaction for this path. Their normal standalone APIs continue to manage their
own commits.

## Inputs and prerequisites

A learning request identifies one terminal `execution_run`, supplies a strict
evaluation case, and may supply:

- model, execution, tool-dependency, ignored, misled, and evaluator attribution
  signals;
- an experience trace from the same task;
- a scope for repeated-pattern skill discovery;
- task class and model dimensions;
- quality, token, duration, and cost regression limits.

The task must have retained context rows. Any attribution reference outside that
selected context fails closed. A task with legacy attribution is rejected to
prevent double utility updates, and one execution run can have only one learning
run.

## Outputs and mutation policy

The transaction retains:

- one grounded Prompt 28 evaluation;
- context-attribution records and conclusive memory/skill utility updates;
- measured token, context-waste, latency, and cost efficiency;
- an optional Prompt 11 distillation plan;
- proposed memory candidates, never confirmed memories;
- a Prompt 19 skill-generation plan, never generated packages;
- proposed routing changes, never automatic route changes;
- review-only regression records;
- all ten ordered stage results.

Uncertain attribution does not update utility. A source counts as successful
only when it contributed and both execution and evaluation passed.

## Failure isolation

Every learning-side insert and utility update shares one explicit transaction.
Any exception triggers an explicit rollback. Tests inject failure after utility
updates and verify that evaluations, attribution, distillation, candidates,
utility counters, and controller rows all return to their prior state while the
successful execution row remains byte-for-byte unchanged.

## CLI

```powershell
python -m acr_runtime.cli --db .acr/acr.db learn run learning-request.json
python -m acr_runtime.cli --db .acr/acr.db learn report <RUN_ID>
```
