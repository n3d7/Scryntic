# F05 Durable Ingestion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a bounded, single-owner SQLite raw spool whose acknowledgements and monotonic offsets never lead durable state.

**Architecture:** A `DurableIngestor` owns a cooperative process lock, a bounded queue, one worker thread and exactly one SQLite connection. The worker uses Python `sqlite3` autocommit mode plus explicit SQL transactions and reconstructs the existing immutable raw contracts from a strict schema.

**Tech Stack:** Python 3.12 stdlib (`sqlite3`, `fcntl`, `queue`, `threading`), pytest, existing Ruff/mypy gates.

**Spec:** `docs/superpowers/specs/2026-09-16-f05-durable-ingestion-design.md`

## Global Constraints

- Keep one SQLite connection for the full writer lifetime.
- Acquire and retain the cooperative `flock` before opening SQLite.
- Use explicit SQL `BEGIN IMMEDIATE`, `COMMIT` and conditional `ROLLBACK` with `autocommit=True`.
- Acknowledge only committed rows; never deduplicate payloads or silently evict intake.
- Do not normalize, publish, prune, checkpoint through a second connection, or add dependencies.

---

### Task 1: State ownership and SQLite startup

**Files:**
- Create: `src/scryntic/ingestion/__init__.py`
- Create: `src/scryntic/ingestion/sqlite_spool.py`
- Create: `tests/ingestion/__init__.py`
- Create: `tests/ingestion/test_sqlite_spool.py`

**Interfaces:**
- Produces: `DurableIngestor`, `IngestionError`, `WriterOwned`, `IntakeFull`, and immutable status values.
- Consumes: F03 raw/time/identity values and the F04 `Installation` path policy.

- [x] Write tests that reject a second owner before its SQLite connection opens and verify the lock remains held for the first owner's lifetime.
- [x] Run the focused tests and confirm they fail because the ingestion package is absent.
- [x] Implement safe fixed-path lock acquisition, one worker-owned connection and deterministic lifecycle errors.
- [x] Write tests that observe effective WAL/FULL pragmas, supported schema and startup status.
- [x] Run the focused tests until green without adding another connection.

### Task 2: Transactional acceptance and envelope round-trip

**Files:**
- Modify: `src/scryntic/ingestion/sqlite_spool.py`
- Modify: `tests/ingestion/test_sqlite_spool.py`

**Interfaces:**
- `DurableIngestor.accept(envelope) -> RawRecord`
- `DurableIngestor.records_after(offset, *, limit) -> tuple[RawRecord, ...]`
- `DurableIngestor.status() -> IngestionStatus`

- [x] Write a failing round-trip test with all optional subject/time fields and two identical deliveries.
- [x] Implement strict schema v1, bounded inserts and validated reconstruction.
- [x] Write failing tests that observe `in_transaction` before/during/after success and force SQL `COMMIT` failure.
- [x] Implement explicit transaction handling so failed commit returns an error, rolls back, and leaves the high-water mark unchanged.
- [x] Run the focused tests until green.

### Task 3: Bounded pressure and crash recovery

**Files:**
- Modify: `src/scryntic/ingestion/sqlite_spool.py`
- Modify: `tests/ingestion/test_sqlite_spool.py`
- Create: `tests/ingestion/crash_writer.py`

**Interfaces:**
- Queue capacity limits pending submissions; `IntakeFull` identifies rejection.
- Restart status and reads derive only from committed rows.

- [x] Write a deterministic failing test that fills the bounded intake and proves rejected work is never reported as accepted.
- [x] Implement queue admission and acknowledgement without silent eviction.
- [x] Write a subprocess test that acknowledges records, calls `os._exit`, then verifies every acknowledged row after restart.
- [x] Add recovery tests for empty and populated spools and unsupported/corrupt state.
- [x] Run the focused test suite until green.

### Task 4: Documentation and project validation

**Files:**
- Create: `docs/INGESTION.md`
- Modify only if required by packaging: existing package metadata checks.

- [x] Document ownership, acknowledgement, transaction, recovery and SQLite-version constraints.
- [x] Run focused tests: `uv run --locked --group dev pytest tests/ingestion -v`.
- [x] Run the complete gate once: `bash scripts/check.sh`.
- [x] Inspect the complete diff, new files and `git status`; confirm no F06 or unrelated work.
- [x] Request code review and address in-scope findings.
- [ ] Commit, push, create the draft PR and inspect GitHub Actions.
