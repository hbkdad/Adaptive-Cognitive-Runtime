# Research notes — July 2026

The shared concept is directionally well supported, with one qualification:
several self-evolving-skill results are recent preprints. They justify
experimentation, not unrestricted self-modification in production.

## Findings applied to v0.1

- Anthropic frames context as a finite resource whose token utility must be
  optimized. ACR therefore compiles context before execution and records
  per-block attribution.
- MUSE-Autoskill treats skills as lifecycle-managed assets with creation,
  memory, reuse, evaluation, and refinement. ACR stores lifecycle state and
  usage outcomes.
- EvoSkills couples skill generation to verification. ACR defaults generated
  skills to quarantine and does not execute them.
- MemSkill makes memory operations selectable and evolvable. ACR begins with
  explicit memory kinds and leaves learned memory policies for a later,
  evaluated milestone.
- Mem2Evolve co-evolves experience and created assets. ACR stores both memory
  outcomes and skill outcomes in one attribution loop.
- Graphiti demonstrates temporal facts, provenance, and hybrid retrieval. ACR
  implements validity windows and evidence now, while deferring the graph
  backend.
- Mem0 and Letta show that durable memory layers and stateful agents are already
  practical open-source building blocks. ACR remains dependency-light until the
  local retrieval benchmark shows where a heavier backend adds value.
- Google OR-Tools documents knapsack and constraint-programming formulations
  for selecting combinations under capacity and logical constraints. Because
  ACR bounds routing to a small candidate set, Prompt 18 enumerates the feasible
  subsets exactly and keeps the implementation dependency-free.
- Evidence Over Plans argues that skills should be distilled from verified
  environment trajectories rather than prior plans. SkillGen further uses
  successful and failed trajectories contrastively and measures regressions.
  Prompt 19 therefore requires repeated successful traces, preserves direct
  evidence, and imports same-class failure events as known failure modes. It
  leaves benchmark comparison and execution to Prompt 20.
- NIST cautions that static analysis is necessary but insufficient for software
  assurance, while Docker documents seccomp as an allowlist-style syscall
  boundary. Prompt 20 therefore makes static scanning one stage rather than the
  promotion proof, requires a real sandbox adapter, and configures the optional
  Docker runner with least privilege and no network. Python's subprocess
  guidance also supports avoiding an implicit shell; the Docker adapter passes
  an argument vector with `shell=False`.
- Semantic Versioning states that released version contents must not be
  modified. Prompt 21 therefore writes a new package and registry record for
  every mutation and rejects duplicate or backward versions.
- SkillMOO treats skill optimization as multi-objective rather than a
  single-score search. Prompt 21 uses the more conservative production rule:
  v2 must not regress quality, tokens, cost, latency, reliability, or security,
  and must strictly improve at least one objective.
- More Skills, Worse Agents reports that expanding skill libraries can degrade
  selection through skill shadowing. Prompt 22 therefore bounds pair analysis
  and treats redundancy as a retrieval-quality concern rather than assuming
  that library growth is harmless.
- SkillComposer separates create, improve, and merge operations, while
  Generative Skill Composition treats subset, count, and order as one
  structural decision. Prompt 22 distinguishes `MERGE` from `COMPOSE` and
  compares procedures and dependencies rather than relying on one similarity
  number.
- QA-Align demonstrates that cross-text content overlap extends beyond lexical
  similarity. Prompt 22 records a lexical proxy for inspection but refuses to
  use it as semantic evidence when no trusted semantic adapter is configured.
- NIST defines paired observations as one-to-one measurements and documents
  the paired sign test for cases where distribution assumptions are suspect.
  Prompt 23 evaluates each mutation against its baseline on matched case IDs
  with a dependency-free one-sided sign test.
- NIST also warns that repeated pairwise comparisons do not preserve the
  overall confidence level. Prompt 23 applies Holm-Bonferroni correction across
  all candidate mutations and separately requires a minimum effect size.
- Search-Time Contamination shows that public benchmark access can inflate
  agent results and recommends controlled benchmark access and isolation.
  Prompt 23 blocks by default and requires explicit isolation evidence from the
  benchmark adapter.
- The OpenAI Agents SDK defines an agent around instructions, tools, and
  handoffs, and distinguishes manager-owned specialist calls from handoffs.
  Prompt 24 records role, tools, communication, and verification explicitly
  while deferring orchestration to Prompt 25.
- OpenAI's context documentation notes that nested agent runs do not receive an
  isolated copy of application state by default. Prompt 24 therefore provides
  an explicit task-and-memory scope filter rather than assuming delegation
  isolates context.
- NIST's software and AI agent identity concept asks how to establish least
  privilege for agents. Prompt 24 resolves exact active skill versions and
  verifies that the worker grants every required tool and permission without
  accepting wildcard scopes.

## Primary sources

- Anthropic, “Effective context engineering for AI agents”
  https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents
- MUSE-Autoskill, arXiv:2605.27366
  https://arxiv.org/abs/2605.27366
- EvoSkills, arXiv:2604.01687
  https://arxiv.org/abs/2604.01687
- MemSkill, arXiv:2602.02474
  https://arxiv.org/abs/2602.02474
- Mem2Evolve, arXiv:2604.10923
  https://arxiv.org/abs/2604.10923
- Graphiti
  https://github.com/getzep/graphiti
- Mem0
  https://github.com/mem0ai/mem0
- Letta
  https://github.com/letta-ai/letta
- Google OR-Tools, packing and knapsack
  https://developers.google.com/optimization/pack
  https://developers.google.com/optimization/pack/knapsack
- Google OR-Tools, constraint optimization
  https://developers.google.com/optimization/cp
- Evidence Over Plans, arXiv:2605.09192
  https://arxiv.org/abs/2605.09192
- SkillGen, arXiv:2605.10999
  https://arxiv.org/abs/2605.10999
- NIST, Static Analysis is not enough
  https://www.nist.gov/publications/static-analysis-not-enough-role-architecture-and-design-software-assurance
- Docker seccomp profiles
  https://docs.docker.com/engine/security/seccomp/
- Python subprocess security considerations
  https://docs.python.org/3/library/subprocess.html#security-considerations
- Semantic Versioning 2.0.0
  https://semver.org/
- SkillMOO, arXiv:2604.09297
  https://arxiv.org/abs/2604.09297
- More Skills, Worse Agents?, arXiv:2605.24050
  https://arxiv.org/abs/2605.24050
- SkillComposer, arXiv:2606.06079
  https://arxiv.org/abs/2606.06079
- Generative Skill Composition for LLM Agents, arXiv:2606.32025
  https://arxiv.org/abs/2606.32025
- QA-Align, EMNLP 2021
  https://aclanthology.org/2021.emnlp-main.778/
