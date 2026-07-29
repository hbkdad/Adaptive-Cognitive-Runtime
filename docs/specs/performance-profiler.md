# Local performance profiler

Prompt 85 adds an opt-in profiler for measured runtime work. It does not enable
distributed tracing, start background collection, or change production policy.
No optimization is performed by the profiler.

## Measurement boundary

An explicit `runtime.performance.capture(...)` activates a context-local
capture. Outside that context, instrumentation performs only a context-variable
check and retains nothing.

The seven fixed categories are:

| Category | Automatic boundary |
| --- | --- |
| `database_queries` | `RuntimeDB` connection calls |
| `retrieval_latency` | public runtime memory retrieval |
| `embedding_latency` | semantic scoring and Ollama embedding calls |
| `model_wait` | Ollama and deterministic mock chat waits |
| `tool_latency` | governed external MCP calls |
| `context_compilation` | public runtime context compilation |
| `serialization` | explicit `ProfileSession.serialize` or measured span |

Components that run outside these boundaries can use
`ProfileSession.measure(category, operation)` or provide an adapter-observed
duration with `ProfileSession.observe`. Operation names must be bounded,
low-cardinality identifiers.

`ProfiledConnection` retains only an SQLite operation class such as
`sqlite.select`. SQL text, bind parameters, result rows, prompts, memory
content, tool arguments/results, and model content are never stored.
Application failures retain only the exception type.

## Storage and analysis

Schema 63 stores completed or failed capture summaries and their immutable
measurements. Run labels and scopes are SHA-256 hashes. Updates and deletes are
rejected.

Reports include count, failures, total, mean, p50, p95, and maximum duration for
every required category. Spans can overlap, so category totals are ranking
evidence rather than wall-clock decomposition. A category is called a
bottleneck only when it has at least five samples and the largest measured
category total among eligible categories. Its mean must also reach 10 ms, so a
tiny relative winner is not mislabeled as an optimization target. Missing
categories remain explicitly unmeasured.

This evidence gate means callers may investigate or optimize only a reported
bottleneck, then run a comparable profile to validate the change. The profiler
does not recommend distributing the local SQLite runtime.

## Usage

```python
with runtime.performance.capture("retrieval baseline", scope="project") as run:
    result = runtime.retrieve_memory(request)
    encoded = run.serialize({"selected": len(result.selected)})

report = runtime.performance.report(run.run_id)
```

The CLI can collect a safe deterministic local baseline and inspect retained
profiles:

```powershell
python -m acr_runtime.cli --db .acr/acr.db performance profile-local --iterations 5
python -m acr_runtime.cli --db .acr/acr.db performance report RUN_ID
python -m acr_runtime.cli --db .acr/acr.db performance list
```

The local CLI baseline intentionally does not invoke a model, embedding model,
or external tool. Those categories remain unmeasured until the relevant
governed operation runs inside an explicit capture.

## Design references

- [Python `time.perf_counter_ns`](https://docs.python.org/3/library/time.html#time.perf_counter_ns)
  provides an integer, highest-resolution performance clock suitable for short
  elapsed durations.
- [Python `sqlite3`](https://docs.python.org/3/library/sqlite3.html) documents
  the connection API wrapped for caller-observed local query duration.
- [OpenTelemetry database span guidance](https://opentelemetry.io/docs/specs/semconv/db/database-spans/)
  recommends measuring the caller-observed database operation and using a
  low-cardinality summary; it also warns that query text needs sanitization.
  ACR therefore stores no query text.
