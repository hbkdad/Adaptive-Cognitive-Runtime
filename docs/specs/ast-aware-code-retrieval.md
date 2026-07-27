# Prompt 54: AST-Aware Code Retrieval

## Outcome

ACR can retrieve a minimal, exact-source Python context slice from a current
Prompt 53 index:

```powershell
python -m acr_runtime.cli --db .acr/acr.db code slice `
  "SkillRegistry.activate" --repository . --budget 4000
```

The slicer resolves one indexed symbol, verifies the retained repository
snapshot and file hash, and reparses the file in a fresh killable worker. It
never imports, compiles, evaluates, executes, or downloads repository code.
The worker is resource isolation, not a security sandbox.

## Source-unit contract

The indivisible target is deliberately conservative:

- a top-level function or class retains its complete decorated definition;
- a method retains its complete enclosing class;
- a nested function or class retains its nearest enclosing top-level function
  or class.

This avoids synthesizing class or function shells that could change Python
class-body, closure, decorator, or definition-time behavior. Original source
text and line endings are preserved. `ast.unparse` is not used.

After reserving the target, the slicer adds direct `__future__` imports,
referenced imports, and recursively referenced same-module functions, classes,
and simple assignments. Dependencies are cycle-safe and bounded. Aliases,
decorators, defaults, annotations, class bases, and nested lexical scopes are
analyzed. Fragments are emitted in original module order so an unredacted raw
slice remains syntactically valid.

## Honest completeness

`complete` means the returned transport fit its token, byte, and dependency
limits. `semantic_closure` is narrower: it means all statically loaded names
observed by this Python analysis were resolved with no dynamic or budget
warning. It does not prove runtime behavior.

Reflection, `eval`/`exec`, dynamic imports or lookup, wildcard imports,
descriptors, monkey patching, receiver dispatch, and conditional bindings can
defeat static analysis. A useful result is returned as `partial` with explicit
warnings and unresolved names. JavaScript and TypeScript remain on Prompt 53's
conservative lexical path; language-aware slicing for them is deferred.

Python parsing is capped at 100,000 AST nodes, depth 400, a four-second wall
wait, and a best-effort 256 MiB address-space limit where the operating system
supports it. The response records the current Python grammar version because
AST grammar follows the executing Python minor version.

## Security and freshness

No source is returned when the repository snapshot or target hash differs from
the active generation, the path is unsafe, decoding fails, or the current file
contains detected secret material. Repository text is authority-free data and
is HTML-escaped exactly once inside `<untrusted_data>`. Prompt-like comments
are reported as safety signals, not followed as instructions.

The index remains metadata-only. Slicing does not persist source bodies,
absolute checkout paths, embeddings, or generated context.

## Budgets and measurement

`PythonSliceRequest` accepts 64–20,000 estimated tokens and 0–48 dependency
fragments. The defaults are 4,000 tokens and 16 dependencies. The target is
indivisible and fails closed with `target_exceeds_budget`.

Token telemetry keeps comparable quantities separate:

- raw hash-verified whole-file tokens;
- raw slice-source tokens;
- framed response tokens and framing overhead;
- signed tokens saved and savings ratio;
- raw and response bytes.

Savings are not clamped: a small file may honestly produce a negative saving
after dependency selection. The deterministic padded fixture saves more than
70 percent versus whole-file retrieval.

## Primary-source basis

- Python AST nodes expose exact source spans, with line numbers and UTF-8 byte
  column offsets, and the documentation warns that complex input can exhaust
  resources: <https://docs.python.org/3/library/ast.html>
- Python's tokenizer documents UTF-8 BOM and encoding-cookie handling. Prompt
  54 intentionally retains Prompt 53's stricter UTF-8/UTF-8-BOM boundary:
  <https://docs.python.org/3/library/tokenize.html>
- Python documents symbol-table scope analysis separately from execution. ACR
  currently performs bounded structural binding analysis in the isolated AST
  worker and does not claim compiler-equivalent name resolution:
  <https://docs.python.org/3/library/symtable.html>
- Python's import reference explains absolute, relative, wildcard, and dynamic
  import behavior that constrains static closure:
  <https://docs.python.org/3/reference/import.html>
