# F06 Recoverable Normalization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Normalize committed fake-candle raw records into immutable, revisioned candle observations or structured rejections while atomically advancing an independently recoverable F06 checkpoint.

**Architecture:** Add an isolated `scryntic.normalization` package with a pure bounded parser/normalizer, a synchronous single-owner SQLite store, and a one-record runner over the accepted F05 `records_after` boundary. F06 owns `normalization.sqlite3`; every outcome and checkpoint advance share one explicit transaction, while unsupported schemas and unavailable metadata remain bounded retryable barriers.

**Tech Stack:** CPython 3.12 stdlib (`dataclasses`, `decimal`, `enum`, `fcntl`, `hashlib`, `json`, `sqlite3`), accepted F03/F04/F05 contracts, pytest, Ruff, and mypy. Add no dependency.

**Spec:** `docs/plans/history/specs/2026-09-16-f06-recoverable-normalization-design.md`

## Global Constraints

- Work only on KER-10 / F06. Exclude Parquet, archive codecs, manifests, publication state/checkpoints, pruning, networking, credentials, instrument discovery, and all other F07 work.
- Do not modify F03 dataclasses or F05's `ingestion.sqlite3` schema. Consume raw records only through `records_after(offset, *, limit)`.
- Always request `records_after(checkpoint_offset, limit=1)`. Offsets are ordered existing records, not a gap-free integer sequence.
- Decode strict UTF-8 before JSON. Limit F06 payloads to 4096 bytes. Unsupported schema name/major/newer-minor values create a retryable barrier, not a rejection.
- Keep ingestion identity, logical key, semantic revision, instrument revision, schema versions, and normalizer version separate.
- Use exact `Decimal` values, context-independent canonicalization, and no float conversion.
- Fix `FAKE_CANDLE_SCHEMA = SchemaRef("fake_candle", Version(1, 0))`, `NORMALIZER_VERSION = "f06.fake_candle.v1"`, and F06 `PRAGMA user_version = 1`.
- Own fixed 0600 `normalization.lock` and `normalization.sqlite3`; acquire cooperative `flock` before SQLite; use one connection on one owning thread; set and verify WAL, `synchronous=FULL`, and `foreign_keys=ON`.
- Use `autocommit=True` with SQL `BEGIN IMMEDIATE`, `COMMIT`, and conditional `ROLLBACK`. Never call Python `Connection.commit()` or `Connection.rollback()`.
- Commit an outcome/rejection and checkpoint in one F06 transaction. Barrier transactions never advance the checkpoint.
- Before treating an existing semantic hash as `duplicate`, decode every stored semantic value and require equality with the new `CandleSemantics`. Mismatch is a fixed-message integrity failure with no progress.
- Preserve current user changes in `.github/dependabot.yml` and `.gitignore`. Keep `docs/plans/history/` ignored.
- Commits in this plan are conditional: execute them only if that execution session has explicit commit authorization. Never infer permission to push, create a PR, or update Linear/GitHub.

## File Map

Production:

- Create `src/scryntic/normalization/__init__.py` for narrow public exports.
- Create `src/scryntic/normalization/candle.py` for pure inspection, rejection values, decimal/fingerprint logic, and candle construction.
- Create `src/scryntic/normalization/sqlite_store.py` for file ownership, schema/startup checks, immutable state, atomic progress, and reads.
- Create `src/scryntic/normalization/runner.py` for the F05 reader protocol, metadata mapping, ordering, and barriers.
- Create `docs/data/NORMALIZATION.md` for the delivered F06 contract and boundary.

Tests:

- Create `tests/normalization/__init__.py`.
- Create `tests/normalization/helpers.py` with explicit JSON and accepted-contract builders; never use `repr` or `asdict` as wire format.
- Create `tests/normalization/test_candle.py`.
- Create `tests/normalization/test_sqlite_store.py`.
- Create `tests/normalization/test_runner.py`.
- Create `tests/normalization/test_public.py` for the supported export surface.
- Create `tests/normalization/crash_normalizer.py`.
- Create `tests/normalization/test_recovery.py`.

No existing implementation file should change. Existing package discovery must include the new subpackage; verify that at the final package gate.

## Exact Interfaces

`scryntic.normalization.candle` produces:

