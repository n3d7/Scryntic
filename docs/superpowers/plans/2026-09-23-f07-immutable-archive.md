# F07 Recoverable Immutable Archive Publication Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> `superpowers:executing-plans` to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking. The user explicitly requires inline
> execution and prohibits implementation commits.

**Goal:** Implement KER-11 / F07 as a durable local publication pipeline that
archives exact raw bytes and F06 outcomes in immutable Parquet objects and
publishes them through canonical, recoverable manifest chains.

**Architecture:** Add focused `archive` and `publication` packages. The archive
package owns canonical input projections, bounded Parquet codecs, immutable
object installation, and partition views. The publication package owns strict
SQLite reservation/catalog state, canonical manifests, the coordinator and
recovery state machine, and bounded readers; it consumes F05/F06 exclusively
through their existing public methods.

**Tech Stack:** CPython 3.12, stdlib SQLite/filesystem primitives,
`pyarrow==25.0.1`, pytest, Ruff, mypy, uv.

**Spec:** `docs/superpowers/specs/2026-09-23-f07-immutable-archive-design.md`

## Global Constraints

- Work only on `ker-11-immutable-archive`; preserve all pre-existing and
  untracked work.
- Do not query `ingestion.sqlite3` or `normalization.sqlite3`; use
  `records_after`, `outcome`, and `observation` only.
- Keep F05 offsets producer-global, strictly ordered, and gap-tolerant; keep
  manifest sequence/hash chains per producer epoch.
- Use one transaction per publication state transition with explicit `BEGIN
  IMMEDIATE` / `COMMIT` / `ROLLBACK`, WAL, `synchronous=FULL`, and foreign keys.
- Install objects and manifests without replacement, fsync files and committed
  directories, and never regenerate a prepared manifest.
- Do not prune F05 records or implement F08/F10/F11/F16/F17/F29 work.
- Pin only `pyarrow==25.0.1` in the collector group; add no unrelated dependency.
- Do not commit, push, create a PR, merge, or start F08.

## Review Focus

- A reused object hash with different bytes or descriptor metadata must fail,
  never overwrite or silently reuse the path (Task 2 tests).
- A manifest visible before catalog registration must remain fully readable,
  while global continuity remains explicitly unconfirmed until recovery
  (Tasks 4 and 7 tests).
- Interleaved epochs must advance one global checkpoint without resetting or
  skipping either epoch's independent chain (Tasks 5–7 tests).
- Parquet metadata may lie about logical size; reading must validate declared
  limits before decode and recompute exact logical size during iteration
  (Tasks 2 and 3 tests).
- Any F05 read exception or missing F06 outcome must leave reservation,
  checkpoint, manifests, and every spool record unchanged (Task 6 tests).

---

### Task 1: Collector Dependency and Canonical Publication Inputs

**Files:**
- Create: `src/scryntic/archive/__init__.py`
- Create: `src/scryntic/archive/canonical.py`
- Create: `tests/archive/__init__.py`
- Create: `tests/archive/helpers.py`
- Create: `tests/archive/test_canonical.py`
- Modify: `pyproject.toml`
- Modify: `uv.lock`

**Interfaces:**
- Consumes: `RawRecord`, `ProcessingOutcome`, `CandleSemantics`, and the exact
  F06 public values.
- Produces:
  `PublicationInput(raw: RawRecord, outcome: ProcessingOutcome,
  semantics: CandleSemantics | None)`,
  `canonical_json_bytes(value: JsonValue) -> bytes`,
  `raw_projection(record) -> JsonObject`,
  `normalized_projection(outcome, semantics) -> JsonObject`,
  `input_fingerprint(value: PublicationInput) -> str`, and
  `ordered_input_digest(values: tuple[PublicationInput, ...]) -> str`.

- [ ] **Step 1: Add failing normative-vector and consistency tests**

```python
def test_publication_input_vector_matches_spec() -> None:
    value = rejected_vector_input(payload=bytes.fromhex("00ff41"))
    assert input_fingerprint(value) == (
        "aab4bbe154acd69d3bf0b535de0dd15741a4f870a8c5cb7d40246745ca7f7838"
    )


def test_ordered_input_vector_matches_spec() -> None:
    assert ordered_digest_from_pairs(SPEC_VECTOR_PAIRS) == (
        "fa95b24754be337ca3f78d08123abfbee75d4b92f81c05f7456db592a1f1e842"
    )
```

