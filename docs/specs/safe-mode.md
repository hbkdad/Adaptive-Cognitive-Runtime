# Prompt 79: incident-recovery Safe Mode

Safe Mode is an explicit containment state for debugging and incident recovery.
It reduces ACR to retrieval, basic model routing/inference, inspection, audit,
and rollback. It is not a database read-only mode: containment decisions,
blocked attempts, route evidence, and other necessary audit records may still
be appended.

## Commands

```powershell
python -m acr_runtime.cli --db .acr/acr.db safe-mode
python -m acr_runtime.cli --db .acr/acr.db safe-mode enable `
  --actor operator:miche --reason "Contain suspected runtime behavior."
python -m acr_runtime.cli --db .acr/acr.db safe-mode events --limit 50
python -m acr_runtime.cli --db .acr/acr.db safe-mode disable `
  --actor operator:miche --reason "Recovery checks completed."
```

The command without a subcommand reports status. Enable and disable are
deliberately explicit and require a secret-free actor and reason. State survives
process restarts. Definitions of the restricted and permitted surfaces are
returned with every status response.

Set `ACR_SAFE_MODE=1` before startup for an emergency environment latch. Any
non-empty value other than `0`, `false`, `no`, or `off` enables the latch, so a
misspelling fails toward containment. The database mode cannot be reported as
fully disabled until the environment latch is removed. CLI disable refuses to
override it.

## Disabled surfaces

- skill-generation planning and package generation;
- skill-evolution candidate creation and promotion;
- experimental skill-genome mutation;
- Agent Factory generation, including factory calls from hierarchical planning;
- privacy-engine memory erasure;
- autonomous-improvement authorization, candidate creation, and promotion;
- grants and checks for `filesystem.write`, `network.write`,
  `database.write`, `memory.write`, and `shell.execute`.

Existing grants remain retained but ineffective while Safe Mode is active.
Low-level routing records preserve `safe_mode` as the denial reason. Domain
guards are injected into the controllers that own mutation, so calling those
controllers directly does not bypass the CLI.

## Permitted surfaces

- scoped read-only memory, code, and document retrieval;
- inspection and status APIs;
- basic model routing and explicitly configured inference;
- version rollback needed for recovery;
- Safe Mode state changes and append-only audit.

Safe Mode does not erase evidence, silently revoke grants, undo prior state, or
claim that every operating-system write is intercepted. ACR currently has no
production shell or skill executor. The enforced shell boundary is its governed
capability and tool-routing surface; future executors must use that same policy
before dispatch.

## Persistence and audit

Schema 58 stores a singleton containment state and append-only event records.
Enable, disable, and blocked domain actions are timestamped with an actor,
reason, and bounded details. Event update and deletion are prohibited by
database triggers. Blocked operations fail before their domain mutation;
privacy erasure therefore leaves the target memory intact.

Rollback is intentionally not classified as autonomous optimization. It
retains its existing exact-target, state, validation, and compare-and-swap
checks and remains available as a recovery mechanism.
