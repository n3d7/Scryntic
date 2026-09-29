# F11 publication and chain recovery qualification

F11 / KER-15 strengthens the merged F07 state machine rather than replacing it.
Baseline: GitHub main `b4c6de4149b6ba90d0795d47868c9fbffed8a447`; F07 design
`docs/superpowers/specs/2026-09-23-f07-immutable-archive-design.md`; approved
ARCHITECTURE.md publication contract; F10 operating-envelope report
`docs/performance/f10-operating-envelope.md`. Production raw codec remains
`parquet-pyarrow-v1`; no new production decoder or archive API is introduced.

## State and authorities

| Durable evidence | Recovery action | Forbidden action |
| --- | --- | --- |
| No reservation and no unexplained external history | No recovery work; ordinary selection may reserve the next eligible prefix. | Inventing a checkpoint or adopting unknown manifests. |
| Reserved exact ordered inputs, no prepared bytes | Reread the same F05/F06 prefix and fingerprints, seal it, prepare exact bytes. | Shrinking/skipping inputs or reallocating its epoch/sequence. |
| Prepared bytes, no committed manifest | Verify complete raw/normalized evidence and install precisely those bytes. | Regenerating a prepared manifest or substituting an object descriptor. |
| Exact prepared manifest already externally installed | Resynchronize the immutable installation and commit the existing catalog/checkpoint transaction. | Reusing the slot for different bytes or exposing catalog progress before manifest durability. |
| Catalog committed, pending deleted | Check external manifest and immutable object hashes; return no pending recovery. | Recreating missing committed files or treating a lost checkpoint/catalog as genesis. |
| Conflicting, malformed, missing committed or unexplained external evidence | Fail closed and retain inputs/pending/orphans for diagnosis. | "Repairing" the chain by fabricating progress or deleting uncertain files. |

The local store validates producer ownership, complete catalog/checkpoint
presence, global checkpoint order, independent per-epoch sequence/hash chains,
pending ordinal continuity and prepared hash-column equality with canonical
bytes. A return to an earlier epoch at a larger ingestion offset continues that
epoch's chain; valid numeric ingestion gaps do not create synthetic records.

Recovery reconciles the committed manifest namespace with the catalog and the
one exact prepared pending record. Empty crash-created directories are allowed;
noncanonical bytes, multiple hashes in a slot, misplaced identity/path,
unknown current-producer history, or a missing cataloged manifest are not.
Committed referenced objects must retain their supported local format/codec,
length and content hash within ArchiveLimits. Full normalized outcomes and
semantics, not only row identities, are checked against reserved inputs before
manifest publication. Manifest input coverage requires the full final identity
to equal the checkpoint and the first input to follow the previous checkpoint.

Full namespace/catalog/object reconciliation runs on the first coordinator use
and every explicit `recover()`. Subsequent ordinary batches validate the current
committed head rather than walking all accumulated history. This relies on the
existing exclusive publication-store owner and private local installation;
concurrent external modification by a noncooperating writer is not a trust
model introduced by F11. Readers continue to verify requested historical
objects. Restart reconciliation cost is linear in retained history and has not
been qualified for a 90-day deployment.

## Durability and competition

The existing atomic no-replace **hard link** remains the installation primitive
for both objects and manifests. It implements the approved no-replacement
publication intent; F11 does not replace it with `os.rename`/`os.replace` that
could overwrite history. Temporary files are created on the destination
filesystem, fsynced, made read-only and fsynced again. Installed entries and
their directories are synchronized. Newly created manifest directories now
synchronize their parent too.

