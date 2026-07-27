# Prompt 35: tool router

The tool router ranks only Prompt 34 definitions and cannot bypass its
permission, network, filesystem, credential, or destructive-approval checks.
Every considered tool and every rejection is retained.
The task text is used in memory for relevance scoring but only its SHA-256 hash
is stored in the route request.

Selection combines task relevance, task-class historical reliability, measured
or estimated latency, measured or declared monetary cost, and side-effect risk.
Reliability starts from a neutral Beta prior and changes only through append-only
outcomes with non-empty evidence. Cost and latency budgets are hard gates.

The router recognizes four deterministic intent families:

- arithmetic and calculation → calculator;
- file and directory search → filesystem;
- SQL and database queries → database;
- current or latest facts → web retrieval.

When a registered tool matches one of these intents,
`deterministic_tool_required` remains true even if missing grants make the tool
unavailable. This prevents the orchestration layer from silently substituting
model simulation for a reliable deterministic operation. The router selects the
smallest eligible set that covers the detected intent families and never
executes a tool.

Prompt 36 closes the external route boundary: JSON requests name a stored task,
agent, or skill and an explicit resource scope. The router resolves each
required capability through retained exact grants. JSON callers cannot submit
an asserted permission list.

```powershell
python -m acr_runtime.cli --db .acr/acr.db tools route request.json
python -m acr_runtime.cli --db .acr/acr.db tools outcome outcome.json
python -m acr_runtime.cli --db .acr/acr.db tools route-report ROUTE_ID
```
