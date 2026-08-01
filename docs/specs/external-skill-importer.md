# Prompt 109: controlled external skill importer

ACR imports one local [Agent Skills](https://agentskills.io/specification)
directory at a time. OpenAI also identifies Agent Skills as the open format
used by its skills, so ACR does not invent separate Codex, Claude, GitHub, or
marketplace schemas.

An external source is untrusted input. Its origin label records provenance
only; it never changes validation policy, reliability, lifecycle, or
permissions.

## Closed pipeline

`acr skills import-external` performs these gates in order:

1. resolve one local directory or `SKILL.md`;
2. reject symlinks, path escapes, more than 128 files, or more than 1 MB;
3. parse a strict subset of Agent Skills YAML frontmatter without object tags,
   anchors, aliases, multiline values, or implicit YAML types;
4. scan all source text for secrets, authority overrides, policy redefinition,
   covert action, active content, and dangerous executable patterns;
5. map every declared tool to an exact ACR tool and permission;
6. deny unknown tools and any permission outside the read-only import policy;
7. identify explicit `metadata.acr-dependencies` entries without downloading or
   installing them, and require matching active ACR skills;
8. normalize instructions and bounded `scripts/`, `references/`, and `assets/`
   into ACR Skill Format v1;
9. run a fixed sandbox boundary check, generated package smoke test, and the
   fixed adversarial boundary probes;
10. admit the immutable package to the registry as `quarantined`.

If a required sandbox is unavailable or any stage does not pass, no registry
record is created. A newly created normalized package is removed on sandbox
failure. Imported scripts are copied but are not selected as verification
commands and are never run by the importer.

The default permission map accepts only:

| External declaration | ACR tool | Permission |
| --- | --- | --- |
| `Read` | `filesystem.read` | `filesystem:read` |
| `Glob` | `filesystem.glob` | `filesystem:read` |
| `Grep` | `filesystem.search` | `filesystem:read` |

`Write`, `Edit`, `Bash(...)`, `WebFetch`, `WebSearch`, and MCP declarations map
to write, shell, network, or external-MCP permissions and fail until an
explicit future policy surface grants them. Unmapped names always fail closed.

## Operator command

The importer never clones a repository, fetches a URL, pulls an image, installs
a dependency, trusts a publisher, or activates a skill. The operator must first
place the source and sandbox image locally:

```powershell
python -m acr_runtime.cli --db .acr/acr.db skills import-external `
  C:\path\to\agent-skill `
  --source-label github `
  --docker-sandbox `
  --sandbox-image python:3.11-slim
```

Docker uses the existing locked-down, networkless, read-only sandbox with
`--pull never`. Successful import still ends in quarantine. Activation remains
a separate governed lifecycle transition after the complete retained
certification pipeline.

## Supported source fields

The parser accepts the Agent Skills fields `name`, `description`, `license`,
`compatibility`, `metadata`, and experimental `allowed-tools`. Metadata is
restricted to one level of string values. ACR additionally recognizes the
optional `acr-dependencies` metadata key as a comma-separated list of exact
`skill-id@MAJOR.MINOR.PATCH` references.

This deliberately narrow adapter follows the current public standard while
keeping unsupported source claims visible as errors instead of silently
broadening behavior.
