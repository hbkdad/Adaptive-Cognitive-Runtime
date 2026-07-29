# Architecture guard

Prompt 91 adds an executable dependency policy for the small set of modules
that currently form the dependency-free core domain. The policy lives in
`architecture-boundaries.toml`; adding a module to the core is an explicit,
reviewable architecture change.

## Enforced boundary

The declared core modules cannot directly or transitively depend on:

- web and presentation modules or the FastAPI/Pydantic/Uvicorn frameworks;
- concrete local-provider adapters or their governed executor; or
- the SQLite implementation and migration modules.

The generic provider interface is deliberately not classified as a concrete
provider. The current core does not import it, but future domain ports may do
so without coupling the core to Ollama, the deterministic mock, or an execution
adapter.

`acr_runtime.architecture_guard` parses every package source file with Python's
AST, resolves absolute and relative imports, recognizes literal
`importlib.import_module()` and `__import__()` calls, and checks the shortest
direct or transitive path to each forbidden boundary. It imports no inspected
module and executes no project source.

## Running the lint

```powershell
python -m acr_runtime.architecture_guard check
```

The command emits JSON and uses exit code `0` for a kept contract, `1` for a
boundary violation, and `2` for an invalid policy or unreadable source. CI runs
the command before the tiered deterministic tests. Unit tests additionally
exercise direct web/provider/database violations, a transitive violation,
literal dynamic imports, strict policy validation, and the real repository.

## Design basis

- [Python AST documentation](https://docs.python.org/3/library/ast.html)
  defines the non-executing `Import`, `ImportFrom`, and traversal representation
  used by the guard.
- [Import Linter contracts](https://import-linter.readthedocs.io/en/stable/contract_types.html)
  document forbidden and layered dependency checks, including indirect imports.

The implementation keeps the repository's dependency-light foundation instead
of adding Import Linter as a runtime or development requirement.
