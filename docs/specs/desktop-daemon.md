# Prompt 108: desktop daemon

## Commands

```powershell
python -m acr_runtime.cli --db .acr/acr.db daemon start
python -m acr_runtime.cli --db .acr/acr.db daemon status
python -m acr_runtime.cli --db .acr/acr.db daemon stop
```

`start` launches the existing `serve` command as a detached background process.
On Windows the process uses a new process group, detached-process flag, hidden
startup window, null standard input, and an append-only `.acr/daemon.log`.
Linux and macOS use a new session; native service integration is deferred.

The default endpoint is `127.0.0.1:8000`. Override the loopback port with
`--port`. A non-loopback host additionally requires `--allow-network` and an
`ACR_API_TOKEN` inherited by the child. Zero-cloud configuration refuses every
non-loopback daemon host.

## Lifecycle safety

`.acr/daemon.json` is a strict, atomic, versioned state file containing only:

- canonical instance UUID;
- PID;
- IP host and port;
- timezone-aware start time;
- database path.

No API token is persisted. State and log symbolic links are rejected.

Start waits for `/health` to return both healthy database status and the exact
instance UUID. Status requires the PID to be live and the UUID to match. Stop
checks the same identity before signaling, so stale or reused PIDs are never
terminated. Dead stale state is removed safely; an unverified live PID fails
closed for operator inspection.

## Windows rehearsal

Prompt 108 was rehearsed against `.acr/acr.db` on port 8765:

1. start returned `running`, PID 23392, and `identity_verified=true`;
2. status returned the same PID and instance UUID;
3. stop returned `stopped`;
4. final status returned `stopped` with no state file.

This is a local lifecycle rehearsal, not Windows Service installation or a
production availability claim. The complete deterministic gate passed 743
tests across all six tiers, and the architecture guard reported zero
violations.
