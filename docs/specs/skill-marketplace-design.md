# Prompt 110: skill marketplace design

Status: design only. This document does not authorize or implement a remote
service, publisher account, signing key, network client, database migration,
download, dependency installation, trust decision, or skill activation.

## Decision

Use a two-layer distribution model:

1. a portable, content-addressed ACR skill bundle that can be copied and
   verified entirely offline; and
2. an optional remote catalog and blob transport that improves discovery but
   has no authority to trust, install, certify, or activate a skill.

The bundle is the product boundary. A marketplace is only one transport and
discovery surface for that bundle. Local installation remains a first-class
path and must apply the same verification policy as remote acquisition.

## Goals and non-goals

The design must:

- distribute immutable ACR Skill Format v1 packages by exact digest;
- identify publisher claims without treating identity as correctness;
- bind signatures, permissions, dependencies, tests, benchmarks, software
  inventory, and security claims to the same immutable package;
- support offline hash and signature verification;
- preserve Prompt 109 scanning, permission mapping, sandboxing, and quarantine;
- distinguish publisher claims from ACR-reproduced evidence;
- tolerate catalog loss without preventing installation of a retained bundle;
- resist substitution, rollback, freeze, dependency-confusion, typosquatting,
  and popularity-gaming attacks.

It does not design social feeds, payments, advertising, ranking optimization,
automatic dependency installation, automatic updates, remote code execution,
or production deployment.

## Trust model

Trust is a vector, not one marketplace score:

| Dimension | Meaning | Can establish |
| --- | --- | --- |
| Content digest | Exact bytes are unchanged | Integrity only |
| Publisher signature | A configured identity signed the digest | Authenticity only |
| Build provenance | Artifact was produced by an expected source and builder | Supply-chain evidence |
| Static and sandbox validation | The package passed named checks under a named policy | Test evidence |
| Reproduced benchmark | ACR reran fixed cases locally | Local quality evidence |
| Operational history | This exact digest succeeded under bounded task classes | Local reliability evidence |
| Popularity | People viewed, downloaded, or starred an entry | Discovery signal only |

A valid signature is not a safety certificate. A verified publisher is not a
trusted skill. Catalog ownership, download count, rating, sponsorship, and
GitHub origin never satisfy an activation gate.

