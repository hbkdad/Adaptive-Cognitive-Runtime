# Prompt 64: task resource governor

ACR schema 44 adds immutable, task-scoped resource budgets for:

- `max_input_tokens`
- `max_output_tokens`
- `max_model_calls`
- `max_tool_calls`
- `max_agents`
- `max_cost`
- `max_duration`

Cost is stored as integer microunits and duration as integer milliseconds.
`max_agents` includes the root agent. Every budget contains a soft vector and an
immutable hard vector; each soft value must be less than or equal to its hard
value.

## Reservation contract

Work must reserve an upper-bound quote before dispatch. `BEGIN IMMEDIATE`
serializes the read/check/write sequence, and a database trigger independently
rejects any usage update whose held plus committed totals exceed one hard
limit.

A reservation has one of three states:

- `reserved`: capacity is held before work starts;
- `committed`: authoritative actual usage, bounded by the quote, replaces the
  hold;
- `released`: capacity is returned only with evidence that work never started.

Idempotency keys are unique within a task. Replaying the same key and quote
returns the original reservation; changing the quote is rejected. Provider or
tool failure after dispatch leaves the reservation held because the runtime
cannot prove that remote work stopped. Restarting ACR never auto-releases a
possibly live reservation.

Hard budgets cannot be changed or escalated. A direct database update beyond a
hard ceiling is rejected by the schema.

## Soft escalation

Crossing any soft value is denied unless the immutable budget declared
`manual_exact` escalation and an unexpired approval matches:

- the exact task;
- the complete resource quote;
- one approval reference, reason, and evidence set.

The approval is one-shot because only one reservation may reference it.
Automatic model escalation, confidence adjustment, retries, and routing
recommendations cannot approve resource escalation. An approval above a hard
limit is rejected.

## CLI

Create a JSON budget:

```json
{
  "soft": {
    "input_tokens": 6000,
    "output_tokens": 400,
    "model_calls": 1,
    "tool_calls": 2,
    "agents": 1,
    "cost": 0,
    "duration": 90000
  },
  "hard": {
    "input_tokens": 8000,
    "output_tokens": 512,
    "model_calls": 2,
    "tool_calls": 3,
    "agents": 2,
    "cost": 0,
    "duration": 120000
  },
  "escalation_mode": "manual_exact",
  "evidence": ["operator:task-plan"]
}
```

```powershell
python -m acr_runtime.cli --db .acr/acr.db resources create TASK_ID budget.json
python -m acr_runtime.cli --db .acr/acr.db resources status TASK_ID
python -m acr_runtime.cli --db .acr/acr.db resources approve TASK_ID quote.json `
  --approval-reference APPROVAL_ID --reason "Reviewed extra call" `
  --evidence operator-review
```

Raw reservation and commit operations are intentionally library boundaries, not
operator CLI shortcuts.

`acr run` now creates finite hard limits, reserves the root agent, reserves the
model call before dispatch, sets Ollama's native output cap, configures its
transport timeout from `max_duration`, and commits returned token and latency
usage. Tool selection remains distinct from tool invocation; the external MCP
adapter charges `max_tool_calls` immediately before the remote call.

## Enforcement boundary

The ledger guarantees that ACR never authorizes capacity above a hard limit.
Physical wall-time enforcement additionally requires a cooperative native
transport timeout or killable process boundary. `TaskRunner` uses a monotonic
deadline between every phase, Ollama uses a native request timeout, and the MCP
adapter uses a bounded async timeout. A new blocking adapter must provide an
equivalent boundary before it can claim governed hard-duration execution.

Input-token enforcement likewise requires an authoritative upper-bound quote.
Post-hoc telemetry is evidence, not authorization. Unknown or unbounded quotes
must fail closed instead of relying on estimates.

Design references:

- [SQLite transaction control](https://sqlite.org/lang_transaction.html) for
  serialized `BEGIN IMMEDIATE` reservations.
- [SQLite trigger `RAISE`](https://sqlite.org/lang_createtrigger.html) for the
  independent hard-limit invariant.
- [Kubernetes ResourceQuota](https://kubernetes.io/docs/concepts/policy/resource-quotas/)
  for hard desired-versus-used quota semantics.
- [Python monotonic clocks](https://docs.python.org/3/library/time.html#time.monotonic)
  for in-process deadlines.
