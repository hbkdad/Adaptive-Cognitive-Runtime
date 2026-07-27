# Security policy

## Current trust boundary

ACR stores local memory and telemetry in SQLite. It can call an explicitly
configured local Ollama endpoint and run generated-skill validation only inside
the explicitly enabled hardened Docker adapter. It does not install generated
packages or accept network clients. Remote Ollama endpoints fail closed
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
The Token Economist reserves output and reasoning headroom, applies a smaller
input allowance to simpler tasks, and never treats the full model context window
as available input. Its baseline policy is deterministic; telemetry is recorded
for later evaluation but does not autonomously change ranking or budgets.
Context attribution stores source identifiers and bounded numeric evidence, not
copied source content. Missing evidence remains uncertain and does not reduce
historical utility. Only explicit ignored or misleading evidence can count
against a selected memory or skill; caller references to unselected context fail
closed.
Compression preserves caller-marked exact content and conservatively detects
commands, diagnostics, cryptographic values, legal language, and unscoped code.
Python AST extraction copies original source segments instead of unparsing and
rewriting them. Artifact references are caller-provided identifiers; the
compressor does not fetch them or broaden filesystem/network access.
Skill Format v1 validation is non-executing. It rejects symlinks, traversal,
oversized packages, malformed manifests, and unbounded instructions, then
computes a deterministic content hash. Manifest status does not grant runtime
trust; registry admission, activation, verification execution, and publisher
signatures remain separate controls.
Registry admission always quarantines packages. Static registry testing never
executes declared verification commands or scripts; a virtual environment is
dependency isolation, not a security sandbox. Activation requires explicit
operator action, a successful static test, a fully passed mandatory validation
run, and an unchanged package digest.
Retirement is terminal. Skill search indexes metadata rather than instruction or
script content, and optional semantic adapters exchange only IDs and bounded
scores.
Skill routing retrieves only active registry entries, expands only exact active
dependencies, and evaluates bounded metadata in process. It does not load
instructions until after selection or execute package scripts. Quarantined,
deprecated, retired, missing-dependency, and over-budget candidates cannot enter
the compiled context.
The skill generator reads only locally persisted traces and requires repeated
successful evidence. It filters known unsafe-content patterns, writes beneath
the configured skills directory, validates the complete package before
admission, and never executes generated files. Generated manifests are
experimental with low initial reliability; registry admission forces quarantine
regardless of manifest status. Declared permissions do not grant authority.
Static registry testing no longer authorizes activation. A candidate must retain
a fully passing ten-stage validation run bound to its unchanged package digest.
The default execution, evaluator, and benchmark adapters block promotion.
Optional Docker checks use a preinstalled image only, no shell, no network,
read-only root and package filesystems, dropped capabilities,
`no-new-privileges`, and bounded resources. Unit-test command allowlisting does
not replace scenario, adversarial, evaluator, or benchmark evidence.

## Secrets

Credentials are represented by opaque references to environment variables, the
optional OS keyring adapter, or an explicitly configured external-store
resolver. Resolution requires an exact, expiring `credential.use` grant and
returns a one-use lease. SQLite retains only a reference hash, provider,
governed subject, decision, capability-decision ID, and timestamp.

Memory, prompts, embeddings, task traces, failure environments, and skill
packages reject detected credential material. Imported content is quarantined
by hash, and telemetry recursively redacts secret fields and common credential
formats before persistence. Staged Git blobs are scanned by the repository
hook. Detection is incomplete by nature, so operators must still avoid placing
credentials in prompts or files. `.env` files and `.acr` state are excluded
from source control.

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
