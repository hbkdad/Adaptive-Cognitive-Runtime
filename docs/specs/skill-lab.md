# Prompt 51: Skill Lab

Prompt 51 replaces the generic Skills table with a bounded operator workbench
for exact, immutable skill versions. It exposes instructions, self-declared
origin and author, declared tests and permissions, retained validator evidence,
benchmark summaries, token cost, attributed success rate, lifecycle history,
and evolution history. Missing use evidence renders as unavailable rather than
as a fabricated zero-percent success rate.

## Comparison and visibility

Comparison accepts two exact references from one manifest family. The API
returns a bounded unified instruction diff plus all changed manifest fields
used by the workbench. The frontend labels every line as added, removed, or
context, renders all package text as inert React text, and states when the
800-line safety limit is reached. Automatically generated mutation and
comparison records remain visible; the API explicitly returns
`automatic_changes_hidden: false`.

Published package contents remain immutable. Changes require a new version,
following [Semantic Versioning 2.0.0](https://semver.org/).

## Governed actions

Reads use the existing API token boundary. Every write additionally requires:

1. `ACR_API_TOKEN` and `ACR_API_OPERATOR_ID` configured on the server;
2. the token in `X-ACR-Token`;
3. a fresh `Idempotency-Key`;
4. the current server-issued revision;
5. a bounded, secret-free reason where applicable; and
6. an active exact resource grant for the server-bound operator.

Lifecycle actions and rollback require capability `skill.activate` on
`skill:<skill-id>`. Rollback checks both the source and candidate versions.
Benchmark retention requires `database.write` on
`skill-benchmark:<manifest-id>`.

Activation also re-hashes the package and requires registry static validation
plus a fresh retained run in which all ten mandatory validation stages passed.
Retirement requires typing the exact `manifest@version` and is terminal.
Rollback is limited to a promoted evolution run and changes the source,
candidate, evolution, histories, rollback record, and idempotency receipt in
one `BEGIN IMMEDIATE` transaction.

The benchmark form analyzes caller-supplied, paired three-arm measurements. It
does not execute tasks. Recommendations are proposals only and cannot change a
skill lifecycle.

## Retention and privacy

Schema 38 stores content-minimized action receipts. It retains the operator ID,
one-use key, action, exact target, canonical request hash, optional reason hash,
status, bounded result, and timestamp. It does not retain API tokens or raw
reasons. Skill Lab projections exclude host package paths, bound history and
benchmark counts, omit raw benchmark trials, and cap instruction and diff
payloads with explicit truncation markers.

## Evidence basis

- OWASP recommends deny-by-default authorization and validating permission on
  every request and specific object:
  <https://cheatsheetseries.owasp.org/cheatsheets/Authorization_Cheat_Sheet.html>
- Semantic Versioning requires released version contents to remain immutable:
  <https://semver.org/>
- SQLite documents that `BEGIN IMMEDIATE` starts a write transaction
  immediately, which supports one atomic lifecycle decision:
  <https://www.sqlite.org/lang_transaction.html>