- NIST, analysis of paired observations
  https://www.itl.nist.gov/div898/handbook/prc/section3/prc311.htm
- NIST Dataplot, paired sign test
  https://www.itl.nist.gov/div898/software/dataplot/refman1/auxillar/signtest.htm
- NIST, multiple comparisons and Bonferroni control
  https://www.itl.nist.gov/div898/handbook/prc/section4/prc47.htm
  https://www.itl.nist.gov/div898/handbook/prc/section4/prc473.htm
- Search-Time Contamination, arXiv:2606.05241
  https://arxiv.org/abs/2606.05241
- OpenAI Agents SDK, agents and orchestration
  https://openai.github.io/openai-agents-python/agents/
  https://openai.github.io/openai-agents-python/multi_agent/
- OpenAI Agents SDK, context management
  https://openai.github.io/openai-agents-python/context/
- NIST, Software and AI Agent Identity and Authorization concept paper
  https://www.nccoe.nist.gov/sites/default/files/2026-02/accelerating-the-adoption-of-software-and-ai-agent-identity-and-authorization-concept-paper.pdf
- Anthropic, How we built our multi-agent research system
  https://www.anthropic.com/engineering/multi-agent-research-system
- OpenAI Agents SDK, orchestrating multiple agents
  https://openai.github.io/openai-agents-python/multi_agent/
- OpenAI Agents SDK, usage tracking
  https://openai.github.io/openai-agents-python/usage/
- Silo-Bench: Benchmarking Multi-Agent Coordination, arXiv:2603.01045
  https://arxiv.org/abs/2603.01045
- DarkForest: Multi-Agent Search with Communication Costs, arXiv:2605.25188
  https://arxiv.org/abs/2605.25188

Prompt 25 follows the convergent operational guidance: parallelize only
independent work, count creation and communication tokens, and make coordination
overhead explicit. Anthropic reports substantial token amplification in its
research system and warns that shared context and dependencies reduce the
benefit. OpenAI distinguishes model-led handoffs from deterministic
code-orchestrated flows and exposes usage accounting. The recent coordination
benchmarks reinforce that communication overhead can erase nominal parallel
gains. ACR therefore chooses the smallest feasible topology by fixed,
inspectable estimates and emits proposals without spawning.

- OpenAI Agents SDK, tracing
  https://openai.github.io/openai-agents-python/tracing/
- OpenAI Agents SDK, usage
  https://openai.github.io/openai-agents-python/usage/
- Learning Latency-Aware Orchestration for Parallel Multi-Agent Systems,
  arXiv:2601.10560
  https://arxiv.org/abs/2601.10560

Prompt 26 follows the observable-run boundary in the OpenAI Agents SDK:
workflow traces retain agent, handoff, generation, and tool spans, while usage
records aggregate request and token counts. Anthropic's production report
similarly emphasizes measured token amplification and task-dependent value.
LAMaS explicitly supervises latency and critical-path structure. ACR therefore
stores topology, models, skills, parallelism, tokens, latency, and quality, but
does not infer success from topology alone. Recommendations require repeated
verified outcomes and remain advisory.

Prompt 75 turns the research-specific proposal into a bounded execution
contract. OpenAI's Agents SDK documents manager-owned synthesis and recommends
parallel execution for independent work; Anthropic's published research-system
experience likewise describes orchestrator-worker breadth-first research while
warning that multi-agent systems consume substantially more tokens. Python's
executor documentation warns against nested-future deadlocks and long-running
thread-pool work. ACR consequently caps workers, forbids worker-to-worker
future coordination, shares immutable reference IDs instead of histories,
centralizes synthesis and SQLite writes, and requires paired local measurement
instead of transferring another system's reported speed or quality gains.

- OpenAI Agents SDK, orchestrating multiple agents
  https://openai.github.io/openai-agents-python/multi_agent/
- OpenAI Agents SDK, running agents
  https://openai.github.io/openai-agents-python/running_agents/
- Anthropic, How we built our multi-agent research system
  https://www.anthropic.com/engineering/multi-agent-research-system
- Python, `concurrent.futures`
  https://docs.python.org/3/library/concurrent.futures.html
- SQLite, write-ahead logging
  https://www.sqlite.org/wal.html

Prompt 76 uses W3C PROV's explicit entity, activity, derivation, usage, and
qualified-relation ideas as a provenance model without claiming ontology
conformance. SQLite documents recursive CTEs as a native way to walk graphs.
ACR therefore starts with immutable relational nodes and typed edges, indexed
in both directions, and a depth- and result-bounded recursive query. A graph
database remains unjustified until representative measurements show the
relational implementation cannot meet a concrete query or scale requirement.

- W3C, PROV-O
  https://www.w3.org/TR/prov-o/
- W3C, PROV model primer
  https://www.w3.org/TR/prov-primer/
- SQLite, recursive common table expressions
  https://www.sqlite.org/lang_with.html

Prompt 77 follows NISTIR 8312's four explainability principles: provide
evidence or reasons, make the result meaningful to its user, accurately
reflect the process that produced the result, and disclose knowledge limits.
NIST's AI RMF also treats systematic documentation as a basis for transparency
and accountability. ACR consequently reads the exact historical scoring and
lifecycle rows, returns structured facts and limitations, and never asks a
model to invent a post-hoc story. Missing evidence produces `unavailable` or
`partial`, while Agent Factory proposals are identified as not executed.
The existing schema already retains the required audit evidence, so adding a
second explanation ledger would create another truth source without improving
fidelity.

- NIST, Four Principles of Explainable Artificial Intelligence (NISTIR 8312)
  https://www.nist.gov/publications/four-principles-explainable-artificial-intelligence
- NIST, AI Risk Management Framework Core
  https://airc.nist.gov/airmf-resources/airmf/5-sec-core/
- UK ICO, documentation for explaining decisions made with AI
  https://ico.org.uk/for-organisations/uk-gdpr-guidance-and-resources/artificial-intelligence/explaining-decisions-made-with-artificial-intelligence/part-3-what-explaining-ai-means-for-your-organisation/documentation/

Prompt 78 follows the NIST AI RMF outcomes that human oversight be defined and
documented, that mechanisms exist to supersede or deactivate components, and
that appeals and overrides be part of post-deployment monitoring. The NIST
Playbook also recommends recording policy exceptions, escalations, go/no-go
decisions, and override statistics. ACR therefore stores immutable operator
intent plus append-only lifecycle events and enforces controls at the actual
model, skill, Agent Factory, learning, architecture, memory, and version
rollback seams. Human selection does not bypass capability, eligibility,
budget, security, or compare-and-swap checks.

- NIST, AI Risk Management Framework Core
  https://airc.nist.gov/airmf-resources/airmf/5-sec-core/
