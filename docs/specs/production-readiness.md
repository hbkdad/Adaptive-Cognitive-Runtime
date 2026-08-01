# Prompt 106: production readiness

## Target and scoring

Prompt 106 evaluates commit `6582164` for a `networked_production` profile. It
does not evaluate the narrower loopback-only development profile and does not
change deployment, release, permission, or runtime state.

Every required dimension has four cumulative evidence levels:

| Score | Required contiguous evidence |
| --- | --- |
| 0 | No verified specification |
| 1 | Specified |
| 2 | Specified and deterministic |
| 3 | Specified, deterministic, and rehearsed |
| 4 | Specified, deterministic, rehearsed, and production-observed |

Evidence cannot skip a level. Unavailable evidence has no reference. Any score
below four requires explicit deficiencies and a recommendation. The readiness
result and blockers are derived; the report cannot supply them.

For the networked production target, all fourteen dimensions must score four.
Correctness, security, reliability, backup, migration, rollback, data privacy,
provider failures, and rate limiting are additionally marked critical so their
incomplete state is explicit in the blocker list.

## Current result

| Dimension | Score | Highest evidence | Critical |
| --- | ---: | --- | --- |
| Correctness | 3 | Local rehearsal | yes |
| Security | 2 | Deterministic | yes |
| Reliability | 2 | Deterministic | yes |
| Observability | 3 | Local rehearsal | no |
| Performance | 2 | Deterministic | no |
| Backup | 3 | Local rehearsal | yes |
| Migration | 3 | Local rehearsal | yes |
| Rollback | 2 | Deterministic | yes |
| Data privacy | 2 | Deterministic | yes |
| Provider failures | 2 | Deterministic | yes |
| Rate limiting | 1 | Specified | yes |
| Cost controls | 2 | Deterministic | no |
| Human override | 2 | Deterministic | no |
| Documentation | 3 | Local rehearsal | no |

The derived score is **32/56 (57.14%)**. ACR is **not production-ready** for
networked deployment.

The complete evidence, deficiencies, and recommendations are retained in
`docs/audits/prompt-106-readiness.json`. Validate them with:

```powershell
python -m acr_runtime.production_readiness `
  docs/audits/prompt-106-readiness.json
```

Exit code `0` means all dimensions reached four, `1` means a valid but
not-ready assessment, and `2` means the report is invalid.

## Operational evidence

The complete deterministic gate passed 733 tests across all six tiers. The
architecture guard scanned 110 modules and 1,278 imports with zero violations,
and all 105 deterministic test files are uniquely classified.

The Prompt 106 rehearsal created and verified a schema-63 backup containing 19
entries, confirmed `quick_check=ok` with no secret values, and restored it to a
fresh inactive target. The live doctor check passed Python, filesystem,
database, migration, FTS5, Ollama, and skill-directory checks.

Live telemetry contains six execution runs, five completed and one failed, 56
events, and five calls to `qwen2.5-coder:1.5b`. Cost accounting correctly
reports incomplete monetary coverage because the local model has no enabled
local-cost profile. These observations support local rehearsal only, not a
production claim.

## Highest-priority gaps

1. Keep the API loopback-only. Rate limiting, authentication, TLS,
   origin/CSRF controls, and an independent security assessment are required
   before network exposure.
2. Define SLIs/SLOs and run concurrency, restart, soak, provider-outage, and
   operator incident rehearsals.
3. Establish immutable releases and rehearse application rollback separately
   from database restore.
4. Define the deployment data inventory, privacy obligations, RPO/RTO, backup
   retention, alerting, and operator ownership.
5. Keep paid providers disabled until price coverage, hard budgets, billing
   reconciliation, and alerts are verified.

Green deterministic tests remain necessary but cannot substitute for
deployment-specific rehearsal or production observations.
