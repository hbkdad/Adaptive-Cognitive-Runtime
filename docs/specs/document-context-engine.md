# Prompt 55: Document Context Engine

## Outcome

ACR can build a semantic Markdown document index and retrieve bounded lexical
or exact context:

```powershell
python -m acr_runtime.cli --db .acr/acr.db code index .
python -m acr_runtime.cli --db .acr/acr.db docs index .
python -m acr_runtime.cli --db .acr/acr.db docs retrieve `
  "Retrieval contract" --repository .
python -m acr_runtime.cli --db .acr/acr.db docs retrieve `
  "CodeContextRequest" --mode exact --repository .
```

Prompt 55 deliberately layers on Prompt 53. The code index supplies a bounded
Git file set, safe repository-relative paths, an atomic generation, source
hashes, and freshness verification. Document indexing refuses to run when that
generation is absent or stale.

## Supported profile

Version 1 supports tracked strict UTF-8 or UTF-8-BOM `.md`, `.markdown`, and
`.txt` files. Markdown recognizes CommonMark-style ATX and Setext headings
outside fenced code and HTML comments. Plain text becomes one level-zero root
section. A Markdown preamble is also a level-zero section. Heading parents use
the nearest preceding lower level, and duplicate anchors receive stable
numeric suffixes.

MDX, RST, HTML, PDF, DOCX, XML, archives, OCR, encrypted documents, remote
URLs, and office automation are unavailable in v1. Structured binary
formats require a separately hardened parser boundary and honest
page/extraction provenance; they are not silently treated as exact text.

## Retained model

Schema version 40 adds:

- `document_indexes`: the active Prompt 53 generation, snapshot, parser
  configuration, bounded counts, and completion time, including empty indexes;
- `documents`: opaque repository/file/generation identity, relative path,
  source-byte hash, bounded metadata, parser configuration, and safety signals;
- `document_headings`: exact heading spans, hierarchy, qualified paths, and
  stable anchors;
- `document_sections`: non-overlapping preamble/heading spans and parents;
- `document_chunks`: non-overlapping source coordinates, hashes, token cost,
  chunk kind, and split reason;
- `document_relationships`: explicit parent, previous, next, and local anchor
  links.

Document prose, chunk text, absolute checkout paths, embeddings, summaries,
normalized search copies, and FTS terms are not persisted. A database search
for an ingested quotation therefore finds no source body.

Indexing parses and validates all documents before its replacement transaction.
A failed refresh leaves the previous document graph intact. IDs are
deterministic for repository generation, path, source hash, and source span.

## Semantic-first sections and chunks

Every source character belongs to exactly one non-overlapping section.
Sections that fit the configured character ceiling become one
`semantic_section` chunk. Oversized sections group complete blank-line blocks.
An indivisible oversized block remains whole and is explicitly labelled
`oversize_atomic_block`; it is not invisibly truncated. Version 1 uses no
overlapping token windows.

Character offsets are zero-based, half-open offsets into the strictly decoded
source. Byte offsets refer to the original UTF-8 text after BOM handling.
Line endings, whitespace, Unicode, fence delimiters, and final-newline state
remain unchanged in the selected raw slice.

Relationships are structural, not inferred semantic similarity. Shared words
never create edges. Local anchor links are parsed only outside fenced code and
comments; no link is fetched.

## Retrieval and exactness

Lexical retrieval reads current hash-verified chunks on demand and requires
every normalized query term. Exact mode performs a case-sensitive substring
match without stemming or whitespace normalization. Repeated quotations return
`ambiguous` until `--occurrence` selects one. Missing text returns `not_found`;
no fuzzy substitute is used.

The target is selected before token accounting. If no complete target can fit,
retrieval returns `target_exceeds_budget` rather than truncating it. Results
include exact character, byte, and line spans plus both source-byte and slice
hashes.

`original_text_exact=true` describes the verified raw slice.
`transport_framed=true` records that returned text is HTML-escaped inside
`<untrusted_data>`. These are distinct claims: the safe transport is not
byte-identical to the raw document. Title and heading metadata are also escaped.

Each selected chunk receives a durable content-security assessment. Document
text always has `authority=none`; suspicious instructions are telemetry and
cannot authorize tools, permissions, memory, skills, agents, or policy changes.
Secret-bearing files remain excluded by Prompt 53 and are rechecked before
document metadata is published.

## Deliberate API boundary

The service and CLI accept a local repository root. No browser endpoint accepts
an arbitrary server filesystem path. A future API or MCP surface must bind an
opaque repository ID to a server-controlled root and enforce an exact read
capability.

## Primary-source basis

- CommonMark specifies ATX and Setext headings and treats fenced-code contents
  as literal text rather than Markdown structure:
  <https://spec.commonmark.org/0.30/>
- SQLite foreign keys and transactions provide the retained graph and atomic
  replacement boundary:
  <https://sqlite.org/foreignkeys.html>
- Python documents strict path resolution and symlink handling used by the
  inherited Prompt 53 path boundary:
  <https://docs.python.org/3/library/pathlib.html>
- OWASP recommends extension allowlists, independent type validation, size
  limits, authorization, isolated storage, and hardened parsers for uploaded
  documents:
  <https://cheatsheetseries.owasp.org/cheatsheets/File_Upload_Cheat_Sheet.html>
