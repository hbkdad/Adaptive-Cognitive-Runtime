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
