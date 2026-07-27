# Prompt 33: local model router

Prompt 33 composes the Ollama adapter, benchmark framework, and Prompt 32 model
router. It does not create a second execution engine or a second quality policy.

## Discovery

`models local-discover` lists installed models through `GET /api/tags`, then
uses `POST /api/show` for each model's advertised capabilities and model-family
context length. A model without an authoritative positive context capacity is
retained in the discovery report but is not registered for routing. Registered
Ollama profiles are marked local and have zero provider input/output price.

## Local benchmark

The versioned `benchmarks/v1/local-router.jsonl` suite covers three cases in
each required class:

- classification
- summarization
- memory extraction
- simple planning
- code analysis

`models local-benchmark` runs the existing provider-independent benchmark,
atomically converts every case result into a verified task-class model outcome,
and retains the report summary and outcome IDs. Raw prompts and responses are
not copied into routing tables.

## Local-first and external-context policy

A local route request contains routing thresholds plus:

- `risk_level`: `low`, `medium`, or `high`
- `contains_sensitive_context`: boolean
- `cloud_escalation_configured`: boolean
- optional `external_permission_reference`

It cannot contain context content. Unknown fields fail closed. Qualified local
models are preferred even when a cloud candidate has lower configured price.
Cloud candidates are excluded unless cloud escalation is configured. If the
request contains sensitive context, they remain excluded until a non-empty
policy permission reference is supplied. Only the SHA-256 hash of that
reference is retained.

The router is advisory and never transmits context or invokes a provider.
When cloud is allowed, Prompt 32 may recommend one stronger cloud model after a
failed local verification. The caller still owns the separately authorized
provider call.

## Commands

```powershell
python -m acr_runtime.cli --db .acr/acr.db models local-discover
python -m acr_runtime.cli --db .acr/acr.db models local-benchmark `
  benchmarks/v1/local-router.jsonl --model MODEL
python -m acr_runtime.cli --db .acr/acr.db models local-route request.json
python -m acr_runtime.cli --db .acr/acr.db models local-policy ROUTE_ID
```
