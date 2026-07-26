# ADR 0002: Separate operations and cinematic control-center layers

Status: accepted

## Decision

The future ACR control center will have two front-end layers over one sanitized
telemetry contract:

1. an operations dashboard for tasks, routing, providers, tokens, memory,
   skills, failures, benchmarks, security, and rollback;
2. an optional cinematic visualization for spatial exploration of the same
   factual runtime relationships.

The Python runtime remains independently operable. A loopback service will
provide bounded REST snapshots and cursor-based event replay before live
WebSocket delivery is added. A UI failure must not stop or mutate the runtime.
The dashboard and 3D experience will be separate routes and lazy-loaded bundles.

## State ownership

Server data belongs in a query cache. View-only state belongs in a small client
store. Visual relationships must be derived from real task, run, provider,
memory, skill, and event identifiers; the UI must not invent agents or links.
Memory mutations remain disabled until a governed write controller and audit
trail exist.

## Delivery order

1. canonical memory model and storage port;
2. retrieval, temporal validity, and write governance;
3. stable sanitized API and event cursor;
4. basic operations dashboard;
5. replay and live updates;
6. memory inspector and real agent/tool panels;
7. 2D graph;
8. lazy-loaded 3D visualization;
9. optional Tauri desktop shell.

## Security

The initial API binds only to loopback. LAN access requires authentication,
TLS, origin/CSRF controls, rate limits, scope enforcement, bounded queues, and
content-redaction review.