- `FAKE_CANDLE_SCHEMA`, `NORMALIZER_VERSION`, and `PAYLOAD_LIMIT = 4096`.
- `RejectionCode` and `RejectionField` enums defined exactly in Task 1.
- Frozen `ParsedFakeCandle(schema, start_ns, interval_ns, open, high, low, close, volume, finalized, publication_time)`.
- Frozen `UnsupportedSchema(schema)`.
- Frozen `NormalizationRejection(raw_record, raw_sha256, normalizer_version, code, field, input_schema=None, instrument_schema=None, instrument_revision=None, output_schema=None, normalized_at_ns=None)`.
- Frozen `CandleSemantics(key, open, high, low, close, volume, volume_unit, finalized, source_time, publication_time, quality_flags)` with `canonical_bytes() -> bytes` and `revision() -> str`.
- Frozen `CandleNormalization(candle, input_schema, instrument_schema, instrument_revision, semantics)`.
- `canonical_decimal(value: Decimal) -> str`.
- `inspect_fake_candle(record: RawRecord) -> ParsedFakeCandle | NormalizationRejection | UnsupportedSchema`.
- `normalize_parsed_candle(record: RawRecord, parsed: ParsedFakeCandle, instrument: Instrument, *, normalized_at_ns: int) -> CandleNormalization | NormalizationRejection`.

`scryntic.normalization.sqlite_store` produces:

- Fixed-message `NormalizationError` and `NormalizerOwned`.
- `OutcomeKind`: `accepted`, `duplicate`, `open_revision`, `finalization`, `conflict`, `rejected`.
- `BarrierReason`: `unsupported_schema`, `metadata_unavailable`.
- Frozen `ProcessingOutcome(identity: IngestionId, predecessor: IngestionId | None, raw_sha256: str, kind: OutcomeKind, semantic_revision: str | None, rejection_code: RejectionCode | None, rejection_field: RejectionField | None, input_schema: SchemaRef | None, instrument_schema: SchemaRef | None, output_schema: SchemaRef | None, instrument_revision: str | None, normalizer_version: str, receipt: ClockSample, normalized_at_ns: int | None)`.
- Frozen `ProcessingBarrier(blocker: IngestionId, predecessor: IngestionId | None, raw_sha256: str, reason: BarrierReason, schema: SchemaRef | None, instrument: InstrumentId | None)`.
- Frozen `NormalizationStatus(producer: str, checkpoint: IngestionId | None, barrier: ProcessingBarrier | None, journal_mode: str, synchronous: str, sqlite_version: str)`.
- `ProcessedValue = CandleNormalization | NormalizationRejection`.
- `NormalizationStore(installation: Installation, *, producer: str)` with:
  - `process(record, value, *, expected_predecessor) -> ProcessingOutcome`;
  - `block(record, *, expected_predecessor, reason, schema=None, instrument=None) -> ProcessingBarrier`;
  - `checkpoint() -> IngestionId | None`;
  - `barrier() -> ProcessingBarrier | None`;
  - `outcome(identity) -> ProcessingOutcome | None`;
  - `observation(revision) -> CandleSemantics | None`;
  - `status() -> NormalizationStatus`;
  - idempotent `close()` plus context-manager methods.

`scryntic.normalization.runner` produces:

- `RawRecordReader` protocol with `records_after(offset: int, *, limit: int) -> tuple[RawRecord, ...]`; unchanged `DurableIngestor` satisfies it.
- Frozen `NoWork(checkpoint)`, `Processed(outcome)`, and `Blocked(barrier)` plus their union `RunResult`.
- `NormalizationRunner(reader: RawRecordReader, instruments: Mapping[InstrumentId, Instrument], store: NormalizationStore, *, producer: str)`.
- `process_next(*, normalized_at_ns: int) -> RunResult`.

The metadata mapping is an explicit deterministic input and performs no discovery, networking, credential resolution, or clock reads.

---

### Task 1: Strict Fake-Candle Inspection and Rejection Contracts

**Files:** Create `src/scryntic/normalization/{__init__,candle}.py`, `tests/normalization/{__init__,helpers,test_candle}.py`.

**Interfaces:** Produce the Task 1 constants, enums, `ParsedFakeCandle`, `UnsupportedSchema`, `NormalizationRejection`, and `inspect_fake_candle`. Consume unchanged F03 raw/identity/time values.

- [ ] **Step 1: Add explicit builders and failing happy-path tests**

Implement this test encoder in `helpers.py`, plus builders for `Installation`, `Instrument`, `RawEnvelope`, and `RawRecord` with explicit offset/epoch/payload/subject arguments:

```python
def fake_candle_payload(
    *,
    schema: tuple[str, int, int] = ("fake_candle", 1, 0),
    start_ns: int = 1_700_000_000_000_000_000,
    interval_ns: int = 60_000_000_000,
    open: str = "100.10",
    high: str = "101.25",
    low: str = "99.90",
    close: str = "100.75",
    volume: str = "12.3400",
    finalized: bool = False,
    publication_time: dict[str, object] | None = None,
) -> bytes:
    name, major, minor = schema
    value = {
        "schema": {"name": name, "major": major, "minor": minor},
        "start_ns": start_ns,
        "interval_ns": interval_ns,
        "open": open,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
        "finalized": finalized,
        "publication_time": publication_time,
    }
    return json.dumps(value, separators=(",", ":")).encode("utf-8")
```

