# F11 publication and chain recovery implementation plan

**Goal:** complete KER-15 against the existing F07 state machine, approved architecture and F10 Parquet decision.

**Architecture:** preserve reserve → seal → persist exact prepared bytes → publish immutable manifest → catalog/checkpoint commit. Strengthen reconciliation and evidence checks; retain all uncertain inputs/orphans. Hard links remain the existing atomic no-replace publication operation (not replacing them with overwriting rename).

**Baseline:** GitHub main `b4c6de4149b6ba90d0795d47868c9fbffed8a447`; local audit commit stays on main outside this branch. F07 design `docs/superpowers/specs/2026-09-23-f07-immutable-archive-design.md`, approved ARCHITECTURE.md publication contract, and `docs/performance/f10-operating-envelope.md` govern the change. KER-14 is Done. Inline execution authorized without intermediate approvals.

## Constraints and review focus

- RawArchive / ArchiveLimits and canonical v1 formats remain unchanged; production codec stays `parquet-pyarrow-v1` with fixed local decoder selection.
- Never recreate missing committed history, skip pending inputs, shrink a reservation or reuse a sequence for other bytes. Unknown committed evidence blocks recovery.
- Check complete normalized evidence, raw descriptors, prepared hash columns and catalog/checkpoint presence as well as ordinary happy paths.
- Serialize competing no-replace manifest installs; inject faults before/after file and directory durability operations and SQLite commits.
- No F15 source/coverage policy, F16 decoder isolation, F17 trust/sync, or F29 pruning/backup qualification.

## Tasks

### KER-55: reconcile pending state and history

Files: publication/coordinator.py, publication/sqlite_store.py, publication/manifest.py; tests/publication/test_hardening.py.

- [x] Write and observe regressions for missing catalog checkpoint, mismatched prepared hash, changed normalized evidence and false raw metadata.
- [x] Validate store invariants and complete archive evidence. Reconcile committed manifest namespace against catalog and exact prepared pending bytes; refuse unexplained external history.
- [x] Verify safe retries, changed/missing raw or normalized inputs, epoch/global history and canonical/encoding conflicts without progress or deletion.

### KER-56: durability boundaries and exclusion

Files: archive/storage.py, raw_parquet.py, normalized_parquet.py; publication/manifest.py, sqlite_store.py; focused storage/publication tests.

- [x] Demonstrate concurrent distinct-hash manifest installation cannot both succeed at one epoch/sequence.
- [x] Add optional fault callbacks around existing file/directory fsync and link operations; preserve public archive port. Persist newly created manifest directories and guard sequence installation with an OS lock.
- [x] Verify failures preserve externally committed bytes and do not advance the catalog prematurely.

### KER-57: fault matrix and compatibility

Files: tests/publication/test_recovery.py, crash_publisher.py, helpers.py; tests/archive/fixtures and codec compatibility test; docs/publication/f11-recovery.md.

- [x] Extend deterministic process-exit and exception matrix through object/manifest/SQLite boundaries, including repeated recovery failure and uncertain orphans.
- [x] Commit an F07/main Parquet fixture with recorded hash, producer/identity/raw bytes and baseline provenance; verify exact reads using current codec without running fixture-controlled code.
- [x] Run focused recovery/archive tests, fresh `bash scripts/check.sh`, review all diff/new files and one bounded independent branch review.
- [ ] Repair attributable failures, commit/push branch, create PR with validation and limitations; leave parent/sub-issues In Review, no merge.

## Execution evidence

- Initial sandbox tests failed because SQLite uses `/proc/self/fd` paths; equivalent focused baseline outside sandbox passed. This is an environment limitation, not a source regression.
- Existing reservation value already recomputes ordered-input digest; preserve it rather than duplicate speculative fixes.
- Ruling: full history reconciliation runs at first coordinator use and explicit recovery; ordinary publication verifies the current committed head under the existing exclusive owner. Rewalking/redecoding all history per batch would contradict F10's measured point-read cost and introduce avoidable quadratic steady-state work. Noncooperating external writes remain outside the local owner trust boundary; restart qualification at 90-day volume is F29.
- Review finding resolved: resynchronize existing validated directory parents after interrupted mkdir/fsync, including object-prefix and constructor roots; three isolated tests observed RED then GREEN.
- Final state-machine check resolved: invalidate a prior successful cache before every full reconciliation so a detected conflict cannot be bypassed on the next publish; observed RED then GREEN and targeted independent review.
- Final fresh complete gate: 949 tests passed in 45.76 s, Ruff/format, mypy112, packaging/profile/round-trip checks and dependency audits passed. No material review findings remain.
