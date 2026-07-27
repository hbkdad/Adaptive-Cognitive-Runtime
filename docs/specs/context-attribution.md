# Prompt 14: Context attribution

Context selection statistics are predictions. Context attribution records the
best available evidence about what mattered after a task finishes.

## Evidence channels

Each selected block can receive evidence from four independent channels:

- model attribution, such as an explicit source reference;
- execution traces that show a skill or context-backed operation ran;
- tool dependencies that connect an executed operation to an input artifact;
- evaluator judgment, represented as a bounded score from -1 to 1.

The deterministic fusion policy weights these channels at 20%, 30%, 25%, and
25%. It records the individual scores alongside the fused result so the decision
remains inspectable. The stored role distinguishes memories that affected an
answer, skills and tools that were used, and documents that contributed.

## Conservative outcomes

Every selected block receives exactly one outcome:

- `contributed` when at least one positive channel provides evidence;
- `ignored` only when the caller explicitly reports it and no positive channel
  contradicts that report;
- `misled` for explicit misleading evidence or a negative evaluator judgment;
- `uncertain` when the available signals do not establish an effect.

No explicit citation is required for usefulness, and its absence is never
treated as proof of waste. Unknown source references fail closed. Uncertain
records do not change memory or skill utility history.

Approximate realized ROI is the fused impact multiplied by task quality and
divided by the block's token cost. Conclusive successful contributions update
positive memory or skill history; explicitly ignored or misleading evidence
updates negative history. Raw source content is not duplicated in attribution
records.

The design follows the W3C PROV distinction between entities, activities, and
their use/derivation relationships, and uses structured, correlatable event
fields consistent with OpenTelemetry guidance.
