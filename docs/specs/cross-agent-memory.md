# Prompt 59: cross-agent shared memory

## Outcome

Memory scope is an explicit immutable tree, not a similarity filter or a string
prefix. The closed kinds are:

`global`, `organization`, `user`, `project`, `repository`, `task`, and `agent`.

Every non-global scope has one registered parent. The allowed parent
relationships are deliberately constrained:

| Child | Allowed parent |
| --- | --- |
| organization | global |
| user | organization |
| project | global, organization, user |
| repository | project |
| task | project, repository |
| agent | organization, user, project, repository, task |

The few optional shortcuts support standalone projects and long-lived agents
without inventing placeholder owners. They do not change retrieval direction.

## Retrieval rule

A query can see its exact scope and registered ancestors only. It cannot see
children, siblings, cousins, or a scope selected because its text or embedding
is similar. For example:

```text
global
└── organization:acme
    ├── project:a
    │   └── repository:a
    │       ├── task:a1
    │       │   └── agent:a1
    │       └── task:a2
    │           └── agent:a2
    └── project:b
```

Both agents can retrieve memory written at `repository:a`, `project:a`,
`organization:acme`, or `global`. Neither can retrieve the other agent's
private memory, and no memory from `project:b` enters the candidate pool.
Keyword and semantic ranking run only after this boundary.

`MemoryQuery(include_global=False)` retains an exact-scope-only mode. The
field name is preserved for API compatibility; when true it now means
registered ancestor visibility, including global when global is an ancestor.

## Registration and compatibility

Schema 41 adds `memory_scopes` and seeds the parentless `global` root.
Migration registers every existing flat non-global scope as an isolated
project directly below global. New direct writes to an unregistered legacy
scope receive the same isolated registration, preserving the v0.1 API without
creating a relationship to any other project.

Register deliberate relationships before writing shared memory:

```powershell
python -m acr_runtime.cli --db .acr/acr.db memory scope-add `
  organization:acme organization --parent global
python -m acr_runtime.cli --db .acr/acr.db memory scope-add `
  project:runtime project --parent organization:acme
python -m acr_runtime.cli --db .acr/acr.db memory scope-add `
  repository:acr repository --parent project:runtime
python -m acr_runtime.cli --db .acr/acr.db memory scope-path repository:acr
```

Scope IDs are immutable. Re-registering the same definition is idempotent;
attempting to reuse an ID with a different kind or parent fails.

## Provider authorization

An MCP caller still needs an exact active `memory.read` grant for the leaf
scope supplied in the call. That grant permits the hierarchy resolver to read
only that leaf's registered ancestors. Tool arguments cannot add arbitrary
visible scopes, and ancestor access never grants access to descendants or
siblings. Existing public/internal sensitivity filtering and authority-free
content framing still apply.
