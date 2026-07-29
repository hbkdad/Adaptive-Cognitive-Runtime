# Architecture-review agent workflow

Use this workflow for one proposed subsystem or structural change. The review
complements the executable architecture guard; it does not replace that guard,
inspect a repository by itself, execute code, or authorize changes.

## Review sequence

1. Define the exact change, responsibilities, public interfaces, owned data,
   dependencies, provider boundary, tests, failure behavior, and expected
   replacement seams.
2. Review all eight dimensions in order: `cohesion`, `coupling`, `interfaces`,
   `data_ownership`, `testability`, `failure_modes`,
   `provider_independence`, and `future_replacement`.
3. Classify each dimension as `sound`, `concern`, or `unverified`. Every concern
   needs severity, evidence status, bounded evidence, and a multi-step path from
   the design choice to maintenance cost, failure, lock-in, or unsafe ownership.
4. Inventory new interfaces, factories, adapters, wrappers, base classes,
   indirection layers, and extension points. Mark an abstraction `needless` only
   when verified evidence shows its complexity cost and a simpler multi-step
   removal path. Mark uncertain future value `uncertain`, not needless.
5. Run the existing AST boundary check separately, then validate this report:

   ```powershell
   python -m acr_runtime.architecture_guard check
   python -m acr_runtime.architecture_review validate .\review.json
   ```

The review validator exits `0` for pass, `1` for reject, and `2` for invalid
input. The verdict is derived. Verified high or critical dimension concerns
reject, as does every verified needless abstraction. Supported or speculative
concerns remain visible without rejecting the change.

## Review principles

A cohesive module owns one design responsibility and hides decisions likely to
change. Coupling is justified at explicit interfaces, not through shared
storage internals or concrete providers. Data has one authoritative owner.
Tests can substitute boundary dependencies and exercise failure modes. Provider
adapters remain outside the domain. Replacement evidence must describe an
actual volatile dependency or current use—not only a hypothetical future.

Do not add an abstraction merely to satisfy a pattern name. Equally, do not
remove a boundary that currently protects ownership, policy, testing, or
provider replacement. The rejection rule requires evidence in either direction.

## Runtime role template

`examples/agent-spec/architecture-review-worker.json` is a valid Prompt 24 role
definition, not an executable worker. It declares no tools, skills,
permissions, peers, paid-model budget, or fallback. The template itself cannot
inspect code, execute checks, change architecture, or write memory.

## Basis

- [Parnas, “On the Criteria To Be Used in Decomposing Systems into Modules”](https://citeseerx.ist.psu.edu/document?doi=5d752e29e29b42cc509417699a98d9dca8212c83&repid=rep1&type=pdf)
  frames modules as responsibility assignments that hide changeable design
  decisions and support independent understanding and replacement.
- [Python `typing.Protocol`](https://docs.python.org/3/library/typing.html#typing.Protocol)
  provides explicit structural interface contracts without concrete
  inheritance coupling.
- [Martin Fowler, YAGNI](https://martinfowler.com/bliki/Yagni.html) explains the
  lifecycle costs of speculative features and future-flexibility abstractions.
