# Prompt 71: cost accounting

Schema 51 separates lifecycle pricing from model and tool behavior. The
authoritative accounting path is:

`immutable rate -> physical usage attempt -> meter lines -> allocation views`

Legacy `estimated_cost` floats, model profile prices, router outcomes, resource
reservations, and benchmark costs are retained for compatibility or their
original purpose. They are not summed into the schema-51 financial ledger.

## Rate catalog

Each model or tool rate binds an exact provider, SKU, operation, meter,
currency, unit size, inclusive start, exclusive end, HTTPS source, and source
digest. Rates are immutable. The catalog rejects overlapping intervals for the
same identity, including overlaps expressed in another currency, so a physical
attempt can never select two prices.

No live prices ship with ACR. An operator captures an official provider rate
and imports it as a retained revision. Historical events stay pinned to the
rate that covered their occurrence time; later catalog changes cannot reprice
them.

Supported v51 meters are:

- uncached input tokens;
- cache-read tokens;
- cache-write tokens;
- output tokens; and
- tool calls.

Cache categories are mutually exclusive parts of total input. ACR rejects a
record where cache-read plus cache-write tokens exceed input tokens. Missing
prices produce `unpriced` or `partially_priced` coverage, never a fabricated
zero charge. A single event cannot mix currencies. Schema 51 intentionally
admits only the ISO 4217 codes `CAD` and `USD`; adding another billing currency
requires a reviewed schema revision rather than accepting arbitrary
three-letter text.

Amounts are integer currency microunits. Calculation uses conservative integer
ceiling of `quantity * price_micros / unit_size`; binary floating-point money
does not enter the ledger.

## Usage and allocation

Every physical provider or tool attempt needs a stable opaque `attempt_id`.
Replay of the same ID is rejected, while a real retry uses another ID and can
incur additional cost. Events retain bounded identifiers, quantities, price
revision IDs, task/project binding, evidence hashes, status, and evidence
quality. They do not retain prompts, responses, credentials, raw errors, or
invoice account data.

Each engine-created event declares its exact meter and allocation counts, then
atomically writes an immutable seal over the complete rate IDs, amounts,
currency, local-profile revision, and allocation identities. Database triggers
reject mismatched rates, incomplete seals, allocation totals that do not
reconcile, and every later child insertion.

Provider adapters now issue an attempt ID to telemetry. Remote calls enter the
ledger automatically when their telemetry sink is active. Unknown prices remain
visible as unpriced. Governed provider execution consumes a conservative catalog
quote when the accounting service is injected, using the highest applicable
input/cache price plus the output price. Model metadata prices continue only as
a compatibility fallback for older direct integrations; the cost ledger never
reads them.
Operator `record-model` and `record-tool` imports are always labelled
`estimated`; only the adapter-bound path can claim provider-reported usage.

Skill cost is a secondary allocation of the same event, not additional spend.
The initial policy uses deterministic equal shares across the exact loaded skill
IDs and reconciles rounding so allocations equal the event's priced subtotal.
Reports label this view non-additive and do not claim causal contribution.

## Reports

`cost report` returns:

- priced subtotal and estimated portion per currency;
- cost per attempted task;
- cost per successful task, including failed-attempt spend in the numerator;
- cost by exact task scope/project;
- cost by provider/model/operation;
- tool API cost; and
- non-additive cost allocation by skill.

A zero-success denominator returns `null`. Reports never perform implicit
foreign-exchange conversion and disclose priced, partially priced, unpriced,
local-estimate, and local-disabled coverage counts. Legacy telemetry is
explicitly excluded.

## Optional local inference

Local electricity and hardware estimates are disabled by default. Without an
enabled effective-dated profile, a local attempt is `local_disabled`, not
“free.” An enabled profile supplies:

- power in milliwatts;
- electricity tariff in currency microunits per kWh;
- hardware amortization in currency microunits per hour; and
- an evidence digest and effective interval.

The estimator uses measured runtime:

`electricity = power_mW * duration_ms * tariff / 3,600,000,000,000`

`hardware = duration_ms * hardware_rate / 3,600,000`

Both values use conservative integer ceiling and remain `local_estimate`,
separate from provider API charges. ACR performs no hardware probing, carbon
claim, tariff lookup, or automatic enablement.

## Commands

```powershell
python -m acr_runtime.cli --db .acr/acr.db cost rate-add rate.json
python -m acr_runtime.cli --db .acr/acr.db cost rates
python -m acr_runtime.cli --db .acr/acr.db cost record-model usage.json
python -m acr_runtime.cli --db .acr/acr.db cost record-tool usage.json
python -m acr_runtime.cli --db .acr/acr.db cost local-profile-add local.json
python -m acr_runtime.cli --db .acr/acr.db cost local-status
python -m acr_runtime.cli --db .acr/acr.db cost record-local usage.json
python -m acr_runtime.cli --db .acr/acr.db cost event <EVENT_ID>
python -m acr_runtime.cli --db .acr/acr.db cost report
```

## Research basis

The design follows the different meter structures in the official
[OpenAI model guide](https://developers.openai.com/api/docs/guides/latest-model),
[Anthropic pricing reference](https://platform.claude.com/docs/en/about-claude/pricing),
[Google Vertex AI pricing](https://cloud.google.com/vertex-ai/generative-ai/pricing),
and [AWS Bedrock pricing](https://aws.amazon.com/bedrock/pricing/). Cost,
quantity, currency, and correction concepts are informed by
[FinOps FOCUS 1.4](https://focus.finops.org/focus-specification/v1-4/).
Optional local estimates use the transparent-boundary principles of
[ISO/IEC 21031:2024](https://www.iso.org/standard/86612.html) and the
[Software Carbon Intensity specification](https://sci.greensoftware.foundation/),
without claiming carbon measurement. Hardware lifecycle assumptions follow
[NIST Handbook 135e2025](https://nvlpubs.nist.gov/nistpubs/hb/2025/NIST.HB.135e2025.pdf).
