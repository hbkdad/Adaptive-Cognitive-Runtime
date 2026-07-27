# Prompt 22: advisory skill merger

The skill merger is a bounded comparison and recommendation system. It does not
edit packages, change lifecycle states, create composed skills, or retire
anything.

## Analysis dimensions

Every retained pair contains:

- semantic overlap from an explicitly configured trusted adapter;
- a clearly labeled lexical proxy that is never substituted for unavailable
  semantic evidence;
- task-class overlap;
- deterministic procedure similarity over ordered instruction terms and token
  overlap;
- dependency-set similarity and the exact dependency lists;
- performance history including uses, success rate, reliability, tokens, cost,
  latency, and conservative dominance evidence;
- both package hashes, versions, manifest lineages, and lifecycle states.

Analysis is capped at 100 skills. Retired skills are excluded. A run may compare
the bounded library or one requested skill against the bounded remainder.

## Recommendations

The retained recommendation vocabulary is:

- `KEEP_SEPARATE`: evidence is missing, weak, conflicting, or insufficient;
- `MERGE`: separate lineages have high overlap across semantic, task,
  procedure, and dependency dimensions;
- `DEPRECATE_ONE`: high-overlap skills have sufficient performance history and
  one Pareto-dominates the other, or a matching older lineage has a validated
  active successor;
- `COMPOSE`: skills have related task/semantic scope but complementary
  procedures and dependencies.

Thresholds and minimum evidence counts are stored with each run. A semantic
adapter score must be in `[0, 1]`; invalid evidence aborts the run.

## Safety boundary

Every report requires human review. `automatic_action_allowed` is constrained
to zero in schema 18. Active involvement is explicit in every pair, and active
skills are never merged automatically. This component has no lifecycle mutation
method, so the same rule also protects quarantined skills during the initial
release.

```powershell
python -m acr_runtime.cli --db .acr/acr.db skills merge-analysis
python -m acr_runtime.cli --db .acr/acr.db skills merge-analysis `
  --skill sqlite-diagnostics --limit 25
python -m acr_runtime.cli --db .acr/acr.db skills merge-report <RUN_ID>
```

The default CLI has no semantic adapter and therefore fails conservatively
toward `KEEP_SEPARATE`, except for explicit same-lineage successor evidence.
Deployments can inject a trusted local semantic adapter through the Python API.
