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
