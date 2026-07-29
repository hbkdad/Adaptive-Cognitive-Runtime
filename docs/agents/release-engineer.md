# Release-engineer workflow

Use this workflow for one exact version and commit. Building release evidence
does not authorize a tag, push, package upload, or GitHub release.

## Ordered release gate

The canonical manifest gate IDs are `tests`, `migrations_test_db`,
`security_scans`, `benchmark_subset`, `cli`, `api`, `clean_install`, `upgrade`,
and `changelog`.

1. Freeze the exact 40-character commit SHA and three-part release version.
   Require an empty worktree and synchronized release branch.
2. Run the deterministic test gate.
3. Run all migrations against a disposable test database, including a
   fixture-based upgrade and rollback/failure coverage.
4. Run staged/repository secret checks, architecture boundaries, and the
   security tier.
5. Run the deterministic benchmark subset.
6. Verify the installed `acr` CLI contract and representative read-only
   command.
7. Verify API schema, health, auth/default-deny, and representative endpoints
   using the API test dependency set.
8. Build wheel and source distribution, create a fresh isolated environment,
   install the wheel (not the source tree), and run smoke checks.
9. Install the prior immutable release in a disposable environment and
   database, upgrade to the candidate artifact, run `acr migrate`, and verify
   data/schema integrity. Downgrade remains backup restore, never reverse DDL.
10. Move user-visible changes from `Unreleased` into a versioned
    `CHANGELOG.md` section and verify it matches the release version.
11. Record each gate's command, exit code, run reference, output-artifact hash,
    completion time, and bounded evidence in the strict manifest. Evidence must
    be no older than 24 hours:

    ```powershell
    python -m acr_runtime.release_engineering validate .\release.json
    ```

The validator exits `0` only when all nine gates pass, the version tag is
absent, and GitHub immutable releases are enabled. Tag absence and immutability
each require a bounded evidence reference. It exits `1` for a valid but blocked
manifest and `2` for invalid input. It never executes a gate or creates a tag.

## Immutable publication

After a fresh ready manifest, obtain explicit human release approval bound to
its `review_hash`, version, tag, and commit. Create an annotated or signed tag
without `--force`; Git refuses an existing name by default. Prepare a draft
GitHub release, attach all final artifacts and checksums, and publish only when
complete. GitHub release immutability must be enabled so the published tag and
assets cannot be moved or replaced. Never reuse a released version or tag.

This repository does not create or push a release tag while implementing this
workflow. Tag and release publication are separate externally visible actions.

## Runtime role template

`examples/agent-spec/release-engineer-worker.json` is a valid Prompt 24 role
definition, not an executable worker. It has no tools, permissions, peers,
fallback, or paid budget. A release host must separately bind exact commands
and read/write grants for a disposable environment. Tag and release permissions
remain absent until explicit final approval.

## Basis

- [Python Packaging User Guide: packaging flow](https://packaging.python.org/en/latest/flow/)
  defines source/wheel builds and end-user installation of built artifacts.
- [Git `tag`](https://git-scm.com/docs/git-tag.html) recommends annotated tags
  for releases and refuses existing tag names unless force is explicitly used.
- [GitHub immutable releases](https://docs.github.com/en/code-security/concepts/supply-chain-security/immutable-releases)
  lock the published tag and assets and generate release attestations.
- [SQLite transactions](https://www.sqlite.org/lang_transaction.html) and the
  repository migration policy define transactional upgrade evidence.
