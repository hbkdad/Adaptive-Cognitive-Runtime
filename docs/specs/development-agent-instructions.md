# Development agent instructions

Prompt 92 makes the repository's existing `AGENTS.md` contract explicit and
testable. It does not add a second autonomous agent or a runtime instruction
store.

For every feature change, the development agent must:

1. inspect the affected subsystem;
2. search for existing interfaces;
3. read adjacent tests;
4. identify architecture constraints;
5. implement the minimum complete change;
6. add or update focused tests;
7. run targeted tests and then broader relevant tests;
8. inspect the diff and staged secret scan;
9. update affected documentation; and
10. report measured results without fabricating unavailable metrics.

Unrelated refactors are outside the task. Existing user changes and architecture
remain preserved unless changing them is an explicit part of the requested
outcome.

The compact root contract is the authoritative instruction surface. Detailed
commands and rationale remain in `docs/integrations/codex.md` so the always
loaded file stays small.

## Design basis

The current [Codex `AGENTS.md` documentation](https://learn.chatgpt.com/docs/agent-configuration/agents-md)
defines repository instruction discovery and precedence. The
[Codex best-practices guide](https://learn.chatgpt.com/guides/best-practices)
recommends keeping durable repository guidance practical, documenting build and
test commands and constraints, and requiring tests, checks, and diff review.

The integration test fixes the required checklist vocabulary and ordering while
retaining the existing 4 KiB size ceiling. Mechanical test, architecture, and
secret checks remain executable gates rather than prose-only promises.
