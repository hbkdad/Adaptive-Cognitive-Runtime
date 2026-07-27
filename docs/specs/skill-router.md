# Prompt 18: task-to-skill router

The router is a metadata-first boundary between the governed registry and the
context compiler. It never executes skill code and only considers skills whose
registry and lifecycle states are both active.

## Selection contract

For a task, task class, and hard skill-token budget, the router returns:

- selected skills and numeric selection reasons;
- rejected alternatives and one bounded rejection reason;
- applicability, expected benefit, token overhead, reliability, historical
  success, overlap, and final score for every retrieved candidate.

Applicability combines metadata search rank with an exact task-class match.
Expected benefit combines applicability, declared reliability, and observed
success, preferring task-class-specific performance when available. Dependency
packages are resolved only to an exact active version.

The candidate set is deliberately bounded. All subsets of up to four skills are
evaluated exactly under the token and dependency constraints. The objective
subtracts token overhead, redundant metadata coverage, and an additional-skill
penalty. This selects the smallest useful set and rejects overlapping skills
unless their marginal value survives the overlap penalty.

## Outcome loop

Schema 14 stores the complete route alongside the compiler's final decision.
`router_selected` and `compiler_selected` are separate because the compiler may
still reject a routed skill while optimizing the complete context bundle.

Task completion maps conservative context attribution back to the selected
skill as `contributed`, `ignored`, `misled`, or `uncertain`. Rejected candidates
retain a null outcome. This keeps estimates and realized evidence distinguishable
for later evaluation.

Use `acr skills route TASK --task-class CLASS --budget N` to inspect a route
without creating a task. Use `acr telemetry routing` to inspect aggregate
outcomes by task class.
