# Documentation agent

Prompt 90 maintains seven source-derived references:

- architecture map;
- API reference;
- CLI reference;
- skill format;
- memory schema;
- provider setup;
- troubleshooting.

The workflow is intentionally review-gated:

```powershell
python -m acr_runtime.cli docs propose-reference . --output .acr/docs-review
python -m acr_runtime.cli docs review-reference .acr/docs-review
python -m acr_runtime.cli docs publish-reference .acr/docs-review `
  --review-hash <EXACT_REVIEW_HASH> --approve
```

`propose-reference` writes a new candidate directory and refuses to overwrite an
existing path. The manifest binds the generator version, complete runtime source
digest, exact seven-file set, and artifact hashes.

`review-reference` regenerates all content in memory from current source. It
issues a review hash only when the proposal is untampered and still fresh, and
reports each published artifact as missing, changed, or unchanged.

`publish-reference` requires the exact fresh review hash and `--approve`.
It writes only the seven fixed filenames. A source edit or candidate edit makes
the review stale and blocks publication.

The generated material is descriptive, not authoritative configuration.
Generation does not change runtime state, execute models, contact providers, or
include secrets. Human review remains responsible for clarity and for deciding
whether a source-derived change should be published.

The CLI reference follows Python's
[`argparse` subcommand model](https://docs.python.org/3.12/library/argparse.html).
API routes come from FastAPI decorator declarations, while the schema reference
uses SQLite's documented
[`PRAGMA table_info`](https://sqlite.org/pragma.html#pragma_table_info)
against a fresh migrated database.
