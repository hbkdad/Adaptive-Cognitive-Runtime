# Freshness engine (Prompt 116)

ACR stores freshness as bounded evidence, not as a model-inferred claim that a
fact is current. Schema version 66 adds four first-class memory fields:

- `observed_at`: when the referenced source was actually observed;
- `source_freshness`: `unknown`, `asserted`, or `verified`;
- `expected_half_life_days`: an optional record-specific validity expectation;
- `requires_refresh`: whether normal retrieval must stop when freshness cannot
  be established.

Legacy rows migrate with a null observation, unknown freshness, no override,
and refresh disabled. The migration does not invent source dates.

## Write contract

Asserted or verified freshness requires `observed_at`. Verified freshness also
requires a source type, source identifier, and at least one evidence reference.
Half-life overrides range from one hour to 100 years. Refresh is a strict
boolean.

These fields describe evidence and policy. They do not grant permission to
browse, call a provider, mutate memory, or mark their own source verified.

## Retrieval contract

The half-life policy uses `observed_at` when present and otherwise retains
`valid_from` as its anchor. A record requiring refresh is excluded from normal
selection with reason `refresh_required` when:

- freshness evidence is unknown;
- no usable half-life exists; or
- the observation age reaches the applicable half-life.

The rejected record remains inspectable so it can guide a focused source
search, but its content is not selected as current context. Explicit temporal
invalidity and supersession remain authoritative.

Cache expiry is capped at the earliest observation-plus-half-life deadline
among refresh-gated candidates. Historical point-in-time queries remain
separate and continue to respect retention.

## CLI

```powershell
python -m acr_runtime.cli --db .acr/acr.db memory add environment `
  "Current vendor API price" --scope my-project `
  --source-type vendor-api --source-id pricing-v1 `
  --evidence receipt:pricing-v1 `
  --observed-at 2026-08-02T00:00:00Z `
  --source-freshness verified `
  --expected-half-life-days 1 --requires-refresh

python -m acr_runtime.cli --db .acr/acr.db memory half-life <MEMORY_ID>
```

## Research boundary

Time-aware retrieval work and the HoH benchmark show that temporal constraints
and outdated retrieved evidence matter:

- <https://aclanthology.org/2024.emnlp-main.394/>
- <https://aclanthology.org/2025.acl-long.301/>
- <https://aclanthology.org/2024.findings-emnlp.999/>

ACR does not claim to reproduce their reported improvements. The implemented
boundary is deterministic metadata validation and fail-closed selection.

## Limitations

- Evidence references are caller supplied and not cryptographic attestations.
- `asserted` freshness is not independently verified.
- No automatic network refresh exists.
- A source may change before its expected half-life.
- Refresh outcome and provenance need a separately governed update workflow.
