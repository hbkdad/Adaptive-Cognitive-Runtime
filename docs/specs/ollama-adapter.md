# Prompt 33A: local Ollama provider adapter

This milestone implements the adapter dependency of Prompt 33. Routing and
benchmark-based task selection remain deferred.

## Implemented

- Local model discovery through `GET /api/tags`.
- Non-streaming and streaming chat through `POST /api/chat`.
- Embeddings through `POST /api/embed`.
- Ollama-reported prompt and output token accounting.
- Structured-output and tool-call request/response plumbing.
- Loopback-only endpoints by default. Remote endpoints require an explicit code
  override and future policy approval.
- `acr run "<task>" --model <installed-model>` using the provider-independent
  task engine and persistent telemetry.

The adapter follows Ollama's official REST API:
https://docs.ollama.com/api

## Deferred

- Model ranking requires Prompt 32 and benchmark evidence.
- Local-versus-cloud escalation requires provider policies and sensitivity
  classification.
- Vision capability detection requires model metadata more reliable than model
  name heuristics.