Assert a supported record becomes `ParsedFakeCandle`, preserves `Decimal("12.3400")`, and requires no metadata.

- [ ] **Step 2: Add failing malformed/quarantine tests**

Cover strict UTF-8 and rejection of UTF-16/32, 4097-byte payload, invalid JSON, nested duplicate keys, `NaN`/infinities, recursion/decoder errors, 21-character integer tokens, 129-character float tokens, missing/non-object/extra-field schema declarations, invalid schema identifiers, boolean versions, and signed-64 bounds. Assert fixed code/field values and absence of input/exception text in result rendering.

Use these exact enums:

```python
class RejectionCode(StrEnum):
    PAYLOAD_TOO_LARGE = "payload_too_large"
    INVALID_UTF8 = "invalid_utf8"
    INVALID_JSON = "invalid_json"
    DUPLICATE_JSON_KEY = "duplicate_json_key"
    JSON_TOKEN_TOO_LONG = "json_token_too_long"
    INVALID_SCHEMA = "invalid_schema"
    INVALID_SUBJECT = "invalid_subject"
    FIELD_SET_MISMATCH = "field_set_mismatch"
    INVALID_FIELD_TYPE = "invalid_field_type"
    INTEGER_OUT_OF_RANGE = "integer_out_of_range"
    INVALID_DECIMAL = "invalid_decimal"
    INCONSISTENT_OHLC = "inconsistent_ohlc"
    INVALID_TIME = "invalid_time"
    INVALID_DOMAIN_VALUE = "invalid_domain_value"


class RejectionField(StrEnum):
    SCHEMA = "schema"
    START_NS = "start_ns"
    INTERVAL_NS = "interval_ns"
    OPEN = "open"
    HIGH = "high"
    LOW = "low"
    CLOSE = "close"
    VOLUME = "volume"
    FINALIZED = "finalized"
    PUBLICATION_TIME = "publication_time"
    SUBJECT = "subject"
```

Assert valid unknown names, positive unknown majors, and newer minors return `UnsupportedSchema` even when their body contains version-1-invalid floats/unknown structures and the subject is absent.

- [ ] **Step 3: Run red test**

```bash
uv run --locked --no-sync python -m pytest tests/normalization/test_candle.py -q
```

Expected: import failure for absent normalization package.

- [ ] **Step 4: Implement generic inspection**

Decode bytes first with `payload.decode("utf-8", errors="strict")`. Feed text to `json.loads` with internal tagged `parse_int`/`parse_float`, duplicate-rejecting `object_pairs_hook`, and nonfinite-rejecting `parse_constant`. Enforce generic token lengths before interpreting the exact schema object. Only a valid `SchemaRef` reaches compatibility routing.

- [ ] **Step 5: Add failing supported-body tests**

Cover exact top-level/publication-time fields, floats, booleans-as-integers, int64 overflow, negative start, nonpositive interval, decimal regex and 128-character/96-digit bounds, signs/exponents/negative decimals, inconsistent OHLC, missing/`EntityId` subjects, optional publication time, and all `TimeUnit` values. Malformed supported bodies retain `input_schema=FAKE_CANDLE_SCHEMA`; pre-declaration failures do not.

- [ ] **Step 6: Implement supported validation and rejection construction**

After routing only, validate version-1 fields. Convert tagged integers after bounds checks; build `Decimal` directly from matching strings; require exact bool and `InstrumentId`; construct `SourceTime` explicitly. One helper records raw identity/hash, normalizer version, fixed code/field, and optional input schema; it never records arbitrary keys, payloads, or exception strings.

- [ ] **Step 7: Run focused validation**

```bash
uv run --locked --no-sync python -m pytest tests/normalization/test_candle.py -q
```

Expected: all inspection, rejection, and quarantine tests pass.

- [ ] **Step 8: Review and conditionally commit**

Run `git diff --check` and `git status --short`. If authorized:

```bash
git add src/scryntic/normalization tests/normalization
git commit -m "feat: add strict candle payload inspection"
```

Otherwise leave the reviewed Task 1 checkpoint uncommitted.

---

### Task 2: Deterministic Semantic Fingerprints and Pure Normalization

**Files:** Modify `candle.py`, its exports, `helpers.py`, and `test_candle.py`.

**Interfaces:** Produce `canonical_decimal`, `CandleSemantics`, `CandleNormalization`, and `normalize_parsed_candle` exactly as declared.

- [ ] **Step 1: Write failing decimal and fixed-vector tests**

```python
@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (Decimal("0"), "0"),
        (Decimal("-0.000"), "0"),
        (Decimal("12.3400"), "12.34"),
        (Decimal("0012.3400"), "12.34"),
        (Decimal("0.0001000"), "0.0001"),
        (Decimal("1000"), "1000"),
        (Decimal("1E+6"), "1000000"),
    ],
)
def test_decimal_canonicalization_ignores_context(
    value: Decimal, expected: str
) -> None:
    with localcontext() as context:
        context.prec = 2
        assert canonical_decimal(value) == expected
```

