# Prompt 13: Token Economist

The Token Economist makes context allocation explicit, bounded, and measurable.
For every candidate it computes the baseline:

```text
expected utility = relevance × confidence × historical utility × task importance
utility per token = expected utility ÷ token cost
```

Required rules and dependencies are admitted first and still fail closed if
they cannot fit. Optional blocks are then selected with an exact deterministic
0/1 knapsack optimizer:

```text
maximize sum(expected utility)
subject to sum(tokens) <= context budget
```

This maximizes total expected utility rather than greedily selecting the highest
utility-per-token item. Ties prefer fewer tokens and stable source identifiers.

## Adaptive budget

Each request is classified as low, medium, or high complexity using bounded,
deterministic task features. The default policy allocates 60%, 80%, or 100% of
the usable requested input allowance respectively. Before that allocation, 15%
of the model window is reserved for output and 10% for reasoning, with a small
minimum reserve for each. The task text itself also consumes input capacity.

The compiler records the requested and effective input budgets, model window,
headroom, complexity, candidate and selected counts, and expected utility in
`token_budget_plans`. Normal context attribution records later receive useful or
wasted outcomes. `acr telemetry economy` exposes aggregate allocation evidence.

This is a transparent baseline, not an autonomous learning system. Prompt 14
must require evaluation evidence, bounded policy versions, and rollback before
historical outcomes can change allocation behavior.
