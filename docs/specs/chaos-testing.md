# Chaos testing

Prompt 89 adds deterministic, local fault injection for eight runtime
boundaries. Every experiment defines a steady-state recovery assertion and
uses a temporary database, fake provider, or deterministic adapter. It does not
contact production services or require credentials.

| Injected fault | Expected degraded behavior | Recovery assertion |
| --- | --- | --- |
| Provider unavailable | Task ends `failed` with a retained retryable `ConnectionError`. | Runtime process remains usable. |
| Model timeout | Task ends `failed` with a retained retryable `TimeoutError`; no false action/result is produced. | A later task may retry. |
| Database locked | The bounded SQLite wait ends in a retryable `OperationalError`; no memory row is partially written. | The same write succeeds after lock release. |
| Tool crash | The task captures a non-retryable `RuntimeError`, emits `task.error`, and produces no false result. | Failure is terminal and inspectable. |
| Corrupt memory | Retrieval fails closed at the task boundary without altering healthy memory. | Retrieval succeeds after the row is repaired. |
| Invalid skill | Format validation fails before registry mutation. | A valid package can still be validated afterward. |
| Partial writes | Mixed-currency accounting failure rolls back both telemetry and cost rows. | Neither half of the transaction remains. |
| Agent failure | One research worker failure retains a failed run but commits no partial findings. | SQLite quick-check remains healthy. |

Retryability is deliberately narrow: connection loss, timeouts, explicit
provider-unavailable errors, and SQLite lock contention are transient.
Malformed state and deterministic code failures are not automatically retried.
This avoids retry storms while preserving safe recovery opportunities.

The design follows the
[Principles of Chaos Engineering](https://principlesofchaos.org/) by stating
measurable steady state, injecting realistic failures, and minimizing blast
radius. It also follows
[AWS Well-Architected graceful-degradation guidance](https://wa.aws.amazon.com/wellarchitected/2020-07-02T19-33-23/wat.question.REL_5.en.html)
by making dependency failures explicit without corrupting adjacent state.
