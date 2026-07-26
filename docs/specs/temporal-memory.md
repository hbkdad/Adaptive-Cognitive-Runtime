# Temporal memory (Prompt 6)

## Truth intervals

Every memory has a half-open validity interval:

```text
[valid_from, valid_until)
```

`valid_from` is inclusive and `valid_until` is exclusive. Timestamps are parsed
as ISO 8601 instants and normalized to UTC for new writes. SQLite filtering uses
`julianday` so equivalent timestamps with different offsets compare correctly.

## Public temporal view

The runtime exposes:

- `memory.current(subject, scope=...)`
- `memory.at(subject, timestamp, scope=...)`
- `memory.history(subject, scope=...)`

Current and point-in-time resolution return a preferred record, all other valid
alternatives, an unresolved-conflict flag, and an explanation. History returns
the trusted records in effective-time order with evidence and supersession links
intact.

## Explicit change

A replacement points to the old record through `supersedes`. The old record
points back through `superseded_by`, and its `valid_until` becomes the
replacement's effective time. A future-effective replacement leaves the old
record confirmed and current until that instant. Backdated and immediate changes
remain queryable as historical truth.

Supersession does not delete or rewrite evidence.
The store rejects replacements that begin before the old record, self/cyclic
links, and attempts to give one record multiple competing replacements. An
existing earlier expiration is never extended by a later replacement.

## Resolution rules

1. filter to trusted records whose validity interval contains the requested time;
2. prefer the requested scope over a global default;
3. prefer the latest `valid_from` within that scope;
4. return remaining valid records as alternatives;
5. flag differing, unlinked records in the same scope as unresolved conflicts.

The resolver does not pretend that a conflict is settled. The later knowledge
conflict engine can compare evidence and reliability before proposing a change.

## Historical hybrid retrieval

`RetrievalRequest.valid_at` includes confirmed and superseded records whose
validity interval contains the requested instant. Normal retrieval continues to
select only currently confirmed records.

## CLI

```powershell
python -m acr_runtime.cli --db .acr/acr.db memory current database `
  --scope project
python -m acr_runtime.cli --db .acr/acr.db memory at database `
  "2026-03-01T00:00:00Z" --scope project
python -m acr_runtime.cli --db .acr/acr.db memory history database `
  --scope project
```
