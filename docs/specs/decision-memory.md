# Prompt 61: decision memory

## Outcome

Architecture and operational decisions use the existing durable `decision`
memory type and temporal supersession chain, with a strict
`acr.decision.v1` structured payload. Each record contains:

- topic and chosen decision;
- context;
- alternatives considered;
- reason;
- consequences;
- decision date;
- exact memory scope;
- non-empty evidence;
- optional named assumptions.

The human-readable decision remains the memory content, the topic is the
subject, and the complete structure is retained in the bounded JSON payload.
Decision memory stays pinned by the existing lifecycle policy. A replacement
uses the existing `supersedes` link, closes the old validity interval, and
preserves both records and their evidence.

## Architecture preflight

Before changing architecture, use a focused decision check:

```powershell
python -m acr_runtime.cli --db .acr/acr.db memory decision-check `
  "database architecture" --scope project:runtime `
  --assumption deployment=single-node `
  --assumption offline_required=true
```

The check retrieves only current confirmed decision memories in the exact
scope or registered ancestors. Scope filtering occurs before lexical or
semantic ranking.

Each result has one applicability state:

- `applicable`: every stored assumption was supplied and matches;
- `needs_validation`: at least one stored assumption was not checked;
- `stale_assumptions`: at least one supplied value differs;
- `unstructured_legacy`: an older decision lacks the v1 payload and needs
  manual review.
- `invalid_structured_decision`: a record claims v1 but is malformed and fails
  closed to manual review.

Changed and unverified assumption names are explicit. A stale or legacy
decision remains evidence about why a prior direction was chosen; it is not an
instruction to preserve that direction. The checker never changes code,
supersedes a decision, or writes a replacement automatically.

## Recording and inspection

```powershell
python -m acr_runtime.cli --db .acr/acr.db memory decision-add decision.json
python -m acr_runtime.cli --db .acr/acr.db memory decision-show MEMORY_ID
```

`decision-add` rejects missing alternatives, consequences, evidence, invalid
dates, duplicate assumption names, secrets, oversized fields, invalid
sensitivity, and malformed or unknown JSON fields. Existing flat decision
memories remain retrievable but are labeled `unstructured_legacy`.

The root coding-agent contract now requires this bounded check before an
architecture change. If no exact ACR grant is available, the agent continues
from repository evidence without weakening authorization and reports the
missing decision context.
