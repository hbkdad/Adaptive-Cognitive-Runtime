# Prompt 41: benchmark framework

## Implemented

- Versioned JSONL datasets with immutable case IDs and validated categories.
- Reproducible case ordering from an explicit random seed.
- Provider-independent benchmark execution.
- Normalized exact-match baseline scorer.
- Per-case and aggregate quality, token, cost, latency, tool-call, and failure
  metrics.
- Explicit nullable fields for retrieval precision/recall and skill
  effectiveness so absent evidence is not represented as zero.
- Validation and local Ollama execution commands.

## Commands

```powershell
python -m acr_runtime.cli benchmark validate benchmarks/v1/core.jsonl
python -m acr_runtime.cli benchmark run benchmarks/v1/core.jsonl `
  --model qwen2.5-coder:1.5b --seed 0
```

Memory, temporal, and skill datasets belong to Prompts 44–45. A/B strategy
experiments and regression thresholds belong to Prompts 42–43.

Prompt 33 adds `benchmarks/v1/local-router.jsonl` for classification,
summarization, memory extraction, simple planning, and code analysis. Its
results are retained as local model-routing evidence.
