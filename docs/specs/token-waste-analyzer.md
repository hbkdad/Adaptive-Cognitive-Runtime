# Prompt 72: token waste analyzer

Schema 52 adds a content-minimized, advisory analyzer for nine distinct token
efficiency risks. It does not treat every large prompt, repeated sentence,
reflection, agent, or model escalation as waste.

The decision ladder is:

`observed signal -> candidate/confounded finding -> paired counterfactual -> quantified recommendation`

Every scan emits exactly one finding for each requested category:

1. large retrieved blocks never used;
2. repeated instructions;
3. duplicate memories;
4. unnecessary skill text;
5. oversized tool descriptions;
6. full files when symbols were sufficient;
7. excessive reflection;
8. too many agents; and
9. unnecessary model escalation.

Missing evidence remains `insufficient_evidence`. Caller-derived
`context_attributions.outcome='ignored'` is a candidate signal, not independent
proof that removal preserves the answer. A second model attempt is likewise
confounded by the first attempt's failure. Multi-agent plans are never counted
as executed work; only retained topology outcomes are visible, and those still
need a comparable single-agent counterfactual.

## Evidence tiers

The analyzer uses six verdicts:

- `observed_overhead` describes measured resource use without claiming it was
  avoidable;
- `candidate_waste` identifies a bounded review target;
- `counterfactually_avoidable` requires a paired, deterministic,
  security-passing comparison with non-regressing quality and measured or
  provider-reported token counts;
- `protected` reserves content that must not be compacted;
- `confounded` reports a signal whose cause is not isolated; and
- `insufficient_evidence` makes coverage gaps explicit.

Only `counterfactually_avoidable` may eventually carry low/base/high savings.
Schema 52 deliberately rejects that verdict and every savings value because
ACR does not yet have a sealed paired replay harness bound to the same task,
seed, model/tool settings, evaluator, and budget. A future schema must add that
lineage before quantified savings are admissible. Estimated token counts,
correlations, labels, static size thresholds, and manually imported JSON never
receive a quantified savings claim.

## Category-specific safeguards

Exact repeated instruction blocks selected in the same task and carrying the
same content origin and authority are review candidates only. Reuse across
tasks or authority boundaries is not counted. Ordering remains intact, and no
text is rewritten. Duplicate-memory evidence is consumed only from sealed,
scope-partitioned exact Prompt-66 deduplication matches; near or semantic
similarity is not reclassified as waste.

Tool size includes description, input schema, and output schema. The analyzer
never truncates the canonical definition because a final safety caveat or enum
may be the only detail preventing an unsafe call. A compact tool projection
needs separate routing, valid-call, and safety benchmarks.
Registry size alone is reported as missing delivery evidence with zero observed
tokens; schema 52 cannot distinguish a delivered tool definition from an
unused registry entry.

Uncompressed file context is visible, but “a symbol was sufficient” remains
unproven until a hash-verified slice is evaluated against the whole-file
baseline. Symbol bundles must remain bound to the repository revision and file
digest, with full-file fallback for ambiguity, stale indexes, generated or
dynamic code, and module-wide invariants.

Reflection is already limited to one pass per run. Multiple runs for one task
are therefore reported as confounded rather than automatically excessive.
Agent parallelism may reduce elapsed time while increasing aggregate tokens;
model escalation may recover a failed cheap attempt. Both require paired
quality-preserving comparisons that include orchestration, failed attempts,
retries, synthesis, and verification.

## Persistence and commands

Runs and findings are append-only. A run can transition once from `running` to
`completed` only after all nine findings exist. Database constraints reject
late findings, incomplete reports, quantified savings, mutation, and deletion.
Repeating a scan at the same evidence revision returns the existing run.
Each evidence family is deterministically limited to 10,000 rows. Every
finding discloses per-source available-row counts and truncation, so a bounded
scan cannot be mistaken for complete coverage.

Commands:

```text
acr --db PATH waste scan --scope SCOPE
acr --db PATH waste report RUN_ID --scope SCOPE
```

All output is advisory. Prompt 72 cannot delete or supersede memory, rewrite
skills or tools, alter reflection depth, change agent count, change model
routing, widen permissions, or activate an optimization.

## Research basis

The measurement policy follows current primary guidance:

- OpenAI prompt caching requires exact shared prefixes and reports cached usage
  separately: https://developers.openai.com/api/docs/guides/prompt-caching
- Anthropic reports cache creation, cache reads, and uncached input as distinct
  meters with provider-specific TTL behavior:
  https://platform.claude.com/docs/en/build-with-claude/prompt-caching
- OpenAI evaluation guidance recommends task-specific, representative,
  continuously evaluated comparisons:
  https://developers.openai.com/api/docs/guides/evaluation-best-practices
- Language Server Protocol symbol, definition, and reference operations provide
  the standard structural retrieval basis:
  https://microsoft.github.io/language-server-protocol/
- NIST AI RMF Measure emphasizes documented methods, uncertainty, test sets,
  and drift monitoring:
  https://airc.nist.gov/airmf-resources/playbook/measure/

Prompt caching can reduce provider prefill or billing work, but cached tokens
still occupy logical context. Cache savings and logical-context reduction must
therefore remain separate measures.
