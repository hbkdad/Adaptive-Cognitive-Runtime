# ACR core smoke benchmark v1

This small public dataset verifies provider execution, deterministic ordering,
quality scoring, and token/latency accounting. It is not a memory benchmark and
must not be used to claim ACR retrieval improvements.

The JSONL header fixes the dataset version. Case IDs are immutable within v1.
Material case changes require a new dataset version.

`memory.jsonl` is the separate Prompt 44 deterministic memory benchmark. It
compares no memory, raw history, frozen lexical RAG, and governed ACR retrieval
across eight adversarial categories. See `docs/specs/memory-benchmark.md` for
its exact protocol and limitations.

`token-optimization.jsonl` is the Prompt 46 excessive-context benchmark. It
compares full context, semantic retrieval, hybrid retrieval, and the real ACR
context compiler under a hard quality-no-regression gate.
