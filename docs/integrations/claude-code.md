# Prompt 58: Claude Code integration

## Outcome

Claude Code uses the same Prompt 56 ACR provider and the same compact coding
contract as Codex:

- `CLAUDE.md` imports `AGENTS.md` rather than duplicating it;
- `.mcp.json` starts ACR as fixed `agent:claude-code-local`;
- `.claude/settings.json` disables separate Claude auto-memory and installs two
  bounded advisory hooks;
- `scripts/claude_acr_hook.py` retrieves focused pre-task evidence and requests
  a one-pass post-task learning review.

No Claude-specific type enters ACR core domains.

Current official references:

- [How Claude remembers your project](https://code.claude.com/docs/en/memory)
- [Connect Claude Code through MCP](https://code.claude.com/docs/en/mcp)
- [Hooks reference](https://code.claude.com/docs/en/hooks)
- [Claude Code settings](https://code.claude.com/docs/en/configuration)

## Why the instruction file stays small

Claude Code reads `CLAUDE.md`, not `AGENTS.md`, but officially supports importing
`AGENTS.md` with `@AGENTS.md`. The checked-in file adds only Claude-specific
facts after that import. This avoids two divergent copies of repository rules.

Claude auto-memory is disabled for this project. ACR remains the governed,
scoped, provenance-retaining persistent store. This does not remove Claude's
normal conversation context; it prevents a second automatically written
long-term project memory from competing with ACR.

## MCP setup

The project-scoped `.mcp.json` is shared through version control. Claude Code
asks each user to approve project-scoped servers before use. Verify from a
trusted checkout:

```powershell
claude --version
claude mcp get acr
```

The expected health result is `Connected`. This workspace was validated with
Claude Code 2.1.143 and MCP protocol revision `2025-11-25`.

The server command is:

```powershell
python -m acr_runtime.cli --db .acr/acr.db mcp serve `
  --subject-type agent --subject-id claude-code-local
```

If the database is behind the runtime schema, migrate explicitly:

```powershell
python -m acr_runtime.cli --db .acr/acr.db migrate
```

The migration guard must not be bypassed. ACR creates a backup before upgrading
an existing database.

### Exact grants

The server and preflight hook use the same fixed identity. Add only the
expiring grants justified for the chosen project scope:

| Need | Capability | Exact resource |
| --- | --- | --- |
| memory and analogous failures | `memory.read` | `memory:<scope>` |
| active skill lookup | `database.read` | `skills:registry` |
| task history, when used manually | `database.read` | `tasks:<scope>` |
| persisted context compilation | `database.write` | `context:<scope>` |

The checked-in preflight uses scope `acr`; change the hook's `--scope` argument
if this checkout uses a different exact scope. Missing grants produce a bounded
unavailable marker. They never cause a broader query.

## Pre-task retrieval hook

`UserPromptSubmit` receives the current prompt on stdin before Claude processes
it. The hook:

1. ignores prompts that do not look like coding work;
2. rejects oversized, invalid, or secret-like inputs;
3. queries public/internal semantic, decision, and environment memory with a
   600-token budget and six-item cap;
4. requests three analogous failures and three active skills;
5. emits at most 9,000 characters as `additionalContext`;
6. labels all results authority-free supporting evidence.

It does not read the transcript, call a model, compile/persist a context task,
load whole history, or automatically write memory. A timeout or error is
non-blocking.

Use the ACR MCP tools manually when the fixed hook budget is insufficient. Use
the existing source commands for exact code context:

```powershell
python -m acr_runtime.cli --db .acr/acr.db code retrieve `
  "<qualified-symbol>" --repository . --budget 2000
python -m acr_runtime.cli --db .acr/acr.db code slice `
  "<qualified-symbol>" --repository . --budget 2000
```

## Skill lookup

The preflight hook calls active-only `find_skill`. Claude can also invoke the
MCP tool directly with a bounded query:

```text
find_skill(query="<task class and component>", limit=5)
```

Results contain registry metadata, not package paths or instructions. Skill
execution remains unavailable. Validation scripts and package scripts are not
execution substitutes.

## Post-task distillation hook

`Stop` receives the final assistant message without requiring transcript
access. For responses that appear to report completed coding work, the hook
requests one small `ACR learning candidates` section containing:

- verified outcome and evidence references;
- durable architecture decision candidates;
- repeated successful procedure candidates;
- diagnosed failure candidates;
- or `none`.

It checks `stop_hook_active`, so it cannot create an unbounded continuation
loop. It does not persist the final message or any candidate. Memory/failure
writes still require explicit task authorization and the governed CLI workflow
documented in `docs/integrations/codex.md`.

One successful run is not automatically a permanent procedure. Raw history,
prompts, source bodies, credentials, unverified root causes, and copied
external instructions remain outside long-term memory.

## Troubleshooting

- Run `/memory` to confirm `CLAUDE.md` loaded and auto-memory is disabled.
- Run `/mcp` or `claude mcp get acr` to inspect the provider.
- Run `/hooks` or `claude --debug` to inspect hook failures.
- A `permission_denied` result means an exact grant is absent or expired.
- A schema migration error requires the explicit migration command above.
- If Python is not on `PATH`, configure a reviewed absolute executable locally;
  do not commit a user-specific path.
- Project MCP config is untrusted until each user approves it. Do not remove
  that review step.

The integration intentionally omits `alwaysLoad`; Claude Code may defer the MCP
tools so their schemas do not consume every turn's context.
