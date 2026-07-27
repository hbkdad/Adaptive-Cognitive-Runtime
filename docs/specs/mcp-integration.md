# Prompt 56: MCP integration

## Outcome

ACR exposes a static six-tool MCP provider over local stdio and supplies a
separate adapter contract for consuming operator-configured external MCP tools.
MCP sessions, JSON-RPC, transport framing, and remote metadata do not enter the
memory, failure, skill, compiler, or permission domains.

The pinned protocol revision is `2025-11-25`. The v1 transport intentionally
does not advertise prompts, resources, sampling, elicitation, logging,
experimental MCP Tasks, subscriptions, or list-change notifications.

## Provider catalog

The catalog is deterministic:

1. `execute_skill`
2. `failure_lookup`
3. `find_skill`
4. `retrieve_context`
5. `search_memory`
6. `task_history`

`search_memory`, `failure_lookup`, `find_skill`, and `task_history` are
read-only and idempotent. `retrieve_context` is not marked read-only or
idempotent because the compiler persists a task and context-use audit.

`execute_skill` is present so the catalog matches the build contract, but it
returns the structured domain error `skill_execution_unavailable`. ACR skills
are declarative instruction packages. Prompt 36 has no `skill.execute`
capability and the runtime has no governed production executor. The MCP layer
never substitutes package scripts, registry validation, activation, a shell,
or model sampling.

## Identity and authorization

The stdio process binds one identity at startup:

```powershell
python -m acr_runtime.cli --db .acr/acr.db mcp serve `
  --subject-type agent --subject-id local-mcp-agent
```

Tool arguments cannot supply or replace that identity. Every data operation
uses the existing exact, expiring, default-deny capability controller:

| Operation | Required exact grant |
| --- | --- |
| `search_memory`, `failure_lookup` | `memory.read` on `memory:<scope>` |
| `find_skill` | `database.read` on `skills:registry` |
| `task_history` | `database.read` on `tasks:<scope>` |
| `retrieve_context` | `memory.read` on `memory:<scope>` and `database.write` on `context:<scope>` |

Create grants through the existing reviewed capability workflow before
starting a useful provider session. Stdio locality is transport, not
authorization.

## Privacy and content authority

- Memory search is constrained to confirmed public/internal records in the
  exact queried scope or its explicitly registered ancestors before ranking.
  Personal, confidential, secret, archived, deleted, superseded, descendant,
  sibling, and unrelated-scope records are not projected.
- Returned memory is assessed and escaped as authority-free
  `<untrusted_data>`.
- Skill discovery returns active registry metadata only. It excludes package
  paths and instructions.
- Task history excludes objectives and raw telemetry payloads.
- Failure lookup excludes environment JSON, error messages, host paths, and
  evidence references.
- Input and output are finite JSON, secret-scanned, and bounded to 64 KiB and
  1 MB respectively.

## Stdio lifecycle

The server accepts newline-delimited UTF-8 JSON-RPC on stdin and writes only
JSON-RPC messages to stdout. Initialization must negotiate revision
`2025-11-25`, followed by `notifications/initialized`. Calls made before that
boundary fail deterministically. Invalid JSON, envelopes, parameters, methods,
tool names, and batch requests receive bounded protocol errors; valid tool
domain failures use `CallToolResult` with `isError: true`.

Notifications produce no response. Request IDs preserve string, integer, and
zero values. Logs and tracebacks never share stdout. EOF closes the local
runtime and exits cleanly.

## External MCP adapter

`ExternalMcpToolAdapter` consumes an injected `ExternalMcpClient`; it never
accepts a model-provided command, URL, environment, credential, or working
directory. A concrete client remains responsible for an operator-approved
transport and its process/network isolation.

Discovery:

- caps catalogs at 256 tools and schemas at 64 KiB;
- rejects unsafe names, remote `$ref` schemas, and unsupported roots;
- wraps the remote input schema under one required `arguments` property so
  optional remote fields do not weaken ACR's strict root-schema contract;
- assigns `mcp.<namespace>.<tool>.<definition-hash>` so schema/description
  changes create a new immutable local definition;
- uses only operator-supplied permissions, network, and filesystem policy;
- ignores remote descriptions and annotations as authority.

Invocation is read-only in v1. Arguments and results are finite, bounded,
secret-free JSON; results are assessed and escaped as untrusted `tool_output`.
External HTTP/OAuth and stdio process launchers are deferred until fixed-server
registries, sandboxing, token audience enforcement, and process-tree cleanup
have their own adversarial integration suite.

## Acceptance evidence

Focused tests cover the deterministic catalog, lifecycle ordering, structured
errors, JSON-RPC-only subprocess stdout, exact-scope denial, sensitivity
filtering, content minimization, fail-closed skill execution, optional remote
schemas, definition versioning, untrusted output framing, secret rejection,
and unsupported remote references. Repository-wide tests remain the release
gate.