- NIST, AI RMF Measure Playbook
  https://airc.nist.gov/airmf-resources/playbook/measure/
- NIST, AI RMF Manage Playbook
  https://airc.nist.gov/airmf-resources/playbook/manage/
- EUR-Lex, Regulation (EU) 2024/1689, Article 14
  https://eur-lex.europa.eu/eli/reg/2024/1689/oj/eng

Prompt 79 applies incident containment as graceful degradation rather than a
second unrestricted runtime profile. NIST SP 800-61 Rev. 3 recommends allowing
incident handlers to manually select containment actions. CISA's federal
playbook pairs isolation and service restriction with logging and evidence
preservation. OWASP's fail-safe and least-privilege principles support denying
mutation when containment state is uncertain. ACR therefore makes Safe Mode
persistent, adds a fail-closed environment latch, records state changes and
blocked operations, and enforces restrictions at domain controllers. Rollback
remains available because it is a recovery action with its own validation and
compare-and-swap boundaries.

- NIST, SP 800-61 Rev. 3, Incident Response Recommendations and Considerations
  for Cybersecurity Risk Management
  https://csrc.nist.gov/pubs/sp/800/61/r3/final
- CISA, Federal Government Cybersecurity Incident and Vulnerability Response
  Playbooks
  https://www.cisa.gov/sites/default/files/2024-08/Federal_Government_Cybersecurity_Incident_and_Vulnerability_Response_Playbooks_508C.pdf
- OWASP Developer Guide, fail-safe and least-privilege principles
  https://devguide.owasp.org/en/02-foundations/03-security-principles/
- OWASP Logging Cheat Sheet
  https://cheatsheetseries.owasp.org/cheatsheets/Logging_Cheat_Sheet.html

Prompt 80 follows SQLite's online backup API instead of copying a potentially
changing database file. NIST SP 800-53 calls for protecting backup
confidentiality, integrity, and availability and testing reliability and
integrity; the newer NIST SP 1339 guide likewise recommends stored content
hashes and recurring non-production restore tests. CISA recommends offline
backups and regular integrity testing. Python's ZIP documentation warns that
archive path objects do not sanitize traversal names. ACR therefore fixes the
source and restore path vocabulary, records per-entry SHA-256, prints an
out-of-band archive digest, verifies SQLite and compatibility before restore,
and never uses general archive extraction.

- SQLite, Online Backup API
  https://www.sqlite.org/backup.html
- NIST, SP 800-53 Rev. 5.1, CP-9 System Backup
  https://csrc.nist.gov/pubs/sp/800/53/r5/upd1/final
- NIST, SP 1339, OT Backup Quick Start Guide
  https://csrc.nist.gov/pubs/sp/1339/final
- CISA, StopRansomware Guide
  https://www.cisa.gov/stopransomware/ransomware-guide
- Python, `zipfile` path security note
  https://docs.python.org/3/library/zipfile.html

- OpenAI Agents SDK, agent orchestration
  https://openai.github.io/openai-agents-python/multi_agent/
- An Overview of Hierarchical Task Network Planning, arXiv:1403.7426
  https://arxiv.org/abs/1403.7426
- From Coarse to Fine: Self-Adaptive Hierarchical Planning for LLM Agents,
  arXiv:2604.23194
  https://arxiv.org/abs/2604.23194
- Hierarchical Task Network Planning with LLM-Generated Heuristics,
  arXiv:2605.07707
  https://arxiv.org/abs/2605.07707

Prompt 27 uses deterministic code orchestration for inspectable state and
structured outputs, consistent with OpenAI's guidance that code-owned flows are
more predictable in speed, cost, and performance. HTN planning supplies the
successive-refinement model. AdaPlan-H specifically motivates starting coarse
and refining based on complexity to reduce overplanning. ACR therefore retains
small macro plans, expands only marked nodes, and treats LLM- or user-provided
work hints as bounded candidates that still must pass deterministic dependency,
scope, capability, resource, and prerequisite validation.

- OpenAI API, Graders
  https://platform.openai.com/docs/api-reference/graders
- NIST AI RMF 1.0, Measure function
  https://airc.nist.gov/airmf-resources/airmf/5-sec-core/
- Zheng et al., Judging LLM-as-a-Judge with MT-Bench and Chatbot Arena,
  NeurIPS 2023
  https://proceedings.neurips.cc/paper_files/paper/2023/file/91f18a1287b398d378ef22505bf41832-Paper-Datasets_and_Benchmarks.pdf

Prompt 28 follows OpenAI's current separation of string checks, executable
graders, score-model graders, and multi-grader composition. NIST's Measure
function calls for objective and repeatable TEVV, documented results,
uncertainty measures, benchmarks, and independent assessors. Zheng et al.
demonstrate that LLM judges can be useful while exhibiting position, verbosity,
and self-enhancement biases. ACR therefore records all judge disagreement but
requires deterministic evidence on each passing criterion; model confidence is
never treated as ground truth.

- Shinn et al., Reflexion: Language Agents with Verbal Reinforcement Learning
  https://arxiv.org/abs/2303.11366
- Madaan et al., Self-Refine: Iterative Refinement with Self-Feedback
  https://arxiv.org/abs/2303.17651
- OpenAI Agents SDK, tracing
  https://openai.github.io/openai-agents-python/tracing/
- OpenAI Agents SDK, usage
  https://openai.github.io/openai-agents-python/usage/

Prompt 29 adopts the feedback-and-trace premise of Reflexion and Self-Refine but
does not import an open-ended generator/critic loop. OpenAI's current tracing
contract makes generations, tool calls, handoffs, and guardrails observable,
while its usage contract measures requests and tokens across model and tool
activity. ACR therefore reflects once over explicit evaluation, attribution,
cost, and trace evidence; it answers a fixed schema, records uncertainty, and
cannot learn or recursively invoke itself.

- SQLite, Transaction control
  https://www.sqlite.org/lang_transaction.html
- SQLite, Atomic Commit
  https://www.sqlite.org/atomiccommit.html
- OpenAI Agents SDK, lifecycle hooks
  https://openai.github.io/openai-agents-python/ref/lifecycle/
- OpenAI Agents SDK, usage
  https://openai.github.io/openai-agents-python/usage/
- NIST AI RMF Playbook
  https://airc.nist.gov/docs/AI_RMF_Playbook.pdf

Prompt 30 treats the completed execution as an immutable upstream fact and the
learning pass as one separate unit of work. SQLite documents that an explicit
transaction persists until commit or rollback and does not support nested
`BEGIN` transactions; ACR therefore lets participating stores join one
controller-owned transaction. OpenAI lifecycle hooks and per-run usage provide
the post-run observation model. NIST recommends monitoring and documenting
production metrics against pre-deployment measurements, which informs the
review-only quality, token, latency, and cost regression records.

