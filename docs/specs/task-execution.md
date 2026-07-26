# Prompt 2: task execution engine

## Implemented

- Immutable `Task`, `Step`, `Action`, `Observation`, `Artifact`, `Result`,
  `Failure`, `Evaluation`, event, and completed `TaskRun` records.
- Validated lifecycle:
  `CREATED -> PLANNING -> EXECUTING -> VERIFYING -> COMPLETED`.
- Explicit `FAILED` and `CANCELLED` terminal paths.
- Planner, executor, verifier, and evaluator protocols.
- Synchronous event bus that later telemetry can subscribe to without coupling
  storage into the task engine.
- Deterministic function executor and one-step planner.
- Exception capture with an explicit failure record and error event.

## Deferred by dependency

- Provider-backed execution belongs to Prompt 31.
- Persistent event and run telemetry belongs to Prompt 3.
- Model-based planning belongs after the provider protocol and benchmarks.
- Retry policy belongs with failure telemetry and resource governance.

No CLI `acr run` command is exposed yet because there is no configured provider
or generally useful deterministic task catalog. A command that pretended to
complete arbitrary tasks would be misleading.

