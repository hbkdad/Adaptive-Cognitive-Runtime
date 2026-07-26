# Prompt 28: critic and evaluator

## Implemented

- Independent criterion-level scores for correctness, constraint compliance,
  schema compliance, efficiency, security, completeness, and evidence quality.
- Deterministic exact-match, machine-readable constraint, JSON Schema subset,
  token-efficiency, and credential-exposure judges.
- Optional structured-output LLM judge behind explicit content-transmission
  authorization.
- Evaluation panels require at least one deterministic judge; an LLM judge
  cannot be the only source of truth.
- Per-criterion judge counts and score ranges record disagreement rather than
  averaging it away invisibly.

## Deferred

- Domain-specific factuality and evidence-grounding judges require benchmark
  evidence and source provenance.
- Panel results will be persisted with benchmark and task records when the
  evaluator is connected to the execution workflow.

