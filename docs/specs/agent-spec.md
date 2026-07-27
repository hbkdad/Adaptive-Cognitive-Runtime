# Prompt 24: ACR AgentSpec

`AgentSpec` is an immutable definition for a bounded runtime worker. Prompt 24
does not create, schedule, or execute workers.

## Exact fields

Every definition contains exactly:

- `id`;
- `role`;
- `objective`;
- `task_scope`;
- `tools`;
- exact `stable-id@semantic-version` skills;
- `memory_scope`;
- a model allowlist, preferred model, local-only flag, and fallback flag;
- positive token and time budgets plus a finite non-negative money budget;
- permissions;
- an allowlisted communication policy;
- complete termination conditions; and
- verification requirements.

Unknown fields are rejected, including personality fields. Roles describe
responsibility, not persona.

## Least privilege and immutable dependencies

On definition, every referenced skill must:

- be an exact active, validated version;
- retain the validated package hash;
- have all required tools and permissions included in the AgentSpec;
- overlap the worker's task scope; and
- be compatible with the model allowlist.

Resolved skill record IDs, versions, and package hashes are retained with the
spec. Reusing an ID with the identical canonical definition is idempotent;
changing any field under the same ID is rejected.

Task, memory, tool, and permission scopes reject `*` and `all`. Communication is
`none`, `manager_only`, or an explicit allowlist with a bounded message count.
Termination must cover objective success, verification failure, budget
exhaustion, time exhaustion, and cancellation.

## Context boundary

`AgentContextItem` carries a source ID, task scope, optional memory scope, and
content. `AgentSpec.filter_context()` admits an item only when its task scope is
assigned to the worker and its memory scope is explicitly allowed. Non-memory
tool or policy context still requires a matching task responsibility.

This is a structural boundary for Prompt 24. The Prompt 25 factory must use it
when compiling each temporary worker's context.

```powershell
python -m acr_runtime.cli --db .acr/acr.db agents define `
  examples/agent-spec/database-worker.json
python -m acr_runtime.cli --db .acr/acr.db agents list
python -m acr_runtime.cli --db .acr/acr.db agents inspect database-worker
```
