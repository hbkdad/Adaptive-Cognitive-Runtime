# Performance-review agent workflow

Use this workflow to review measurements for one exact change. The reviewer
consumes bounded evidence; it does not collect profiles, run queries or tools,
call models, or apply optimizations.

## Review sequence

1. Define the exact change, representative workload, environment, inputs,
   warm-up policy, baseline revision, candidate revision, and measurement
   references.
2. Review all six categories in order: `token_usage`, `model_calls`,
   `retrieval_volume`, `database_queries`, `tool_calls`, and `latency`.
3. Classify each category as:
   - `unmeasured` when no comparable evidence exists;
   - `observed_overhead` when resource use is measured but avoidability is not;
   - `measured_waste` only for a repeated paired comparison where the candidate
     is lower and both quality and security gates pass.
4. Cite bounded evidence and recommend the minimum optimization or next
   measurement. Keep profiler overhead and confounders explicit.
5. Validate and rank the report:

   ```powershell
   python -m acr_runtime.performance_review validate .\review.json
   ```

The validator exits `0` for a valid report and `2` for invalid input. It rejects
unknown fields, missing category coverage, unit mismatches, unpaired savings,
fewer than three samples, failed comparison gates, and detected secret
material.

## Priority policy

Only `measured_waste` becomes an optimization opportunity. Ranking uses the
paired relative reduction so unlike absolute units are not compared directly:
`high` is at least 25%, `medium` is at least 10%, and lower positive reductions
are `low`. Ties retain the fixed category order. These thresholds prioritize
review; they are not permission to change routing, budgets, retrieval, queries,
tools, or code.

Use provider-reported tokens where available and keep cached, input, and output
meters distinct. Use the existing opt-in performance profiler for elapsed
boundaries, including p50 and p95 rather than an average alone. Count database
and tool calls at governed boundaries. SQLite `EXPLAIN QUERY PLAN` may support
interactive diagnosis, but its output format is not a stable application
contract.

## Runtime role template

`examples/agent-spec/performance-review-worker.json` is a valid Prompt 24 role
definition, not an executable worker. It has no tools, skills, permissions,
peers, paid-model budget, or fallback. A host must separately bind the minimum
read-only measurements required for one review. The template itself cannot
profile code, query storage, call a model or tool, optimize the runtime, or
write memory.

## Basis

- [Python profilers](https://docs.python.org/3/library/profile.html) use call
  counts and deterministic timing to identify surprising work, hot paths, and
  algorithmic cost.
- [OpenTelemetry metric conventions](https://opentelemetry.io/docs/specs/semconv/general/metrics/)
  define understandable units and consistent metric semantics.
- [Google SRE monitoring guidance](https://sre.google/sre-book/monitoring-distributed-systems/)
  distinguishes latency, demand, errors, and saturation and recommends
  attention to latency distributions.
- [SQLite EXPLAIN QUERY PLAN](https://sqlite.org/eqp.html) supports interactive
  query-plan diagnosis while warning that its output format may change.
