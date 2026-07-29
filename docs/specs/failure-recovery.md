# Prompt 83: failure recovery

ACR's recovery controller persists an exact classified step plan before work
begins. Each step retains:

- sequence and operation;
- bounded secret-scanned JSON input plus its SHA-256 hash;
- one globally unique idempotency key;
- action class and destructive marker;
- automatic-attempt limit and current attempt count;
- pending, running, completed, failed, or review-required state;
- content-minimized failure hashes; and
- successful output and evidence.

The four action classes are:

- `idempotent`: an interrupted or known-failed attempt may replay with the same
  idempotency key, within its attempt limit;
- `retryable`: a known failure may retry, but an interruption with unknown
  outcome requires review;
- `non-retryable`: one attempt is allowed; failure or ambiguous interruption
  requires review; and
- `human-review-required`: a reviewer must approve the initial attempt and
  resolve any later ambiguity.

Destructive steps may only be `non-retryable` or
`human-review-required`.

## Checkpoint and resume protocol

The controller commits `step.started` and changes both the run and step to
`running` before calling external executor code. It commits output evidence and
`step.completed` only after the executor returns. Completed earlier steps are
never called again during resume.

If a process terminates between those checkpoints, the run remains `running`.
Another worker cannot claim it. An operator must first confirm the old worker is
dead with `recovery interrupt`. Idempotent work then becomes retryable with the
same key; every other unknown outcome becomes `review_required`.

A reviewer can:

- `execute` after checking that a retry is appropriate;
- `accept_completed` only when independent evidence proves an interrupted
  ambiguous action actually completed; or
- `abort`.

These decisions retain actor, reason hash, and bounded evidence. The controller
does not infer success from a timeout and does not execute arbitrary operations
from the CLI. Host code supplies an explicit `RecoveryExecutor`; the CLI manages
plans, interruption acknowledgement, review, and inspection only.

```powershell
python -m acr_runtime.cli --db .acr/acr.db recovery create plan.json
python -m acr_runtime.cli --db .acr/acr.db recovery inspect <RUN_ID>
python -m acr_runtime.cli --db .acr/acr.db recovery interrupt <RUN_ID> `
  --actor operator --reason "Worker exit confirmed" `
  --evidence "process:stopped"
python -m acr_runtime.cli --db .acr/acr.db recovery review <RUN_ID> 2 `
  accept_completed --actor operator `
  --reason "Provider status confirms completion" `
  --evidence "provider:operation-id"
```

## Primary references

- [AWS: Making retries safe with idempotent APIs](https://aws.amazon.com/builders-library/making-retries-safe-with-idempotent-APIs/)
- [AWS: Control and limit retry calls](https://docs.aws.amazon.com/wellarchitected/latest/framework/rel_mitigate_interaction_failure_limit_retries.html)
- [Stripe idempotent requests](https://docs.stripe.com/api/idempotent_requests)
- [Azure Service Bus duplicate processing guidance](https://learn.microsoft.com/en-us/azure/service-bus-messaging/service-bus-message-loss-and-duplicates)
- [Azure background job guidance](https://learn.microsoft.com/en-us/azure/well-architected/design-guides/background-jobs)
