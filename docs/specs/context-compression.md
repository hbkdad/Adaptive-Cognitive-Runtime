# Prompt 15: layered context compression

Compression runs after dependency expansion and before token pricing. Every
selected block records its original token estimate, final token estimate,
strategy, and whether exact source text was preserved.

## Strategy order

1. Protect exact material and return it unchanged.
2. Replace caller-identified, already-accessible artifacts with a reference.
3. Compact structured JSON without changing its data.
4. Extract caller-requested Python symbols with the standard AST and original
   source segments.
5. Extract requested symbols from other code without rewriting matched lines.
6. Distill task-relevant conversation turns while retaining their exact text.
7. Deduplicate repeated paragraphs.
8. Extract task-relevant paragraphs verbatim.

The compiler already performs whole-block deduplication and dependency
expansion. Compression never performs retrieval or resolves references itself.

## Exactness boundary

The following classes bypass lossy compression:

- cryptographic hashes or caller-marked cryptographic material;
- commands and exact shell syntax;
- diagnostic errors and stack traces;
- legal or contractual wording;
- code without an explicit symbol-selection request;
- anything marked `exact_required`.

Python's `ast.get_source_segment` is used because `ast.unparse` may produce code
that is semantically equivalent but not textually equal to the source. If
parsing or symbol selection fails, the original code is retained. Generic code
selection copies matched lines and does not summarize expressions.

`acr telemetry compression` groups persisted blocks by strategy and reports
original tokens, selected tokens, and measured savings. Schema v12 adds only
this content-free metadata to context attribution rows.
