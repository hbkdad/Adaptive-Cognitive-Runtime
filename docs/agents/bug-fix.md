# Bug-fix agent workflow

Use this workflow for bounded code debugging. A live safety incident may require
containment before reproduction; preserve evidence, enter the appropriate
incident or Safe Mode process, and return to this workflow in a safe
non-production environment.

## Evidence-first sequence

1. **Reproduce first.** State expected and actual behavior, then find the
   smallest deterministic command, request, or fixture that still fails. If the
   failure cannot be reproduced, report that limitation and gather more
   evidence; do not pretend to have confirmed a cause.
2. **Capture the exact error.** Retain the command, exit status, exception type,
   failing assertion, and bounded relevant output. Redact secrets and personal
   data before displaying or retaining evidence.
3. **Identify the smallest failing boundary.** Reduce the failure across
   interface, module, function, input, and environment boundaries. Stop when a
   smaller case no longer produces the same failure.
4. **Inspect recent changes.** Use focused `git log`, `git show`, `git diff`, and
   `git blame` against the failing surface. Use `git bisect` only with a reliable
   pass/fail command and in an isolated clean worktree; never rewrite the user's
   active dirty worktree.
5. **Form hypotheses.** Write a short ranked list grounded in the reproducer,
   system design, and recent changes. For each hypothesis, state an observable
   result that would support or refute it.
6. **Test hypotheses.** Run the cheapest safe discriminating test first. Record
   both positive and negative results. Change one meaningful variable at a
   time, and restore any temporary diagnostic change.
7. **Fix the root cause.** Make the minimum complete patch at the confirmed
   failing boundary. Do not mask symptoms, swallow exceptions, weaken tests, or
   make random edits until the error disappears.
8. **Add a regression test.** The test must reproduce the original failure
   mechanism and fail against the defective behavior. It must not merely assert
   an implementation detail introduced by the patch.
9. **Verify.** Run the reproducer, regression test, adjacent tests, broader
   relevant tier, architecture guard, and diff checks. Report what was and was
   not measured.

## Runtime role template

`examples/agent-spec/bug-fix-worker.json` is a valid Prompt 24 role definition,
not an executable worker. It deliberately declares no tools, skills,
permissions, peers, paid-model budget, or fallback. A concrete factory plan must
bind only the exact repository, test, and edit tools and grants justified for
one assigned defect before execution can exist. The template itself cannot
change code or write memory.

## Failure memory

After verification, record useful failure memory only when the task explicitly
authorizes ACR state changes and the evidence establishes the symptom, attempted
strategy, failed action, error type, root cause, resolution, avoidance rule, and
regression-test or run reference. Do not store raw logs, prompts, secrets,
personal data, source bodies, or an unresolved root-cause guess.

Use the governed `failure record` command documented in
`docs/integrations/codex.md`. A failed hypothesis is useful investigation
evidence but is not itself a confirmed failure-memory root cause.

## Basis

- [Google SRE: Effective Troubleshooting](https://sre.google/sre-book/effective-troubleshooting/)
  describes reproducible cases, boundary reduction, hypothesis testing,
  negative results, and corrective action.
- [Git `bisect` documentation](https://git-scm.com/docs/git-bisect) defines
  evidence-driven binary search between known good and bad revisions.
- [Python `unittest`](https://docs.python.org/3/library/unittest.html) provides
  the repository's deterministic regression-test foundation.
