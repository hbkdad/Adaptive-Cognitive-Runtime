# Prompt 28: independent critic and evaluator

## Contract

Evaluation is independent of task execution. A case supplies explicit reference
data only for the dimensions that can be verified:

- `expected` grounds correctness by normalized exact comparison;
- `required_elements` grounds completeness;
- machine-readable `constraints` ground constraint compliance;
- `output_schema_json` grounds the supported JSON Schema subset;
- evidence entries carry a source, claim, and externally established
  `verified` flag;
- token counts plus `token_budget` ground efficiency;
- token counts plus `necessary_token_estimate` ground unnecessary token usage;
- credential-pattern redaction grounds the current security check.

An omitted reference dimension produces no deterministic score. It is not
silently treated as a pass.

## Judge and aggregation policy

Panels support any number of uniquely identified deterministic and LLM judges.
Every score is retained. Aggregation records the mean, max-minus-min
disagreement, judge counts by kind, and whether the criterion is grounded.

A criterion can pass only when:

1. at least one deterministic judge scored that criterion;
2. its aggregate meets the configured threshold; and
3. every deterministic score for that criterion passed.

Consequently, a model judge can expose disagreement or add a secondary
assessment, but neither one model's confidence nor several correlated model
opinions can override failed deterministic evidence. LLM evaluation also
requires explicit content-transmission authorization and strict bounded
structured output.

## Persistence and privacy

Schema v24 retains:

- one evaluation run with score, pass state, and maximum disagreement;
- ordered per-judge results, including empty/not-applicable results;
- per-criterion aggregates and grounding status.

The run stores SHA-256 digests, lengths, counts, and token metadata for the case.
It does not store the objective, actual answer, expected answer, constraint
content, required-element content, or evidence content. Judge evidence is
redacted before model-judge results are retained.

## CLI

Run the local deterministic panel:

```powershell
python -m acr_runtime.cli --db .acr/acr.db evaluate run evaluation-case.json
```

Inspect the durable result:

```powershell
python -m acr_runtime.cli --db .acr/acr.db evaluate report <RUN_ID>
```

LLM judges remain API-only because their provider, model, and content
transmission permission must be configured explicitly.

## Deliberate limits

- `verified` evidence is an input assertion from an external verifier; this
  component does not fetch or independently validate sources.
- The JSON Schema judge implements only the documented required/type subset.
- The secret-pattern judge is a narrow safety signal, not a complete security
  review.
- Necessary-token estimates are benchmark inputs, not learned ground truth.
