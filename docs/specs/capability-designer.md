# Prompt 101: capability and implementation-prompt designer

## Boundary

Prompt 101 is a stateless, deterministic classification and specification
boundary. It accepts one strict request, chooses the smallest sufficient
capability form, and only then renders an implementation prompt. It never
executes that prompt, creates an agent, grants permissions, changes runtime
state, or authorizes implementation.

The closed classifications are:

- `deterministic_code`
- `tool`
- `skill`
- `agent`
- `memory_strategy`
- `context_strategy`
- `model_routing_rule`
- `workflow`
- `new_subsystem`

## Classification

One primary boundary trait selects a tool, memory strategy, context strategy,
model-routing rule, or new subsystem. Requests containing more than one such
boundary fail closed and must be decomposed.

An agent requires all three of `delegated_goal`, `adaptive_planning`, and
`multi_step_orchestration`, plus at least two explicit reasons why simpler
forms are insufficient. Without that evidence, orchestration remains a fixed
workflow. A reusable procedure over existing boundaries is a skill; bounded
computation defaults to deterministic code.

This deliberately prevents “agent” from becoming the default answer.

## Specification and prompt generation

Every request supplies the complete Prompt 101 specification surface:
objective, inputs, outputs, interfaces, dependencies, permissions, data model,
failure modes, tests, benchmark, telemetry, security, and rollout strategy.
Unknown or missing fields, secrets, invalid references, duplicate items, and
oversized text are rejected.

The design ID is the SHA-256 of canonical request JSON. Safe requests receive a
deterministic prompt with the specification encoded as authority-free JSON.
Suspicious instruction patterns remain classified for review but prompt
generation stops. Every result states that automatic execution, agent
creation, implementation, permission expansion, deployment, and activation are
not authorized.

## CLI

```powershell
python -m acr_runtime.cli design capability `
  examples/capability-design/tool-request.json
```

The command reads local JSON and writes no database state.
