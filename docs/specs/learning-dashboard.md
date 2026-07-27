# Prompt 52: Learning Dashboard

Prompt 52 adds a content-minimized audit feed at `/learning` and
`GET /learning-dashboard/v1/events`.

The feed does not imply that ACR runs a self-initiated autonomous improvement
loop. It distinguishes five governance states:

- `explicit_approval`: a planned change was applied through an approval method;
- `proposal_only`: retained advice that did not change production policy;
- `workflow_unattributed`: a workflow changed state but the initiating actor
  was not retained;
- `runtime_derived_advisory`: the runtime derived an advisory record from a
  verified reported outcome; and
- `automatic_within_requested_run`: a deterministic action occurred inside a
  compile or learning run that another caller requested.

## Authoritative event sources

| Dashboard category | Retained source | Truth boundary |
| --- | --- | --- |
| Memory promotions | `memory_consolidation_actions` | Applied only after explicit consolidation approval; proposals stay labelled proposals |
| Memory deletions | `memory_deletion_requests` | Planned and separately approved verified erasure; no memory identity or content is exposed |
| New skills | `skill_generation_candidates` | Generated only after approval and remains quarantined |
| Skill mutations | `skill_evolution_runs` | Immutable candidate/version state; initiating actor was not historically retained |
| Routing changes | `learning_routing_improvements` | Recommendations are proposal-only; no production routing policy changed |
| Topology discoveries | `agent_topology_recipes` | Automatically derived from a reported successful verified outcome and remains advisory |
| Context optimizations | `token_budget_plans` plus aggregate `context_uses` | Applied selection/compression facts inside a requested compile, not a benchmarked causal improvement |

The older `learning_events` chart counts ten pipeline stage rows per completed
learning run. The UI therefore labels it **Learning pipeline stages**, not
learning improvements.

## Privacy and audit contract

The API uses allowlisted projections only. It never returns memory IDs,
content, subjects, scopes, classifications, evidence, task objectives, local
paths, package paths, mutation instructions, operator identifiers, raw
reasons, or generic retained JSON. Deletion events expose only the erasure
requirement and verification booleans/counters.

Every event includes a namespaced stable ID, category, action, exact status,
governance state, actor-attribution quality, safe summary, timestamp, bounded
numeric or enum evidence, source table, reversibility statement, and any known
audit gap. `Cache-Control: no-store` is set on the API response, and the browser
also requests `no-store`; its token remains in React memory.

Results use descending keyset pagination with a versioned cursor bound to the
active category and governance filters. Limits are 1–100, unknown filters and
forged cursors fail closed, and an empty result is distinct from an unavailable
API.

## Accessibility

The interface uses a semantic ordered list of labelled articles, machine
readable timestamps, text alongside color for every status, keyboard-operated
filters and pagination, a polite status region, visible focus, reduced-motion
support, and a single-column 390 px layout without page-level horizontal
overflow.

## Evidence basis

- OWASP recommends recording event source and confidence while excluding or
  sanitizing secrets, personal data, paths, and other sensitive values:
  <https://cheatsheetseries.owasp.org/cheatsheets/Logging_Cheat_Sheet.html>
- NIST AI RMF identifies documentation as a way to improve transparency,
  review, and accountability and calls for limitations to be documented:
  <https://airc.nist.gov/airmf-resources/airmf/5-sec-core/>
- W3C documents `role=status` for politely announcing updated result counts
  without moving focus:
  <https://www.w3.org/WAI/WCAG21/Techniques/aria/ARIA22>
