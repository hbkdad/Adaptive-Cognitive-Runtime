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

## Trust status and lifecycle

Trust status is `candidate`, `confirmed`, `superseded`, `archived`,
`quarantined`, or `deleted`. Schema v6 adds an independent storage lifecycle:
`active`, `cold`, `archived`, or `deleted`. Normal retrieval returns confirmed,
currently valid records from active or cold storage. Lifecycle deletion is a
tombstone; garbage collection never proposes automatic deletion.

Supersession is bidirectional: the replacement points to `supersedes`, the old
record points to `superseded_by`, and the old record becomes invalid and
superseded atomically.

## Provenance and payload

Every record may carry a subject, JSON structured payload, source type/source
identifier, and evidence list. Confidence, importance, observed utility, token
cost, validity, and successful/failed use counts are stored separately so they
can be inspected and ranked without rewriting content.

Schema v4 adds mandatory retention reasons to every record. Legacy and direct
administrative writes receive an explicit default reason; governed writes carry
the deterministic policy reason that caused retention.

Schema v5 adds content-minimized consolidation runs and actions. Plans reference
memory IDs and expected versions; raw memory content remains in the memory table
and is never copied into consolidation telemetry.

Schema v6 adds lifecycle state, pin metadata, per-scope activity, and
content-minimized garbage-collection audit records. Pinned records and protected
decisions, critical failures, high-value procedures, and structured security
events cannot be moved automatically.

Schema v7 adds normalized failure records linked one-to-one with failure memory.
Confidence and evidence remain on the canonical memory; repeat counts,
environment, symptoms, failure analysis, and remediation links are queryable
without parsing prose.

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
