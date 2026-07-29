# Research-scout agent workflow

Use this workflow to compare current research and open-source implementations
with ACR. It validates a bounded report; it does not browse, fetch repositories,
run external code, approve an integration, or write memory.

Cover LLM memory, agent skills, self-evolving agents, context engineering,
agent orchestration, model routing, RAG, temporal memory, experience
distillation, AI evaluation, tool routing, prompt-injection defense, and
sandboxed code execution. Every topic must have either a finding or an
evidence-backed `no_relevant_finding` coverage record.

Prefer original research, official repositories, and maintainer documentation.
Bind each source to its HTTPS locator, publisher, publication and retrieval
dates, and a content hash. Available source code uses an immutable commit
reference. For each finding return `WHAT IS GENUINELY NEW`,
`EVIDENCE`, `SOURCE CODE`, `DIFFERS FROM ACR`, `SAFE ADAPTATION`,
`DO NOT COPY`, `EXPECTED IMPROVEMENT`, and `INTEGRATION COST`. Compare against
exact ACR files or tests rather than model recollection.

Claim maturity is explicit:

- `research_claim` is a paper or source claim not reproduced by ACR;
- `documented_implementation` proves an implementation exists, not its
  performance;
- `reproduced_engineering_result` requires an exact ACR reproduction reference;
- `insufficient_evidence` remains visible without being promoted.

Code availability is separate from license verification. Never reuse code with
an unverified license. Source-reported improvements remain source claims;
hypotheses cannot contain invented baseline or target measurements. Every
candidate adaptation includes baseline, candidate, quality, and security
benchmark steps.

```powershell
python -m acr_runtime.research_scout validate .\research-scout.json
```

`examples/agent-spec/research-scout-worker.json` is a valid Prompt 24 role
definition, not an executable worker. It has no tools, permissions, peers,
fallback, or paid budget. A host must separately grant bounded network access,
content-security assessment, repository reads, and any later benchmark work.

## Basis

- [Anthropic: Effective context engineering for AI agents](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)
- [OpenAI Agents SDK](https://openai.github.io/openai-agents-python/)
- [RouteLLM official repository](https://github.com/lm-sys/RouteLLM)
- [Graphiti official repository](https://github.com/getzep/graphiti)
- [ARES official repository](https://github.com/stanford-futuredata/ARES)
- [NIST agent-hijacking evaluations](https://www.nist.gov/news-events/news/2025/01/technical-blog-strengthening-ai-agent-hijacking-evaluations)
- [gVisor security architecture](https://gvisor.dev/docs/architecture_guide/intro/)