The publication store retains its process-level exclusive flock. In addition,
manifest sequence-directory flock serializes the check/install/fsync critical
section; contention fails promptly and distinct-hash retries fail as conflicts.
Locks are advisory and only protect cooperating writers. Kernel-held locks are
released on closing descriptors/process exit; no stale-lock-file deletion or
timeout-driven takeover was added. See authoritative Linux
[fsync(2)](https://man7.org/linux/man-pages/man2/fsync.2.html) and
[flock(2)](https://man7.org/linux/man-pages/man2/flock.2.html).

A selective SOFA search for hard-link/fsync crash consistency returned a
locking post `004ff4df-6079-4cd0-a97d-e0cf31c28650`.
Its advisory-lock warning was relevant; its stale-lock cleanup recommendation
was not adopted. Linux documentation and deterministic local contention tests
were the authority. No private project source was submitted, and no community
code was copied.

## Fault/conflict matrix

`tests/publication/faults.py` defines 53 deterministic boundaries. Each is
exercised in genesis, same-epoch continuation and new-epoch transition, both by
subprocess `os._exit(73)` and one-shot I/O exception (318 matrix cases), followed
by recovery, exact prepared-byte comparison where preparation existed, one
new catalog entry/checkpoint and repeated idempotent recovery.
The continuing histories additionally preserve their committed predecessor and
the correct per-epoch next link. Isolated storage tests reproduce and verify
parent fsync on an already-created object prefix and on both constructor-root
retries, without incidental synchronization by another component.

| Area | Before/after boundaries or conflicts | Evidence |
| --- | --- | --- |
| Raw and normalized objects | File fsync, read-only fsync, no-replace install, object directory fsync, created-directory parent fsync, staging directory fsync | Both fault modes for both roles; real syscall fsync/link errors for each role. |
| Partition views | Created-directory fsync, link install and partition directory fsync | Both fault modes; views are derived, not publication authority. |
| Manifests | Created-parent directory fsync, writable/read-only file fsync, link install, committed-directory and staging-directory fsync | Both fault modes; exact pending bytes survive restart. |
| SQLite | Before reserve/prepare commit; after catalog insert/checkpoint update; before catalog commit; after durable reserve/prepare/catalog completion | Rollback or already committed state converges without duplicate success. |
| Competition | Held sequence lock and two distinct-hash publishers rendezvousing at install; existing exclusive store owner tests | Second publisher fails; only one immutable manifest remains. No sleeps used as race assertions. |
| Pending/input evidence | Hash column mismatch, ordinal holes, unavailable inputs, changed fingerprints/full normalized projection, misrepresented raw descriptors | No checkpoint movement; pending record and uncertain inputs retained. |
| History/identity | Missing checkpoint/manifest/object, local catalog rollback, foreign rows, unknown external epoch history, epoch return and sequence/predecessor conflicts | Reject conflicting evidence without recreation or synthetic progress. |
| Encodings/hashes | Canonical vectors; duplicate keys, BOM, invalid UTF-8, whitespace/newline, float/exponent/boolean integers, unknown algorithm/schema and body/hash disagreements | Parser/reader/conflict tests fail closed. |
| Retention | Unknown staging objects/manifests and repeated input-unavailable recovery; real F05 spool records across publication failures | Uncertain files and raw rows remain; no pruning added. |
| Raw compatibility | Fixed F07 Parquet object, exact identity/SHA-256/envelope/binary payload | `tests/archive/test_prior_codec.py`; fixture provenance sidecar/README. Decoder chosen in local code. |

## Validation and limits

Additional directory synchronization is required for correct retry durability;
F10 throughput figures remain evidence for its original tested tree and are not
requalified performance limits for F11.

Focused command (from the F11 worktree):

```sh
uv run --locked --no-sync python -m pytest tests/publication tests/archive
```

Required complete gate:

```sh
bash scripts/check.sh
```

Final fresh full gate passed: **949 tests in 45.76 s**, Ruff checks/formatting,
mypy (112 files), source/wheel/build/profile-installation checks, raw Parquet
round-trip and all dependency audits (no known vulnerabilities). The pinned uv
0.12.13 environment was selected explicitly via PATH when invoking
`bash scripts/check.sh`. Focused publication/archive suites and direct
red/green regressions passed during implementation.

One bounded independent branch review found the interrupted parent-fsync retry
gap; three isolated regressions demonstrated it and verified the fix. A final
state-machine regression additionally showed that failed full reconciliation
must invalidate a previous successful history cache. The next publication now
repeats the full check and cannot bypass a known history conflict. Both fixes
received targeted independent source review with no remaining material finding.
Final diff/whitespace checks passed; the changes preserve public archive limits,
exact raw bytes/identity/hash and canonical v1 bytes.

The Linux sandbox cannot initialize the pinned SQLite `/proc/self/fd` path;
tests are executed outside that sandbox with unchanged private-path checks.

These tests model process death and reported I/O failures at controlled
boundaries. They do not simulate physical power loss, lying device caches,
filesystem recovery after a power failure, backup restoration or a complete
90-day retention volume. No F15 source/coverage recovery, F16 hostile decoder
isolation, F17 synchronization/trust anchors or F29 pruning/disaster-recovery
qualification is claimed.
