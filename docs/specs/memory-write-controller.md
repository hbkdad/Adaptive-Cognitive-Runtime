# Governed memory writes (Prompt 7)

## Purpose

Memory quality depends as much on what is rejected as on what is stored.
`MemoryWriteController` applies deterministic rules before any future model
judgment and produces one of eight outcomes:

- `IGNORE`
- `STORE_TEMPORARY`
- `STORE_CANDIDATE`
- `STORE_CONFIRMED`
- `UPDATE_EXISTING`
- `SUPERSEDE_EXISTING`
- `REQUEST_VERIFICATION`
- `QUARANTINE`

## Inputs

A `CandidateFact` identifies type, content, subject, scope, confidence,
importance, expected usefulness, stability, evidence, provenance, trust,
validity, temporary intent, and explicit privacy/security risk.

## Deterministic policy

Rules are evaluated in safety-first order:

1. privacy, security, prompt-injection, exfiltration, or active-content risk is
   quarantined for review without storing raw candidate content;
2. greetings, one-off calculations, and low-utility content are ignored;
3. unknown scope requests verification;
4. exact duplicates are ignored unless evidence or quality improved;
5. improved duplicates update the existing record, and trusted evidence can
   promote an existing candidate rather than creating a second copy;
6. useful unstable facts become expiring temporary memory;
7. contradictions require verification unless strong trusted evidence supports
   explicit supersession;
8. stable, high-value, high-confidence claims with trusted evidence are
   confirmed;
9. other potentially useful claims remain candidates.

Thresholds and temporary TTL are held in `WritePolicy`.

## Retention identity

Schema v4 adds `retention_reason_json` to memory. Every record therefore
identifies why it exists:

- migrated records: `legacy_or_direct_write`;
- explicit low-level writes: `explicit_direct_write`;
- controller writes: the deterministic decision reason.

Direct `remember` and `memory add` remain an explicit administrative escape
hatch for tests and trusted maintenance. Generated, imported, and future
network-facing claims must use `memory consider`.

## Content-minimized audit

Every decision is recorded in `memory_write_decisions`. The audit contains a
SHA-256 candidate fingerprint, outcome, memory links, reasons, risk flags, scope,
type, confidence, evidence count, and timestamp. It deliberately has no raw
content column, so ignored or unsafe content is not retained through telemetry.

## CLI

```powershell
python -m acr_runtime.cli --db .acr/acr.db memory consider decision `
  "Use SQLite for local state" --scope project --subject database `
  --confidence 0.98 --usefulness 0.95 --stability 0.95 `
  --evidence architecture.md --trusted-source

python -m acr_runtime.cli --db .acr/acr.db memory decisions --limit 20
```
