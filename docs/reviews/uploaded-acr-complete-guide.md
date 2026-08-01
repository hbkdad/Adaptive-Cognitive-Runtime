# Uploaded ACR complete-guide comparison

## Status

The uploaded text is a useful learning and product checklist, but it describes
a different implementation identity. Its command examples use `uv run acr` and
name `src/acr/cli.py`; this repository's executable boundary is
`python -m acr_runtime.cli`, generated from `acr_runtime/cli.py`. Commands in
the uploaded text must therefore be verified individually rather than treated
as current documentation for this codebase.

## Capability map

| Guide area | This repository | Decision |
| --- | --- | --- |
| Setup wizard | No interactive credential-writing wizard. Configuration is explicit and inspectable. | Do not add until a secret-safe, non-destructive setup contract is designed. |
| Doctor | `doctor` verifies Python, writable state, SQLite integrity, schema, FTS5, provider configuration, Ollama, and skills. | Already present. |
| Version | No standalone `version` command. | Low-value convenience candidate; package metadata must become authoritative first. |
| Zero-config first run | `demo` is deterministic and local; `run` intentionally requires an installed Ollama model. | Preserve the distinction instead of presenting mock output as a real model run. |
| Task explanation | `explain` replays retained decision evidence. | Already present under a stricter evidence contract. |
| Dashboard | `serve` exposes the loopback API; the control center is a separate local application. | Present as two layers by architectural decision. |
| Context and memory | Compile, scoped retrieval, temporal history, lifecycle, calibration, deduplication, and GC are available. | Existing implementation is broader. |
| Skills and agents | Validation, quarantine, evidence, evolution, routing, agent specs, factory plans, and topology learning are available. | Existing implementation is broader and more conservative. |
| Models | Local discovery, benchmark, routing, escalation, reasoning control, and multi-model workflow evidence are available. | Local Ollama is real; cloud-provider claims from the guide do not apply. |
| Tools and plugins | Default-deny tools, exposure, invocation, capability grants, and declarative plugins are available. | Existing implementation is permission-governed. |
| Learning and improvement | Experience distillation, atomic learning, bounded improvement, evaluation, and regression evidence are available. | Prompt 100 remains gated on varied real usage. |
| Safe mode, backup, MCP | Persistent containment, verified backup/restore, and a fixed local MCP catalog are available. | Already present. |

## Verified learning result

The comparison prompted a real Windows/Ollama smoke test. It found that the
`run --environment` option accepted an undocumented string even though the
task boundary required a JSON object. Invalid input consequently reached
`json.loads` and escaped as a traceback.

The CLI now:

- documents `--environment` as a bounded JSON object;
- canonicalizes valid object input;
- rejects malformed, non-object, and oversized input during argument parsing;
- creates no database and starts no model call for invalid input.

After the fix, `qwen2.5-coder:1.5b` completed the bounded request `Return
exactly: ACR local execution works.` through the real ACR execution path.

## Recommended next use

Use this guide as a gap checklist, not as a replacement architecture. The next
candidate should be selected only when repository evidence shows a repeated
learning or operational cost. An interactive setup wizard, cloud adapters, and
automatic proposal application are specifically not authorized by the uploaded
text alone.
