# Prompt 19: skill generator

The skill generator creates reviewable Skill Format v1 candidates from repeated,
successful experience. It does not use a model, execute generated content, or
activate a skill.

## Trigger contract

A pattern must occur in at least three distinct successful traces above the
significance threshold. Whitespace and case are normalized, but the default
detector deliberately does not infer broad semantic similarity. Structured
event metadata distinguishes:

- repeated successful procedures;
- repeated reasoning patterns whose recorded token cost is expensive;
- repeated tool sequences;
- repeated human instructions that solved the same task class.

One event maps to one trigger using the order human instruction, expensive
reasoning, tool sequence, then successful procedure. This prevents the same
evidence from manufacturing several candidates.

## Candidate contents

Every candidate persists its trigger, task class, scope, occurrence count,
average significance, trace IDs, and evidence references. The generated package
contains applicability boundaries, typed inputs and outputs, the observed
procedure, verification criteria, known failure modes, declared permissions and
tools, a declarative scenario-test file, and an immutable history entry.

Missing structured fields receive narrow defaults. Failure events from the same
task class become known failure modes. Content matching the existing
prompt-injection, exfiltration, or active-content risk patterns is not generated.
Permissions are declarations only and grant no authority.

## Governance

`acr skills generate --dry-run [--scope SCOPE]` saves a plan without writing a
package. `--approve RUN_ID` writes each package under the configured local skills
directory, validates the complete v1 package, and admits it through the registry.

The manifest starts at version `0.1.0`, status `experimental`, and reliability
below `0.5`. Registry admission independently forces the package to
`quarantined`; activation still requires the later validation and operator
boundaries. All generation plans, candidates, evidence, package paths, errors,
and admitted skill IDs are retained in schema 15.