Also cover binary/empty payload hex, instrument/entity/null subjects, accepted
semantics, explicit nulls, raw/outcome identity/hash/receipt mismatch, reordered
inputs, and non-increasing offsets.

- [ ] **Step 2: Run the canonical tests and confirm RED**

Run: `uv run --no-default-groups --group dev python -m pytest tests/archive/test_canonical.py -q`

Expected: collection fails because `scryntic.archive.canonical` does not exist.

- [ ] **Step 3: Pin PyArrow and implement exact canonical projections**

```toml
[dependency-groups]
collector = ["pyarrow==25.0.1"]
```

Implement strict `canonical-json-v1`, lowercase payload hex and SHA-256,
publication-input-v1, and ordered-input-v1 exactly as the normative spec defines.
`PublicationInput.__post_init__` must validate the complete F05/F06 relationship
before any hash is produced.

- [ ] **Step 4: Refresh the lock and run focused GREEN checks**

Run: `uv lock`

Expected: exit 0 and a lock containing `pyarrow==25.0.1` in the collector closure.

Run: `uv run --no-default-groups --group dev --group collector python -m pytest tests/archive/test_canonical.py -q`

Expected: all canonical tests pass, including both fixed digests.

### Task 2: Immutable Storage and Raw Parquet Archive

**Files:**
- Create: `src/scryntic/archive/model.py`
- Create: `src/scryntic/archive/storage.py`
- Create: `src/scryntic/archive/raw_parquet.py`
- Create: `tests/archive/test_raw_parquet.py`
- Modify: `src/scryntic/archive/__init__.py`

**Interfaces:**
- Consumes: F03 `ArchiveLimits`, `RawArchive`, `RawSegment`, `RawRecordRef` and
  Task 1 raw projection/logical-size helpers.
- Produces:
  `ArchiveRole`, `ArchiveObject`, `Partition`,
  `ImmutableArchiveStorage(installation)`, and
  `ParquetRawArchive(installation)` implementing the existing async
  `seal(records, limits) -> RawSegment` and
  `read(reference, limits) -> RawRecord` methods.

- [ ] **Step 1: Add failing raw archive behavior tests**

```python
async def test_raw_binary_round_trip_is_exact(tmp_path: Path) -> None:
    records = raw_records(payloads=(b"", b"\x00\xffnot-utf8"))
    archive = ParquetRawArchive(installation(tmp_path))
    segment = await archive.seal(records, generous_limits())
    assert (
        await archive.read(
            RawRecordRef(segment.sha256, 1, records[1].identity), generous_limits()
        )
        == records[1]
    )
```

Cover all envelope fields, repeated bytes/distinct identities, exact/one-below
record and logical limits, encoded-size failure before installation, corrupted
hash/footer/schema/row count/payload hash, invalid reference ordinal/identity,
lying logical-size metadata, symlink/non-regular paths, and existing-hash
identity conflicts.

- [ ] **Step 2: Run raw archive tests and confirm RED**

Run: `uv run --no-default-groups --group dev --group collector python -m pytest tests/archive/test_raw_parquet.py -q`

Expected: imports fail because the concrete archive is absent.

- [ ] **Step 3: Implement immutable storage and codec-v1 raw Parquet**

Use the explicit v1 schema, one row group, no compression, no dictionary,
Parquet 2.6, fixed schema metadata, a 1,024-row iteration request, content hash
paths, staging/fsync/hard-link no-replace installation, and directory fsync.
Compute `decoded_bytes` from canonical logical rows before Arrow construction
and recompute while reading; do not use Arrow allocations or footer
uncompressed sizes as the logical limit.

- [ ] **Step 4: Run focused archive checks and confirm GREEN**

Run: `uv run --no-default-groups --group dev --group collector python -m pytest tests/archive/test_raw_parquet.py tests/contracts/test_ports.py -q`

Expected: all selected tests pass and the F03 contract remains unchanged.

### Task 3: Normalized Outcome Parquet

**Files:**
- Create: `src/scryntic/archive/normalized_parquet.py`
- Create: `tests/archive/test_normalized_parquet.py`
- Modify: `src/scryntic/archive/__init__.py`

**Interfaces:**
- Consumes: `PublicationInput`, `ArchiveLimits`, `ArchiveObject`, and
  `ImmutableArchiveStorage`.
- Produces:
  `ArchivedNormalization(identity: IngestionId, outcome: ProcessingOutcome,
  semantics: CandleSemantics | None)`,
  `NormalizedParquetArchive.seal(inputs, limits) -> ArchiveObject` and
  `NormalizedParquetArchive.read(descriptor, limits) ->
  tuple[ArchivedNormalization, ...]`. The publication reader pairs these rows
  with the matching raw rows by ordinal before reconstructing
  `PublicationInput` and its fingerprint.

