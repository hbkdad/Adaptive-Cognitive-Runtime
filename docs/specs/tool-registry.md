# Prompt 34: tool registry

The tool registry stores immutable, non-executable tool boundary definitions.
Each definition contains exactly:

- name and description;
- strict input and output JSON Schemas;
- required permissions from Prompt 36's closed capability vocabulary;
- estimated monetary cost and latency;
- `READ_ONLY`, `REVERSIBLE_WRITE`, or `DESTRUCTIVE` side effect;
- network access;
- `NONE`, `READ`, or `WRITE` filesystem access; and
- credential requirement identifiers.

Both schemas must be closed root objects: every property is required and
`additionalProperties` is false. Read-only tools cannot claim filesystem write
access. Network, filesystem, and credential metadata must be backed by matching
capabilities. Re-registering an identical hash is idempotent; changing a
definition under the same name is rejected.

## Boundary checks

`tools check` compares an agent/run grant with the definition and reports every
missing permission, network grant, filesystem level, or credential. A
destructive tool also requires a non-empty per-call approval reference. The
registry does not execute tools, fetch credentials, or infer permission from a
tool description.

```powershell
python -m acr_runtime.cli --db .acr/acr.db tools register tool.json
python -m acr_runtime.cli --db .acr/acr.db tools list
python -m acr_runtime.cli --db .acr/acr.db tools inspect filesystem.delete
python -m acr_runtime.cli --db .acr/acr.db tools check access-request.json
```

Prompt 35 may rank only definitions that pass this boundary. Prompt 34 itself
does not select or call a tool.
