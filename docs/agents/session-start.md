# Session-start workflow

Use this workflow at the beginning of a bounded development session. It
assembles enough current evidence to act without loading prior sessions.

1. Inspect `git status --short`, the current branch, and the requested
   subsystem. Preserve unrelated work.
2. Identify the current milestone from the user request and the applicable
   roadmap or specification. Do not infer a milestone from old task history.
3. Retrieve project decisions within a small budget. For architecture work,
   run `memory decision-check` and validate every named or legacy assumption
   against current repository evidence.
4. Query analogous recent failures separately.
5. Search only active skills relevant to the milestone.
6. Search the affected subsystem and its specifications for explicit,
   task-relevant debt. Do not produce a repository-wide TODO inventory.
7. Load source only after this orientation, using `rg` and bounded symbol or
   section retrieval.

`retrieve_context` remains optional because it persists an audit and may add
context already supplied by the focused reads.

## Minimal working-context summary

Report only:

- repository state and unrelated changes that must be preserved;
- exact scope and current milestone;
- relevant decisions plus their current validation state;
- analogous failures and applicable active skills, including explicit `none`;
- technical debt that can affect the task;
- the next bounded action and unresolved evidence gaps.

Do not include raw memory bodies beyond the few selected facts, historical
session summaries, unrelated milestones, complete skill manifests, repository
trees, secrets, or speculative debt.

Prompt 104's validation run started from a clean `main`, retrieved one 56-token
procedural memory from eight scoped candidates, found no analogous failures or
active skills, and correctly marked a legacy-form architecture decision for
manual validation against `architecture-boundaries.toml`.
