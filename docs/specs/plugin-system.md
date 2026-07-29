# Prompt 82: declarative plugin system

ACR plugins extend the runtime without adding an unrestricted code-loading
surface. A plugin manifest contains exactly:

- `name`: stable lowercase identifier;
- `version`: exact Semantic Version;
- `capabilities`: unique namespaced features such as `research.search`;
- `permissions`: the exact union of permissions required by its entrypoints;
- `entrypoints`: capability-to-tool mappings; and
- `dependencies`: exact `plugin@version` references.

Entrypoint values are names in ACR's immutable tool registry. They are not
Python modules, callables, shell commands, URLs, or filesystem paths. ACR does
not use `importlib`, execute a package installer, resolve a dependency from the
network, or invoke a tool while registering or validating a plugin.

## Compatibility gate

Registration uses a closed manifest schema and fails compatibility when:

- a dependency is not already registered at the exact version;
- an entrypoint does not resolve to an immutable registered tool definition;
- declared permissions omit a tool permission; or
- declared permissions contain unused authority.

Every validation attempt is retained with the manifest hash, dependency
snapshot, entrypoint/tool hashes, and machine-readable reasons. Only compatible
manifests enter the immutable plugin registry. Re-registering the same hash is
idempotent; changing a published name/version is rejected.

## Non-bypassable authorization

A manifest declares requirements but grants no authority. Routing an entrypoint
restricts the existing `ToolRouter` to its exact tool definition. The router
then asks the central `PermissionController` for each exact capability and
resource scope, applies network/filesystem/credential/destructive checks, and
retains every allow or default-deny decision. Plugin routing selects metadata
only and reports `execution_performed: false`.

```powershell
python -m acr_runtime.cli --db .acr/acr.db plugins register plugin.json
python -m acr_runtime.cli --db .acr/acr.db plugins list
python -m acr_runtime.cli --db .acr/acr.db plugins inspect research 1.0.0
python -m acr_runtime.cli --db .acr/acr.db plugins route `
  research 1.0.0 research.search tool-route-request.json
```

## Primary references

- [PyPA plugin discovery guidance](https://packaging.python.org/en/latest/guides/creating-and-discovering-plugins/)
- [PyPA entry-points specification](https://packaging.python.org/en/latest/specifications/entry-points/)
- [Python `importlib.metadata` documentation](https://docs.python.org/3.11/library/importlib.metadata.html)
- [OWASP authorization guidance](https://cheatsheetseries.owasp.org/cheatsheets/Authorization_Cheat_Sheet.html)
- [NIST SP 800-218 SSDF](https://csrc.nist.gov/pubs/sp/800/218/final)
