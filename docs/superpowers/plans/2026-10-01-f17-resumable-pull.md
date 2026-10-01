# F17 resumable authenticated pull implementation plan

> **For agentic workers:** Use superpowers:subagent-driven-development for the independent transport and catalog tasks; root owns integration and filesystem staging. Complete each task with focused tests and source review.

**Goal:** Implement the complete KER-21 contract with durable operator trust and fail-closed resumable SFTP pulls.

**Architecture:** A read-only typed SSH adapter feeds bounded opaque bytes to a chain coordinator. PullCatalog retains enrollment/history and atomically commits validated F16 imports and anchors. Private staging owns resumable files; native decoding stays in F16 workers.

**Tech Stack:** Locked CPython 3.12.14, uv 0.12.13, AsyncSSH 2.24.0, SQLite FULL/WAL, existing Linux F16 controls.

**Spec:** ../specs/2026-10-01-f17-resumable-pull-design.md

## Global constraints

- Current main baseline f30c4a11626a62ce0fe07f2f6610ec6adb8cfc63; branch ker-21-f17-resumable-pull.
- No transport/enrollment trust from F16 acceptance; no SSH agent, forwarding, shell, writes/pruning or financial execution.
- Unknown host/epoch/history conflicts preserve accepted anchors; later corrections follow publication sequence.
- No native imports decoding in credential-bearing code; use existing independently checked F16 worker results.
- User authorized branch/commit/push/PR and tracker updates; final task In Review, PR unmerged.

## Review focus

- Crash after object publication but before SQL commit must retry the same identity.
- A shorter remote listing cannot reset a retained anchor, including after restoration.
- Partial files replaced by symlinks/FIFOs/hardlinks must never trigger unsafe I/O.
- Epoch approval must preserve old anchors and global checkpoint continuity.
- A successful transport result cannot bypass current F16 validation or restore data conformance.

## Task 1: Shared contracts and pinned dependency

Files: src/scryntic/sync/model.py, tests/sync/test_model.py, pyproject.toml,
uv.lock, scripts/check.sh, scripts/check_package.py, docs/QUALITY.md.

- [x] Write and run failing model validation tests.
- [x] Define Enrollment, Anchor, RemoteEntry, PullLimits, PullResult and ReadOnlyRemote.
- [x] Pin AsyncSSH in analysis; include analysis in gate environment and forbid SSH in collector/base installs.
- [x] Verify exact installed APIs, type checking and dependency metadata.

## Task 2: Authenticated read-only SFTP adapter

Files: src/scryntic/sync/sftp.py, tests/sync/test_sftp.py.
Consumes Enrollment/PullLimits; exposes endpoint plus async entries(relative)
and read(relative, offset, count) -> bytes, async context manager, protected
FileCredentials synchronization capability.

- [x] Failing real SSH server tests for explicit pin/auth and refused mismatch.
- [x] Implement only SFTP read/list with bounded operations and safe generated paths.
- [x] Exercise wrong credentials, unsafe key files, stalled reads, cancellation and secret redaction.
- [x] Review exact connect options and demonstrate no shell, forwarding or writes.

## Task 3: Durable enrollment and atomic anchor catalog

Files: src/scryntic/sync/catalog.py, tests/sync/test_catalog.py;
root coordinates the protected hook change in imports/catalog.py.
Public contracts: PullCatalog.enroll(Enrollment, checkpoint: bytes | None),
enrollment(name) -> Enrollment, anchor(name, epoch=None) -> Anchor,
history(name) -> tuple[bytes, ...], authorize_epoch(name, epoch, checkpoint=None),
accept_next(name, data, incoming: Path, receipt: ClockSample) -> ManifestRef,
backup() -> bytes, restore(data: bytes) -> None.

- [x] Failing enrollment/bootstrap, reopen, atomic rollback and restore tests.
- [x] Persist explicit enrollment and retained manifests in the F16 SQLite connection.
- [x] Add CAS progress in the same transaction as conformance registration.
- [x] Enforce immutable old epochs, explicit transitions, bounded canonical backup and no rollback/conflicts.
- [x] Test plain ImportCatalog acceptance never advances sync state, and backup does not claim data conformance.

## Task 4: Chain discovery and resumable opaque staging

Files: src/scryntic/sync/pull.py, src/scryntic/sync/staging.py,
tests/sync/test_pull.py, tests/sync/test_staging.py.
Public contract: async pull(catalog, name, remote, receipt, limits=None) -> PullResult.

- [x] Failing convergence/correction/history attack tests using actual F11 manifests.
- [x] Discover bounded committed slots and independently compare all retained history.
- [x] Implement bounded per-object downloads/resume, cached reuse, exact hash/length and fail-closed staging.
- [x] Feed only completed opaque objects to accept_next; never skip failed objects.
- [x] Test stale hints, checkpoint bootstrap, old/new epochs and partial/SQL failures.

## Task 5: Qualification and publication

Files: docs/sync/F17_PULL.md, relevant README/component docs and F17 evidence.

- [x] Integrate real SFTP + actual restricted F16 validation; run focused/full normal gates.
- [x] Inspect final diff and independent review; repair confirmed failures.
- [x] Run Sonar, inspect only F17 attributable findings and record actual global gate/coverage limitations.
- [x] Run Codex Security security-diff-scan only origin/main working-tree diff or origin/main..HEAD; validate/fix confirmed F17 findings.
Publication after qualification: commit/push, create the PR against main and leave KER-21 In Review. Do not merge.
