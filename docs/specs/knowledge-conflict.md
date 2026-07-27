# Prompt 62: knowledge conflict engine

ACR classifies disagreeing memory records deterministically before any model can
use them as a single truth. Every comparison reports evidence counts and shared
references, validity timestamps and overlap, source reliability, confidence,
and exact scopes.

The closed outcomes are:

- `no_conflict` when normalized claims match;
- `one_supersedes_another` only when an explicit bidirectional supersession
  link exists;
- `both_valid_different_scopes` when claims belong to different explicit
  scopes;
- `both_valid_different_times` when same-scope validity intervals do not
  overlap;
- `unresolved_contradiction` for overlapping, unlinked, same-scope claims.

Only an explicit supersession link produces `preferred_id`. Evidence volume,
recency, confidence, and source reliability are comparison evidence; none is
allowed to silently choose an unresolved winner.

```powershell
python -m acr_runtime.cli --db .acr/acr.db memory conflict-check database `
  --scope project:runtime
python -m acr_runtime.cli --db .acr/acr.db memory conflict-compare LEFT_ID RIGHT_ID
```

Subject analysis uses current and superseded records visible through the
registered scope hierarchy. It emits `requires_review` whenever any pair is
unresolved. The engine is read-only: it never links, supersedes, archives, or
rewrites memory. Explicit correction remains a separate governed write.