- Ong et al., RouteLLM: Learning to Route LLMs with Preference Data
  https://arxiv.org/abs/2406.18665
- Chen et al., FrugalGPT: How to Use Large Language Models While Reducing Cost
  and Improving Performance
  https://arxiv.org/abs/2305.05176
- NIST/SEMATECH e-Handbook, confidence intervals for proportions
  https://itl.nist.gov/div898/handbook/prc/section2/prc241.htm

Prompt 32 uses RouteLLM's cost/performance routing objective and FrugalGPT's
cascade pattern without introducing an opaque learned policy. Because small
histories make raw success fractions overconfident, ACR uses inspectable Wilson
lower bounds, a minimum comparable sample count, and explicit capability gates.
Provider calls alone do not prove quality: only independently evidenced,
task-class-specific outcomes qualify a model. Escalation is limited to one
historically stronger candidate and its actual benefit is retained.

- Ollama API, List models
  https://docs.ollama.com/api/tags
- Ollama API, Show model details
  https://docs.ollama.com/api-reference/show-model-details
- Ollama, Tool calling
  https://docs.ollama.com/capabilities/tool-calling
- NIST AI RMF 1.0
  https://www.nist.gov/publications/artificial-intelligence-risk-management-framework-ai-rmf-10

Prompt 33 uses Ollama's installed-model endpoint for discovery and its show
endpoint for advertised features and model-family context length. Model-name
guessing is not sufficient for routing admission. NIST's privacy-enhanced and
risk-governance framing informs a content-free routing request and an explicit
permission gate: sensitive context cannot make a cloud model eligible merely
because it is more capable.

- OpenAI Agents SDK, Tools
  https://openai.github.io/openai-agents-python/tools/
- OpenAI Agents SDK, Human-in-the-loop
  https://openai.github.io/openai-agents-python/human_in_the_loop/
- Model Context Protocol, Tool annotations as risk vocabulary
  https://blog.modelcontextprotocol.io/posts/2026-03-16-tool-annotations/

Prompt 34 follows the current function-tool pattern of structured schemas and
explicit enabling/approval boundaries. MCP's read-only, destructive,
idempotent, and open-world annotations are explicitly hints rather than trusted
enforcement. ACR therefore stores a smaller mandatory side-effect vocabulary
and evaluates actual grants in deterministic code. Destructive definitions
cannot pass authorization without per-call approval evidence.

Prompt 35 follows OpenAI's current guidance to keep tool surfaces small through
namespaces or deferred tool search and to run input guardrails immediately
before execution. ACR applies the analogous principle locally: select the
smallest relevant permitted set, retain rejected candidates, and keep selection
separate from execution. Deterministic tools are preferred over simulated model
arithmetic, filesystem lookup, database access, or current-fact recall.

- NIST SP 800-171 Rev. 3, Least Privilege (03.01.05)
  https://nvlpubs.nist.gov/nistpubs/SpecialPublications/800-171r3/NIST.SP.800-171r3.html
- Model Context Protocol, Authorization (2025-11-25)
  https://modelcontextprotocol.io/specification/2025-11-25/basic/authorization
- Model Context Protocol, Security Best Practices
  https://modelcontextprotocol.io/docs/tutorials/security/security_best_practices

Prompt 36 applies NIST's requirement to permit only necessary access and log
privileged functions. MCP likewise requires least-privilege scopes and
server-side enforcement rather than trusting client-declared access. ACR uses a
fixed vocabulary, exact resource scopes, expiry, retained decisions, and default
deny. Delegation is monotonic: capability and scope remain identical, expiry
cannot increase, and revocation propagates through the grant tree. Skills have
no grant authority, preventing generated-skill self-escalation.

- NIST AI 100-2e2025, Adversarial Machine Learning taxonomy
  https://nvlpubs.nist.gov/nistpubs/ai/NIST.AI.100-2e2025.pdf
- OpenAI Model Spec, Ignore untrusted data by default (2025-10-27)
  https://model-spec.openai.com/2025-10-27
- OpenAI, Improving instruction hierarchy in frontier LLMs
  https://openai.com/index/instruction-hierarchy-challenge/
- Model Context Protocol, Tools security considerations (2025-11-25)
  https://modelcontextprotocol.io/specification/2025-11-25/server/tools

NIST describes indirect prompt injection as an attacker using control of a
resource to inject instructions into RAG or agent data channels, and recommends
hierarchical trust, filtering or spotlighting, well-defined interfaces, and an
assumption that attacks remain possible. OpenAI's Model Spec assigns quoted,
attached, and tool-returned data no authority by default and recommends
structured delimiting. Its instruction-hierarchy research separately orders
system, developer, user, and tool channels. MCP requires access controls and
tool-result validation and recommends confirmation for sensitive operations.
Prompt 37 therefore makes provenance-derived authority the hard boundary,
frames clean external data, quarantines suspicious external input, and keeps
approval and least privilege independent of detection.

- Docker, `docker container run`
  https://docs.docker.com/reference/cli/docker/container/run/
- Docker, Seccomp security profiles
  https://docs.docker.com/engine/security/seccomp/
- Docker, Bind mounts
  https://docs.docker.com/engine/storage/bind-mounts/
- Docker, tmpfs mounts
  https://docs.docker.com/engine/storage/tmpfs/
- Docker, Rootless mode
  https://docs.docker.com/engine/security/rootless/
- Microsoft, Create Process in Sandbox APIs
  https://learn.microsoft.com/en-us/windows/win32/secauthz/createprocessinsandbox

Docker documents independent container filesystems, networks, and process
trees; read-only roots and mounts; capability removal; non-root execution;
`no-new-privileges`; seccomp; and CPU, memory, PID, file, and timeout controls.
It warns that writable bind mounts can modify host files and that privileged
containers are not sandboxes. Prompt 38 therefore mounts only the skill
read-only, uses bounded tmpfs for all writes, explicitly removes network and
capabilities, resolves the image to its immutable local ID, and retains the
enforced profile. Microsoft documents AppContainer, filesystem, network,
process, environment, and Job Object limits as the equivalent Windows-native
security primitives; those experimental APIs are not treated as a portable
fallback. The runtime fails closed when the selected isolation adapter is
unavailable.

- Python Keyring documentation, supported backends
  https://keyring.readthedocs.io/en/stable/
- Microsoft, Credential Management API (`wincred.h`)
  https://learn.microsoft.com/en-us/windows/win32/api/wincred/
- Microsoft, `CredReadW`
  https://learn.microsoft.com/en-us/windows/win32/api/wincred/nf-wincred-credreadw
