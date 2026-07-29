# Prompt 78: human override

Prompt 78 adds recorded operator controls for:

- pinning or blocking a memory;
- forcing a model or skill;
- disabling a skill;
- limiting agent count;
- disabling learning;
- freezing architecture changes; and
- rolling back a version.

Override definitions are immutable. Activation, application, failure, and
revocation are append-only events, so an operator action cannot be silently
rewritten or removed. Every request requires an actor, reason, exact scope, a
closed action, and an action-specific value shape. Secret-like material is
rejected before persistence.

## Authority and safety

A human override narrows or selects within existing safety constraints; it does
not waive them. Forced models must remain registered, active, capable, and
qualified by retained evidence. Forced skills must remain active, have all
dependencies available, and fit the skill-count and token budgets. Agent
limits can only reduce the caller's limit. Blocking archives memory rather than
deleting it. Skill disabling quarantines the exact immutable skill version.

Architecture freeze and learning disable fail closed at their public service
entry points. Durable overrides can be revoked, but revocation does not
silently reverse an already-applied domain transition such as an archive or
quarantine. An operator must explicitly restore that domain object.

Version rollback supports existing verified rollback mechanisms:

- an exact promoted skill-evolution run; or
- an improvement-policy head with compare-and-swap on the expected head ID.

Arbitrary Git, database, package, or deployment rollback is intentionally not
invented by this control plane.

## CLI

```powershell
python -m acr_runtime.cli --db .acr/acr.db overrides apply override.json
python -m acr_runtime.cli --db .acr/acr.db overrides list --active
python -m acr_runtime.cli --db .acr/acr.db overrides show OVERRIDE_ID
python -m acr_runtime.cli --db .acr/acr.db overrides revoke OVERRIDE_ID `
  --actor operator:miche --reason "Return control to runtime policy."
```

Example:

```json
{
  "action": "limit_agents",
  "scope": "research",
  "target_id": null,
  "value": {"max_agents": 2},
  "actor_id": "operator:miche",
  "reason": "Bound current research fan-out."
}
```
