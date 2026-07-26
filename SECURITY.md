# Security policy

## Current trust boundary

ACR v0.1 stores local memory and telemetry in SQLite. It does not execute
generated code, call remote models, install packages, or accept network clients.
Generated or imported skills default to quarantine and are never selected until
explicitly activated by a trusted caller.

## Secrets

Credentials must be provided through environment variables or a future secret
store. Diagnostics may report whether a provider is configured but must never
print credential values, prompts containing secrets, or private memory content.
`.env` files and `.acr` state are excluded from source control.

## Reporting

Do not include secrets or private memory contents in a security report. Record
the affected version, reproducible boundary, expected impact, and remediation.

## Deferred controls

Provider permissions, prompt-injection defenses, skill sandboxing, filesystem
allowlists, network policies, and signed skill artifacts must be implemented
before autonomous skill execution or network-facing APIs are enabled.

