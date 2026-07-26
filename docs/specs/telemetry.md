# Prompt 3: telemetry engine

## Implemented

- Schema-versioned execution-run and generic telemetry-event storage.
- Event-bus subscriber that persists task lifecycle telemetry without importing
  storage into the execution engine.
- Fields reserved for provider, model, token, cache, cost, latency, context,
  skill, and memory attribution.
- Aggregate execution and context metrics.
- Task inspection and model, skill, memory, and context-waste CLI views.
- Payload redaction for credential-like fields and values.
- Task objectives are represented in telemetry by a hash and character count,
  not raw prompt text.

## Evidence boundary

Model, tool, routing, retry, and escalation metrics remain empty until those
subsystems emit real events. Telemetry does not invent zero-cost successes for
capabilities that do not exist.

## Commands

```powershell
python -m acr_runtime.cli telemetry
python -m acr_runtime.cli telemetry summary
python -m acr_runtime.cli telemetry task <task-id>
python -m acr_runtime.cli telemetry models
python -m acr_runtime.cli telemetry skills
python -m acr_runtime.cli telemetry memory
python -m acr_runtime.cli telemetry waste
```

