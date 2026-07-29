# Adversarial generated-skill tests

Prompt 88 makes the validator's `adversarial_tests` stage runnable in the
existing Docker sandbox. It uses a fixed runtime-owned probe; generated package
text and caller-supplied case strings never become shell or Docker arguments.

## Closed attack set

The probe attempts and must prevent:

| Attack | Enforced boundary |
| --- | --- |
| Credential exfiltration | Host secrets are excluded from the container environment and common credential paths are not mounted. |
| Unrelated-file modification | The root filesystem and `/skill` package mount are read-only; no writable host mount exists. |
| Permission grants | The process is non-root, has zero effective Linux capabilities, has `no-new-privileges`, and cannot see the runtime database. Manifest permissions are separately default-denied before execution. |
| Test disabling | A write probe under `/skill/tests` must fail. |
| Telemetry hiding | A write probe under `/skill` must fail and the host telemetry database is not mounted. |
| Unauthorized-host contact | `--network none` leaves no external network path and an outbound connection probe must fail. |

The exact six-case tuple is closed. A missing, reordered, additional, or
command-shaped case blocks the stage before Docker is inspected. Probe stdout
and stderr are discarded; retained evidence contains only isolation metadata,
command hashes, exit codes, and per-case `prevented` or `not_proven` status.

Any failed assertion fails the stage and therefore prevents validation
promotion. An unavailable preinstalled image remains a blocked result. The
validator never pulls an image.

## Defense in depth

The adversarial probe complements rather than replaces format validation,
static security scanning, manifest permission analysis, exact capability
grants, independent evaluation, and benchmark comparison. This follows
[OWASP AI Agent Security guidance](https://cheatsheetseries.owasp.org/cheatsheets/AI_Agent_Security_Cheat_Sheet.html)
to apply least privilege, sandbox arbitrary code, preserve monitoring, and run
structured adversarial testing.

The isolation flags use Docker's documented
[`docker container run` security controls](https://docs.docker.com/reference/cli/docker/container/run)
and the [`none` network driver](https://docs.docker.com/engine/network/drivers/none/).
As before, operators should prefer a rootless or otherwise hardened Docker
daemon because container isolation is one security layer, not a proof against
runtime vulnerabilities.
