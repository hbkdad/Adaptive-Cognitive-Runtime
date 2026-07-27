# Prompt 39: secret management

ACR represents credentials as opaque `provider:key` references. The reference
key is never written to runtime tables: authorization and audit records use
`secret:<sha256(reference)>` and the provider name only.

## Providers

- `env` reads a bounded uppercase environment-variable name.
- `keyring` uses the optional Python `keyring` adapter and the operating
  system's credential store. Install it with `pip install -e ".[secrets]"`.
- `external` is an injectable callback boundary for a separately configured
  vault. No remote vault SDK is selected or installed by default.

Resolution is default-deny. A task, agent, or skill needs an exact, unexpired
`credential.use` capability grant whose resource scope equals the reference's
hash-derived scope. Provider lookup happens only after authorization.

## Value lifetime and audit

A successful lookup creates a one-use `SecretLease`. Its representation exposes
no value; `use(callback)` closes and best-effort zeroes its mutable byte buffer
after the callback, including on failure. Returning the leased value from the
callback is rejected. Python and downstream libraries may still create
immutable copies, so this is lifetime minimization rather than a claim of
perfect process-memory erasure.

`secret_access_events` in schema 33 retains only the reference hash, provider,
subject, decision, exact capability-decision ID, and timestamp. Provider
exceptions are replaced with content-free errors. No value, reference key,
prompt, or provider error text enters the audit table.

## Storage boundaries

High-confidence credential shapes and labeled secret assignments are rejected
at memory, experience, failure-environment, task-prompt, chat, embedding, and
skill-package boundaries. Imported content containing secret material is
quarantined by hash instead of stored as raw context. Telemetry recursively
redacts secret-named fields and detected formats before serialization.

Detection is defense in depth, not the source of authorization and not a
complete data-loss-prevention system.

## Git boundary

The repository hook scans staged blob contents rather than the working tree:

```powershell
git config core.hooksPath .githooks
python -m acr_runtime.cli secrets scan-staged --repository .
```

Findings contain file path, detector type, and blob hash only. The scanner does
not print matched values. Repository hosting secret scanning remains a useful
independent control.

## Operator flow

1. Put a value in an environment variable, OS credential store, or configured
   external vault.
2. Use the `SecretReference.resource_scope` in an exact `credential.use` grant.
3. Resolve for the same governed subject.
4. Consume the lease once inside the smallest possible callback.
5. Inspect the value-free audit event if needed.

The CLI `secrets resolve` verifies availability and permission without printing
or consuming the credential for another operation. Integrations should use the
Python API so the credential is passed directly to the intended provider call.
