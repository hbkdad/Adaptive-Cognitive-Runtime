# Adversarial memory tests

Prompt 87 turns the memory trust model into an executable security contract.
The suite attacks public runtime APIs rather than helper-only test doubles.

## Required invariant

Untrusted content may be rejected, quarantined, or retained as a non-authoritative
candidate. It must never become `confirmed` memory without an independent trusted
source and the existing explicit workflow controls.

The test fixture tags every hostile source with `attack:` and performs a final
database-level assertion that none of those sources became confirmed.

## Attack matrix

| Attack | Expected boundary |
| --- | --- |
| Prompt injection | Suspicious instructions are hash-audited and quarantined; no memory row is created. |
| False claim | Plausible but unverified content remains a candidate. |
| Contradiction | An untrusted conflict requests verification and cannot supersede a confirmed fact. |
| Repeated misinformation | Repetition can deduplicate or enrich one candidate, but cannot self-promote it. |
| Scope confusion | Retrieval cannot expose a sibling project's memory. |
| Malicious web text | Web content has authority `none` and cannot authorize `memory.create`. |
| Obsolete fact | A fact at or after its exclusive `valid_until` boundary is absent from current retrieval. |
| Oversized junk | Content beyond 1,000,000 characters is rejected before memory storage. |

## Security rationale

The architecture separates instructions from untrusted data, constrains external
content to no authority, and requires exact scoped approval for sensitive
derivations. These controls follow the defense-in-depth guidance in the
[OWASP LLM Prompt Injection Prevention Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/LLM_Prompt_Injection_Prevention_Cheat_Sheet.html)
and its guidance to test remote injection vectors. The attack classes also cover
NIST's distinct categories of
[indirect prompt injection and data poisoning](https://nvlpubs.nist.gov/nistpubs/ai/NIST.AI.100-2e2023.pdf).

The suite is deterministic, local-only, credential-free, and belongs to the
security tier in `tests/suites.json`.