- Microsoft, `CredFree`
  https://learn.microsoft.com/en-us/windows/win32/api/wincred/nf-wincred-credfree
- Python, `os.environ`
  https://docs.python.org/3/library/os.html#os.environ
- GitHub, About secret scanning
  https://docs.github.com/en/code-security/secret-scanning/introduction/about-secret-scanning

Python Keyring exposes native Windows, macOS, and Linux credential backends
behind one optional adapter. Microsoft's native contract requires credential
buffers returned by `CredReadW` to be released with `CredFree`; using the
maintained keyring adapter keeps that platform-specific ownership outside ACR.
Environment variables remain the zero-dependency local provider, and an
injectable callback permits a separately configured external store without
selecting a vendor.

Prompt 39 keeps authorization independent of detection: only an exact
`credential.use` decision allows provider lookup. References and audits are
hash-only, resolved values receive a one-use lifetime-minimizing lease, and
durable runtime boundaries reject or redact detected formats. Git staged-blob
scanning and hosted secret scanning are independent repository controls.

- NIST, Privacy Framework
  https://www.nist.gov/privacy-framework
- NIST, Using Privacy Framework 1.1
  https://www.nist.gov/privacy-framework/using-privacy-framework-11
- EUR-Lex, GDPR Article 17 right to erasure
  https://eur-lex.europa.eu/legal-content/EN/TXT/HTML/?uri=CELEX%3A02016R0679-20160504
- SQLite, `VACUUM`
  https://sqlite.org/lang_vacuum.html
- SQLite, `PRAGMA secure_delete`
  https://sqlite.org/pragma.html#pragma_secure_delete
- SQLite, FTS5 secure-delete
  https://www.sqlite.org/fts5.html#the_secure_delete_configuration_option

NIST treats collection, retention, disclosure, transfer, and disposal as one
data lifecycle and recommends expressing privacy requirements to external
providers. Article 17 supplies the right-to-erasure reference point, while
retention periods remain purpose- and policy-dependent rather than universal.
Prompt 40 therefore uses explicit versioned local policy instead of claiming a
legal default.

SQLite warns that ordinary deletion can leave recoverable bytes, core
`secure_delete` alone may not remove FTS shadow-index traces, and `VACUUM`
rewrites the active file. ACR combines core and FTS secure deletion with a WAL
checkpoint and policy-required vacuum, then verifies the logical record and FTS
result. It reports backup cleanup separately because rewriting the active file
cannot erase independent copies.

- NIST, Completely randomized designs
  https://www.itl.nist.gov/div898/handbook/pri/section3/pri331.htm
- Google Research, Overlapping Experiment Infrastructure
  https://research.google/pubs/overlapping-experiment-infrastructure-more-better-faster-experimentation/
- Microsoft Research, Patterns of Trustworthy Experimentation: Pre-Experiment
  https://www.microsoft.com/en-us/research/group/experimentation-platform-exp/articles/patterns-of-trustworthy-experimentation-pre-experiment-stage
- Microsoft Research, Trustworthy analysis of online A/B tests
  https://www.microsoft.com/en-us/research/publication/trustworthy-analysis-of-online-a-b-tests-pitfalls-challenges-and-solutions/

NIST grounds controlled comparison in random assignment to experimental units.
Google's infrastructure work separates experiment layers and parameters so
tests do not silently collide. Microsoft emphasizes an explicit hypothesis, the
correct randomization unit, allocation-ratio checks, trustworthy metrics, and
replication before shipping; it also warns that unjustified independence
assumptions can make inference unreliable.

Prompt 42 therefore freezes definitions before assignment, hashes a stable
caller-selected unit with an explicit seed, diagnoses allocation drift, and
reports descriptive baseline deltas only. It does not perform significance
claims or production promotion. Later evaluation may add stronger inference
only when randomization-unit independence and sample assumptions are explicit.

## MCP integration

- Model Context Protocol, server tools, revision 2025-11-25
  https://modelcontextprotocol.io/specification/2025-11-25/server/tools
- Model Context Protocol, lifecycle, revision 2025-11-25
  https://modelcontextprotocol.io/specification/2025-11-25/basic/lifecycle
- Model Context Protocol, transports, revision 2025-11-25
  https://modelcontextprotocol.io/specification/2025-11-25/basic/transports
- Model Context Protocol, authorization, revision 2025-11-25
  https://modelcontextprotocol.io/specification/2025-11-25/basic/authorization
- Official Model Context Protocol Python SDK
  https://github.com/modelcontextprotocol/python-sdk

Prompt 56 pins the `2025-11-25` lifecycle and tool contract while using a
dependency-free local stdio implementation. The transport specification
requires stdout to contain only valid MCP messages; the server therefore sends
all protocol output as bounded newline-delimited JSON-RPC and introduces no
logging path to stdout. Tool annotations remain descriptive hints and never
grant authority. The process binds a server-configured ACR identity, then the
existing exact grant controller authorizes every operation.

The authorization specification forbids token passthrough. ACR's external MCP
adapter consequently accepts no inbound credential or transport configuration
from tool arguments. Remote descriptions, schemas, annotations, errors, and
results are untrusted; local policy supplies permissions and risk. Streamable
HTTP/OAuth is deliberately deferred until Origin validation, audience-bound
tokens, protected-resource discovery, and SSRF controls can be implemented and
tested as one complete boundary.

## Codex integration

- OpenAI Codex, custom instructions with `AGENTS.md`
  https://learn.chatgpt.com/docs/agent-configuration/agents-md
- OpenAI Codex, Model Context Protocol
  https://learn.chatgpt.com/docs/extend/mcp
- OpenAI Codex, configuration reference
  https://learn.chatgpt.com/docs/config-file/config-reference

Codex automatically loads concise repository guidance from `AGENTS.md`, with
nearer nested files taking precedence, while trusted-project
`.codex/config.toml` owns repository-specific MCP configuration. Prompt 57
therefore keeps the coding workflow in those host surfaces rather than adding
Codex concepts to ACR core services.

The local Codex CLI 0.137.0 was treated as authoritative for its installed
configuration vocabulary: it accepted `auto`, `prompt`, and `approve` approval
modes but rejected the manual's additional `writes` value. The checked-in
configuration uses `auto` only for the allowlisted read tools and a per-tool
`prompt` override for audit-writing context compilation. This difference is
documented rather than hidden.

## Claude Code integration

- Anthropic, How Claude remembers your project
  https://code.claude.com/docs/en/memory
- Anthropic, Connect Claude Code to tools via MCP
  https://code.claude.com/docs/en/mcp
- Anthropic, Hooks reference
  https://code.claude.com/docs/en/hooks
- Anthropic, Claude Code settings
  https://code.claude.com/docs/en/configuration

