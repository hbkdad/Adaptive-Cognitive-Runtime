# Benchmarks

Benchmark implementation is intentionally deferred until the task lifecycle,
provider protocol, and expanded telemetry are present. The first benchmark will
compare:

- no retrieved memory;
- all in-scope memory;
- ACR-selected memory under a hard token budget.

It will measure quality, estimated and provider-reported tokens, latency,
context usefulness, failures, and wasted tokens.

