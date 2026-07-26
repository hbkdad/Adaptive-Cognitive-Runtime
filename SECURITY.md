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
