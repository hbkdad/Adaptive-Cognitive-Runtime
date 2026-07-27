# Prompt 25: costed temporary Agent Factory

The Agent Factory is a deterministic topology planner. Given a strict request,
it evaluates the permitted shapes and retains the complete decision evidence:

- one agent;
- focused workers plus a synthesizer;
- independent parallel workers;
- a specialist plus an independent critic; and
- researchers plus a synthesizer.

It creates proposed temporary `AgentSpec` values only. It does not register,
spawn, schedule, or execute workers, and it never creates task rows.

## Minimum justified team

Single-agent work is the baseline. Other shapes are considered only when the
request supports them: multiple workstreams for multi-agent work, explicit
parallelizability for parallel workers, critique need for specialist–critic,
and broad research plus synthesis need for researchers–synthesizer.
When multiple workstreams explicitly require synthesis, unsynthesized parallel
and specialist–critic shapes fail closed. A requested independent critique
similarly rejects multi-worker shapes without a critic.

Every candidate records:

- expected quality gain;
- parallelism benefit;
- coordination overhead;
- additional token cost;
- estimated total tokens, money, and wall time;
- net benefit;
- budget feasibility; and
- explicit rejection reasons.

The formulas are fixed and inspectable in `AgentFactory._estimate()`. A
multi-agent candidate must provide at least a 0.05 net quality gain, positive
net benefit, and fit the agent, token, money, and time limits. Selection then
uses the fewest workers; net benefit and topology name break ties. If no
alternative qualifies, the one-agent baseline wins. The request itself is
rejected when even that baseline exceeds a budget.

These estimates are conservative planning heuristics, not learned promises.
Prompt 26 may use retained outcomes to learn topology decisions, but must not
silently reinterpret Prompt 25 history.

## Scoped temporary workers

Each proposed worker gets:

- only its assigned task and memory scopes;
- a proportional share of total budgets;
- the request's explicit tool and permission allowlists;
- only exact active skills applicable to its task scope;
- bounded communication appropriate to the topology; and
- the complete Prompt 24 termination and verification contract.

Independent parallel workers cannot communicate. Specialists and critics may
communicate only with one another. Worker–synthesizer teams use manager-only
worker links and an explicit synthesizer allowlist.

## CLI

```powershell
python -m acr_runtime.cli --db .acr/acr.db agents factory-plan `
  examples/agent-factory/research-plan.json
python -m acr_runtime.cli --db .acr/acr.db agents factory-report <PLAN_ID>
```

The report is a retained proposal. A later, separately governed execution layer
must revalidate costs, dependencies, permissions, and context before spawning.
