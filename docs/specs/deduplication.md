# Prompt 66: bounded deduplication

## Decision

ACR implements duplicate detection as a bounded, advisory evidence engine, not
as a destructive consolidation service. It scans at most 500 artifacts and
10,000 eligible similarity pairs in one run. An artifact is capped at 1 MiB
and a run at 16 MiB. Database scans default to 100 artifacts.

The supported artifact kinds are:

- `memory`
- `context`
- `skill`
- `tool_output`
- `model_request`

Generated procedures and agent specifications are not silently mapped onto a
different kind. They require their own stable identity, authority, and behavior
contracts before they can enter this engine.

Every reported match requires review and has
`automatic_action_allowed: false`. `MERGE`, `REFERENCE`, `SUPERSEDE`,
`COMPOSE`, and `KEEP_SEPARATE` are recommendations only. The current detector
emits exact, semantic, near-duplicate, and overlapping-capability evidence. It
does not infer version succession from names or similarity; the
`version_successor` relation is reserved for later explicit lineage support.

## Canonical identity

Exact comparison runs first. A typed identity envelope includes:

- canonicalizer version;
- artifact kind;
- exact scope partition;
- privacy partition;
- identity fields; and
- behavior-affecting fields.

Strings use Unicode NFC and normalize CRLF and CR line endings to LF. JSON is
serialized deterministically with recursively sorted keys, compact separators,
UTF-8, finite numbers, and no duplicate keys after normalization. SHA-256 of
that versioned representation is the canonical hash.

This follows the deterministic representation objective of
[RFC 8785](https://www.rfc-editor.org/rfc/rfc8785.html) and the digest
properties specified by
[NIST FIPS 180-4](https://csrc.nist.gov/pubs/fips/180-4/upd1/final).
ACR deliberately uses canonical NFC, not compatibility normalization:
[Unicode UAX #15](https://unicode.org/reports/tr15/) warns that NFKC and NFKD
can erase distinctions that matter to meaning.

The canonicalizer is identified as `acr-canonical-json-nfc-v1`. It is a
runtime-specific deterministic encoding informed by RFC 8785, not a claim of
full cross-language JCS conformance. Changing normalization,
field selection, or serialization requires a new version; old reports remain
interpretable against the version they recorded.

## Comparison safety

The engine never compares artifacts across kind, scope, or privacy partitions.
An exact duplicate additionally requires the same behavior contract. This
prevents content similarity from collapsing different permissions, tool
dependencies, temporal rules, schemas, or authority.

After exact hashing, the built-in near-duplicate detector combines ordered-token
similarity and token-set Jaccard overlap. Its high threshold is only a candidate
rule, not proof of equivalence. Differing numeric values and negation signals
are retained as blockers. A blocked pair is kept separate even when its wording
is close.

Semantic matching is disabled unless an adapter is:

- explicitly marked `trusted_local`;
- identified by a non-empty model ID and version;
- used only for public artifacts;
- reached only after a minimum lexical candidate floor; and
- constrained to a score in the closed interval from zero to one.

The adapter identity is persisted with the match so results from different
models or revisions are not conflated. No remote semantic service is enabled by
this contract.

[W3C SKOS](https://www.w3.org/TR/skos-reference/#mapping) distinguishes exact
from close matches and deliberately makes close matches non-transitive.
Accordingly, ACR does not form semantic clusters by transitive closure. A
similarity score is pair evidence only.

Thresholds are versioned policy, not universal truth. Before semantic
recommendations can influence a later consolidation workflow, they must be
calibrated independently for each artifact kind, language, and adapter version
against representative labelled pairs. Precision, recall, false-positive and
false-negative rates, uncertainty, and deployment drift must be reported.
This follows the testing, documentation, uncertainty, and ongoing monitoring
expectations in the
[NIST AI RMF Measure function](https://airc.nist.gov/airmf-resources/airmf/5-sec-core/)
and its
[validity and accuracy guidance](https://airc.nist.gov/airmf-resources/airmf/3-sec-characteristics/).

## Provenance and persistence

Each single-scope scan writes three sealed, append-only record classes:

- a run with detector policy, requested kinds, bounds, and comparison counts;
- content-minimized items with hashes and pseudonymous source references; and
- matches with relation, recommendation, score, method, blockers, evidence,
  provenance references, and review flags.

Raw identity, similarity text, prompts, memory bodies, tool output, and model
requests are not copied into the deduplication audit. The source systems remain
authoritative and addressable. Stored item references and provenance references
are one-way hashes salted by the run ID rather than raw identifiers; source
revisions are also hashed.
Database triggers reject update or deletion of
deduplication runs, items, and matches. Child insertion is rejected after the
declared item and match counts are atomically sealed. Report loading requires
the same exact scope and verifies both counts. A scan fails closed if an artifact ID,
provenance reference, or semantic-adapter identity resembles secret material.

Database scanning holds one `BEGIN IMMEDIATE` transaction from source reads
through report sealing, preventing a source revision from changing between
fingerprinting and audit commit. Memory adapters admit only the requested exact
scope, live retention, candidate/confirmed status, and active/cold lifecycle.

ACR intentionally does not retain raw tool output, so database scans do not
pretend that `tool_outcomes` evidence is the original output. Callers can use
the Python artifact API to analyze in-memory tool outputs with an explicit
scope, privacy class, behavior contract, and source revision. Persisted
`context_uses` also lacks content, so retrieved context is deduplicated at the
task-local compiler boundary rather than by comparing source identifiers.

This implements the preservation principle behind
[W3C PROV-O](https://www.w3.org/TR/prov-o/): revisions and derived entities
retain relationships to their sources, while alternate and specialization
relations preserve distinctions between different aspects. Any future merge
or composition must create a new derived entity referencing every source.
Supersession may change retrieval preference, but must not erase the
predecessor or its evidence.

## Context compiler integration

The context compiler has one narrow automatic optimization: it may coalesce
exact context candidates only when all of these fields agree:

- source type;
- content origin;
- security authority;
- content kind;
- exact-preservation requirement; and
- canonical content hash.

The retained candidate takes the strongest required flag, utility, and
confidence and unions all dependencies and provenance. The other candidate is
recorded as an exact-duplicate rejection referencing the retained source.
Candidates from different authority or content partitions never coalesce.

This is deterministic transport compaction, not a semantic merge and not an
authorization decision.

## CLI

Run one bounded scan across the artifact kinds safely materialized for an exact
scope. Kinds without a safe persisted content/privacy contract are skipped:

```powershell
python -m acr_runtime.cli --db .acr/acr.db dedup scan `
  --scope project:a --limit 100
```

Repeat `--kind` to restrict the scan:

```powershell
python -m acr_runtime.cli --db .acr/acr.db dedup scan `
  --kind memory --scope project:a --limit 100
```

Load the immutable content-minimized report:

```powershell
python -m acr_runtime.cli --db .acr/acr.db dedup report <RUN_ID> `
  --scope project:a
```

The CLI does not approve, merge, reference, supersede, compose, archive, or
delete any source artifact.
