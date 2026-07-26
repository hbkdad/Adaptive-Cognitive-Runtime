# Prompt 31: model provider interface

## Implemented

- Provider-independent capability and model metadata.
- Immutable chat, stream, embedding, token-usage, and response contracts.
- Optional structured-output, tool, vision, embedding, and context-window
  capability flags.
- Deterministic mock provider with token accounting and streaming.
- Adapter from any chat provider to the core task executor protocol.
- Content-free model-call telemetry containing only operational metadata,
  attribution IDs, token counts, latency, status, and error class.

## Deferred

- Ollama belongs to Prompt 33 and will be a separate adapter.
- OpenAI-compatible and Anthropic-compatible adapters require explicit provider
  setup and secret handling.
- Routing and escalation belong to Prompts 32–33.
- Tool-call execution requires the permission and tool registries.

