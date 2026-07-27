# Memory benchmark

Prompt 44 adds a deterministic, offline benchmark for memory selection. It is a
release gate for retrieval correctness and context cost, not a claim about a
language model's answer quality.

## Four paired arms

Every case uses the identical committed synthetic history:

1. `no_memory` selects no historical context.
2. `raw_conversation` supplies the complete history and establishes the token
   baseline.
3. `simple_rag` applies a frozen unscoped lexical ranker, a fixed top-k, and the
   same token budget as ACR, without lifecycle or trust governance.
4. `acr_memory` writes the history through the real SQLite memory store and
   runs the real hybrid retriever with scope, status, temporal, conflict, and
   token-budget behavior.

The required cases cover durable facts, irrelevant facts, temporal changes,
unresolved contradictions, cross-project isolation, failure recall, memory
poisoning, and a 501-record large history.

Run it with:

```powershell
python -m acr_runtime.cli benchmark validate-memory benchmarks/v1/memory.jsonl
python -m acr_runtime.cli benchmark memory benchmarks/v1/memory.jsonl
```

## Scoring

Accuracy is deterministic evidence-selection accuracy:

- all required evidence must be selected;
- stale, cross-scope, or poisoned evidence marked harmful must not be selected;
- a contradiction case passes only when both claims are selected and the
  retriever explicitly reports a conflict.

The report keeps accuracy, context tokens, retrieval precision, retrieval
recall, harmful selections, and conflict detection separate for every case and
arm. Input tokens mean selected historical-context tokens; the common current
query is excluded from all arms. No external network, model, model judge, or
embedding service is used. This makes the suite reproducible and prevents model
variance from hiding memory regressions.

An optional provider-backed benchmark may later reuse these exact histories,
but it must report model answer quality separately from retrieval correctness.

## Research basis

- [LoCoMo (ACL 2024)](https://aclanthology.org/2024.acl-long.747/) separates
  long-conversation QA categories and annotated evidence retrieval.
- [LongMemEval (ICLR 2025)](https://arxiv.org/abs/2410.10813) evaluates
  information extraction, multi-session reasoning, knowledge updates, temporal
  reasoning, and abstention, and reports retrieval separately from final QA.
- [MemoryAgentBench](https://arxiv.org/abs/2507.05257) evaluates retrieval,
  test-time learning, long-range understanding, and selective forgetting under
  controlled memory budgets.
- [AgentPoison (NeurIPS 2024)](https://proceedings.neurips.cc/paper_files/paper/2024/hash/eb113910e9c3f6242541c1652e30dfd6-Abstract-Conference.html)
  motivates explicit poisoned-retrieval and downstream-safety cases.

The committed dataset uses original synthetic facts rather than copying public
benchmark questions, reducing contamination and keeping expected evidence exact.
