# Negative procedures (Prompt 114)

Negative procedures represent evidence-backed guidance about what not to do.
They are not skills, prompts, tools, or executable policies. They are a
read-only projection of first-class failure intelligence and therefore cannot
gain authority independently of their source evidence.

Research supports retaining lessons from unsuccessful trajectories, but it
does not establish that one failure is a universally valid prohibition.
Reflexion stores linguistic feedback in episodic memory, ExpeL extracts
experience-derived insights, and Wang et al. show that failed trajectories can
be useful when quality controlled:

- <https://arxiv.org/abs/2303.11366>
- <https://arxiv.org/abs/2308.10144>
- <https://arxiv.org/abs/2402.11651>

ACR applies the narrower engineering interpretation below. These papers are
research context, not implementation authority, and ACR does not claim to
reproduce their reported results.

## Representation

`FailureIntelligence.assess_negative_procedures` evaluates existing failure
records for one exact scope and task class. The source scan is capped at 500
records and fails closed above that bound. An eligible projection contains:

- a deterministic identifier derived from the scoped source;
- the exact scope, task class, failed action, and structured environment;
- the avoidance rule;
- source failure and memory identifiers;
- occurrence, distinct-evidence, and confidence counts;
- fixed authority `planning_constraint_only`.

The source failure record remains authoritative. No second registry, copied
evidence body, model-generated rule, skill package, memory write, tool call, or
execution path is created.

## Eligibility

Every gate is mandatory:

- the requested scope and normalized task class match exactly;
- the scope is not `global`;
- the failure is unresolved and explicitly deterministic;
- confidence is at least `0.95`;
- at least three occurrences and three distinct evidence references exist;
- a non-empty avoidance rule exists.

An ineligible assessment reports stable reason codes. Resolved records cease
to produce a negative procedure and instead retain their successful remediation
link through ordinary failure intelligence.

Global failures remain useful as weighted planning evidence, but cannot become
negative procedures. The current schema has no independent cross-scope evidence
contract, so treating a global record as a prohibition would over-generalize.

## Planning behavior

The existing failure advisor remains the planning integration. Prompt 114
aligns its absolute-block threshold with the stricter negative-procedure
threshold: three occurrences and three distinct evidence references. Fuzzy
analogy must still meet the existing `0.75` threshold at query time.

This preserves two distinct concepts:

1. ordinary, possibly uncertain failure evidence yields a weighted warning;
2. repeated deterministic evidence can yield a scoped planning prohibition.

Neither concept grants execution authority.

## Operator workflow

```powershell
python -m acr_runtime.cli --db .acr/acr.db failure negatives `
  --scope my-project --task-class "sqlite migration"
```

Use `--include-ineligible` to audit rejected source records and their reason
codes. The command is read-only.

## Limitations and next evidence

- Evidence references are caller supplied; their independence is not
  cryptographically proven.
- Exact scope and task-class matching favors false negatives.
- Environment applicability is retained but not generalized.
- Cross-scope or global promotion needs a separate evidence model and benchmark.
- A future skill bridge would require explicit validation and promotion; this
  projection is intentionally insufficient.
