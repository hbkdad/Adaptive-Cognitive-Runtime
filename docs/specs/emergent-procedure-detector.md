# Prompt 113: emergent procedure detector

Prompt 113 adds deterministic, proposal-only discovery of repeated operation
sequences from preserved experience traces. It does not parse prose into
commands, create procedural memory, generate a skill, or execute a candidate.

## Evidence boundary

The detector reads `experience_traces`, where Prompt 11 already isolates raw
task histories from default memory retrieval. A trace is one process-mining
case. Only a `procedure` or `tool_sequence` event with exactly one valid
`operation_sequence_v1` metadata value is eligible:

```json
{
  "operation_sequence_v1": [
    {
      "operation": "file.search",
      "parameters": {
        "scope": "src",
        "query_kind": "symbol"
      }
    },
    {
      "operation": "test.run",
      "parameters": {
        "suite": "focused"
      }
    }
  ]
}
```

The sequence contains 2–64 steps. Operation names and parameter names use a
closed identifier grammar. Each step has at most 16 scalar parameters, and a
string parameter is at most 256 characters. Natural-language event content is
ignored. Missing, malformed, or multiple structured sequences in one trace
cannot support a candidate.

Ordinary execution summaries and telemetry are not used because they do not
retain a complete, stable operation sequence. Reconstructing missing actions
from prose or model inference would manufacture evidence.

## Bounded snapshot

A version 1 request fixes:

- one exact scope;
- 1–16 task classes;
- an inclusive `observed_before` timestamp;
- 3–20 minimum successful occurrences;
- 3–minimum-successes distinct task IDs;
- a maximum non-success rate from 0 to 0.25;
- a minimum significance from 0.6 to 1;
- a 10–500 trace cap.

The detector refuses a matching source set larger than the configured cap. It
hashes the complete selected source snapshot and request. Repeating the same
request against the same snapshot returns the same immutable run.

## Conservative clustering and conformance

Version 1 clusters only traces with the same task class and exact ordered
operation-name skeleton. Parameters do not define the cluster. This recognizes
the same procedure with different inputs without using fuzzy semantic
similarity.

A suggestion requires:

- the configured number of significant successful traces;
- the configured number of distinct non-null task IDs;
- non-success evidence at or below the configured rate.

Failed, partial, and cancelled traces with the same skeleton are non-success
conformance evidence. Low-significance successes cannot support a suggestion.
The default non-success allowance is zero.

Clusters below any threshold remain aggregate run counts and never become
candidate rows.

## Variability boundaries

For each step and parameter name across supporting successes, the detector
reports:

- `invariant`: present every time with one observed value hash;
- `variable`: present every time with multiple value hashes;
- `optional`: absent from at least one occurrence;
- observed scalar types;
- distinct-value, present, and occurrence counts.

Raw parameter values and their hashes are not retained in detection reports.
Candidates retain only operation names, the classifications and counts,
supporting trace IDs, evidence totals, and hashes of the request, source, and
operation signature.

## Persistence and authority

Migration 65 adds immutable `procedure_detection_runs` and
`procedure_detection_candidates`. SQLite triggers reject updates and deletes.
Safe Mode blocks new detection runs but permits report inspection.

Every candidate has status `suggested`. There is intentionally no accept,
promote, memory-write, skill-generation, activation, or execution command.
Promotion would require the existing governed distillation, skill validation,
benchmark, quarantine, and human approval boundaries.

## CLI

```powershell
python -m acr_runtime.cli --db .acr/acr.db procedures detect `
  examples/procedure-detection/request.json
python -m acr_runtime.cli --db .acr/acr.db procedures report <RUN_ID>
```

## Research basis

- Process mining models a case as an ordered event trace and separates process
  discovery from conformance checking:
  <https://www.processmining.org/event-data.html>
- Process discovery uses event and sequence frequency while excluding
  infrequent paths from heuristic models:
  <https://www.processmining.org/process-discovery.html>
- ProcMEM describes reusable skills with activation, execution, and
  termination conditions plus a verification gate:
  <https://arxiv.org/abs/2602.01869>
- Research on skill leakage shows that execution trajectories can expose
  procedural knowledge, supporting content-minimized retained candidates:
  <https://arxiv.org/abs/2607.25560>

The implementation borrows the case, frequency, conformance, applicability,
and privacy boundaries. It does not reproduce the papers' learned policies or
claim their benchmark results for ACR.