Also reject nonfinite/negative values. Fix this exact vector in the test:

```python
expected_bytes = (
    b'["scryntic-candle-semantic-v1",["fake","spot","BTC-USDT"],'
    b'1700000000000000000,60000000000,"100.1","101.25","99.9",'
    b'"100.75","12.34","BTC",false,[1700000000123,"ms"],null,[]]'
)
expected_revision = (
    "sha256:57cb824df731e0c154a3dabd0faca58453a48606b845b7fe7b05c69714de6929"
)
```

Mutate each key component, OHLCV value, volume unit, finality, source/publication value/unit/null, and quality flags; each must change revision. Provenance-only changes must not.

- [ ] **Step 2: Run red test**

```bash
uv run --locked --no-sync python -m pytest tests/normalization/test_candle.py -q
```

Expected: missing semantic/canonicalization symbols fail.

- [ ] **Step 3: Implement context-free decimal rendering**

Use `Decimal.as_tuple()`: canonicalize all zero to `"0"`, strip coefficient trailing zeroes while increasing exponent, and place the decimal point manually. Do not call `normalize`, `quantize`, rounding arithmetic, context-sensitive formatting, or `float`.

- [ ] **Step 4: Implement exact positional encoding**

```python
semantic_value = [
    "scryntic-candle-semantic-v1",
    [key.instrument.venue, key.instrument.category, key.instrument.symbol],
    key.start_ns,
    key.interval_ns,
    open_text,
    high_text,
    low_text,
    close_text,
    volume_text,
    volume_unit,
    finalized,
    None if source_time is None else [source_time.value, source_time.unit.value],
    None
    if publication_time is None
    else [publication_time.value, publication_time.unit.value],
    list(quality_flags),
]
encoded = json.dumps(
    semantic_value,
    ensure_ascii=True,
    allow_nan=False,
    separators=(",", ":"),
).encode("utf-8")
revision = f"sha256:{sha256(encoded).hexdigest()}"
```

Validate canonical decimal strings and sorted unique identifier flags in `CandleSemantics` so SQLite reconstruction is safe.

- [ ] **Step 5: Write failing pure-normalizer tests**

Equal explicit inputs must remain equal under different Decimal contexts. Trailing-zero and signed/scaled-zero forms share revisions. Raw identity/receipt, schema versions, instrument revision, normalizer version, and normalization time alone do not affect revision; volume unit does. Assert the existing `Candle` preserves raw/receipt/source/publication evidence, explicit normalization time, `CANDLE_SCHEMA`, empty flags, and `NORMALIZER_VERSION`. Invalid caller time and mismatched instrument identity raise instead of becoming rejections.

- [ ] **Step 6: Implement pure normalization**

Validate identity and caller time first, build `CandleSemantics`, compute revision, construct existing `Candle`, and return input/instrument provenance in `CandleNormalization`. Convert only expected domain-construction failures caused by supported raw values to `INVALID_DOMAIN_VALUE`; propagate programming, resource, storage, lookup, and caller-input failures.

In `CandleNormalization.__post_init__`, require `candle.revision == semantics.revision()` and every semantic `Candle` field to match the canonical semantic value. In `NormalizationRejection.__post_init__`, validate the digest, fixed normalizer version, and all-or-none instrument provenance so inconsistent result objects cannot reach persistence.

- [ ] **Step 7: Run pure validation**

```bash
uv run --locked --no-sync python -m pytest tests/normalization/test_candle.py -q
```

Expected: parser, decimal, fixed-vector, semantic/provenance, and pure-normalizer tests pass.

- [ ] **Step 8: Review and conditionally commit**

Run `git diff --check`. If authorized:

```bash
git add src/scryntic/normalization tests/normalization
git commit -m "feat: add deterministic candle normalization"
```

Otherwise leave the reviewed Task 2 checkpoint uncommitted.

---

### Task 3: Single-Owner F06 SQLite State and Startup Validation

**Files:** Create `sqlite_store.py` and `test_sqlite_store.py`; modify exports/helpers.

**Interfaces:** Produce store errors/enums/value types plus lifecycle/status/read methods. Do not add temporary transaction stubs: Task 4 adds `process`, Task 5 adds `block`, and Task 7 finalizes exports after both exist.

- [ ] **Step 1: Write failing ownership/file-safety tests**

Cover 0600 file creation; symlink, hardlink, wrong-owner, and unsafe-mode rejection; competing ownership before the second SQLite open; lock lifetime; idempotent close; and replacement after close.

