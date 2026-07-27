# Memory lifecycle garbage collection (Prompt 9)

Memory trust and storage lifecycle are independent. A confirmed memory can be
active, cold, or archived without rewriting its content or provenance.

## Operator workflow

```powershell
python -m acr_runtime.cli --db .acr/acr.db memory gc --dry-run
python -m acr_runtime.cli --db .acr/acr.db memory gc --approve <RUN_ID>
python -m acr_runtime.cli --db .acr/acr.db memory pin <ID> --reason "operator hold"
python -m acr_runtime.cli --db .acr/acr.db memory unpin <ID>
python -m acr_runtime.cli --db .acr/acr.db memory archive <ID>
python -m acr_runtime.cli --db .acr/acr.db memory restore <ID>
```

Dry runs are persisted but do not mutate memory. Approval reloads the exact run,
checks each target's version, lifecycle state, and current protection policy,
then skips stale targets. A pinned memory requires `archive --force`; restoring
memory is always allowed because it increases preservation.
Legacy records whose trust status was also archived return as candidates so
they can be verified again instead of silently becoming trusted.

## Retention score

The configurable policy has thresholds, half-lives, weights, and a supersession
penalty. It measures:

- time since last use;
- logarithmically normalized usage count;
- importance;
- confidence;
- observed utility;
- supersession status;
- activity recency of the owning scope.

Old, low-retention active memory can become cold. Cold memory must remain cold
for the configured archive interval before it can be archived. Active and cold
memory remain eligible for normal retrieval; archived and deleted lifecycle
states do not.

## Preservation and deletion

Pinned records, all decision memory, critical failure memory, high-value
procedures, and payloads explicitly marked with `"security_event": true` receive
no automatic action. The garbage collector never proposes the deleted state.
Archive is reversible and content is not copied into GC telemetry.
