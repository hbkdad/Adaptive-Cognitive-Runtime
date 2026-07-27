# Prompt 40: privacy engine

Every memory has one closed sensitivity label:

`public < internal < personal < confidential < secret`

The default is `internal`, including for records migrated into schema 34.
Credential detection remains a stronger independent boundary: tagging content
`secret` never permits API keys or other detected credentials into memory.

## Versioned policy

Each classification specifies:

- exact provider identifiers allowed to receive the data (`local` is the only
  reserved provider class; broad `cloud` permission is forbidden);
- a retention period or explicit no-automatic-expiry setting;
- whether an exact set may be exported;
- standard or secure deletion.

Defaults are conservative. All classes allow local processing only. Personal
memory expires after 365 days, confidential after 90, and secret after 30.
Confidential and secret memory are non-exportable. Policy replacement creates
an immutable version event; existing memory remains bound to the version used
when it was created or explicitly reclassified.

Sensitivity increases are permitted with a recorded actor and reason.
Downgrades need the separate `allow_downgrade` signal. Provider evaluation is
all-or-nothing across the exact memory IDs, and the local model router applies
it when its request identifies memories.

## Retention and export

Creation computes `retention_until` from the active classification policy.
`privacy retention-due` returns IDs, classification, and deadline only. It does
not return content or automatically destroy data. Export is all-or-nothing:
one non-exportable or deleted record blocks the complete requested set and
records the decision.

## Verified erasure

Erasure is deliberately two-step:

1. `delete-plan` binds a request to the exact memory version and deletion
   requirement. The operator's reason is retained as a hash, not raw text.
2. `delete-approve` rejects stale plans, erases every content-bearing memory
   field, removes the old FTS terms, marks an irreversible UUID tombstone, and
   verifies the result.

Direct status/lifecycle transitions to `deleted` are blocked. The tombstone
preserves foreign-key audit integrity but contains only fixed deletion text and
non-content identifiers.

The runtime enables SQLite core `secure_delete`. Erasure also enables FTS5
`secure-delete`; secure-class deletion checkpoints WAL and runs `VACUUM`.
Verification records whether content fields and FTS were cleared, whether the
file rewrite completed, and that backup cleanup is still required.

Database backups are independent copies and cannot be truthfully erased by
rewriting the active database. Operators must apply a documented backup
retention/deletion process to every backup named before the erasure completion
time. Storage-device snapshots and provider-side copies are likewise outside
the local SQLite guarantee.

## CLI

```powershell
python -m acr_runtime.cli --db .acr/acr.db privacy policies
python -m acr_runtime.cli --db .acr/acr.db privacy classify <MEMORY_ID> personal `
  --actor privacy-admin --reason "Contains a user preference"
python -m acr_runtime.cli --db .acr/acr.db privacy provider-check ollama `
  <MEMORY_ID> --local
python -m acr_runtime.cli --db .acr/acr.db privacy retention-due
python -m acr_runtime.cli --db .acr/acr.db privacy delete-plan <MEMORY_ID> `
  --actor privacy-admin --reason "User erasure request"
python -m acr_runtime.cli --db .acr/acr.db privacy delete-approve <REQUEST_ID>
python -m acr_runtime.cli --db .acr/acr.db privacy delete-report <REQUEST_ID>
```
