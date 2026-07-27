# Prompt 32: model router

## Contract

The router selects the lowest expected request cost among active models that
have enough verified outcomes for the exact task class and conservatively meet
all requested quality, success, context, and tool-reliability thresholds.
Provider/model identity is explicit; model names alone are not globally unique.

Static profiles retain context capacity, tool support, input/output price,
locality, and an operator-declared small/medium/strong tier. Prompt 60 uses the
tier for role-specialized workflows; Prompt 32 does not infer or require a tier
for ordinary cheapest-qualified routing.
Verified outcomes retain task class, success, bounded quality, latency, token
use, actual input/output cost, tool attempts/successes, and non-empty evidence
references. Ordinary model call telemetry is not treated as quality evidence.

## Conservative qualification

Qualification uses a configurable Wilson lower bound for success, bounded
quality observations, and tool reliability. The default requires at least three
comparable outcomes. Sparse histories, capability mismatches, and every failed
threshold remain visible as candidate rejection reasons. A route with no
qualified candidate is retained as `exhausted` rather than silently falling
back.

Expected request cost is:

```text
(estimated input tokens * input price per million
 + estimated output tokens * output price per million) / 1,000,000
```

The deterministic tie-break order is lower cost, stronger quality lower bound,
lower historical latency, then stable provider/model ID.

## Escalation

A selected attempt must include independent verification evidence, confidence,
quality, latency, token use, and tool results. Passing requires verification,
the route confidence threshold, and the route quality threshold.

If the first attempt fails, the router may recommend exactly one qualified
model with a strictly stronger historical quality lower bound. It does not call
the provider itself. The escalated result is re-evaluated against the same
thresholds and the route records whether verification, quality, or confidence
actually improved. Both attempts also become future verified model outcomes.
No third attempt is accepted.

## CLI

```powershell
python -m acr_runtime.cli --db .acr/acr.db models register profile.json
python -m acr_runtime.cli --db .acr/acr.db models outcome outcome.json
python -m acr_runtime.cli --db .acr/acr.db models route request.json
python -m acr_runtime.cli --db .acr/acr.db models attempt ROUTE_ID attempt.json
python -m acr_runtime.cli --db .acr/acr.db models route-report ROUTE_ID
python -m acr_runtime.cli --db .acr/acr.db models profiles
```

`models list` remains the network-facing Ollama availability check. Registration,
routing, and reporting are local SQLite operations.