```python
with NormalizationStore(target, producer="collector-a"):
    with pytest.raises(NormalizerOwned, match="already owned"):
        NormalizationStore(target, producer="collector-a")
assert len(opened) == 1
```

- [ ] **Step 2: Run red test**

```bash
uv run --locked --no-sync python -m pytest tests/normalization/test_sqlite_store.py -q
```

Expected: missing `sqlite_store` import.

- [ ] **Step 3: Implement descriptor-pinned ownership and one connection**

Reuse F04 `directory(...)` and the accepted F05 fixed-path pattern with separate names. Acquire `LOCK_EX | LOCK_NB` first. Open one `sqlite3.Connection(autocommit=True)` on the constructing thread; create no worker/queue. Use `ExitStack` so constructor failure and close release the connection before the lock.

- [ ] **Step 4: Define and test exact strict schema v1**

Create only:

1. `normalization_metadata(singleton, producer)`.
2. `candle_observations` with revision, complete key, canonical OHLCV text, unit, boolean finality, paired nullable source/publication times/units, and canonical flags JSON.
3. `processing_outcomes` with full current/predecessor identities, unique `(producer, offset)`, raw hash, constrained kind, nullable semantic FK, rejection fields, three schema triples, instrument revision, normalizer version, complete receipt/time-quality evidence, and normalized time.
4. Partial unique index `one_successor_per_outcome` over non-null predecessor identity.
5. Singleton `processing_checkpoint` whose full identity FK targets an outcome.
6. Singleton `processing_barrier` with blocker/hash, nullable predecessor FK, constrained reason, optional schema, and optional instrument identity.

Use checks for all-or-none composites, enums/booleans, rejection-versus-semantic shape, reason-specific barrier fields, and smaller predecessor offset. Create schema and `user_version=1` inside explicit SQL transaction control.

- [ ] **Step 5: Add failing startup-validation tests**

Cover effective WAL/FULL/foreign keys, runtime SQLite version, empty reads, same/different producer, unsupported version, wrong table SQL, unexpected table/index/view/trigger, `quick_check` failure, and foreign-key inconsistency. For the FK case, create valid state, close, disable FK only on a direct test connection, inject a dangling reference, then require startup failure even with `quick_check == ok`.

- [ ] **Step 6: Implement exact schema/integrity validation and decoders**

Read back pragmas, compare normalized `sqlite_schema` rows to exact DDL, verify producer, require `quick_check(1) == ("ok",)` and empty `foreign_key_check`. Never repair. Strictly decode schemas, identities, times, receipt evidence, semantics, outcomes, and barriers; reject bad types, hashes, noncanonical values, and incomplete composites without echoing rows.

- [ ] **Step 7: Implement lifecycle/read APIs and run green**

Return frozen owned values from `checkpoint`, `barrier`, `outcome`, `observation`, and `status`; expose no row/cursor/connection/path/SQL. Calls after close use a fixed error.

```bash
uv run --locked --no-sync python -m pytest tests/normalization/test_sqlite_store.py -q
```

Expected: ownership, pragma, schema, integrity, FK, producer, lifecycle, and empty-read tests pass.

- [ ] **Step 8: Review and conditionally commit**

Inspect full DDL and run `git diff --check`. If authorized:

```bash
git add src/scryntic/normalization tests/normalization
git commit -m "feat: add durable normalization state"
```

Otherwise leave the reviewed Task 3 checkpoint uncommitted.

---

### Task 4: Atomic Outcomes, Semantic History, and Checkpoint Chain

**Files:** Modify `sqlite_store.py`, `test_sqlite_store.py`, and helpers.

**Interfaces:** Complete `NormalizationStore.process`, immutable observations/outcomes, predecessor chain, checkpoint transaction, and all outcome kinds.

- [ ] **Step 1: Write failing accepted/rejected transaction tests**

The first normalization must expose observation, outcome, and checkpoint together. A malformed rejection must expose no semantic row, one `REJECTED` outcome, and checkpoint together. Observe SQL order:

```text
BEGIN -> semantic insert/read -> outcome insert -> checkpoint write -> readback -> COMMIT
```

Assert `autocommit=True`, expected `in_transaction` changes, and no Python `commit()`/`rollback()` calls.

- [ ] **Step 2: Run red transaction subset**

```bash
uv run --locked --no-sync python -m pytest \
  tests/normalization/test_sqlite_store.py -k 'process or transaction' -q
```

Expected: incomplete `process` behavior fails.

- [ ] **Step 3: Implement one-record atomic processing**

Within `BEGIN IMMEDIATE`, reread checkpoint and require exact expected predecessor; require matching producer and strictly greater offset, never `+1`; insert/verify semantic row; insert one linked outcome; insert/update checkpoint; delete only an exactly matching barrier; read back complete state; SQL `COMMIT`.