- [ ] **Step 1: Add failing normalized archive tests**

```python
@pytest.mark.parametrize("kind", tuple(OutcomeKind))
async def test_every_outcome_kind_round_trips(
    kind: OutcomeKind, tmp_path: Path
) -> None:
    value = publication_input_for_kind(kind)
    archive = NormalizedParquetArchive(installation(tmp_path))
    descriptor = await archive.seal((value,), generous_limits())
    assert await archive.read(descriptor, generous_limits()) == (
        archived_normalization(value),
    )
```

Cover accepted, duplicate, open revision, finalization, conflict, rejection
before metadata, exact decimal strings, nullable times/schemas, quality flags,
identity order, exact/one-below logical limits, declared/actual mismatch,
unsupported codec/schema, and row-count mismatch.

- [ ] **Step 2: Run normalized archive tests and confirm RED**

Run: `uv run --no-default-groups --group dev --group collector python -m pytest tests/archive/test_normalized_parquet.py -q`

Expected: import fails because `NormalizedParquetArchive` is absent.

- [ ] **Step 3: Implement the explicit normalized Parquet schema and reader**

Store one self-contained outcome row per ingestion record, including complete
semantics for non-rejections and explicit nullable provenance for rejections.
Apply the same immutable install and logical-size rules as Task 2.

- [ ] **Step 4: Run focused normalized checks and confirm GREEN**

Run: `uv run --no-default-groups --group dev --group collector python -m pytest tests/archive/test_normalized_parquet.py tests/normalization/test_public.py -q`

Expected: all selected tests pass.

### Task 4: Canonical Manifests and Durable Installation

**Files:**
- Create: `src/scryntic/publication/__init__.py`
- Create: `src/scryntic/publication/manifest.py`
- Create: `tests/publication/__init__.py`
- Create: `tests/publication/test_manifest.py`

**Interfaces:**
- Consumes: `ArchiveObject`, `Partition`, canonical JSON, ordered-input digest,
  and generated immutable storage paths.
- Produces:
  `ManifestRef`, `ManifestBody`, `ManifestDocument`, `ManifestError`,
  `prepare_manifest(body) -> bytes`,
  `parse_manifest(data, max_bytes) -> ManifestDocument`, and
  `ManifestStorage.install_exact(data, ref) -> None` /
  `ManifestStorage.read_exact(ref, max_bytes) -> bytes`.

- [ ] **Step 1: Add failing manifest vector and installation tests**

```python
def test_manifest_vectors_fix_body_wrapper_and_chain_hashes() -> None:
    first = prepare_manifest(sequence_one_body())
    second = prepare_manifest(sequence_two_body(previous=body_hash(first)))
    assert first == EXPECTED_SEQUENCE_ONE_BYTES
    assert body_hash(second) == EXPECTED_SEQUENCE_TWO_HASH
```

Cover strict UTF-8/no BOM, duplicate keys, exact fields/types, canonical key
order/spacing, signed-int64 bounds, genesis and predecessor hashes, epoch
transition/global checkpoint increase, continuation of an earlier epoch,
uppercase hashes, unsupported algorithms, sequence-directory conflicts,
no-replace idempotence, fsync ordering, and exact prepared-byte reuse.

- [ ] **Step 2: Run manifest tests and confirm RED**

Run: `uv run --no-default-groups --group dev --group collector python -m pytest tests/publication/test_manifest.py -q`

Expected: imports fail because publication manifests are absent.

- [ ] **Step 3: Implement canonical v1 manifests and storage**

Hash the canonical body only, canonicalize the wrapper, generate
`manifests/<producer-epoch-digest>/<sequence>/<hash>.json`, inspect at most two
sequence-directory entries, fsync the prepared file, install without
replacement, and fsync the committed directory before returning.

- [ ] **Step 4: Run focused manifest checks and confirm GREEN**

Run: `uv run --no-default-groups --group dev --group collector python -m pytest tests/publication/test_manifest.py -q`

Expected: all manifest and installation tests pass.

### Task 5: Publication SQLite Store

**Files:**
- Create: `src/scryntic/publication/sqlite_store.py`
- Create: `tests/publication/test_sqlite_store.py`
- Modify: `src/scryntic/publication/__init__.py`

