# Security policy

## Current trust boundary

ACR stores local memory and telemetry in SQLite. It can call an explicitly
configured local Ollama endpoint, but it does not execute generated code,
install packages, or accept network clients. Remote Ollama endpoints fail closed
unless the caller explicitly overrides the local-only guard. Generated or
imported skills default to quarantine and are never selected until explicitly
activated by a trusted caller.

Memory has an independent trust lifecycle. Retrieval selects only confirmed,
currently valid records by default. Candidate, quarantined, superseded, archived,
and deleted records remain unavailable to normal context compilation.
The governed write controller audits ignored and quarantined inputs by hash and
metadata rather than persisting their raw content. Prompt-injection, exfiltration,
active-content, privacy, and security flags prevent automatic storage.
Memory consolidation is non-autonomous: a persisted dry-run plan must be
approved by its exact run ID. Changed targets are skipped, conflicts remain
review-only, and raw source records are archived rather than destroyed.
Lifecycle garbage collection follows the same approval boundary. It computes
content-free retention factors, skips stale or newly protected targets, and
only proposes reversible active-to-cold or cold-to-archived transitions. It
never proposes deletion. Operators can pin memory, and decisions, critical
failures, high-value procedures, and explicitly structured security events are
strongly preserved by policy.
Failure intelligence requires evidence, bounds stored error messages, and does
not store stack traces in default planning context. Pre-planning telemetry
contains failure IDs and numeric weights rather than failure text. A failure can
block planning only under strict deterministic, repeated, high-confidence,
multi-evidence criteria; ordinary matches remain weighted warnings.
Raw experience traces are stored outside the memory retrieval path and are
bounded before JSON parsing. Distillation requires measurable significance and
explicit approval. Memory candidates still pass through governed-write risk
checks, candidate skills remain quarantined, and approval never deletes raw
history.
Required system rules and dependencies fail closed if they cannot fit the hard
context budget. Candidate file content and tool definitions must be supplied by
the trusted caller; the compiler does not broaden filesystem or tool access.

## Secrets

Credentials must be provided through environment variables or a future secret
store. Diagnostics may report whether a provider is configured but must never
print credential values, prompts containing secrets, or private memory content.
`.env` files and `.acr` state are excluded from source control.

## Reporting

Do not include secrets or private memory contents in a security report. Record
the affected version, reproducible boundary, expected impact, and remediation.

## Deferred controls

Provider permissions, prompt-injection defenses, governed memory writes, skill
sandboxing, filesystem allowlists, network policies, authentication, origin
checks, rate limits, and signed skill artifacts must be implemented before
autonomous skill execution or network-facing APIs are enabled. The first API
must bind to loopback only and expose sanitized, bounded telemetry rather than
raw prompts or private memory by default.
