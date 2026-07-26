# Contributing

## Development loop

1. Inspect the affected implementation and tests.
2. Make the smallest coherent change.
3. Add or update tests.
4. Run `python -m unittest discover -s tests -v`.
5. Run `python -m acr_runtime.cli doctor`.
6. Update architecture documentation or add an ADR when boundaries change.

Do not commit `.acr` runtime databases, credentials, virtual environments, or
generated caches. Avoid provider-specific behavior in core modules and avoid
introducing infrastructure without measurements demonstrating the need.

## Completion report

Each milestone should report files changed, tests run, results, architectural
decisions, technical debt, and the next highest-value step.

