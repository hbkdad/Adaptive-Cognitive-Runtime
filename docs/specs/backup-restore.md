# Prompt 80: backup, verification, and restore

ACR backups use a fixed, versioned ZIP container named `acr-backup-v1`. The
archive is a recovery artifact, not a general-purpose filesystem packer. Its
source allowlist is:

- one coherent SQLite snapshot created with the SQLite backup API;
- files beneath the configured skills directory;
- public runtime settings generated from typed configuration;
- files beneath the selected benchmarks directory;
- learning history retained inside the SQLite snapshot, with per-table counts
  repeated in the manifest for verification.

No other state directory, environment file, keyring, external secret store,
credential value, cache directory, or arbitrary caller-selected source tree is
captured.

## Commands

```powershell
python -m acr_runtime.cli --db .acr/acr.db backup `
  backups/acr-2026-07-29.acrb --benchmarks-dir benchmarks

python -m acr_runtime.cli verify-backup backups/acr-2026-07-29.acrb

python -m acr_runtime.cli restore `
  backups/acr-2026-07-29.acrb recovered/acr-2026-07-29
```

Backup refuses to overwrite an existing archive. Restore requires a target
directory that does not exist, writes into a same-parent staging directory,
rechecks hashes while writing, and atomically renames the completed staging
tree. It never replaces the live database or silently activates restored
configuration. The restored layout is:

```text
TARGET/
  acr.db
  skills/
  benchmarks/
  configuration.json
  backup-manifest.json
```

Point `ACR_DATABASE`, `ACR_STATE_DIR`, and `ACR_SKILLS_DIR` at the recovered
layout only after review. An older compatible schema is restored unchanged and
still requires the explicit `acr migrate` workflow. A schema newer than the
running code is verifiable but not restorable.

## Integrity and compatibility

The manifest fixes every non-manifest path, component, uncompressed size, and
SHA-256 digest. Verification rejects duplicate, absolute, parent-traversing,
backslash, directory, encrypted, oversized, unexpected-component, and symlink
members. It does not call `extractall`; each allowlisted member is streamed to
an explicit target path.

`verify-backup` also:

- validates the exact format version and required components;
- streams and checks every entry hash and size;
- runs SQLite `PRAGMA quick_check`;
- compares the database schema to the manifest and current runtime;
- compares learning-history table counts with the manifest;
- repeats the secret scan.

The printed whole-archive SHA-256 should be retained out of band. Per-entry
hashes and the printed archive hash detect corruption only when the expected
hash or manifest is independently trusted; this format is not digitally signed
and does not claim archive authenticity.

## Secret boundary

Environment values, dotenv files, keyring values, external-store values, and
API tokens are never requested. Secret-like filenames in skill or benchmark
trees cause backup to fail rather than silently copying them. Configuration,
skill, and benchmark text use both high-confidence and labeled-secret
detection. SQLite text columns use high-confidence detection because the
database intentionally retains opaque labels such as `secret:<reference_hash>`;
those references do not contain secret values.

Backup aborts before publishing the destination if a secret pattern is found.
Restore repeats the same scan after archive hash verification. This is a
defense-in-depth heuristic, not proof that arbitrary data is non-sensitive.
The archive can still contain personal or confidential governed memory and
must be protected according to its highest data classification.

## Operational boundary

`verify-backup` proves structural consistency, hashes, schema compatibility,
and SQLite integrity. It does not prove that an archive is malware-free, that
every external dependency is available, or that a full application recovery
exercise succeeded. Store copies offline or separately, retain the printed
hash independently, and test restoration into a non-production location.