On failure, SQL `ROLLBACK` only if `in_transaction`. Failed/residual rollback closes the connection, marks the store fatal, and makes later methods raise `NormalizationError("Durable normalization store failed")` until restart.

- [ ] **Step 4: Write failing classification history**

```text
open A         -> accepted
open A repeat  -> duplicate
open B         -> open_revision
final C        -> finalization
final D        -> conflict
open E         -> conflict
final D repeat -> duplicate
```

Use distinct raw offsets; assert one outcome per raw record, one observation per unique semantic revision, and no update/overwrite. Provenance-only differences remain duplicates. The first observation is accepted whether open or final.

- [ ] **Step 5: Implement classification inside the transaction**

```python
if existing_same_revision:
    kind = OutcomeKind.DUPLICATE
elif no_observation_for_key:
    kind = OutcomeKind.ACCEPTED
elif incoming.finalized and not any_existing_final:
    kind = OutcomeKind.FINALIZATION
elif not incoming.finalized and not any_existing_final:
    kind = OutcomeKind.OPEN_REVISION
else:
    kind = OutcomeKind.CONFLICT
```

Rejections bypass semantic classification.

- [ ] **Step 6: Add the required hash/value hardening test**

Commit an observation, close the store, directly mutate one still-valid semantic column without changing revision, reopen, and process a later record with that computed revision:

```python
with pytest.raises(NormalizationError, match="semantic revision"):
    store.process(later_record, normalization, expected_predecessor=first.identity)
assert store.checkpoint() == first.identity
assert store.outcome(later_record.identity) is None
```

- [ ] **Step 7: Verify complete stored semantics before duplicate**

Decode key, all five decimals, unit, finality, both times/units, and flags to `CandleSemantics`. Require equality with incoming semantics before `DUPLICATE`. On mismatch raise `NormalizationError("Stored semantic revision does not match computed values")`, roll back, and leave progress unchanged. Never trust only the PK/hash or selected columns.

- [ ] **Step 8: Add write/commit/rollback failure injection**

Fail semantic insert, outcome insert, checkpoint write, and commit; each leaves no partial progress and permits retry. Fail rollback and require fatal store behavior. Barrier deletion injection belongs to Task 5 after `block` exists.

- [ ] **Step 9: Run complete store suite**

```bash
uv run --locked --no-sync python -m pytest tests/normalization/test_sqlite_store.py -q
```

Expected: startup plus all classifications, integrity hardening, transaction failures, and offset gaps pass.

- [ ] **Step 10: Review and conditionally commit**

Confirm no SQL touches `ingestion.sqlite3`; run `git diff --check`. If authorized:

```bash
git add src/scryntic/normalization/sqlite_store.py tests/normalization
git commit -m "feat: persist normalized outcomes atomically"
```

Otherwise leave the reviewed Task 4 checkpoint uncommitted.

---

### Task 5: Ordered Runner, Metadata Resolution, and Retryable Barriers

**Files:** Create `runner.py` and `test_runner.py`; modify store, exports, and helpers.

**Interfaces:** Complete `NormalizationStore.block` and produce the runner protocol/result types and `process_next`.

- [ ] **Step 1: Write failing `limit=1` gap/order tests**

Use a recording reader with offsets `2, 5, 9`. Three calls must request `(0,1)`, `(2,1)`, `(5,1)` and link checkpoints `2 -> 5 -> 9`. Also cover no work, more than one returned row, wrong producer, equal/decreasing offset, and reader exception; none may mutate progress.

- [ ] **Step 2: Write failing parsing/metadata ordering tests**

Use a mapping that records `get`: malformed supported evidence and invalid subject become rejections with zero lookups; unsupported schema becomes a barrier with zero lookups; supported parsed evidence performs one lookup; missing metadata becomes a barrier; mismatched returned instrument identity raises with no progress.

- [ ] **Step 3: Run red runner test**

```bash
uv run --locked --no-sync python -m pytest tests/normalization/test_runner.py -q
```

Expected: missing `runner` import.

- [ ] **Step 4: Implement bounded barrier transaction**

Inside `BEGIN IMMEDIATE`, require checkpoint equality, producer equality, and blocker offset greater than predecessor. Insert the singleton barrier only when absent. An existing barrier is idempotent only if blocker identity/hash, predecessor, reason, schema, and instrument match exactly. Read it back before commit. Never alter checkpoint/outcomes.

`UNSUPPORTED_SCHEMA` requires schema and no instrument. `METADATA_UNAVAILABLE` requires parsed schema and instrument. Store no payload or arbitrary error.

- [ ] **Step 5: Implement one-record runner flow**