Claude Code officially supports a compact `CLAUDE.md` importing an existing
`AGENTS.md`, project-scoped stdio MCP servers in `.mcp.json`, and command hooks
that receive bounded JSON on stdin. `UserPromptSubmit` can add context before a
turn, while `Stop` exposes the final assistant message and a
`stop_hook_active` loop guard.

Prompt 58 uses those host boundaries without making them authoritative over ACR
permissions. The preflight skips non-coding and secret-like prompts, applies
small retrieval caps, uses exact server-bound grants, and emits untrusted
additional context. The postflight asks for learning candidates once but never
writes them. Claude auto-memory is disabled at project scope so it does not
silently duplicate governed ACR memory.

## Architecture guard

- Python, `ast` — Abstract syntax trees
  https://docs.python.org/3/library/ast.html
- Import Linter, contract types
  https://import-linter.readthedocs.io/en/stable/contract_types.html
- Ruff, rule catalog
  https://docs.astral.sh/ruff/rules/

Python exposes imports as syntax-tree nodes without requiring inspected modules
to be imported or executed. Import Linter's forbidden and layered contracts
also treat indirect dependency paths as architecture violations, while Ruff's
general import rules do not express project-specific transitive boundaries.

Prompt 91 therefore uses a small standard-library AST graph rather than adding
a linter dependency. A strict TOML policy names the existing dependency-free
core and three forbidden boundary categories. The checker resolves relative,
absolute, and literal dynamic imports, rejects stale policy module names, and
reports each shortest forbidden dependency path as deterministic JSON. CI runs
this contract before every test tier.

## Bug-fix agent

- Google SRE, Effective Troubleshooting
  https://sre.google/sre-book/effective-troubleshooting/
- Git, `git bisect`
  https://git-scm.com/docs/git-bisect
- Python, `unittest`
  https://docs.python.org/3/library/unittest.html

Google's troubleshooting method starts from system understanding and preserved
observations, reduces failures at observable interfaces, and iteratively tests
ranked hypotheses. It treats negative results as useful evidence and warns
against confusing correlation with causation. Git bisect requires known good
and bad states and repeatedly evaluates a real pass/fail property.

Prompt 93 therefore refuses random-edit debugging. The worker first obtains a
minimal reproducer and bounded exact error, isolates the smallest failing
boundary, inspects relevant history, and tests explicit predictions. A
root-cause patch is complete only with a regression test and broader checks.
Failure-memory persistence remains separately authorized and requires verified
cause, resolution, and evidence.

## Security-review agent

- NIST, Secure Software Development Framework 1.1
  https://www.nist.gov/publications/secure-software-development-framework-ssdf-version-11-recommendations-mitigating-risk
- OWASP, Threat Modeling Cheat Sheet
  https://cheatsheetseries.owasp.org/cheatsheets/Threat_Modeling_Cheat_Sheet.html
- OWASP, Secure Code Review Cheat Sheet
  https://cheatsheetseries.owasp.org/cheatsheets/Secure_Code_Review_Cheat_Sheet.html
- FIRST, CVSS v4.0 Specification
  https://www.first.org/cvss/specification-document

NIST SSDF calls for review or analysis of human-readable code and documented
triage and remediation. OWASP grounds threat review in data flows, trust
boundaries, attack points, authorization, input validation, traversal, and
explained attack paths. FIRST defines stable qualitative severity labels, but a
label without a calculated vector is not represented here as a CVSS score.

Prompt 94 therefore separates observations from authority. Every finding needs
bounded evidence and an attack path, but only verified high or critical
findings derive a blocking result. Supported and speculative risks remain
reportable and non-blocking. The validator has no scanning or execution
capability, rejects secret material and unknown fields, and requires explicit
coverage of all eleven security categories.

## Performance-review agent

- Python, deterministic profilers
  https://docs.python.org/3/library/profile.html
- OpenTelemetry, metric semantic conventions
  https://opentelemetry.io/docs/specs/semconv/general/metrics/
- Google SRE, Monitoring Distributed Systems
  https://sre.google/sre-book/monitoring-distributed-systems/
- SQLite, EXPLAIN QUERY PLAN
  https://sqlite.org/eqp.html

Python profiling uses call counts, internal time, and cumulative time to find
surprising work, hot loops, and algorithm-selection problems. OpenTelemetry
emphasizes explicit, understandable units. Google SRE separates latency,
traffic, errors, and saturation and warns that latency distributions matter.
SQLite supports interactive query-plan inspection but explicitly does not
promise a stable EXPLAIN QUERY PLAN output format.

Prompt 95 therefore consumes existing measurements instead of introducing new
collection. It covers six fixed resource categories and separates unmeasured
work, observed overhead, and paired measured waste. Only a lower candidate with
at least three samples and passing quality and security gates is ranked. The
ranking uses relative reduction so tokens, calls, queries, and nanoseconds are
not compared as if their absolute units were interchangeable.

## Architecture-review agent

- David L. Parnas, On the Criteria To Be Used in Decomposing Systems into
  Modules
  https://citeseerx.ist.psu.edu/document?doi=5d752e29e29b42cc509417699a98d9dca8212c83&repid=rep1&type=pdf
- Python, `typing.Protocol`
  https://docs.python.org/3/library/typing.html#typing.Protocol
- Martin Fowler, YAGNI
  https://martinfowler.com/bliki/Yagni.html

Parnas treats a module as a responsibility assignment and recommends hiding
design decisions likely to change so modules can be understood and replaced
independently. Python Protocols express structural contracts without requiring
concrete inheritance. YAGNI catalogs the carrying, delay, repair, and
opportunity costs of speculative features and future-flexibility abstractions.

Prompt 96 therefore separates executable dependency enforcement from design
review. Every dimension receives evidence and uncertainty, while concerns need
a concrete impact path. A needless abstraction is rejectable only with verified
complexity cost and a simpler removal path; uncertain future value remains
visible but cannot be mislabeled as proven needlessness.

## Release engineer

- Python Packaging User Guide, packaging flow
  https://packaging.python.org/en/latest/flow/
- Git, `git tag`
  https://git-scm.com/docs/git-tag.html
- GitHub, immutable releases
  https://docs.github.com/en/code-security/concepts/supply-chain-security/immutable-releases
- SQLite, transactions
  https://www.sqlite.org/lang_transaction.html

PyPA distinguishes built wheel/source artifacts from editable source-tree
installs. Git recommends annotated tags for releases and refuses to replace an
existing tag unless force is explicitly requested. GitHub immutable releases
lock the published tag and assets and generate an attestation. SQLite
transactions remain the foundation for migration rollback evidence.

Prompt 97 therefore gates release readiness on nine fresh evidence families
bound to one commit. Clean installation uses a built wheel, upgrade starts from
the prior immutable release, and migration checks use disposable databases.
The validator never runs commands or tags. An actual annotated or signed tag
and immutable GitHub release require a separate approval after validation.

