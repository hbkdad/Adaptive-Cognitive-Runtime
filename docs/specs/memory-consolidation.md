# Memory consolidation (Prompt 8)

## Safety boundary

Consolidation is initially human-approved. The required workflow is:

```powershell
python -m acr_runtime.cli --db .acr/acr.db memory consolidate `
  --dry-run --scope project

python -m acr_runtime.cli --db .acr/acr.db memory consolidate `
  --approve <RUN_ID>
```

The dry run is persisted with target IDs, expected `updated_at` values and
statuses, reasons, and content-minimized payloads. Approval reloads that exact
plan. Any memory changed after planning is skipped rather than acted upon.
Approving a run twice is rejected.

## Proposed action groups

- `MERGES`: exact normalized duplicates in the same scope, type, and subject;
- `ARCHIVES`: expired temporary memories, stale unused candidates, and stale
  low-value records;
- `SUPERSESSIONS`: adjacent temporal truths with matching boundaries but
  missing explicit links;
- `PROMOTIONS`: evidenced candidates with repeated high utility;
- `CONFLICTS`: overlapping, unlinked claims for the same subject and scope;
- `DECAYS`: stale utility scores reduced by a configurable half-life.

The required five prompt groups are always present in output. `DECAYS` is an
additional measurable group for the prompt's stale-utility requirement.

## Conservative equivalence

Automatic merge equivalence is deliberately restricted to exact normalized
text. Semantic similarity can suggest candidates later, but it cannot mutate
memory until benchmarked. Repeated identical episodes use the same safe merge
path.

Promotion preserves existing retention reasons and adds
`consolidated_high_utility_promotion`.

## Provenance preservation

Merge application:

1. chooses the strongest record as survivor;
2. unions evidence and retention reasons;
3. adds `consolidated_exact_duplicates`;
4. archives every duplicate source record.

No raw record is deleted. Conflicts are marked `review_required` and never
automatically resolved. Consolidation audit tables contain IDs and action
metadata, not copied memory content.

## Configurable policy

`ConsolidationConfig` controls stale ages, candidate archival age, low-value
thresholds, promotion evidence/usage/utility thresholds, decay age and
half-life, floor, and bounded scan size.

Autonomous consolidation remains disabled until repeatable evaluations
demonstrate reliability.