```python
checkpoint = store.checkpoint()
offset = 0 if checkpoint is None else checkpoint.offset
records = reader.records_after(offset, limit=1)
validate_zero_or_one_ordered_record(records, checkpoint, producer)
reconcile_existing_barrier(records, store.barrier())
if not records:
    return NoWork(checkpoint)
record = records[0]
inspected = inspect_fake_candle(record)
if isinstance(inspected, NormalizationRejection):
    return Processed(store.process(record, inspected, expected_predecessor=checkpoint))
if isinstance(inspected, UnsupportedSchema):
    barrier = store.block(
        record,
        expected_predecessor=checkpoint,
        reason=BarrierReason.UNSUPPORTED_SCHEMA,
        schema=inspected.schema,
    )
    return Blocked(barrier)
subject = record.envelope.subject
if not isinstance(subject, InstrumentId):
    raise NormalizationError("Inspector returned candle for invalid subject")
instrument = instruments.get(subject)
if instrument is None:
    barrier = store.block(
        record,
        expected_predecessor=checkpoint,
        reason=BarrierReason.METADATA_UNAVAILABLE,
        schema=inspected.schema,
        instrument=subject,
    )
    return Blocked(barrier)
normalized = normalize_parsed_candle(
    record, inspected, instrument, normalized_at_ns=normalized_at_ns
)
return Processed(store.process(record, normalized, expected_predecessor=checkpoint))
```

Use type narrowing for `InstrumentId`. Validate normalization time before durable writes. Never fetch a second record in one call.

- [ ] **Step 6: Add barrier reconciliation/retry tests**

Restart must reread from unchanged checkpoint with `limit=1`. Empty/different identity/hash with an existing barrier is fatal. Same blocker is idempotent. Adding metadata processes that record and deletes barrier in the outcome/checkpoint transaction. Simulated new parser support does the same for unsupported schema. Never read a later record while blocked.

Inject barrier insert failure, barrier commit failure, and failure during matching barrier deletion in `process`. Insert/commit failures leave no barrier; deletion failure rolls back the new semantic/outcome/checkpoint and preserves the old barrier for retry.

- [ ] **Step 7: Add real F05 integration**

Use one `DurableIngestor` and one `NormalizationStore` in the same F04 state directory. Commit explicit fake payloads via `accept`, pass the ingestor itself as `RawRecordReader`, and assert F06 uses separate `normalization.sqlite3`, public `records_after`, and one semantic row plus multiple provenance outcomes for duplicate deliveries. Do not query F05 SQLite directly.

- [ ] **Step 8: Run focused runner/store validation**

```bash
uv run --locked --no-sync python -m pytest \
  tests/normalization/test_runner.py \
  tests/normalization/test_sqlite_store.py -q
```

Expected: gaps, parser/metadata ordering, barriers/reconciliation, later metadata, F05 integration, and store tests pass.

- [ ] **Step 9: Review and conditionally commit**

Confirm `records_after` is the only F05 dependency and run `git diff --check`. If authorized:

```bash
git add src/scryntic/normalization tests/normalization
git commit -m "feat: coordinate recoverable normalization"
```

Otherwise leave the reviewed Task 5 checkpoint uncommitted.

---

### Task 6: Startup Reconciliation and Process-Kill Durability

**Files:** Create `crash_normalizer.py` and `test_recovery.py`; modify store/helpers.

**Interfaces:** Use public store/runner behavior plus a subprocess-only connection subclass. Add no production crash hook.

- [ ] **Step 1: Write failing startup-chain tests**

Create valid state publicly, close, then inject independently with a direct FK-disabled test connection: outcomes without checkpoint, checkpoint without outcome, two roots, missing predecessor, two successors, nonincreasing predecessor, checkpoint not only tail, unreachable outcome, barrier predecessor mismatch, and blocker not greater than predecessor. Reopen must fail fixed-message. A valid `2 -> 5 -> 9` chain must pass.

- [ ] **Step 2: Implement complete startup chain validation**

After schema/pragmas/quick/FK checks, enforce zero-outcome/no-checkpoint, exactly one root otherwise, at most one successor, increasing offsets, checkpoint as only tail, predecessor traversal count equal to total outcomes, and barrier/checkpoint ordering. Never infer, advance, delete, or repair progress.

- [ ] **Step 3: Create subprocess SQL-stage kill harness**

The helper takes root path and one stage. Its custom connection calls `os._exit(86)` immediately before or after the selected real operation:

```text
before_begin
after_observation_insert
after_outcome_insert
after_checkpoint_write
after_barrier_delete
before_outcome_commit
after_outcome_commit
after_barrier_insert
before_barrier_commit
after_barrier_commit
```

Construct fixed accepted-contract inputs. Use no production hook, environment branch, sleep, or second production connection.

- [ ] **Step 4: Write kill/restart matrix**

Every pre-commit outcome stage restarts at old checkpoint with no partial outcome and permits one retry. `after_outcome_commit` restarts with complete outcome/checkpoint and moves to the next record. Pre-commit barrier stages rediscover; post-commit preserves/reconciles. Include an existing barrier so killing after its deletion proves deletion cannot persist without outcome/checkpoint.

