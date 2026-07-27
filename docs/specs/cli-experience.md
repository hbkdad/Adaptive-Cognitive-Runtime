# CLI experience

Prompt 48 makes the installed `acr` command a first-class human and automation
interface while retaining `python -m acr_runtime.cli`.

The primary command groups are:

```text
acr run
acr task
acr memory
acr skills
acr agents
acr models
acr tools
acr benchmark
acr telemetry
acr config
acr doctor
```

Additional governance and diagnostics groups remain available.

## Output modes

Interactive terminals receive dependency-free, human-readable key/value and
list output. Redirected output retains the existing JSON-compatible behavior
for script compatibility. `--json` always forces machine-readable output and
can appear before or after the command. `--verbose` writes safe command and
database diagnostics to stderr, leaving JSON stdout parseable.

```powershell
acr config show
acr task list --limit 20
acr memory summary --json | ConvertFrom-Json
acr --verbose doctor
```

## Dry runs

The global `--dry-run` validates the command line and returns a JSON preview
before opening the runtime, creating a database, calling a provider, writing a
report, or executing a tool. It can appear before or after commands that do not
already have a domain-specific planning flag.

Existing governed planners retain their richer local dry-run contracts:

- `memory consolidate --dry-run`
- `memory gc --dry-run`
- `experience distill --dry-run TRACE_ID`
- `skills generate --dry-run`

Global dry-run output includes `dry_run: true`, `executed: false`, the resolved
command, and validated arguments.

## Design basis

- Python documents `argparse` subcommands as the standard-library mechanism
  for multi-function CLIs:
  <https://docs.python.org/3.12/library/argparse.html#sub-commands>
- The Command Line Interface Guidelines recommend human-focused defaults,
  structured JSON for automation, diagnostics in verbose mode, conventional
  `--json`, and side-effect-free `--dry-run` behavior:
  <https://clig.dev/>