## Expansion discovery

- Google SRE Workbook, Eliminating Toil
  https://sre.google/workbook/eliminating-toil/
- NIST AI RMF, Measure
  https://airc.nist.gov/airmf-resources/airmf/5-sec-core/
- CISA and FBI, Product Security Bad Practices update
  https://www.cisa.gov/news-events/alerts/2025/01/17/cisa-and-fbi-release-updated-guidance-product-security-bad-practices

Google SRE recommends quantifying repetitive work and comparing automation
benefit with implementation cost and risk. NIST Measure calls for metrics,
benchmark comparisons, uncertainty, and documented measurable change. CISA
places security across the full product lifecycle.

Prompt 98 therefore derives expansion decisions from repeated measured demand.
BUILD needs verified evidence across multiple tasks and source families,
nonzero cost, a measurable target, bounded complexity, acceptable security
risk, and baseline/candidate/quality/security benchmarks. DEFER preserves
weaker or riskier repeated demand; speculative, one-off, and zero-cost ideas
are rejected rather than rewarded for novelty.

## Research scout

- Anthropic, Effective context engineering for AI agents
  https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents
- OpenAI Agents SDK
  https://openai.github.io/openai-agents-python/
- Graphiti, temporal context graph implementation
  https://github.com/getzep/graphiti
- SkillFoundry
  https://arxiv.org/abs/2604.03964
- AutoSkill
  https://github.com/ECNU-ICALK/AutoSkill
- RouteLLM
  https://github.com/lm-sys/RouteLLM
- ARES, automated RAG evaluation
  https://github.com/stanford-futuredata/ARES
- Apple ToolSandbox
  https://github.com/apple/ToolSandbox
- Anthropic, Demystifying evals for AI agents
  https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents
- NIST, Strengthening AI agent hijacking evaluations
  https://www.nist.gov/news-events/news/2025/01/technical-blog-strengthening-ai-agent-hijacking-evaluations
- gVisor, security architecture
  https://gvisor.dev/docs/architecture_guide/intro/

The reviewed sources expose distinct kinds of evidence. Graphiti, AutoSkill,
RouteLLM, ARES, ToolSandbox, the OpenAI Agents SDK, and gVisor have inspectable
implementations, but existence does not reproduce their performance or safety
claims inside ACR. SkillFoundry and other papers provide research claims that
remain hypotheses for ACR until evaluated on matched local cases. Maintainer
documentation establishes supported interfaces and intended boundaries, not
comparative benchmark superiority.

The safely adaptable pattern is evidence structure: incremental temporal
provenance, lifecycle-managed skill candidates, paired router evaluation,
retrieval relevance and faithfulness dimensions, stateful tool scenarios,
trace-aware agent evaluation, adversarial hijacking cases, and explicit
isolation boundaries. ACR should not copy source-reported numbers, dependency
stacks, prompts, benchmark answers, or code without current license and security
review. In particular, ordinary containerization must not be mislabeled as a
complete hostile-code security boundary.

Prompt 99 therefore requires complete topic coverage, primary or maintainer
provenance, retrieval dates, content hashes, exact ACR comparison references,
code and license status, and a baseline/candidate/quality/security benchmark
plan. `research_claim`, `documented_implementation`, and
`reproduced_engineering_result` are separate states. Source-reported
improvements stay source claims; only an exact ACR reproduction reference can
promote them to an engineering result.

## Capability and implementation-prompt designer

- OpenAI, A practical guide to building agents
  https://openai.com/business/guides-and-resources/a-practical-guide-to-building-ai-agents/
- Anthropic, Trustworthy agents in practice
  https://www.anthropic.com/research/trustworthy-agents
- NIST, AI Risk Management Framework Core
  https://airc.nist.gov/airmf-resources/airmf/5-sec-core/

OpenAI distinguishes standardized data, action, and orchestration tools and
recommends incremental orchestration rather than beginning with complex
autonomy. Anthropic distinguishes a self-directed agent loop from a fixed
script and emphasizes human control, explicit tool permissions, transparency,
and stopping for ambiguous user intent. NIST Map and Measure require intended
context, assumptions, risks, test methods, benchmarks, uncertainty, and
documented results throughout the lifecycle.

Prompt 101 therefore classifies closed structural traits in deterministic code.
Typed external, memory, context, routing, and foundational boundaries take
precedence over autonomy. Cross-boundary requests must be decomposed. Agent
classification requires a delegated adaptive multi-step goal and explicit
rejection of simpler forms. The generated specification includes every required
interface, permission, data, failure, test, benchmark, telemetry, security, and
rollout field, while remaining non-executable and non-authorizing.

## Architectural simplification

- Python Packaging Authority, Entry points specification
  https://packaging.python.org/en/latest/specifications/entry-points/
- Python Packaging Authority, Creating and discovering plugins
  https://packaging.python.org/en/latest/guides/creating-and-discovering-plugins/
- GitHub, Analyze your code with CodeQL
  https://docs.github.com/en/code-security/tutorials/customize-code-scanning/analyze-code

PyPA documents entry points and multiple plugin-discovery mechanisms that load
code without ordinary direct imports. GitHub documents static analysis queries
for potential unused imports, functions, and classes. Together these support a
conservative distinction: static results are useful candidates for review, but
they do not prove that a public, CLI, entry-point, or dynamically discovered
surface is unused.

Prompt 102 therefore removes only verified exact private duplication in this
checkpoint. The shared bounded validator preserves secret scanning and every
existing validation contract. Static low-reference candidates remain visible
but cannot authorize deletion without supported-use and compatibility evidence.

## Production readiness

- NIST SP 800-218, Secure Software Development Framework
  https://csrc.nist.gov/pubs/sp/800/218/final
- NIST AI Risk Management Framework Core
  https://airc.nist.gov/airmf-resources/airmf/5-sec-core/
- OWASP API4:2023, Unrestricted Resource Consumption
  https://owasp.org/API-Security/editions/2023/en/0xa4-unrestricted-resource-consumption/
- OpenTelemetry, Observability primer
  https://opentelemetry.io/docs/concepts/observability-primer/
- NIST Privacy Framework
  https://www.nist.gov/privacy-framework/privacy-framework
- SQLite Backup API
  https://www.sqlite.org/backup.html
- SQLite transactions
  https://www.sqlite.org/lang_transaction.html
- GitHub, Immutable releases
  https://docs.github.com/en/code-security/concepts/supply-chain-security/immutable-releases
- Google SRE, Release Engineering
  https://sre.google/sre-book/release-engineering/

