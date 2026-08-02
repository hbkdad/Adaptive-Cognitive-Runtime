# Active learning (Prompt 118)

ACR treats active learning as a proposal boundary over repeated, retained
uncertainty. It does not use a model confidence threshold, ask a person, browse,
run a command, or spend resources automatically.

## Observation source

The engine reads only `missing_information` findings from Prompt 29 reflection
runs whose task exists in the exact requested scope. It normalizes an explicit
uncertainty key and requires at least:

- three matching reflection occurrences;
- three distinct tasks; and
- a bounded scan of at most 500 findings and 500 reflected tasks.

Repeated reflections for one task do not count as independent demand.
Other-scope findings do not count. Evidence returned by the assessment contains
reflection IDs, hashed task IDs, and hashes of the original evidence lists
rather than copied task or prompt text.

## Value test

The request supplies an evidence-backed impact estimate, probability that the
verification would resolve the uncertainty, expected future uses, interruption
cost, and verification cost. All values are integer millionths of normalized
decision utility, not currency. Recurrence is measured from retained scoped
tasks:

```text
recurrence = distinct tasks with uncertainty / reflected tasks in scope

expected benefit =
  impact × recurrence × resolution probability × expected future uses

expected net value =
  expected benefit - interruption cost - verification cost
```

An action is suggested only when the repetition gates pass and expected benefit
strictly exceeds both declared costs. Otherwise the immutable assessment is
`deferred` with explicit reasons. The calculation is deterministic and rounds
down.

Caller estimates are not learned facts. They require evidence references and
remain visible in the report. A future outcome-calibration benchmark is required
before ACR may tune or infer these values.

## Verification actions

The closed action vocabulary is:

| Action | Required capability if later executed |
| --- | --- |
| ask user | none represented by the capability controller |
| inspect repository | `filesystem.read` |
| consult official documentation | `network.read` |
| run local diagnostic | `shell.execute` |
| compare primary sources | `network.read` |

The report always sets `execution_authority` to false. Required capability is an
informational prerequisite, not a grant. Execution needs a separate authorized
workflow and may still be rejected by zero-cloud policy, Safe Mode, human
override, budgets, or tool policy. Safe Mode blocks assessment writes inside
both the runtime service and the engine.

## Persistence and CLI

Schema version 68 retains immutable, idempotent assessment runs. The request and
exact observation-set hashes prevent duplicate records. Stored rows include
bounded action metadata, decision inputs, derived integer values, hashed
observation references, evidence references, and reasons. They contain no
retrieved memory body, task objective, prompt, tool output, or execution result.

```powershell
python -m acr_runtime.cli --db .acr/acr.db learn active-assess request.json
python -m acr_runtime.cli --db .acr/acr.db learn active-report ASSESSMENT_ID
```

## Research boundary

- Value of Information: A Framework for Human-Agent Communication
  <https://aclanthology.org/2026.acl-long.1987/>
- Active Learning for Cost-Sensitive Classification
  <https://proceedings.mlr.press/v70/krishnamurthy17a.html>
- Practical Obstacles to Deploying Active Learning
  <https://aclanthology.org/D19-1003/>

These sources motivate comparing information gain with human or acquisition
cost and caution that active-learning benefits do not generalize reliably.
ACR does not reproduce their algorithms or reported improvements. Prompt 118
implements only a transparent repeated-evidence and expected-value gate.

## Limitations

- Impact, future-use, resolution, and cost estimates are caller supplied.
- Exact normalized keys do not detect semantically equivalent uncertainty.
- Reflection evidence can become historically stale; the action executor must
  revalidate current need before acting.
- The engine does not track whether a proposed action later resolved the issue.
- No quality, interruption reduction, or cost saving is claimed.
