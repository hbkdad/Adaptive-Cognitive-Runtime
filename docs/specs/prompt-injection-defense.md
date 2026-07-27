# Prompt 37: prompt-injection defense

ACR separates instruction authority from content usefulness. Every assessed
item has exactly one origin:

| Origin | Authority | Treatment |
| --- | --- | --- |
| `system_policy` | system | trusted instruction |
| `developer_instruction` | developer | trusted instruction |
| `user_instruction` | user | trusted instruction |
| `skill_instruction` | scoped skill | procedure only; no policy authority |
| `retrieved_memory` | none | untrusted data |
| `web_content` | none | untrusted data |
| `document` | none | untrusted data |
| `tool_output` | none | untrusted data |

Authority comes from the channel, never from text inside the channel. A web
page claiming to be a system message therefore remains data with no authority.
Skill instructions cannot redefine security policy or approve their own
effects.

## Assessment and provenance

`ContentSecurityController` normalizes Unicode for detection and identifies
bounded high-signal patterns such as authority overrides, policy redefinition,
secret exfiltration, covert action, security mutation, tool coercion, active
content, and invisible characters. Detection is not treated as proof and is not
the main security boundary:

- clean external content remains `data_only`;
- suspicious external content is `quarantine`;
- external content never gains instruction authority even when no pattern is
  detected.

Assessments retain the origin, source ID, SHA-256 content hash, authority,
disposition, findings, and provenance references. Raw assessed content is not
stored in the security tables.

## Context assembly

The context compiler assesses every system rule, selected skill, retrieved
memory, document, tool item, agent state, and observation before pricing.
Suspicious external content is rejected with an inspectable reason. Clean
external content is compressed and then XML-escaped inside an
`<untrusted_data>` boundary. The framing overhead is included in the hard token
budget. Clean skill procedures receive a separate escaped
`<skill_instruction authority="scoped_skill">` boundary; suspicious skill
instructions are rejected. Context-use records link back to the security
assessment and retain the origin and authority.

Retrieved memory, files, and tool output cannot redefine policy merely because
they were selected as relevant context.

## Sensitive derivation

External content and skill instructions cannot directly authorize:

- `memory.create`
- `skill.create`
- `agent.create`
- `permission.grant`

An approval binds one assessment, action, and exact target reference. Only a
system, developer, or user instruction channel can create it, and it is
one-shot. Memory writes derived from external content are hash-only quarantined
without such approval. Content-derived Prompt 36 grants use the same gate.

Skill generation already requires a separate explicit approval and produces a
quarantined package; the Agent Factory remains proposal-only and does not create
workers. These existing non-automatic boundaries are preserved.

```powershell
python -m acr_runtime.cli --db .acr/acr.db security assess `
  examples/security/injected-document-assessment.json
python -m acr_runtime.cli --db .acr/acr.db security inspect <ASSESSMENT_ID>
python -m acr_runtime.cli --db .acr/acr.db security approve approval.json
python -m acr_runtime.cli --db .acr/acr.db security approval <APPROVAL_ID>
```

Prompt injection has no complete pattern-based solution. Prompt 36 least
privilege, explicit approvals, tool execution separation, and the future Prompt
38 sandbox remain independent layers even when detection misses an attack.
