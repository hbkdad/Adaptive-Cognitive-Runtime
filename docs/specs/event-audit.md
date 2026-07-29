# Immutable mutation audit

Prompt 84 adds a deliberately narrow audit projection for important autonomous
mutations. Authoritative subsystem tables still own application state. The
runtime does not reconstruct ordinary state from audit events.

## Event boundary

Schema 62 installs permanent SQLite `AFTER` triggers so each source mutation and
its audit event commit or roll back together:

| Event | Authoritative mutation |
| --- | --- |
| `MEMORY_CREATED` | insert into `memories` |
| `MEMORY_SUPERSEDED` | first change of `memories.superseded_by` |
| `SKILL_GENERATED` | candidate status changes to `generated` |
| `SKILL_PROMOTED` | skill history changes to `active` |
| `SKILL_RETIRED` | skill history changes to `retired` |
| `ROUTING_CHANGED` | skill-routing policy is promoted or rolled back |
| `AGENT_CREATED` | insert into `agent_specs` |
| `PERMISSION_DENIED` | denied capability decision is recorded |

There is no historical backfill. Events begin when schema 62 is installed.
Each event contains an increasing local sequence, random event ID, source
identity, timestamp, and a minimal JSON detail object. Memory content, skill
instructions, prompts, credentials, and other source payloads are excluded.

`audit_events` rejects updates and deletes. The source-row uniqueness constraint
also prevents duplicate emission for the same event kind. This is application
tamper evidence, not a substitute for protected storage, signed logs, or
off-device retention against a database administrator.

## Read-only viewer

The runtime exposes `runtime.audit` with bounded `list`, exact `get`, and
aggregate `summary` operations. The CLI provides:

```powershell
python -m acr_runtime.cli --db .acr/acr.db audit summary
python -m acr_runtime.cli --db .acr/acr.db audit list --event-type PERMISSION_DENIED --limit 50
python -m acr_runtime.cli --db .acr/acr.db audit show EVENT_ID
```

List filters are parameterized and limited to at most 1,000 rows. The viewer has
no write method.

## Design references

- [SQLite `CREATE TRIGGER`](https://www.sqlite.org/lang_createtrigger.html)
  defines persistent trigger and transaction behavior used by the projection.
- [OWASP Logging Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Logging_Cheat_Sheet.html)
  motivates security-relevant events, bounded event attributes, and excluding
  sensitive data.
- [NIST SP 800-92](https://csrc.nist.gov/pubs/sp/800/92/final) provides the
  operational basis for retaining and reviewing security event logs.
