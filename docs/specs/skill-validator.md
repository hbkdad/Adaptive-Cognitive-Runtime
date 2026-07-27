# Prompt 20: mandatory skill validator

Skill activation now requires a retained, digest-bound validation run. Registry
format testing alone is intentionally insufficient.

## Ordered pipeline

Every run retains exactly ten ordered results:

1. syntax and Skill Format v1 validation;
2. exact active-dependency validation;
3. static security scan;
4. permission analysis against an explicit allowlist;
5. sandbox smoke execution;
6. unit tests;
7. scenario tests;
8. adversarial tests;
9. evaluator review;
10. candidate-versus-incumbent benchmark comparison.

A non-passing stage blocks every later stage, but all ten records are still
written. Outcomes are `passed`, `failed`, `blocked`, or `error`; bounded details,
scores, token/cost/latency measures, policy, package digest, and incumbent ID are
retained in schema 16.

The schema-16 migration quarantines previously active skills. They must pass the
same pipeline before returning to active routing; legacy trust is not silently
grandfathered.

## Fail-closed adapters

The default sandbox, evaluator, and benchmark adapters return `blocked`. A Python
subprocess or virtual environment is not treated as a security sandbox.
Deployments must provide evidence-producing adapters for all three boundaries.

An optional Docker sandbox adapter is available for runnable stages. Prompt 38
strengthens it into a complete generated-skill boundary: immutable local image
resolution, no network, no writable host mounts, read-only package/root,
non-root execution, dropped capabilities, built-in seccomp,
`no-new-privileges`, private namespaces, bounded resources and wall time,
empty/allowlisted environment, bounded tmpfs workspace, deterministic boundary
self-test, forced timeout cleanup, and retained content-minimized audit
evidence. It allowlists direct Python unit-test commands. Scenario and
adversarial stages remain blocked until a runnable task harness is supplied.
See [skill-sandbox.md](skill-sandbox.md).

## Promotion

Benchmark evidence must include candidate and incumbent quality and cost.
Candidate quality cannot regress, and cost cannot exceed the configured
regression allowance. Evaluator scores must meet policy. Any denied permission,
static finding, missing dependency, unavailable adapter, test failure, benchmark
regression, or package digest change prevents promotion.

`acr skills certify SKILL` records a default fail-closed run.
`--docker-sandbox --sandbox-image IMAGE` opts into the Docker adapter without
pulling an image. `acr skills validation RUN_ID` inspects a run, and
`acr skills promote RUN_ID` explicitly promotes only a fully passed run.
