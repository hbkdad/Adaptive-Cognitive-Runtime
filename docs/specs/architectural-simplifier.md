# Prompt 102: architectural simplifier

## Decision

Prompt 102 performs a conservative, evidence-bound complexity audit. It does
not treat low static reference counts as deletion authority. The verified
simplification in this checkpoint consolidates repeated private, secret-safe
bounded text validation while preserving every public interface and runtime
extension point.

## Audit

| Category | Evidence | Decision |
| --- | --- | --- |
| Duplicated services | Seven governance modules contained equivalent private bounded-text validation; five also duplicated the same bounded-list validation. | Consolidate the common behavior in `acr_runtime.bounded_validation`. |
| Redundant abstractions | No abstraction had verified replacement and removal evidence. | Retain. |
| Unused interfaces | Static searches found low-reference protocols, API models, and CLI surfaces, but these are public or dynamically reachable. | Review candidates only; do not remove. |
| Overlapping skills | The live database contains one quarantined skill and no competing active implementations. | No simplification justified. |
| Unnecessary agents | Agent specifications are bounded role templates, not continuously running agents. | No simplification justified. |
| Obsolete configuration | No configuration was proven unreachable in supported startup paths. | Retain pending runtime evidence. |
| Dead code | Static non-import evidence cannot distinguish dead code from entry-point, plugin, CLI, or external API use. | Require runtime or compatibility evidence before removal. |
| Over-engineered infrastructure | No subsystem had a measured maintenance or token cost with a verified simpler replacement. | No architectural removal justified. |

## Change and measured cost

Before this change, seven modules independently implemented equivalent
secret-safe bounded text parsing. Five of those modules also independently
implemented the same bounded list parsing. The shared module retains the
existing validation order, whitespace normalization, secret rejection,
length/count bounds, and duplicate rejection.

Across production Python files, the consolidation removes 123 lines, adds 23
import/alias lines, and adds a 35-line shared implementation: a net reduction
of 65 lines. It also reduces the number of implementations that must receive a
future security or validation correction from seven to one for text and from
five to one for the identical list contract.

The research-scout list parser remains local because it permits 1,000-character
items. The security-review list parser remains local because its default
minimum is zero. Both reuse only the exactly equivalent text primitive.

## Compatibility and gates

- No database schema or migration changes.
- No CLI, API, model, skill, agent, or provider interface changes.
- Direct tests cover normalization, bounds, list cardinality, duplicate
  rejection, and secret rejection.
- Existing governance-module tests remain the compatibility oracle.
- The architecture guard and deterministic six-tier suite remain mandatory.

## Deferred candidates

Static duplicate and low-reference scans may identify future review candidates,
but removal requires a supported-use inventory, runtime evidence where
available, a compatibility decision, and focused regression tests. Complexity
must justify itself, but absence from direct imports is not proof of absence
from supported use.