The consumer defines publisher-identity and provenance expectations, following
the verification principle in [SLSA 1.2](https://slsa.dev/spec/v1.2/verifying-artifacts):
authenticate the evidence, then compare it with explicit expectations. ACR
must never accept a signature merely because it is cryptographically valid.

## Distributable bundle

The offline bundle is a deterministic archive with this logical layout:

```text
acr-skill-bundle/
  distribution.json
  package/
    SKILL.yaml
    instructions.md
    history.jsonl
    examples/
    tests/
    scripts/
    assets/
  evidence/
    tests.json
    benchmarks.json
    security.json
    sbom.spdx.json
    provenance.intoto.jsonl
  signatures/
    package.sigstore.json
```

`distribution.json` is strict JSON with unknown-field rejection and contains:

- `schema_version` and `artifact_type`;
- exact skill ID, version, ACR format version, and package SHA-256 digest;
- archive generation time and optional expiry;
- publisher subject, identity issuer, and identity-policy reference;
- declared permissions, tools, models, and exact dependencies;
- digest descriptors for every evidence object;
- signature and provenance format identifiers;
- yanked/revoked state references, when known;
- minimum compatible ACR version.

Dependencies use `skill-id@version#sha256:digest`. Ranges, mutable tags, and
name-only dependencies are insufficient for an install plan. An offline bundle
may include a lock set of dependency bundles, each independently verified.
Verification never downloads or installs a missing dependency.

The package digest is computed over the canonical ACR package, not a catalog
record or mutable archive timestamp. Every evidence document names that same
digest as its subject. Test, benchmark, and security documents are immutable
claims; they do not become verified until ACR validates their producer and,
where required, reproduces their result.

The SBOM uses the current [SPDX specification](https://spdx.dev/use/specifications/)
rather than an ACR-specific dependency inventory. Absence of a known
vulnerability is time-bounded metadata, not proof of safety.

## Signatures and publisher identity

Do not invent cryptography. The preferred interoperable envelope is a
[Sigstore bundle](https://docs.sigstore.dev/cosign/verifying/verify/) bound to
the package digest. A verifier checks:

- the artifact digest;
- signature and certificate chain;
- exact certificate identity and issuer expectations;
- signed timestamp and transparency-log inclusion proof when policy requires;
- signing time against certificate validity and publisher-revocation state;
- the subject digest in attached provenance and evidence.

Sigstore bundles support offline verification when the necessary verification
material is retained. Private installations may define an enterprise or
offline-key profile, but it must have an explicit trust root, rotation,
revocation, threshold, and expiry policy. Raw public keys embedded in the same
untrusted bundle are not trust roots.

Publisher identity is versioned. Identity transfer, key rotation, and account
recovery create auditable events and never rewrite old releases. A compromised
publisher can revoke future trust without deleting the historical artifact
needed for incident analysis.

## Optional remote transport

If remote distribution is later authorized, model a skill as a generic
content-addressed artifact. The
[OCI image manifest artifact guidance](https://specs.opencontainers.org/image-spec/manifest/)
already supports typed artifacts, digest-addressed blobs, and associated
metadata through subject descriptors. The
[OCI Distribution Specification](https://specs.opencontainers.org/distribution-spec/?v=v1.1.1)
provides a content-type-agnostic push/pull protocol and referrers API.

OCI is a candidate transport, not a trust model and not a mandatory local
dependency. The normative ACR bundle remains independently exportable and
verifiable without a registry.

Conceptual remote components are:

- catalog index: bounded metadata and immutable digest references;
- blob store: package and evidence objects addressed by digest;
- publisher gateway: authentication, authorization, quotas, and append-only
  publication events;
- transparency/revocation monitor: signature, identity, yank, and compromise
  signals;
- read-only discovery API: exact lookup and bounded search;
- client verifier: local policy enforcement before Prompt 109 import.

The catalog never returns “trusted=true.” It returns claims and verification
material. Search rank is kept separate from verification status.

## Required records

Each immutable release exposes:

| Record | Required content |
| --- | --- |
| Manifest | Exact package identity, digest, compatibility, and evidence descriptors |
| Version | Semantic version plus immutable digest; versions cannot be republished |
| Publisher | Subject, issuer, policy, status, and rotation/revocation history |
| Hash/signature | SHA-256 digest and verification bundle bound to it |
| Permissions | Exact requested capabilities and risk classification |
| Dependencies | Exact ID, version, digest, optionality, and resolution evidence |
| Tests | Harness version, cases, environment, outcomes, and subject digest |
| Benchmark data | Dataset/harness digests, repetitions, metrics, raw aggregate evidence, and provenance |
| Security metadata | Scan policy, findings, SBOM, advisories, review status, and timestamps |

Publisher-supplied test and benchmark results are labeled
`publisher_asserted`. Marketplace-operated results are labeled
`marketplace_observed`. Only successful ACR runs over the exact digest are
`locally_reproduced`. These labels cannot be collapsed into one boolean.

## Installation and update flow

```text
discover or select local bundle
  -> acquire exact bytes
  -> enforce size and archive limits
  -> verify digest
  -> verify signature against local identity expectations
  -> verify provenance and evidence subjects
  -> resolve an exact dependency plan without installing
  -> inspect permission delta and conflicts
  -> run Prompt 109 parse, scan, normalize, sandbox, and tests
  -> register as quarantined
  -> run retained certification
  -> require explicit promotion authority
```

An update is a new immutable version and digest. The client compares it with
retained accepted versions, rejects rollback, respects explicit yanks without
silently deleting installed content, and requires fresh metadata before
claiming that no security notice exists. No background update may widen
permissions, add dependencies, or activate itself.

Local installation starts at “select local bundle” and uses the same remaining
steps. Unsigned local bundles may be inspected under an explicit development
policy, but they remain unverified and quarantined.

## Security and abuse controls

- Namespace ownership is authenticated, but similar-name warnings and exact
  publisher display remain necessary for typosquatting.
- Publication is append-only by digest; deleting a listing does not erase audit
  evidence.
- All uploads, manifests, archives, evidence sets, and dependency graphs have
  count, depth, and byte limits.
- Archive extraction rejects absolute paths, traversal, links, devices, sparse
  expansion, duplicate normalized names, and case-collision aliases.
- Catalog APIs require quotas, rate limiting, pagination, bounded queries,
  abuse reporting, and content-minimized logs.
- Publisher tokens and signing material never enter skill packages, build
  output, benchmark data, or ACR telemetry.
- Revocation, yank, malware, vulnerability, and identity-compromise events are
  distinct, signed or administrator-attributed, and append-only.
- Dependency resolution detects cycles, namespace substitution, digest
  mismatch, conflicting permissions, and unavailable exact releases.
- Benchmarks use sealed cases where practical and disclose datasets and
  harness digests to reduce gaming.

## Rollout gates

No implementation should begin until all of these are approved:

1. bundle schema and canonical digest algorithm;
2. publisher identity, recovery, rotation, and revocation policy;
3. exact signature and offline-verification profile;
4. dependency lock and conflict semantics;
5. permission-delta review contract;
6. benchmark and security-evidence schemas;
7. archive extraction threat model and adversarial suite;
8. local-only reference verifier acceptance tests;
9. privacy, moderation, retention, and incident-response policy;
10. network production-readiness blockers from Prompt 106, including rate
    limiting and production observation.

Recommended sequencing:

- Phase 0: design review and frozen test vectors only;
- Phase 1: local bundle pack/inspect/verify prototype, still no remote service;
- Phase 2: publisher signing and offline verification rehearsal;
- Phase 3: read-only private catalog behind production security gates;
- Phase 4: controlled publication and revocation;
- Phase 5: public discovery only after abuse, privacy, operations, and incident
  response are rehearsed.

## Open decisions

- Whether OCI is adopted as the first remote transport or only an export
  adapter.
- Which publisher identity issuers and offline roots are accepted.
- Whether a threshold signature is mandatory for privileged publishers.
- Exact retention and privacy rules for publisher identity and audit events.
- Who is authorized to mark malware, yank a release, or revoke a publisher.
- Which benchmark classes are reproducible enough to display.
- How namespace disputes and publisher transfers are governed.

Until those decisions and rollout gates are resolved, Prompt 110 remains a
reviewed architecture specification and must not be interpreted as permission
to build or operate a marketplace.
