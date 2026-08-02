# Knowledge half-life (Prompt 115)

ACR assigns memory types different deterministic recency profiles. Half-life
changes retrieval recency; it does not silently delete, archive, supersede, or
declare a fact false.

## Research boundary

MemoryBank explores time- and significance-sensitive forgetting for agent
memory. Time-Aware Language Models demonstrates that changing facts require
explicit temporal treatment. HoH reports that outdated retrieved information
can degrade answer quality even when current information is also present:

- <https://arxiv.org/abs/2305.10250>
- <https://aclanthology.org/2022.tacl-1.15/>
- <https://aclanthology.org/2025.acl-long.301/>

These are research claims, not reproduced ACR results or integration authority.
ACR uses a smaller deterministic policy that preserves its existing temporal
and supersession contracts.

## Default profiles

The configured semantic baseline remains 90 days. Other profiles are stable
ratios of that baseline:

| Memory type | Default half-life | Mode |
| --- | ---: | --- |
| temporary | 1 day | timed |
| environment | 7 days | timed |
| episodic | 45 days | timed |
| semantic | 90 days | timed |
| failure | 180 days | timed |
| procedural | 360 days | timed |
| preference | 720 days | timed |
| decision | none | supersession only |

Changing the retrieval baseline scales every timed profile proportionally.
Every memory type must have exactly one profile, and supersession-only profiles
cannot define a half-life.

## Assessment semantics

`KnowledgeDecayPolicy` uses `valid_from` as the knowledge-time anchor rather
than file creation or last metadata update. Explicit temporal state has
precedence:

1. a future `valid_from` is not yet valid and scores zero;
2. an elapsed `valid_until` is expired and scores zero;
3. an invalid supersession state scores zero;
4. a valid decision scores one until superseded;
5. other valid records use `0.5 ** (age / half_life)`.

A historical assessment before a supersession boundary still sees the old
record as valid. This preserves the existing point-in-time truth contract.

Timed knowledge becomes `review_due` after one half-life, but Prompt 115 does
not block retrieval or claim that review has occurred. Source freshness is
reported as `unavailable`: the current memory schema does not contain a
verified observation time or source-freshness assertion. Prompt 116 owns that
future contract.

## Retrieval and cache behavior

Hybrid retrieval uses the type-aware score for its measurable recency
component. All other relevance, confidence, temporal validity, conflict, and
source-reliability components remain unchanged. The retrieval cache algorithm
identity is bumped so entries scored under the old global policy cannot be
served under the new policy.

## Operator workflow

```powershell
python -m acr_runtime.cli --db .acr/acr.db memory half-life <MEMORY_ID>
python -m acr_runtime.cli --db .acr/acr.db memory half-life <MEMORY_ID> `
  --at 2026-08-01T00:00:00Z
```

The command is read-only and reports the profile, anchor, age, recency,
validity, review status, and unavailable source-freshness state.

## Limitations

- Memory type is a coarse volatility signal. Prompt 115 does not infer
  volatility from content.
- API pricing and language syntax must be typed appropriately until a governed
  record-level freshness contract exists.
- `review_due` is advisory and is not proof that a source is stale.
- Quality effects require a dedicated temporal retrieval benchmark; passing
  deterministic tests proves contract behavior, not an accuracy improvement.
