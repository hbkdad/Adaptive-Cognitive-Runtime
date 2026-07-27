# Token optimization benchmark

Prompt 46 adds a deterministic excessive-context benchmark with four arms:
full context, precomputed semantic retrieval, frozen hybrid
semantic-plus-lexical retrieval, and the real ACR context compiler.

The committed dataset includes hundreds of irrelevant records plus
precision-sensitive commands, error messages, code expressions, and dependency
expansion. All arms receive the same source entries and required evidence IDs.

```powershell
python -m acr_runtime.cli benchmark validate-token benchmarks/v1/token-optimization.jsonl
python -m acr_runtime.cli benchmark token benchmarks/v1/token-optimization.jsonl
```

## Metrics and gate

Each case reports evidence-selection quality, estimated input tokens, local
selection latency, and estimated input cost from the dataset's frozen price
snapshot. Full context is the quality baseline. An optimized arm meets the
primary goal only when:

1. its aggregate quality is no lower than full context; and
2. it uses fewer context tokens.

Case-level results remain visible so an aggregate cannot conceal a critical
exactness failure. The ACR arm additionally verifies the compiler's
`exact_preserved` result for commands, errors, and code.

This is an offline context-selection benchmark. Latency is local preparation
time, cost is an estimate, and quality is exact required-evidence coverage. It
does not claim provider-reported token usage, model-answer quality, or model
latency. A later live benchmark must keep those measurements separate.

## Research basis

- [Lost in the Middle](https://aclanthology.org/2024.tacl-1.9/) shows that model
  performance can depend strongly on where relevant information occurs in long
  context, motivating paired quality checks rather than token savings alone.
- [LongBench](https://aclanthology.org/2024.acl-long.172/) evaluates multiple
  long-context capabilities and reports efficiency alongside task performance.
- [RECOMP](https://aclanthology.org/2024.iclr-main.337/) trains selective
  compressors and explicitly permits an empty compression when retrieved
  documents add no value.
- [RAGChecker](https://arxiv.org/abs/2408.08067) separates retrieval and
  generation diagnostics, reinforcing this benchmark's narrow retrieval claim.

Precomputed semantic scores make CI reproducible; they are fixtures, not claims
about a production embedding model.
