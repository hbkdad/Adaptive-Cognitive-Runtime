# Session-end workflow

Use this workflow after the requested work reaches a verifiable stopping point.
It coordinates existing evidence and learning boundaries; it is not a second
post-task runtime.

1. Re-read the exact objective and evaluate every requested behavior,
   deliverable, invariant, and gate against current evidence. Classify anything
   untested, contradicted, or missing as incomplete.
2. Inspect repository status and the final diff. Record the actual outcome,
   files, tests, decisions, debt, next step, and available metrics. Do not
   synthesize an ACR execution merely to make a coding session look retained.
3. When a real retained task exists, inspect `telemetry task <TASK_ID>` and run
   `learn plan <TASK_ID>` before considering transactional learning. `learn
   run` still requires reviewed deterministic evaluation and attribution
   evidence; a plan is not authorization.
4. Distill only durable discoveries that are supported by repository, test, or
   retained-run evidence and whose persistence the task authorizes.
5. Record an architectural decision with the strict structured
   `memory decision-add` format. Include context, alternatives, reason,
   consequences, date, scope, evidence, assumptions, and `supersedes` when
   replacing an earlier decision.
6. Record a failure only after its symptom, failed action, error type, root
   cause or verified avoidance rule, and remediation evidence are established.
7. Identify a reusable procedure only after repeated success. A single success
   may support an outcome but does not establish a durable procedure or skill.
8. Update memory or skill utility only through actual attribution from a
   retained task and the transactional learning controller. Never manually
   boost utility because an artifact was retrieved, selected, or mentioned.
9. Measure provider-reported or locally measured tokens and existing waste
   evidence when available. Run a new waste scan only when its state change is
   in scope. Label missing token, cost, latency, and waste measurements
   unavailable instead of estimating them.
10. Run the final diff and staged-secret checks. Commit or publish only when
    authorized, then verify the local and remote checkpoint.

## Persistence filter

Persist structured knowledge, not a transcript. Exclude greetings,
conversational filler, raw prompts, model output, source bodies, complete logs,
credentials, personal data, speculative conclusions, unverified failures,
one-off preferences, and duplicated telemetry.

The session-end summary should be sufficient to resume from repository and ACR
evidence without saving the conversation itself.
