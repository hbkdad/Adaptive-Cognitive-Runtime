# Prompt 107: zero-cloud deployment

## Contract

Set `ACR_DEPLOYMENT_PROFILE=zero-cloud` to activate the fully local ACR
profile. It is an enforceable policy over the existing runtime:

| Boundary | Zero-cloud behavior |
| --- | --- |
| Memory, audit, telemetry | Local SQLite only |
| Skill registry | Local filesystem |
| Model provider | None or Ollama |
| Ollama endpoint | Root loopback HTTP or HTTPS URL |
| Embeddings | Optional local Ollama embedding model |
| Cloud APIs | Not required; configured cloud providers are rejected |
| External telemetry | No exporter; SQLite remains the only destination |

The policy is applied during typed settings construction, before the runtime
opens storage or dispatches a provider. Remote, credential-bearing,
path-bearing, malformed-port, query-bearing, and fragment-bearing Ollama URLs
fail closed.

The Ollama provider filters model names ending in `:cloud` or `-cloud` in this
profile. This matters because Ollama can proxy a cloud model through its local
API. Operators should additionally configure `OLLAMA_NO_CLOUD=1` and restart
Ollama; ACR cannot inspect another process's inherited environment or logs.

## Windows rehearsal

```powershell
$env:ACR_DEPLOYMENT_PROFILE = "zero-cloud"
$env:ACR_PROVIDER = "ollama"
$env:ACR_OLLAMA_URL = "http://127.0.0.1:11434"

python -m acr_runtime.cli config show
python -m acr_runtime.cli --json doctor
python -m acr_runtime.cli run "Return exactly the word LOCAL." `
  --model qwen2.5-coder:1.5b `
  --max-output-tokens 8 `
  --max-input-tokens 128 `
  --max-model-calls 1 `
  --max-tool-calls 0 `
  --max-agents 1 `
  --max-cost 0 `
  --max-duration-seconds 120
```

The Prompt 107 rehearsal passed SQLite schema 63/63, FTS5, the local
filesystem, the deployment-profile check, six locally listed models, and a
bounded `qwen2.5-coder:1.5b` call returning `LOCAL`. The complete deterministic
gate passed 739 tests across all six tiers; the architecture guard reported
zero violations.

## Core behavior without a model

Omit `ACR_PROVIDER`. Memory writes and retrieval, filesystem skill
registration and routing, context compilation, task records, audits, telemetry,
backup, migrations, and deterministic tests remain available. Model-generated
answers are unavailable until a local Ollama model is selected.

## Unavailable without external services

The profile reports these capabilities as unavailable:

- cloud model APIs;
- remote embedding APIs;
- external telemetry export;
- hosted state synchronization;
- automated external research fetching.

Local source ingestion and retained research records still work; the profile
does not fetch external source content. Local backup archives remain available,
but off-device transfer is an operator action outside ACR.
