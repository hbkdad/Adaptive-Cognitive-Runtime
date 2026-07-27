# Prompt 29: bounded self-reflection

## Boundary

Reflection is a single, deterministic, post-task analysis pass. It consumes
structured evidence and emits exactly nine structured findings:

1. what worked;
2. what failed;
3. unnecessary context;
4. missing information;
5. memory impact;
6. skill impact;
7. model economy;
8. tool economy;
9. reusable experience.

It does not call a model or tool, retry the task, create memory, distill an
experience, alter utility, or invoke itself. Prompt 30 will own any transactional
post-task learning workflow.

## Evidence contract

Context observations use the existing attribution vocabulary:
`contributed`, `ignored`, `misled`, or `uncertain`. A non-uncertain attribution
requires an evidence reference. Tool necessity likewise requires evidence
unless it is explicitly `uncertain`.

A cheaper model is reported only when retained input states that it:

- costs less than the current model;
- is capability-compatible;
- passed the relevant benchmark; and
- has evidence references.

A reusable-experience candidate requires evidence, significance of at least
`0.7`, and novelty of at least `0.5`. These are candidate findings only; no
learning write follows.

## Hard limits

- input observations: at most 128;
- evidence references per observation or finding: at most 8;
- evidence and identifier strings: at most 500 characters;
- findings: exactly the nine required categories;
- output estimate: 256–4,000 configured tokens;
- reflection passes: exactly one;
- input `reflection_depth`: exactly zero; retained output depth: exactly one.

The engine fails closed when a budget would be exceeded. It never truncates a
question, starts a second pass, or produces an unbounded essay.

## Persistence

Schema v25 retains one reflection run and nine ordered findings. Findings contain
only category, verdict, subject IDs, evidence references, and bounded numeric or
boolean metrics. Input metadata stores counts, total context tokens, and a task
ID hash; it does not retain an additional free-form task narrative.

## CLI

```powershell
python -m acr_runtime.cli --db .acr/acr.db reflect run reflection-request.json
python -m acr_runtime.cli --db .acr/acr.db reflect report <RUN_ID>
```
