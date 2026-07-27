# Prompt 27: progressive hierarchical planner

The Prompt 27 planner produces bounded, inspectable plans; it does not execute
them. It turns a strict objective and coarse work hints into either one action
for simple work or a small dependency graph for genuinely decomposable work.

## Planning responsibilities

Each request states the objective, task class, constraints, prerequisites,
coarse work hints, available tools, permissions, model policy, resource
envelopes, and verification requirements. The planner:

1. preserves a bounded objective summary and constraints;
2. detects unsatisfied prerequisites, missing tools, missing permissions, and
   invalid required skills;
3. decomposes only when at least two useful work hints and their complexity or
   dependencies justify it;
4. routes the smallest useful set of active skills;
5. derives required tools and checks them against the explicit allowlist;
6. asks the Prompt 25 factory for the minimum justified temporary agent team;
   retains Prompt 26's compatible historical recommendation as advisory
   orchestration evidence;
7. allocates token, money, and time envelopes across leaf work;
8. retains dependencies, scopes, capability evidence, and verification on each
   node; and
9. blocks execution whenever prerequisite evidence is incomplete.

A simple task remains one action. An initial decomposed plan contains one root
and at most twelve coarse children. The absolute revision limit is 50 nodes and
depth four, preventing accidental 50-step plans for simple work.

## Progressive refinement

High-complexity leaves are marked `expandable`. `plans refine` replaces one
expandable action with two to six children. Child budgets sum to the parent
envelope. Refinement cannot expand the parent's task scope, memory scope,
tools, skills, permissions, or assigned agents.

## Editable, retained revisions

Every edit is an immutable full snapshot with a monotonically increasing
revision, parent revision, change kind, bounded reason, canonical content hash,
and optimistic lock. Edits are allowed while a plan is proposed or executing.
Historical revisions remain inspectable.
Execution may append a newly discovered prerequisite and move the plan to
`blocked`; it may not erase prerequisites or rewrite prior evidence. Once the
new prerequisite has bounded evidence, a later revision can return the plan to
`proposed` for explicit restart.

Cycles, unknown dependencies, expanded scopes, capability escalation, budget
overruns, stale writers, invalid phase transitions, and premature completion
fail closed. The mutable plan row stores only the current revision pointer and
phase; it cannot erase revision history.

## CLI

```powershell
python -m acr_runtime.cli --db .acr/acr.db plans create `
  examples/planner/architecture-plan.json
python -m acr_runtime.cli --db .acr/acr.db plans inspect <PLAN_ID>
python -m acr_runtime.cli --db .acr/acr.db plans refine `
  <PLAN_ID> <NODE_ID> children.json --expected-revision 1 `
  --reason "New evidence justifies deeper decomposition"
python -m acr_runtime.cli --db .acr/acr.db plans revise `
  <PLAN_ID> snapshot.json --expected-revision 2 `
  --reason "A verified prerequisite changed"
python -m acr_runtime.cli --db .acr/acr.db plans transition `
  <PLAN_ID> executing --expected-revision 3 --reason "Approved start"
python -m acr_runtime.cli --db .acr/acr.db plans history <PLAN_ID>
```