- [ ] **Step 5: Add deterministic replay tests**

Process equal raw/metadata/time inputs in two independent F06 states and assert equal semantics, revision, Candle, and outcome provenance. Repeat after injected transaction failure using the same inputs. Changing only normalization time keeps semantics equal and provenance intentionally different.

- [ ] **Step 6: Run recovery validation**

```bash
uv run --locked --no-sync python -m pytest \
  tests/normalization/test_recovery.py \
  tests/normalization/test_sqlite_store.py \
  tests/normalization/test_runner.py -q
```

Expected: corrupt chains fail, valid gaps pass, all kill points satisfy atomic progress, barriers recover, and replay is deterministic.

- [ ] **Step 7: Review and conditionally commit**

Inspect subprocess return/output handling and run `git diff --check`. If authorized:

```bash
git add src/scryntic/normalization/sqlite_store.py tests/normalization
git commit -m "test: verify normalization crash recovery"
```

Otherwise leave the reviewed Task 6 checkpoint uncommitted.

---

### Task 7: Public Surface, Documentation, and KER-10 Boundary Validation

**Files:** Finalize `normalization/__init__.py`; create `tests/normalization/test_public.py` and `docs/data/NORMALIZATION.md`.

**Interfaces:** Export the documented constants, pure types/functions, store values/APIs, and runner values/APIs. Keep SQL, decoders, tagged tokens, descriptors, connections, paths, and crash helpers private.

- [ ] **Step 1: Add failing public-surface/package assertions**

In `test_public.py`, assert supported imports work from `scryntic.normalization` and internal connection/path/decoder names are absent from `__all__`. Rely on the existing `scripts/check_package.py` file-manifest comparison at Step 5 to prove the built and installed distributions contain every new package file; do not duplicate that build in a focused test.

- [ ] **Step 2: Finalize explicit exports**

Use explicit imports and `__all__`; expose only immutable values and runner/store entry points. Do not expose arbitrary SQL or cursor access.

- [ ] **Step 3: Write `docs/data/NORMALIZATION.md`**

Document pure staged flow and explicit time; exact payload/bounds; semantic/provenance and fingerprint encoding; outcome classifications; separate SQLite ownership; ordered-gap checkpoint; atomic invariant; singleton barriers; restart/fatal behavior; cooperative-lock limitation; public F05 boundary; and explicit F07 exclusion. Do not copy plan checklists or describe unimplemented schemas.

- [ ] **Step 4: Run focused F06 suite once**

```bash
uv run --locked --no-sync python -m pytest tests/normalization -q
```

Expected: parser, normalizer, store, runner, collision hardening, startup corruption, replay, and kill/restart tests pass.

- [ ] **Step 5: Run the required task-boundary gate once**

```bash
bash scripts/check.sh
```

Expected: lock integrity, Ruff, formatting, strict mypy, full pytest, package build/install, and audit pass. Do not repeat unchanged broad validation unless a subsequent edit can affect it or failure diagnosis requires it.

- [ ] **Step 6: Review scope and repository state**

```bash
git diff --check
git status --short --branch --untracked-files=all
git diff -- src/scryntic/normalization tests/normalization docs/data/NORMALIZATION.md
```

Read every new file. Confirm no F03/F05 implementation/schema, dependency metadata, `.gitignore`, user-owned Dependabot change, or F07 component changed. Record exact focused/full validation results against KER-10.

- [ ] **Step 7: Conditionally commit the final implementation checkpoint**

Only when authorized and both validation steps pass:

```bash
git add src/scryntic/normalization tests/normalization docs/data/NORMALIZATION.md
git commit -m "feat: add recoverable candle normalization"
```

Otherwise leave the reviewed implementation uncommitted. Never push, open a PR, or modify KER-10 without separate authorization.

---

## Required KER-10 Completion Evidence

- Malformed supported input creates bounded rejection evidence and advances atomically with checkpoint.
- Unsupported schema and missing metadata block without checkpoint advancement and later become processable.
- Open/final/duplicate/revision/conflict history matches the approved rules and never overwrites accepted history.
- Equivalent deliveries create one semantic row and multiple auditable outcomes.
- Existing-revision duplicate handling verifies complete stored semantic equality and fails closed on mismatch.
- Offset gaps process in F05 order through `limit=1` without `+1` assumptions.
- Equal explicit raw/metadata/time inputs reproduce equal normalized values.
- Failed transactions and pre-commit kills never put progress ahead of outcome; post-commit restart does not duplicate success.
- Startup rejects schema, pragma, integrity, FK, chain, tail, and barrier inconsistencies.
- Focused F06 validation and one final `bash scripts/check.sh` pass.
- Final diff has no F03/F05 contract change, dependency addition, or F07 work.
