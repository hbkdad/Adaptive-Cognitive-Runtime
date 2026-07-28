# Meta-context engineering

Prompt 68 adds an experimental strategy lab around the real context compiler.
It does not generate compiler code and cannot activate a production strategy.

## Closed strategy profile

One immutable candidate may change only:

- ordering within the already selected optional context tier:
  `production`, `utility_desc`, or `roi_desc`;
- the audited deterministic compressor's minimum-token threshold, 40..200;
- maximum retrieved memories, 4..32;
- maximum selected active skills, 1..4.

The schema accepts no paths, roots, globs, URIs, prompts, templates, content,
scope ancestry, `include_global`, security or permission fields, secret
handling, resource limits, Python, SQL, or arbitrary nested configuration.
Required context remains first, render authority tiers remain fixed, the hard
token budget is unchanged, and skill lifecycle/permission checks remain outside
the strategy.

Retrieval ranking continues to use Prompt 67's separately versioned
`retrieval_weights` policy rather than duplicating weights inside a strategy.
File selection remains unavailable until a governed adapter can bind an
authorized repository/index snapshot, exact file/symbol truth labels, and hard
file/byte/token limits. Candidates can never supply filesystem locations.

## Paired evaluation

`MetaContextEngine.propose` stores only canonical strategy JSON plus hashes of
the production parent and hypothesis. A trusted `SealedContextHarness` runs
production and candidate strategies on the same case manifest and seed and
returns per-case fixed-point evidence. Aggregate metrics are derived inside the
engine.

An offline candidate is `promotion_eligible` only when all cases are present
and unique, the fixed sample floor is met, average practical quality improves,
tokens do not regress, hard violations are zero, protected cases do not
regress, and authority/provenance invariants remain unchanged. A benchmark
exception is durably blocked and cannot be retried through the same candidate.

`promotion_eligible` is not production activation. Shadow/canary evaluation,
one-use operator authorization, and atomic rollback rehearsal are intentionally
still required:

```powershell
python -m acr_runtime.cli --db .acr/acr.db meta-context readiness
```

The CLI can create and inspect immutable candidates, but it cannot accept a
caller-authored metrics file:

```powershell
python -m acr_runtime.cli --db .acr/acr.db meta-context propose candidate.json
python -m acr_runtime.cli --db .acr/acr.db meta-context inspect STRATEGY_ID
python -m acr_runtime.cli --db .acr/acr.db meta-context report RUN_ID
```

The proposal file contains exactly:

```json
{
  "strategy": {
    "ordering_profile": "utility_desc",
    "compression_minimum_tokens": 60,
    "max_memories": 20,
    "max_skills": 3
  },
  "hypothesis": "Reduce irrelevant context while preserving required evidence."
}
```

## Compression security invariant

Candidate content is assessed before filtering as before. When compression
changes the text, the transformed result is assessed again and framed using
the transformed hash. A quarantined transformation aborts compilation. This
prevents a strategy-selected transform from laundering instructions under the
original content hash.

## Research basis

- [BEIR heterogeneous retrieval evaluation](https://arxiv.org/abs/2104.08663)
- [Lost in the Middle](https://arxiv.org/abs/2307.03172)
- [RECOMP](https://arxiv.org/abs/2310.04408)
- [LongLLMLingua](https://arxiv.org/abs/2310.06839)
- [NIST paired observations](https://www.itl.nist.gov/div898/handbook/prc/section3/prc311.htm)
- [OpenAI trustworthy evaluations](https://openai.com/index/trustworthy-third-party-evaluations-foundations/)
- [Google SRE canarying](https://sre.google/workbook/canarying-releases/)
