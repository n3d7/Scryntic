# Security Policy

## Current scope and versions

Scryntic is a Linux/Python market-data and analysis project under development.
The current implementation covers F01–F11, including a deterministic fake-source /
fake-provider demonstration, local ingestion, normalization, publication and
dataset/forecast artifacts. It is not the completed Foundation v1 deployment.
There are no published GitHub releases or established supported-release/backport
matrix. Please identify the affected commit and reproduce against current `main`
where possible. Runtime and dependency profiles are documented in
[QUALITY.md](docs/QUALITY.md); runtime compatibility is not a security-support SLA.

## Reporting vulnerabilities

Use GitHub's enabled [private vulnerability reporting](https://github.com/n3d7/Scryntic/security/advisories/new)
for confidential reports to repository maintainers. Include the revision,
environment, affected boundary, prerequisites, impact and a minimal reproduction
using synthetic data. Do not include real credentials, account data or sensitive
payloads, or publish exploit details in a public issue. The project has no
published response-time or coordinated-disclosure SLA.

## Trust and authority boundaries

The local operator, installed code/dependencies and protected installation roots
are trusted. Ownership/mode checks and advisory locks constrain cooperating local
processes; they do not defend against root, malicious code running as the owning
UID, or a fully compromised producer.

Market payloads, source metadata/timestamps, archive descriptors and provider
results are untrusted claims. Source/provider contracts grant no financial
authority: Scryntic has no order placement, cancellation, withdrawal, account
management or live trading execution capability. Models must not receive exchange
credentials, credential handles or execution authority. Current providers are
locally selected fakes; provider descriptors do not authorize downloaded code,
real model loading or cloud disclosure. See [CONTRACTS.md](docs/CONTRACTS.md).

## Implemented controls

- **Configuration and credentials:** bounded, strict configuration validation;
  allowlisted capabilities and file-backed credential references; descriptor-relative
  no-follow opens with owner/mode/type checks. Public collection resolves no
  credentials; disabled capabilities cannot bind them. Secrets are scoped to the
  owning adapter, excluded from ordinary configuration, redacted in wrapper
  representations and non-pickleable. Buffer clearing does not guarantee erasure
  of Python/native copies; raw payloads are not automatically redacted.
  See [CONFIGURATION.md](docs/CONFIGURATION.md).
- **Supply chain:** pinned runtime/tools/backend, locked dependency profiles and
  hash-constrained builds; lint, typing, tests, package-integrity checks and
  known-vulnerability audits. CI actions are pinned to full commit SHAs, with
  read-only repository permissions and no persisted checkout credentials.
  Provisioning executes trusted tooling; lock hashes and passing audits do not
  prove dependencies safe. See [QUALITY.md](docs/QUALITY.md).
- **Inputs:** bounded envelopes, queues and archive operations; explicit local
  `ArchiveLimits`; typed normalization outcomes/rejections and validated provider
  result correlation. Limits do not establish source truth or native-parser
  isolation. Real exchange adapters and feed recovery are not implemented yet.
  See [NORMALIZATION.md](docs/NORMALIZATION.md).
- **Durable ingestion:** one writer/connection per private state directory,
  verified SQLite WAL/FULL settings and transactional acceptance. A record is
  acknowledged only after commit; original bytes, identity and hash are retained.
  Queue admission is not durable acceptance, and messages lost before commit are
  not recoverable by this guarantee. See [INGESTION.md](docs/INGESTION.md).
- **Publication and recovery:** immutable no-replace object/manifest installation
  with file/directory synchronization, canonical hash validation, reserved input
  identities and exact prepared-manifest bytes. Manifest durability precedes the
  catalog/checkpoint transaction. Recovery converges on consistent evidence and
  fails closed on conflicting, missing committed or unexplained history; it must
  not rewrite history, reuse a sequence for different bytes, fabricate progress
  or delete uncertain inputs/orphans. Production raw storage remains
  `parquet-pyarrow-v1`, with a prior F07 fixture tested for readability. Codec
  implementations are selected locally; archived identifiers never select
  executable imports/downloads. See [F11 recovery qualification](docs/publication/f11-recovery.md)
  and [F10 operating envelope](docs/performance/f10-operating-envelope.md).

## Integrity is not truth

Hashes and chained manifests detect altered or inconsistent bytes relative to
retained history. They do not prove market correctness, completeness, truthful
timestamps or numerical/model accuracy. A compromised producer can append false
data, withhold data or present another history to a client without prior evidence.
Current local checks are not authenticated workstation synchronization or an
independent trust anchor. Immutable/read-only files are not tamper-proof against
their owner, and retained metadata cannot reconstruct missing objects.

## Important limits and unfinished controls

Hostile-import/native-decoder isolation (F16), authenticated workstation
synchronization and retained trust anchors (F17), real-model admission/worker
isolation (F20–F21), and least-privilege deployment profiles (F27) are not current
guarantees. Configuration switches do not implement these services. Native
Parquet decoding currently runs in-process; do not treat arbitrary imported files
as safely sandboxed inputs.

F11 tests cover deterministic process crashes and injected I/O failures, not
physical power loss, dishonest storage caches or backup restoration. Recovery
scans retained history; 90-day recovery-volume qualification, pruning and disaster
recovery remain F29 work. F10 measurements apply to their tested environment/tree,
not universal security or availability limits. Loss of external feeds, exhausted
storage and compromised hosts are not solved by hashing or immutable publication.
