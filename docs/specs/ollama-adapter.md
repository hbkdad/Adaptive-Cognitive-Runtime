# Prompt 33A: local Ollama provider adapter

This milestone implements the adapter dependency of Prompt 33. Prompt 33 now
adds discovery-backed profiles, benchmark evidence, and local-first routing.

## Implemented

- Local model discovery through `GET /api/tags`.
- Non-streaming and streaming chat through `POST /api/chat`.
- Embeddings through `POST /api/embed`.
- Ollama-reported prompt and output token accounting.
- Structured-output and tool-call request/response plumbing.
- Authoritative capability and context-window inspection through `POST /api/show`.
- Loopback-only endpoints by default. Remote endpoints require an explicit code
  override and future policy approval.
- `acr run "<task>" --model <installed-model>` using the provider-independent
  task engine and persistent telemetry.

The adapter follows Ollama's official REST API:
https://docs.ollama.com/api

Vision, tool, embedding, and completion capabilities are accepted only when
advertised by `/api/show`; no model-name heuristic can authorize routing.
