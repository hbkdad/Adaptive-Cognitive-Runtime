# Prompt 36: capability-based permission model

ACR authorizes tasks, agents, and skills with a closed capability vocabulary:

```text
network.read        network.write
filesystem.read     filesystem.write
shell.execute
database.read       database.write
memory.read         memory.write
skill.create        skill.activate
agent.create        credential.use
```

Every grant binds exactly one subject, capability, explicit resource scope,
expiry, delegation bit, reason, and non-empty evidence. Wildcard, `all`, and
`global` scopes are rejected. Absence of an active exact grant is a retained
default-deny decision.

## Delegation and revocation

The trusted workflow control plane may create a root grant. A task or agent may
delegate only the same capability and exact scope from one of its own active,
delegable grants. The child cannot outlive its parent. Skills can receive
capabilities but can never issue grants, so generated or evolved skill content
cannot grant itself additional authority.

Each delegated grant retains its parent ID. Authorization checks verify the
whole parent chain, not merely the leaf. Revoking a grant atomically revokes all
descendants; expiry or revocation anywhere in the chain makes the leaf
ineligible.

## Tool integration

CLI tool-route requests must identify `subject_type`, `subject_id`, and one
explicit `resource_scope`. The router resolves every tool permission through
the capability controller and ignores no denial. Caller-asserted permissions
are rejected by the JSON boundary. Static network, filesystem, credential, and
destructive-approval checks still apply after capability resolution.

Tool definitions must use the closed vocabulary. Network and filesystem access
metadata must be backed by matching capabilities, and credential-bearing tools
must declare `credential.use`.

```powershell
python -m acr_runtime.cli --db .acr/acr.db capabilities grant `
  examples/capabilities/database-read-grant.json
python -m acr_runtime.cli --db .acr/acr.db capabilities check `
  examples/capabilities/database-read-check.json
python -m acr_runtime.cli --db .acr/acr.db capabilities list task task-prompt36
python -m acr_runtime.cli --db .acr/acr.db capabilities revoke <GRANT_ID> `
  --reason "Task completed"
```

The permission model assigns authority and records decisions. It does not
execute a tool, expose a credential, or infer which capability a task needs.
The trusted workflow remains responsible for granting the minimum requirements
supported by task evidence.