**Interfaces:**
- Consumes: `PublicationInput`, `ManifestRef`, `ArchiveObject`, `Partition`, and
  complete `IngestionId` checkpoints.
- Produces:
  `PublicationLimits`, `PublicationStatus`, `PendingPublication`,
  `CatalogEntry`, `PublicationError`, `PublisherOwned`, and
  `PublicationStore` operations `reserve`, `prepare`, `commit_prepared`,
  `status`, `pending`, `catalog_by_hash`, `catalog_by_epoch_sequence`,
  `catalog_by_checkpoint`, and bounded `catalog_page`.

- [ ] **Step 1: Add failing strict-store and transaction tests**

```python
def test_catalog_checkpoint_and_pending_delete_commit_together(tmp_path: Path) -> None:
    store = PublicationStore(installation(tmp_path), producer="collector-a")
    pending = store.reserve(reservation_with_offsets(2, 5))
    prepared = store.prepare(pending, manifest_bytes(), descriptors())
    store.commit_prepared(prepared)
    assert store.status().checkpoint == IngestionId("collector-a", "epoch-a", 5)
    assert store.pending() is None
```

Cover cooperative ownership before SQLite open, exact strict schema/pragmas,
foreign-key consistency, producer ownership, one pending publication, immutable
ordered inputs/fingerprints, gap-tolerant monotonic global checkpoints,
per-epoch next sequence/hash, interleaved epoch continuation, duplicate/conflict
identities, and injected reserve/prepare/catalog/checkpoint/delete/commit/
rollback failures.

- [ ] **Step 2: Run publication-store tests and confirm RED**

Run: `uv run --no-default-groups --group dev --group collector python -m pytest tests/publication/test_sqlite_store.py -q`

Expected: imports fail because `PublicationStore` is absent.

- [ ] **Step 3: Implement the single-owner strict publication store**

Acquire `publication.lock` before `publication.sqlite3`, use one owning-thread
connection, validate schema and rows on startup, persist exact pending inputs
and prepared bytes, and atomically insert catalog/update checkpoint/delete
pending state only after the caller confirms durable manifest installation.

- [ ] **Step 4: Run focused store checks and confirm GREEN**

Run: `uv run --no-default-groups --group dev --group collector python -m pytest tests/publication/test_sqlite_store.py -q`

Expected: all selected tests pass.

### Task 6: Coordinator, Partition Views, and Crash Recovery

**Files:**
- Create: `src/scryntic/publication/coordinator.py`
- Create: `tests/publication/helpers.py`
- Create: `tests/publication/crash_publisher.py`
- Create: `tests/publication/test_coordinator.py`
- Create: `tests/publication/test_recovery.py`
- Modify: `src/scryntic/publication/__init__.py`
- Modify: `src/scryntic/archive/storage.py`

**Interfaces:**
- Consumes: public F05 `records_after`, public F06 `outcome`/`observation`, both
  archives, `PublicationStore`, and `ManifestStorage`.
- Produces:
  `NormalizationReader` protocol,
  `PublicationCoordinator.publish_next() -> Published | NoPublishableWork |
  WaitingForNormalization`, and `PublicationCoordinator.recover() ->
  Recovered | NoRecovery`; optional test fault callbacks observe named durable
  boundaries without altering production ordering.

- [ ] **Step 1: Add failing selection and end-to-end coordinator tests**

```python
def test_offset_gaps_and_epoch_switch_keep_global_progress_and_epoch_chains(
    tmp_path: Path,
) -> None:
    coordinator = configured_coordinator(
        tmp_path, offsets=(2, 5, 9), epochs=("a", "b", "a")
    )
    results = tuple(coordinator.publish_next() for _ in range(3))
    assert [result.checkpoint.offset for result in results] == [2, 5, 9]
    assert [result.manifest.sequence for result in results] == [1, 1, 2]
```

Cover partition/epoch batch boundaries, missing outcomes, F05 read failure,
invalid public return types, fingerprint change after reservation, exact object
and partition links, no manifest before durable objects, no catalog before
manifest-directory fsync, and preservation of every F05 record.

- [ ] **Step 2: Run coordinator tests and confirm RED**

Run: `uv run --no-default-groups --group dev --group collector python -m pytest tests/publication/test_coordinator.py -q`

Expected: import fails because `PublicationCoordinator` is absent.

- [ ] **Step 3: Implement selection, sealing, publication, and reconciliation**

Reserve one non-empty single-epoch/source/family/date prefix, re-read and verify
the exact fingerprints, seal raw and normalized objects, install partition
views, persist the exact prepared manifest BLOB, install those bytes durably,
then register catalog/checkpoint. Startup recovery must reuse verified objects,
install only stored prepared bytes, and reconcile catalog-behind state.

