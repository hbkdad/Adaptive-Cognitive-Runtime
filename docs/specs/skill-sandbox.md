# Prompt 38: generated-skill sandbox

Generated executable skill checks run only through an explicit sandbox adapter.
The default adapter remains unavailable and fails closed. A Python subprocess,
virtual environment, filtered prompt, or static scan is not treated as process
isolation.

## Docker isolation profile

`DockerSandboxAdapter` accepts only a preinstalled image and uses
`--pull never`. Before execution it resolves the configured image reference to
the local immutable `sha256:` image ID and runs that ID, so a tag cannot change
between inspection and execution.

Every generated-skill container has:

- no network namespace attachment (`--network none`);
- a read-only root filesystem and read-only `/skill` bind mount;
- no writable host mounts;
- bounded, automatically deleted `/tmp` and `/workspace` tmpfs mounts;
- all Linux capabilities dropped;
- `no-new-privileges` and Docker's built-in seccomp profile;
- private default PID, explicit private IPC and cgroup namespaces;
- numeric non-root UID/GID `65532:65532`;
- bounded memory, swap, CPU, PIDs, open files, tmpfs, workspace, and wall time;
- no interactive input, no shell invocation, and no image pull;
- an empty container environment rebuilt with seven non-secret runtime values.

The trusted Docker client receives a small host environment allowlist needed to
reach the configured daemon. Those values are not passed into the container.
The Docker socket, home directory, credential files, project root, and
environment secrets are never mounted.

The writable workspace is an in-memory, size-bounded tmpfs inside the container,
not a host directory. It disappears with the container. The package and root
filesystem remain read-only.

## Timeout and cleanup

Each execution has a unique container name. The wall-clock budget applies to
the entire stage, not independently to every declared command. If the Docker
client times out, ACR issues an exact `docker rm --force <name>` cleanup. This
avoids leaving generated code running after the client process is killed.

## Boundary self-test and audit

Before unit-test code runs, the sandbox executes a deterministic boundary
self-test. It verifies:

- the skill package is present but cannot be modified;
- the container root filesystem cannot be modified;
- the bounded temporary workspace is writable;
- the environment contains only the sandbox allowlist;
- an outbound connection cannot be established.

Every validator sandbox stage retains a content-minimized audit record in
`skill_validation_results`: a unique audit ID, immutable image ID, isolation
profile, resource limits, command hashes, exit codes, cleanup result when
needed, latency, and boundary-self-test result. It does not retain command
output, generated content, host environment values, or credentials.

```powershell
docker pull python:3.11-slim
python -m acr_runtime.cli --db .acr/acr.db skills certify <SKILL_ID> `
  --docker-sandbox `
  --sandbox-image python:3.11-slim `
  --sandbox-timeout 60 `
  --sandbox-memory-mb 256 `
  --sandbox-cpus 0.5 `
  --sandbox-pids 64
```

The image installation is an operator action; validation never pulls code.
Production operators should control image provenance and prefer rootless Docker
or Docker Desktop Enhanced Container Isolation where available. Containers are
a security layer, not a proof that a kernel/runtime escape is impossible.

Scenario tests remain fail-closed until a runnable task harness exists. Prompt
88 adds the fixed six-case generated-skill adversarial harness described in
[adversarial-skills.md](adversarial-skills.md). Prompt 39 adds the separate
secret abstraction; the sandbox does not substitute for secret management.
