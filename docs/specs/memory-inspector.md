# Memory inspector

Prompt 50 replaces the generic Memory table with an exact-scope browser that
shows what ACR believes, why it believes it, and how that belief changed.

## Read boundary

The read API is under `/memory-inspector/v1`:

- `GET /search` supports bounded FTS, type, status, lifecycle, confidence,
  utility, subject, exact scope, and cursor filters.
- `GET /{memory_id}` returns one visible record in the requested exact scope.
- `GET /timeline` returns the visible chronological history for one exact
  subject and scope.
- `GET /related` returns only records with the same exact subject and scope.

All filters are applied in SQLite before the 1–100 item limit. Public and
internal records are visible; personal, confidential, secret, and deleted
records are indistinguishable from absent. Archived records remain inspectable.
Secret-like text and local paths are redacted from evidence and provenance, and
links to non-visible supersession records are suppressed.

The UI labels use history as `aggregate_only` because ACR currently retains
access and outcome counters, not individual memory-use events. Related records
are exact-subject history, not an inferred semantic graph.

## Governed actions

Pin, archive, restore, correction, and deletion require all of:

1. `ACR_API_TOKEN` configured on the API and supplied as `X-ACR-Token`;
2. `ACR_API_OPERATOR_ID` bound by the server to an agent subject;
3. an active exact `memory.write` capability for `memory:<scope>`;
4. the `updated_at` version currently displayed by the operator.

The token is held only in React component memory and is not written to browser
storage. Capability decisions provide the authorization audit. Lifecycle
changes retain their timestamps and pin reason. Corrections never edit content
in place: they create a confirmed version that supersedes the exact prior
version and retain only a hash of the operator's reason. Deletion uses the
existing plan/approve privacy pathway and requires typing the complete memory
ID before verified erasure. Backup cleanup remains a separate operator duty.

Pinned records cannot be archived through this UI, and no force option is
exposed.

## Local setup

Read-only inspection works on the default loopback server:

```powershell
python -m acr_runtime.cli --db .acr/demo.db serve --port 8011
cd apps/control-center
npm run dev -- --port 4173
```

To enable actions, set a strong session token and a bounded operator ID before
starting the API:

```powershell
$env:ACR_API_TOKEN = "<strong-random-session-token>"
$env:ACR_API_OPERATOR_ID = "operator-ui"
python -m acr_runtime.cli --db .acr/acr.db serve --port 8011
```

The trusted workflow must separately grant agent `operator-ui` capability
`memory.write` with resource scope `memory:<exact-scope>`, a short expiry,
`delegable: false`, a reason, and evidence. The existing
`capabilities grant <request.json>` command performs that grant. A token alone
never creates memory authority.

## Verification

Backend tests cover scope isolation, pre-limit classification filtering,
redaction, pagination, optimistic conflicts, default-deny authorization,
history-preserving correction, typed delete confirmation, and verified
erasure. Frontend tests cover real evidence rendering and action visibility.
The interface is browser-checked at 1440 by 900 and 390 by 844.

## Research basis

- OWASP recommends deny-by-default authorization and validating permission on
  every request:
  <https://cheatsheetseries.owasp.org/cheatsheets/Authorization_Cheat_Sheet.html>
- W3C documents `role="status"` for announcing dynamic operation results
  without moving keyboard focus:
  <https://www.w3.org/WAI/WCAG21/Techniques/aria/ARIA22>