- [ ] **Step 4: Run coordinator GREEN checks**

Run: `uv run --no-default-groups --group dev --group collector python -m pytest tests/publication/test_coordinator.py -q`

Expected: all coordinator tests pass.

- [ ] **Step 5: Add and run subprocess interruption tests**

Drive a real child process to exit at reservation commit, each durable object,
prepare commit, manifest file fsync, no-replace install, committed-directory
fsync, and catalog commit. Reopen and recover after each point; assert exact
prepared bytes, valid reader visibility, one catalog entry, monotonic progress,
and unchanged F05 record count.

Run: `uv run --no-default-groups --group dev --group collector python -m pytest tests/publication/test_recovery.py -q`

Expected: every kill/restart case passes.

### Task 7: Bounded Manifest Readers and Task-Boundary Qualification

**Files:**
- Create: `src/scryntic/publication/reader.py`
- Create: `tests/publication/test_reader.py`
- Create: `scripts/verify_archive_install.py`
- Modify: `src/scryntic/publication/__init__.py`
- Modify: `scripts/check_package.py`
- Test: `tests/test_package.py`

**Interfaces:**
- Consumes: manifest/catalog lookup methods, both Parquet archives, exact
  canonical input algorithms, `max_manifest_bytes`, and
  `max_manifests_per_read`.
- Produces:
  `ValidatedManifest`, `Continuity` (`SELF`, `EPOCH`, `GLOBAL`),
  `PublicationReader.resolve_exact(ref_or_hash)`, and
  `PublicationReader.catalog_page(anchor, limit)`.

- [ ] **Step 1: Add failing exact-resolution and bounded-page tests**

```python
def test_interleaved_epoch_page_validates_global_and_epoch_continuity(
    published_history: PublishedHistory,
) -> None:
    page = published_history.reader.catalog_page(anchor=None, limit=4)
    assert page[-1].continuity is Continuity.GLOBAL
    assert [item.document.body.epoch for item in page] == ["a", "b", "a", "b"]
```

Cover exact hash through catalog, complete identity without catalog, the
pre-catalog visibility window, canonical/object/input-digest validation,
second sequence-directory entry, wrong global anchor, wrong/missing per-epoch
predecessor, offset gaps, page truncation, descriptor mismatch, and no
exhaustive collision claim.

- [ ] **Step 2: Run reader tests and confirm RED**

Run: `uv run --no-default-groups --group dev --group collector python -m pytest tests/publication/test_reader.py -q`

Expected: import fails because `PublicationReader` is absent.

- [ ] **Step 3: Implement exact and page readers**

Validate canonical manifests and both referenced objects before returning.
Resolve per-epoch predecessors by exact generated path without recursive
history traversal; validate producer-global catalog pages from their explicit
anchor and cap page and direct predecessor work as specified.

- [ ] **Step 4: Add installed-wheel collector smoke coverage**

`scripts/verify_archive_install.py` must run under the isolated collector
environment created by `check_package.py`, import PyArrow and the installed
Scryntic wheel, seal/read a binary raw record, and exit nonzero on any mismatch.
Base and analysis profile checks must continue to exclude collector-only
dependencies.

- [ ] **Step 5: Run all focused F07 tests**

Run: `uv run --no-default-groups --group dev --group collector python -m pytest tests/archive tests/publication tests/test_package.py -q`

Expected: all focused F07 and package tests pass.

- [ ] **Step 6: Run formatting, typing, and lock checks before the boundary gate**

Run: `uv lock --check`

Run: `uv run --no-default-groups --group dev --group collector ruff check .`

Run: `uv run --no-default-groups --group dev --group collector ruff format --check .`

Run: `uv run --no-default-groups --group dev --group collector mypy --no-incremental`

Expected: every command exits 0.

- [ ] **Step 7: Run the complete repository boundary gate**

Run: `bash scripts/check.sh`

Expected: lock integrity, Ruff, formatting, mypy, full pytest, deterministic
wheel/sdist build, isolated base/collector/analysis installs, collector
Parquet import/read-write smoke, and dependency audits all exit 0.

- [ ] **Step 8: Final scope and state review**

Read every changed/new file, inspect staged/unstaged/untracked state, compare the
implementation against every KER-11 completion criterion and spec validation
case, record any remaining gap, and leave KER-11 `In Review` only if all
required evidence is green.
