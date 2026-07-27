# Prompt 16: ACR Skill Format v1

An ACR skill is a small, versioned capability package. It declares a narrow
task interface and verification boundary; it is not a replacement system prompt
and validation never executes its scripts.

## Required layout

```text
skill-id/
  SKILL.yaml
  instructions.md
  history.jsonl
  examples/
  tests/
  scripts/
  assets/
```

ACR v1 uses the JSON-compatible profile of YAML 1.2. A `SKILL.yaml` file is
therefore valid YAML and can be parsed by standard YAML tooling, while the local
runtime can validate it with the Python standard library and no implicit YAML
types. Unknown or missing fields fail closed.

## Manifest fields

Every manifest contains:

```text
id, name, version, description, task_classes, inputs, outputs,
dependencies, permissions, tools, models, token_estimate, applicability,
contraindications, verification, author, origin, created_at, updated_at,
status, reliability
```

`inputs` and `outputs` map stable names to type descriptions so skills can be
composed without interpreting prose. Dependencies use
`stable-skill-id@MAJOR.MINOR.PATCH`. Versions follow Semantic Versioning 2.0.0.
Timestamps must be timezone-aware ISO 8601 values. Reliability is bounded from
zero to one.

Valid statuses are `experimental`, `quarantined`, `active`, `deprecated`, and
`retired`. A manifest status is descriptive package metadata. Validation does
not grant trust or activate a package; registry admission and lifecycle
transitions belong to Prompt 17.

## Integrity and safety

The loader rejects symlinks, paths escaping the package root, malformed history
records, packages over 1 MB, and instructions over 4,000 estimated tokens. It
computes a deterministic SHA-256 digest from sorted relative file names and
individual file digests. This detects modification but is not a publisher
signature.

Verification entries are stored as exact commands and are not run by the format
loader. Scripts and assets are never executed during validation. Use:

```powershell
python -m acr_runtime.cli skills validate examples/skill-v1/sqlite-diagnostics
```

The design uses package origin metadata and deterministic content
identification concepts similar to SPDX while deferring signature trust and
registry policy to later prompts.
