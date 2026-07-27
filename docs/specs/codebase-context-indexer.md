# Prompt 53: Codebase Context Indexer

## Outcome

ACR can explicitly index one local repository and retrieve one exact symbol
with a bounded structural neighborhood. The index contains metadata, not source
bodies or embeddings. Source is read only when requested and only after its
current SHA-256 hash matches the indexed generation.

```powershell
python -m acr_runtime.cli --db .acr/acr.db code index .
python -m acr_runtime.cli --db .acr/acr.db code retrieve `
  "SkillRegistry.activate" --repository . --budget 4000
```

Tracked Git files are the default. `--include-untracked` adds only untracked
files that Git does not ignore. Non-Git filesystem discovery requires the
explicit `--allow-non-git` option.

## Retained model

Schema version 39 adds:

- `code_repositories`: opaque checkout identity, active generation, snapshot
  hash, parser version, and bounded index policy;
- `code_index_runs`: append-only successful-generation audit and measured
  counts;
- `code_files`: repository-relative paths, roles, sizes, hashes, and parser
  status;
- `code_symbols`: identifier-only interfaces and exact line spans;
- `code_imports` and `code_references`: structural import/call/reference
  metadata;
- `code_dependencies`: dependency names and scopes extracted from supported
  manifests.

Absolute checkout paths, Git remotes, source bodies, comments, documentation
prose, configuration values, dependency specifications, and embeddings are not
persisted.

Each successful refresh parses outside the database transaction and replaces
the active file graph in one `BEGIN IMMEDIATE` transaction. A failed refresh
leaves the previous generation intact. Completed run summaries remain
auditable.

## Discovery and safety

The default Git enumerator uses `git ls-files -z --cached`. Optional untracked
discovery adds `--others --exclude-standard`; ignored untracked files are
excluded. Already tracked files still pass ACR's independent deny and secret
checks.
The supplied directory must be the exact Git worktree root.

The indexer:

- resolves the root once and stores only a deterministic, non-plaintext
  checkout fingerprint;
- rejects traversal, symlinks, Windows reparse points, and resolved paths
  outside the root;
- skips dependency caches, VCS/runtime state, generated output, credentials,
  key material, databases, archives, and binaries;
- requires strict UTF-8 or UTF-8 with BOM;
- skips a complete file when the secret scanner detects credential material;
- caps file count before full enumeration, individual bytes, aggregate bytes,
  AST input size, lines, line length, symbols, references, Git output, and
  subprocess duration;
- never imports, compiles, evaluates, executes, or downloads repository code.

The defaults are 10,000 discovered files, 512 KiB per text file, and 50 MiB of
text. Hard ceilings are 20,000 files, 1 MiB per file, and 100 MiB total.
Python AST parsing is further capped at 256 KiB per file.

## Language truth model

Python declarations, nesting, imports, inheritance names, and call names come
from `ast.parse` in a killable worker with a wall timeout and OS resource limits
where supported; parsing does not execute code. Dynamic dispatch and receiver
types are not inferred, so relationships are labelled lexical or possible, not
proven runtime call graphs.

JavaScript and TypeScript currently expose only conservative top-level named
declarations and imports. Their files are retained with
`parse_status=partial` and `error_kind=lexical_declarations_only`. Prompt 54
will add deeper language-aware slicing; this release does not present regex
matches as authoritative call edges.

Markdown headings become semantic documentation sections. Exact backticked
symbol references may relate one section to a target. Supported configuration
manifests expose only a metadata-only candidate and dependency names, never
configuration values or a full configuration body.

## Retrieval contract

`CodeContextRequest` requires one trimmed query, a 64–20,000 estimated-token
budget, and a 1–24 file limit. The default is 4,000 tokens and 12 files.

An unqualified duplicate name returns `ambiguous` with bounded candidates.
Callers must then use a qualified name such as `Worker.run`; ACR never picks an
arbitrary definition.

The target definition is reserved first and is indivisible. If it cannot fit,
the result is `unavailable` with `target_exceeds_budget`. Remaining items are
ranked deterministically:

1. matching production lexical call sites;
2. possible callees with a unique indexed name;
3. matching test lexical call sites;
4. metadata-only language configuration candidates;
5. documentation sections with exact textual code references.

Every returned source segment is HTML-escaped inside the existing
`<untrusted_data>` boundary and includes repository-relative provenance,
`authority=none`, its exact span, hash-verification state, relation confidence,
suspicious-instruction signals, and secret-redaction count. The response
reports measured bytes/files, estimated tokens, omissions, warnings,
generation, parser version, snapshot hash, and `semantic_closure=false`.
`complete` describes only the bounded retained result; it never claims a full
dynamic call graph.

Before retrieval, ACR rebuilds the bounded snapshot hash using the retained
policy. Any added, removed, or changed relevant file returns `stale` and no
source. Each selected target and neighbor is hash-checked again immediately
before its line range is read.

## Deliberate boundary

The service and CLI contracts are available, but Prompt 53 does not add a
browser endpoint that accepts arbitrary server filesystem paths. Later API or
MCP exposure must bind opaque repository IDs to server-side roots and require
an exact filesystem-read capability.

The structural result can later become a bounded `file` candidate for the
existing context compiler. Automatic injection into every compile is deferred
until retrieval precision has benchmark evidence.

## Primary-source basis

- Python documents that `ast.parse` creates an AST without executing the
  program and warns that sufficiently complex input can exhaust interpreter
  resources, which is why ACR adds tighter AST limits:
  <https://docs.python.org/3/library/ast.html>
- Git documents that `ls-files --cached` enumerates tracked files and that
  `--others --exclude-standard` applies repository, info, and global exclusion
  rules:
  <https://git-scm.com/docs/git-ls-files.html>
- Python documents that filesystem traversal does not follow directory links
  by default and warns that following them can recurse indefinitely:
  <https://docs.python.org/3/library/os.html#os.walk>
- OWASP recommends excluding or sanitizing source, secrets, connection
  strings, and sensitive file paths in retained logs. ACR applies the same
  minimization to its durable repository metadata:
  <https://cheatsheetseries.owasp.org/cheatsheets/Logging_Cheat_Sheet.html>
