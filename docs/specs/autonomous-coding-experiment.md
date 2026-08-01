# Prompt 111: controlled autonomous coding experiment

The coding experiment accepts one bounded local snapshot of an open Git issue
and may return one reviewable patch. It never fetches the issue, edits the
authoritative repository, stages files, commits, pushes, opens a pull request,
publishes a release, or deploys.

`patch_ready` means only that every configured adapter gate reported success,
the controller validated scope and content, and read-only `git apply --check`
accepted the patch against the exact clean baseline. It is not implementation
approval or evidence that adapter claims are independently verified.

## Input boundary

The strict request contains:

- one `owner/repository#number` issue snapshot with bounded title and body;
- one local Git top-level directory;
- the exact current 40-character baseline commit;
- 1 to 32 bounded repository-relative path scopes;
- `maximum_iterations` from 1 through 8;
- benchmark mode `required` or `not_relevant` plus a reason;
- literal `false` values for production deployment and scope expansion.

The issue body is untrusted. Secret material, authority overrides, policy
redefinition, identity override, covert action, active content, and invisible
instruction characters fail before adapter execution. Reports retain only the
issue reference and hash, not the title or body.

The authoritative repository must be clean and at the named baseline. The
controller snapshots HEAD and porcelain status before and after every adapter
call. Any mutation stops the experiment and no patch artifact is emitted.

## Execution boundary

The default `UnavailableCodingAdapter` returns a blocked run. Execution requires
an adapter injected by a host. Its identity must declare:

```json
{
  "available": true,
  "isolation": "disposable-worktree",
  "network": "none",
  "deployment": "forbidden",
  "mutation_target": "patch-only"
}
```

These fields are necessary contract claims, not proof of isolation. The
controller can detect authoritative-repository mutation but cannot prove that
an arbitrary in-process adapter avoided network access or an external
deployment. A production adapter therefore remains unavailable until a host
binds independently verified disposable-worktree isolation and trusted
receipts. Safe Mode blocks experiment execution through the existing
`shell_write` containment action.

The adapter receives issue content and repository identity but not the local
authoritative repository path. Each iteration returns:

- understanding and bounded-plan evidence;
- planned paths;
- one Git unified patch;
- test, review, security-review, and benchmark evidence.

Adapter gate evidence is retained as `adapter_asserted`. Patch hashes, path
validation, repository-integrity checks, and Git applicability are
`runtime_validated`.

## Ordered loop

Every retained iteration has exactly:

1. `understand`;
2. `plan`;
3. `implement`;
4. `test`;
5. `review`;
6. `security_review`;
7. `benchmark`;
8. `produce_patch`.

An iteration is accepted only when understanding, planning, tests, review, and
security review pass. A required benchmark must pass. A benchmark marked
`not_relevant` is accepted only when the request explicitly chose that mode.
The loop stops at the first accepted patch or the configured iteration limit.

Failures retain stage outcomes, bounded summaries, evidence references, path
names, and patch hashes. They do not retain patch bodies, issue bodies, command
output, repository file content, secrets, or model prompts.

## Patch gate

The controller accepts at most 1 MB and 64 paths. It requires ordinary Git
unified diff headers and contextual hunks. It rejects:

- absolute paths, traversal, drive-qualified paths, and `.git` paths;
- changes outside `allowed_paths` or outside the adapter plan;
- mismatched file markers;
- rename, copy, submodule, and binary patches;
- embedded secret material;
- patches that fail `git apply --check --whitespace=error-all`.

Git documents that [`git apply --check`](https://git-scm.com/docs/git-apply)
checks applicability without applying the patch, and its
[diff format](https://git-scm.com/docs/diff-format.html) provides the
`diff --git a/path b/path` headers used for the closed path parser.

On success, the controller writes one immutable `<run-id>.patch` and one
content-minimized `<run-id>.json` under the configured ACR state directory.
The repository remains unchanged.

## CLI

```powershell
python -m acr_runtime.cli --db .acr/acr.db coding run `
  examples/coding-experiment/request.json
python -m acr_runtime.cli --db .acr/acr.db coding report <RUN_ID>
```

With the default runtime, `coding run` intentionally records
`trusted_execution_adapter_required`. Library hosts may inject an adapter for a
bounded experiment; the CLI does not offer a flag that loads arbitrary adapter
code.

## Deferred work

- a real disposable-worktree execution adapter;
- independently verifiable test, review, security, and benchmark receipts;
- resource budgets for model and tool execution;
- issue acquisition and authentication;
- patch application, commit, push, pull request, release, or deployment.

Each is a separate capability and authority decision. In particular, producing
a patch never authorizes applying it.
