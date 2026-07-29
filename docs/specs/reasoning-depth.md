# Prompt 74: Adaptive Reasoning Depth

ACR now creates one immutable reasoning-budget decision before execution
components choose their concrete implementation. The decision coordinates the
same three complexity levels already used by the Token Economist:

| Class | Planning | Minimum model tier | Context | Verification | Effort |
| --- | --- | --- | --- | --- | --- |
| low | minimal, no decomposition | small | 60% | deterministic | low |
| medium | bounded standard plan | medium | 80% | standard | medium |
| high | decomposition allowed | strong | 100% | independent | high |

These are eligibility and budget signals. The model router must still select
the cheapest model that meets its measured quality, context, and tool
requirements. A deeper class never grants more permissions, expands scope,
loads secrets, bypasses approvals, or weakens a verifier.

## Conservative classifier

The original lexical Token Economist remains one input. A versioned coordinator
adds bounded structured signals for dependencies, ambiguity, tools, external
effects, required verification, privacy, secrets, permission changes,
destructive work, and high-stakes domains. Caller-supplied labels may request a
higher minimum but cannot lower the derived floor.

Short destructive, credential, permission, medical, legal, payment, production,
or privacy-sensitive requests are always protected and high depth. Context-only
commands such as `yes, proceed` cannot enter the trivial fast path when an
external effect is declared. Repeating complexity words may raise the class,
but cannot increase the fixed decomposition or model-call ceilings.

Only a task hash, bounded features, closed reason codes, policy identity, and
the resulting resource profile are retained. Task text, chain of thought, and
provider reasoning traces are not stored.

## Provider controls and token accounting

`ReasoningControl` separates `provider_default`, `enabled`, `disabled`,
`adaptive`, discrete `effort`, and `fixed_budget` intent. These modes are not
treated as equivalent numeric promises. In particular, boolean enabled
thinking is not mislabeled as adaptive allocation. An adapter must advertise
the exact accepted modes and effort values for the exact model. Unsupported
settings fail closed rather than silently falling back.

Ollama is the first implemented adapter. Operators explicitly and independently
declare which installed models accept enabled, disabled, or named effort
controls; ACR then maps those requests to the top-level `think` field. A named
effort list is rejected unless the model also declares the effort mode. With no
declaration, the policy is advisory only and the provider request remains at
its default.

Reasoning tokens, when authoritatively reported, are an optional subset of
inclusive output tokens. They are never added to output a second time. The
existing hard output reservation therefore remains the resource ceiling.

Provider documentation behind this design:

- [OpenAI model guidance](https://developers.openai.com/api/docs/guides/latest-model)
- [Anthropic effort](https://platform.claude.com/docs/en/build-with-claude/effort)
- [Anthropic adaptive thinking](https://platform.claude.com/docs/en/build-with-claude/adaptive-thinking)
- [Gemini thinking](https://ai.google.dev/gemini-api/docs/thinking)
- [Ollama thinking](https://docs.ollama.com/capabilities/thinking)

The bounded `acr run` path creates a decision before execution, uses its context
fraction in the resource quote, selects its minimal/standard/decomposed planning
instruction, and forwards only a capability-validated provider control. The
effective mode and decision ID are retained in content-free action metadata.
Its current deterministic verifier conforms to the low policy only; medium and
high run receipts are retained but excluded from refinement until their
stronger verification contract is implemented.

## Outcome refinement

Decisions, outcomes, and policy evaluations are immutable schema-54 records.
CLI-imported outcomes are labeled `caller_supplied_unverified` and cannot
influence thresholds. Trusted runtime outcomes can produce a stricter advisory
threshold candidate, but a hard violation rejects it.

No evaluation can persist a supported or automatically activated result.
Promotion requires a future sealed paired benchmark with identical cases,
model/provider revision, prompts, tools, permissions, scope, budgets, seed, and
evaluator. Quality and safety non-inferiority must hold per protected category;
aggregate token savings cannot hide a catastrophic short task.

## CLI

```powershell
python -m acr_runtime.cli --db .acr/acr.db reasoning policy
python -m acr_runtime.cli --db .acr/acr.db reasoning classify request.json
python -m acr_runtime.cli --db .acr/acr.db reasoning inspect DECISION_ID
python -m acr_runtime.cli --db .acr/acr.db reasoning outcome outcome.json
python -m acr_runtime.cli --db .acr/acr.db reasoning refine general
```

These commands are trusted local-operator surfaces. They are not exposed over
HTTP or MCP.
