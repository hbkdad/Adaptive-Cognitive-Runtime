# Prompt 73: self-optimizing tool descriptions

Schema 53 adds immutable, authorization-filtered tool exposure projections and
a paired benchmark ledger. It optimizes the set of tools delivered to a model;
it never rewrites a canonical tool name, description, input schema, output
schema, permission, credential requirement, side-effect classification, or
safety rule.

The runtime distinguishes three boundaries:

1. the API tool catalog submitted to a provider;
2. the tool definitions actually visible in model context; and
3. the tools the provider permits the model to call.

Those boundaries are provider-specific and must not be conflated. ACR's v1
projection is `direct_filtered`: it submits only the selected canonical
definitions. It does not claim to implement hosted deferred loading, provider
tool search, or `allowed_tools`.

## Agent-scoped routing and projection

`agent-route` intersects the existing permission-aware `ToolRouter` with the
exact immutable `AgentSpec.tools` allowlist before selection. The allowlist is
loaded from the registry; it is never accepted inside an ordinary route JSON
request. The existing router still enforces its maximum of eight selected
tools, with three as the default.

A projection is available only when:

- the stored route identity is the exact AgentSpec ID and its task class is in
  the AgentSpec task scope;
- the route was filtered through the AgentSpec tool allowlist;
- every exposed tool is in the complete authorized AgentSpec baseline;
- canonical definitions and their hashes still exist;
- the AgentSpec permissions contain every tool permission;
- exact current capability grants still authorize the same resource scope;
- current network and filesystem bounds still admit the tool; and
- no credential-bearing or destructive tool depends on a credential or
  one-use approval that the retained route cannot safely replay.

Rendering repeats those checks. Grant expiry or revocation, changed lineage, a
missing definition, or any ambiguity denies delivery. There is no unsafe
fallback and no model simulation when a required deterministic tool is absent.
The provider payload contains only canonical `name`, `description`, and input
`parameters`. The canonical output schema remains the execution validator and
is not discarded or replaced.

Estimated projection tokens are planning metadata, not provider measurements.
The baseline and exposed names are retained as restricted internal evidence;
reports never contain prompts, arguments, outputs, credentials, approval
references, or schema bodies.

## Paired benchmark

Each run pins the AgentSpec, catalog, selector, dataset membership, model,
settings, evaluator, seed, and quality margin by lowercase SHA-256 digest. The
dataset hash must match the ordered set of at least five distinct sealed case
hashes. Each case admits exactly one `full_authorized` and one `dynamic` arm,
and both arms must reference the same projection lineage.

The analyzer computes a candidate-preservation signal only when every paired
case satisfies all of these gates:

- both arms succeeded;
- neither arm had a hard violation, unauthorized exposure, or invalid call;
- dynamic required-tool recall is exactly 1.0;
- dynamic quality is within the pinned non-inferiority margin;
- both token counts are provider-reported or locally measured;
- dynamic input tokens do not exceed baseline input tokens for any case; and
- total dynamic input tokens are strictly lower.

Any safety, validity, recall, success, quality, or input-token regression
produces `rejected`. Otherwise the current JSON/CLI import path always produces
`insufficient_evidence` and `collect_verified_receipts`, even when the
candidate signal is positive, because the runtime cannot prove that a
caller-labeled measurement came from the pinned provider and evaluator. A
future schema may add a trusted execution adapter and verifiable receipts; v1
cannot emit `supported` or `retain_dynamic_exposure`.

A completed run is immutable and always reports
`receipt_provenance: caller_supplied_unverified` and
`automatic_activation: false`.

The feature is candidate-only. Prompt 73 adds no policy head, activation
command, self-edit path, autonomous allowlist entry, or permission-widening
mechanism.

## Commands

```text
acr --db PATH tools agent-route REQUEST.json AGENT_SPEC_ID
acr --db PATH tools exposure-project ROUTE_ID AGENT_SPEC_ID
acr --db PATH tools exposure-inspect PROJECTION_ID
acr --db PATH tools exposure-render PROJECTION_ID
acr --db PATH tools exposure-benchmark-start SPEC.json
acr --db PATH tools exposure-benchmark-trial TRIAL.json
acr --db PATH tools exposure-benchmark-seal RUN_ID
acr --db PATH tools exposure-benchmark-report RUN_ID
```

These commands are a trusted local-operator surface. They are not exposed by
the HTTP or MCP APIs, and a projection UUID alone grants no remote access. An
orchestrator must return rendered tools only to the exact AgentSpec execution
for which it created the projection.

## Research basis

- OpenAI tool search dynamically loads deferred tools and recommends compact
  namespaces rather than an unbounded eager catalog:
  https://developers.openai.com/api/docs/guides/tools-tool-search
- OpenAI function calling documents strict schemas and `allowed_tools`; an
  allowed subset is not the same thing as removing definitions from the
  submitted tools array:
  https://developers.openai.com/api/docs/guides/function-calling
- Anthropic tool search distinguishes deferred definitions from initially
  visible context and reports loaded definitions as input tokens:
  https://platform.claude.com/docs/en/agents-and-tools/tool-use/tool-search-tool
- MCP requires authorization-aware discovery and warns clients to treat tool
  annotations as untrusted unless they come from a trusted server:
  https://modelcontextprotocol.io/specification/draft/server/tools
- OpenAI evaluation guidance recommends representative, task-specific,
  continuously evaluated comparisons:
  https://developers.openai.com/api/docs/guides/evaluation-best-practices