NIST SSDF treats security as an explicit part of the development lifecycle.
NIST AI RMF requires repeatable test, evaluation, verification, validation, and
ongoing measurement rather than a one-time checklist. OWASP identifies missing
interaction, resource, payload, operation, and provider-spend limits as API
resource-consumption risks. OpenTelemetry distinguishes instrumentation from
the operational signals and service indicators needed to understand deployed
reliability.

SQLite's online backup API produces a consistent snapshot, while transaction
and application rollback remain separate concerns. The NIST Privacy Framework
keeps data processing and privacy risk operational. GitHub immutable releases
bind tags and assets and create an attestation; Google SRE treats reproducible,
automated release engineering as a distinct discipline.

Prompt 106 therefore uses four cumulative evidence levels. A specification,
test, local rehearsal, or production observation proves only its own level.
The validator rejects skipped levels and derives the not-ready result whenever
any dimension lacks production evidence. This deliberately prevents broad
readiness claims from narrow green tests.

## Zero-cloud deployment

- SQLite, Self-Contained
  https://sqlite.org/selfcontained.html
- SQLite, Serverless
  https://www.sqlite.org/serverless.html
- Ollama FAQ, local-only and server configuration
  https://docs.ollama.com/faq
- Ollama cloud models
  https://docs.ollama.com/cloud
- Ollama API introduction
  https://docs.ollama.com/api/introduction
- Ollama model-list API
  https://docs.ollama.com/api/tags

SQLite is embedded and serverless: application processes read and write the
database file directly without a separate database server. This supports the
existing local memory and telemetry ownership boundary rather than motivating
a new storage abstraction.

Ollama's documented local API defaults to localhost. Ollama also documents
cloud models invoked through that same local endpoint, including `:cloud` and
`-cloud` model names, so a loopback URL alone does not prove local inference.
Ollama's official defense-in-depth control is `OLLAMA_NO_CLOUD=1`, applied when
starting or restarting its service.

Prompt 107 therefore validates a root loopback Ollama URL and filters the
documented cloud-model suffixes at the ACR provider boundary. The operator
guidance also requires Ollama's own cloud-disable setting. ACR's deterministic
core keeps working with no model provider, while optional chat and embeddings
use the existing Ollama adapter.

## Desktop daemon lifecycle

- Python subprocess management and Windows helpers
  https://docs.python.org/3.11/library/subprocess.html
- Python `os.kill`
  https://docs.python.org/3.11/library/os.html#os.kill
- Uvicorn settings
  https://www.uvicorn.org/settings/

Python exposes Windows creation flags for a new detached process group and a
startup-info flag that hides the child window. Its Windows `os.kill` contract
states that ordinary signals terminate through the Windows process API, while
POSIX uses normal signal delivery. Uvicorn supports explicit programmatic host
and port configuration.

Prompt 108 uses those standard-library process boundaries and the existing
Uvicorn application. A PID alone is unsafe because it can be stale or reused,
so status and stop additionally require the child API to return the exact
canonical per-start UUID retained in the atomic daemon state. An identity
mismatch fails closed without signaling the PID.

## Negative procedures

- Reflexion: Language Agents with Verbal Reinforcement Learning
  https://arxiv.org/abs/2303.11366
- ExpeL: LLM Agents Are Experiential Learners
  https://arxiv.org/abs/2308.10144
- Learning From Failure: Integrating Negative Examples when Fine-tuning Large
  Language Models as Agents
  https://arxiv.org/abs/2402.11651

These primary papers support the narrower proposition that failure feedback and
unsuccessful trajectories can carry reusable information. They do not validate
universal prohibitions or authorize ACR integration. Prompt 114 therefore
retains the existing evidence source, requires repeated deterministic evidence
within one exact non-global scope, and exposes only a planning constraint.

## Knowledge half-life

- MemoryBank: Enhancing Large Language Models with Long-Term Memory
  https://arxiv.org/abs/2305.10250
- Time-Aware Language Models as Temporal Knowledge Bases
  https://aclanthology.org/2022.tacl-1.15/
- HoH: A Dynamic Benchmark for Evaluating the Impact of Outdated Information
  on Retrieval-Augmented Generation
  https://aclanthology.org/2025.acl-long.301/

MemoryBank explores forgetting based on elapsed time and significance.
Time-Aware Language Models treats changing facts as explicitly temporal, and
HoH evaluates the harm caused by outdated retrieval content. Prompt 115 does
not reproduce their results. It uses these findings only to motivate a
deterministic, measurable type profile subordinate to explicit validity and
supersession. Source freshness remains visibly unavailable until Prompt 116
defines and verifies that record-level evidence.

## Freshness engine

- TimeR4: Time-aware Retrieval-Augmented Large Language Models
  https://aclanthology.org/2024.emnlp-main.394/
- HoH: A Dynamic Benchmark for Evaluating the Impact of Outdated Information
  https://aclanthology.org/2025.acl-long.301/
- Unified Active Retrieval for Retrieval Augmented Generation
  https://aclanthology.org/2024.findings-emnlp.999/

These works motivate explicit temporal retrieval and selective revalidation.
Prompt 116 does not reproduce their model results. It implements a local,
deterministic evidence contract and refuses to select refresh-gated facts when
freshness is unknown or expired.

## Source reliability

- W3C PROV-O
  https://www.w3.org/TR/prov-o/
- Retrieval-Augmented Generation with Estimation of Source Reliability
  https://aclanthology.org/2025.emnlp-main.1738/
- Provenance: A Light-weight Fact-checker for Retrieval Augmented LLM
  Generation Output
  https://aclanthology.org/2024.emnlp-industry.97/

W3C PROV keeps source derivation explicit, including primary-source
relationships. Reliability-aware RAG reports benefits from accounting for
heterogeneous sources rather than relevance alone, while provenance-based fact
checking separately tests whether an output is supported by retrieved context.
Prompt 117 does not reproduce either paper's results. ACR adds only a closed
source-class vocabulary and a low-weight deterministic retrieval prior; it
does not learn source reputation, cross-check claims, vote across sources, or
treat class as a truth guarantee.

## Active learning

- Value of Information: A Framework for Human-Agent Communication
  https://aclanthology.org/2026.acl-long.1987/
- Active Learning for Cost-Sensitive Classification
  https://proceedings.mlr.press/v70/krishnamurthy17a.html
- Practical Obstacles to Deploying Active Learning
  https://aclanthology.org/D19-1003/

The first work explicitly compares the utility of clarification with user
cognitive cost, cost-sensitive active learning treats acquisition cost as part
of selection, and the deployment study warns that active-learning gains often
fail to transfer across models and tasks. Prompt 118 does not reproduce their
results. ACR uses retained repeated missing-information findings and a visible
integer expected-value calculation to suggest, but never execute, one bounded
verification action.
