# Prompt 65: safe caching

ACR schema 45 introduces one exact, local cache substrate and enables only the
retrieval-result cache. Caching is opt-in per request:

```powershell
python -m acr_runtime.cli --db .acr/acr.db memory retrieve "SQLite FTS5" `
  --task "Inspect the database" --scope project:a --cache-max-age 30
python -m acr_runtime.cli --db .acr/acr.db cache status
python -m acr_runtime.cli --db .acr/acr.db cache prune
```

Omitting `--cache-max-age` prohibits both cache reads and writes. The maximum
age must be between one second and 24 hours. A cache entry is stale at the exact
expiry boundary; stale results are never served.

## Exact key and isolation contract

The SHA-256 key covers:

- cache schema and retrieval algorithm version;
- raw task and query text;
- exact scope and its currently visible ancestor path;
- token budget, target count, temporal point, type and sensitivity filters;
- confidence threshold and global-ancestor policy;
- every retrieval weight and ranking configuration value;
- the caller's maximum-age requirement.

Task and query text exist only transiently while hashing. The database stores
the digest, scope, expiry, source generation, compute duration, and a bounded
JSON payload. Equal hashes in different scopes cannot share an entry.

Hashing is an equality mechanism, not anonymization. If request key material
looks like secret material, caching is bypassed.

## Content minimization and reauthorization

The payload stores only opaque memory IDs, memory revision and privacy-policy
versions, score components, ordering, selection state, and rejection reasons.
It never stores the task, query, prompt, memory content, embeddings, or tool
results.

Every hit reloads each memory and rechecks:

- exact visible scope;
- status and lifecycle;
- requested type and sensitivity;
- confidence;
- temporal validity;
- privacy retention deadline;
- memory revision and privacy-policy version.

A missing, changed, corrupt, oversized, or unauthorized item invalidates the
entry and becomes a normal miss. It never becomes a partial hit.

## Invalidation and savings

SQLite triggers increment a global `memory_retrieval` generation and delete
retrieval entries after any memory insert, update, or delete; scope hierarchy
change; or privacy-policy update. This deliberately favors correctness over hit
rate. A generation change during computation prevents the result from being
published. Moving-time entries also expire at the earliest future `valid_from`,
`valid_until`, or `retention_until` transition visible to the request.

Hit confirmation conditionally updates the entry against the current generation
and expiry while holding SQLite's write lock. If concurrent mutation wins first,
the candidate is recomputed and no hit or savings event is recorded. Cache
writes join, but never commit, a caller-owned transaction.

Cache events contain no request content. A confirmed hit records the original
retrieval compute duration as estimated gross avoided latency. It does not
subtract lookup and rehydration overhead and does not claim saved model tokens,
model calls, tool calls, or cost. Provider-side
`cached_tokens` remains a separate model telemetry field.

Entries are capped at 1,000, payloads at 256 KiB with an independent SQLite
byte-length invariant, and content-minimized events at 10,000. Ordinary
non-opted retrievals do not write bypass telemetry.

## Deliberately disabled cache types

- Model responses remain off because identical requests are not guaranteed to
  reproduce identical outputs, and current requests have no immutable provider
  revision identity.
- Embeddings remain off until requests carry scope, sensitivity, retention,
  and an immutable model digest. Vectors are derived sensitive data.
- Tool results remain off until a trusted local contract proves the operation
  is read-only, side-effect-free, versioned, and freshness-bounded. MCP
  annotations alone are not authority.
- Whole compiled contexts remain off because compilation performs fresh
  security checks and creates task-specific attribution and calibration state.

These types may later reuse the substrate only after their missing contracts
are implemented and tested.

Design references:

- [RFC 9111](https://www.rfc-editor.org/rfc/rfc9111.html) for exact variants,
  explicit freshness, and no implicit stale serving.
- [RFC 8785](https://www.rfc-editor.org/rfc/rfc8785.html) for deterministic
  canonical JSON principles.
- [SQLite transactions](https://www.sqlite.org/lang_transaction.html) and
  [isolation](https://www.sqlite.org/isolation.html) for local concurrency.
- [OWASP data protection guidance](https://devguide.owasp.org/en/04-design/02-web-app-checklist/08-protect-data/)
  for avoiding unnecessary sensitive-data storage.
- [MCP tool annotations](https://modelcontextprotocol.io/specification/2025-06-18/schema#toolannotations)
  for the rule that untrusted annotations cannot drive security decisions.
