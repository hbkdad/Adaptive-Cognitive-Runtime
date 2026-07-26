# Memory model (Prompt 4A)

## Purpose

ACR memory is a governed domain, not a collection of prompt fragments. The core
model is independent of SQLite through `MemoryReader` and `MemoryStore`
protocols. SQLite is the first adapter; an embedding provider is an optional
future retrieval dependency and is not required by storage.

## Types

- semantic: durable facts and concepts
- episodic: observations tied to an event or run
- procedural: reusable methods
- failure: failed approaches and prevention evidence
- decision: chosen direction and rationale
- preference: stable user or project preferences
- environment: machine and runtime facts
- temporary: short-lived working knowledge

## Lifecycle

Records are `candidate`, `confirmed`, `superseded`, `archived`,
`quarantined`, or `deleted`. Normal retrieval returns only confirmed,
currently valid records. Deleted records are tombstones; the runtime does not
hard-delete through its memory port.

Supersession is bidirectional: the replacement points to `supersedes`, the old
record points to `superseded_by`, and the old record becomes invalid and
superseded atomically.

## Provenance and payload

Every record may carry a subject, JSON structured payload, source type/source
identifier, and evidence list. Confidence, importance, observed utility, token
cost, validity, and successful/failed use counts are stored separately so they
can be inspected and ranked without rewriting content.

## Retrieval

`MemoryQuery` supports scope, full-text terms, type/status filters, subject,
validity time, confidence/utility floors, bounded page size, and opaque cursor
pagination. FTS5 covers subject, content, scope, and type. Scope queries include
the requested scope plus global records.

## Migration safety

Schema v3 is an explicit table-rebuild migration from v2. The migrator:

1. creates a coherent SQLite backup;
2. starts an immediate transaction;
3. preserves every memory ID and maps legacy fields;
4. rebuilds FTS5 and supersession links;
5. verifies ID and index counts;
6. commits the schema version only after verification.

Any failure rolls back the database and leaves the backup available.
