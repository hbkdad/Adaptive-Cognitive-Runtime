# Future control-center boundary

This is a planning contract, not an implemented network API.

## First read surface

- `GET /status`
- `GET /overview`
- `GET /tasks?cursor=&limit=`
- `GET /memories?scope=&status=&cursor=&limit=`
- `GET /events?after=&limit=`
- `WS /events?after=`

Every collection is bounded and cursor-paginated. Events have stable sequence
identifiers so a disconnected client can replay before resuming live updates.
Payloads are sanitized DTOs; database rows, raw prompts, secrets, and private
memory content are not telemetry.

## Deferred writes

Memory confirmation, quarantine, archival, supersession, and rollback endpoints
are deliberately deferred until the write controller can enforce transitions,
scope authorization, optimistic concurrency, provenance, and an append-only
audit event.

## UI separation

The operations dashboard is the primary interface. Graph and 3D routes consume
the same DTOs but are separate code chunks and failure boundaries. Command,
Visualize, and Focus are presentation modes, not alternate sources of truth.
