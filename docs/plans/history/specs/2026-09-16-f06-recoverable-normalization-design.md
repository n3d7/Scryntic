# F06 Recoverable Normalization Design

## Status and scope

This specification defines KER-10 / F06, **Normalize with recoverable
progress**, against `main` at
`af489526198ca5a939f1aba2bc5708865c96fc87`.

F06 consumes committed `RawRecord` values through the accepted F05 public
boundary, parses one explicit fake-candle payload schema, produces immutable
candle observations or structured rejection evidence, and commits each outcome
with its processing checkpoint in one F06-owned SQLite transaction.

F06 does not change `ingestion.sqlite3`, query it directly, normalize
uncommitted envelopes, perform networking, resolve credentials, discover
instrument metadata, publish Parquet, create archive manifests, prune the raw
spool, or implement any F07 publication state.

The implementation must preserve Foundation v1's lack of financial execution
authority. Input bytes are evidence only; they never select code, imports,
credentials, paths, SQL, or network destinations.

## Existing contracts

F06 retains the accepted F03 and F05 contracts:

- `IngestionId` identifies each committed delivery as producer, epoch and
  offset. Numeric offsets are monotonically ordered but are not assumed to be
  gap-free.
- `RawRecord` contains the committed identity and bounded `RawEnvelope`.
- `Instrument` supplies versioned metadata after the payload and subject have
  been validated far enough to require it.
- `CandleKey` remains instrument identity, UTC start nanoseconds and positive
  fixed interval nanoseconds.
- `Candle` remains the typed observation, including semantic revision, raw
  provenance, receipt evidence, normalization time and schema.
- `DurableIngestor.records_after(offset, limit=...)` is the only F05 record
  source used by F06. F06 never opens an ingestion database connection.

F06 does not add a required field to any F03 dataclass. It introduces F06-owned
immutable result, rejection, inspection and status values around those
contracts. In particular, instrument revision remains separate from
`Candle.revision`.

## Responsibility boundaries

F06 has four cooperating units:

1. **Payload inspector.** Strictly decodes and structurally inspects one
   `RawRecord` without instrument metadata. It distinguishes malformed evidence,
   a well-formed unsupported schema, and a parsed supported candle payload.
2. **Pure candle normalizer.** Combines a parsed supported payload, its committed
   raw record, an explicit `Instrument`, and an explicit `normalized_at_ns` into
   a `CandleNormalization` or deterministic rejection. It performs no I/O and
   reads no clock or ambient state.
3. **Normalization runner.** Pulls exactly the next existing committed record
   through F05, coordinates inspection and already-available metadata, stops at
   barriers, and submits at most one outcome at a time to the store.
4. **Normalization store.** Owns `normalization.lock`, one
   `normalization.sqlite3` connection, the F06 schema, immutable observations,
   per-ingestion outcomes, the processing chain, the checkpoint and one bounded
   processing barrier.

The runner may depend on a small F06-local structural reader contract matching
`records_after`. `DurableIngestor` satisfies it without modification. Instrument
lookup is an explicit read-only dependency supplied by composition; for F06 it
is an already-populated local mapping or equivalent deterministic lookup. It
must not perform networking or credential resolution.

## One-record pull model

F06 always calls:

```python
reader.records_after(checkpoint_offset, limit=1)
```

The result is either empty or contains the next existing committed record after
the checkpoint according to F05's ascending-offset ordering. This is deliberately
simpler than a read batch because F06 already commits one transaction per raw
record. It removes batch cursor and intra-batch skip state without assuming that
the next numeric offset is `checkpoint + 1`.

For committed offsets `2, 5, 9`, valid progress is:

```text
no checkpoint -> outcome 2 -> outcome 5 -> outcome 9
```

The runner requires a returned record to have the configured producer and an
offset strictly greater than the current checkpoint. It does not require an
increment of one. It does not request another record until the current one has
either committed, established a barrier, or failed processing.

An empty result means there is currently no committed work after the checkpoint;
it does not advance progress or prove future completeness. A future measured
throughput requirement may introduce bounded reads larger than one, but that
would require its own ordering and skip-proof validation. It is not part of
F06.

## Pure staged processing

### Stage 1: inspect without metadata

The first pure function has the conceptual interface:

```python
inspect_fake_candle(
    record: RawRecord,
) -> ParsedFakeCandle | NormalizationRejection | UnsupportedSchema
```

It performs these steps in order:

1. Enforce the F06 payload byte limit.
2. Decode `record.envelope.payload` with explicit strict UTF-8 decoding.
3. Decode a JSON document while rejecting duplicate keys and non-finite
   constants and retaining ordinary numeric tokens in tagged internal forms.
4. Extract and validate the schema declaration before applying the candle-body
   contract.
5. Return `UnsupportedSchema` for a well-formed unknown name, unknown major, or
   newer minor version.
6. For the supported schema, validate the exact body fields, scalar types,
   numeric bounds and decimal syntax.
7. Require `record.envelope.subject` to be an `InstrumentId`.
8. Return a parsed candle or a deterministic structured rejection.

The inspector needs no `Instrument`. A missing, malformed or untyped schema
declaration is malformed evidence, not a claim for a future schema reader. Only
a well-formed schema identity can create an unsupported-schema barrier.

### Stage 2: resolve metadata only when needed

The runner asks its supplied metadata lookup for the parsed
`InstrumentId` only after Stage 1 returns `ParsedFakeCandle`.

- If no metadata is currently available, the runner persists a bounded
  `metadata_unavailable` barrier and does not advance the checkpoint.
- If the lookup returns an `Instrument` whose identity differs from the lookup
  key, F06 treats that as an infrastructure/programming failure and does not
  advance the checkpoint.
- If metadata is available and matches, the runner invokes Stage 3.

Missing metadata is not a permanent data rejection because later metadata may
make the same committed raw evidence processable.

### Stage 3: construct the candle

The second pure function has the conceptual interface:

```python
normalize_parsed_candle(
    record: RawRecord,
    parsed: ParsedFakeCandle,
    instrument: Instrument,
    *,
    normalized_at_ns: int,
) -> CandleNormalization | NormalizationRejection
```

`normalized_at_ns` is an explicit caller input. The normalizer never samples a
clock. Repeating the call with equal inputs produces an equal result. A replay
that requires byte-for-byte equal provenance supplies the same explicit
normalization time.

The normalizer constructs:

- `CandleKey` from the raw subject plus parsed start and interval;
- OHLCV from exact `Decimal` values;
- volume unit and instrument provenance from `Instrument`;
- finality and optional publication time from the supported payload;
- source time and receipt evidence from `RawEnvelope`;
- the empty quality-flag tuple required by `fake_candle` 1.0;
- the semantic revision defined below;
- raw identity from `RawRecord.identity`;
- the supported candle schema and fixed normalizer version
  `f06.fake_candle.v1`.

The result wrapper contains the `Candle`, the separate instrument revision and
the semantic fingerprint. Domain-construction failures caused by supported
input become fixed-code rejections. Unexpected programming, storage or resource
failures propagate and stop progress rather than being mislabeled as bad data.

## Fake candle payload schema

The only F06 payload schema is `fake_candle` version 1.0. The underscore is
intentional: the name is a valid existing `SchemaRef` opaque identifier.
F06 represents the supported reader contract as
`SchemaRef("fake_candle", Version(1, 0))`.

```json
{
  "schema": {
    "name": "fake_candle",
    "major": 1,
    "minor": 0
  },
  "start_ns": 1700000000000000000,
  "interval_ns": 60000000000,
  "open": "100.10",
  "high": "101.25",
  "low": "99.90",
  "close": "100.75",
  "volume": "12.3400",
  "finalized": true,
  "publication_time": {
    "value": 1700000060000,
    "unit": "ms"
  }
}
```

`publication_time` may be `null`. The original source timestamp remains in
`RawEnvelope.source_time`; it is not duplicated in the fake body. Instrument
identity remains in `RawEnvelope.subject`. Volume unit comes from the resolved
`Instrument`, not from untrusted payload text. Fake-candle 1.0 has no
quality-flag field and always normalizes to `quality_flags=()`; a later schema
must define and bound any nonempty flags explicitly.

### Generic JSON safety rules

- The raw bytes are decoded with `payload.decode("utf-8", errors="strict")`
  before `json.loads`; byte input is never passed directly to `json.loads`,
  which would also accept UTF-16 and UTF-32.
- The F06 payload limit is 4096 bytes even when the F05 installation accepts a
  larger envelope.
- Schema names use the existing 256-character opaque-identifier bound. Integer
  token text is limited to 20 characters including an optional leading minus;
  float token text is limited to 128 characters. A token that exceeds its
  generic limit is malformed evidence regardless of schema because F06 cannot
  inspect it within its resource contract.
- An `object_pairs_hook` rejects duplicate object keys at every nesting level.
- `parse_constant` rejects `NaN`, `Infinity` and `-Infinity`.
- `parse_int` and `parse_float` retain bounded token text in distinct internal
  tagged values. The generic pass does not apply supported-schema int64 or
  no-float rules before schema selection.
- Decoder and recursion errors become fixed-code malformed evidence.
- The top level must be an object containing `schema`. That value must be an
  object with exactly `name`, `major` and `minor`: `name` is a valid existing
  opaque identifier, `major` is an integer from 1 through signed-64 maximum,
  and `minor` is an integer from 0 through signed-64 maximum.

This ordering allows a syntactically valid future schema to be quarantined even
if its body contains fields or numeric forms that F06 version 1.0 would reject.

### Supported-schema rules

After `fake_candle` 1.0 is selected:

- The top-level and nested publication-time objects have exact field sets.
- JSON float tokens are rejected; OHLCV must be JSON strings.
- `start_ns`, `interval_ns` and publication-time values are signed 64-bit JSON
  integers; start is nonnegative and interval is positive.
- Decimal strings are 1 through 128 ASCII characters, contain no more than 96
  digits, are finite and nonnegative, and use plain decimal notation matching
  `[0-9]+(?:\.[0-9]+)?`. They contain no sign, whitespace or exponent.
- `finalized` must have exact Python/JSON boolean type.
- Publication time is either `null` or a value/unit object accepted by
  `SourceTime`.
- Unknown fields, missing fields, wrong types, invalid ranges and inconsistent
  OHLC values are rejected.

No dataclass `repr`, `asdict`, pickle or dynamic codec is an external format.
Tests construct this explicit payload shape directly or through an equally
explicit fixture encoder.

## Rejection and quarantine

### Durable rejection

A `NormalizationRejection` records:

- the full raw `IngestionId`;
- raw content SHA-256;
- fixed F06 normalizer version;
- a stable rejection code;
- an optional bounded field path.

It never embeds payload bytes, exception `repr`, credentials, URLs or arbitrary
error text. The full original evidence remains in F05 under the referenced raw
identity and hash.

The stable rejection-code set is `payload_too_large`, `invalid_utf8`,
`invalid_json`, `duplicate_json_key`, `json_token_too_long`, `invalid_schema`,
`invalid_subject`, `field_set_mismatch`, `invalid_field_type`,
`integer_out_of_range`, `invalid_decimal`, `inconsistent_ohlc`, `invalid_time`
and `invalid_domain_value`. The optional field is one of the finite schema paths
`schema`, `start_ns`, `interval_ns`, `open`, `high`, `low`, `close`, `volume`,
`finalized`, `publication_time` or `subject`; an unknown key or parser exception
is never copied into it.

Malformed encoding or JSON, ambiguous duplicate keys, invalid schema
declarations, malformed supported bodies, unsupported subjects and domain-value
violations are durable rejections. A rejection is a processed outcome and moves
the checkpoint in the same transaction.

### Unsupported schema and metadata barriers

An `UnsupportedSchema` is not a rejection and not a processed outcome. The
store records one singleton `processing_barrier` for the producer with:

- blocker and predecessor identities;
- blocker raw SHA-256;
- reason: `unsupported_schema` or `metadata_unavailable`;
- schema name/major/minor when known;
- instrument identity when known;
- no copied raw payload.

The checkpoint does not move past a barrier, and the runner does not request a
later record. At restart or after an upgrade, the runner requests the next F05
record from the unchanged checkpoint and requires its identity and hash to match
the barrier. If it has become processable, the successful outcome, checkpoint
advance and barrier deletion occur in one transaction. A crash before barrier
commit merely causes rediscovery; a crash after barrier commit preserves the
blocked status. Barrier storage is bounded to one row because F06 always stops
at the first blocker.

## Semantic candle revision

Observation revision describes a change in the market observation, not a
change in processing provenance.

### Semantic fields

The semantic fingerprint contains:

- full `CandleKey`: venue, category, symbol, start and interval;
- open, high, low, close and volume;
- volume unit;
- finality;
- source-time value and original unit, or null;
- publication-time value and original unit, or null;
- canonical ordered quality flags.

Changing any of these fields creates a different semantic revision.

### Provenance and version fields

The fingerprint excludes:

- input payload, instrument and output candle schema versions;
- instrument revision;
- normalizer version;
- raw identity and raw hash;
- receipt wall/monotonic/session/time-quality evidence;
- `normalized_at_ns`.

Changing only excluded provenance does not create a new market-observation
revision. Each processing outcome still stores that provenance. If a new
normalizer or instrument revision changes a semantic field, the resulting
semantic change naturally produces a new revision.

### Decimal canonicalization

Canonical decimal text is derived without ambient decimal context:

1. Read sign, coefficient digits and exponent through `Decimal.as_tuple()`.
2. If the numeric value is zero, return `"0"` regardless of sign, exponent or
   encoded scale.
3. Remove trailing coefficient zeros while increasing the exponent by the same
   count.
4. Render plain decimal notation manually, without exponent, leading `+`, or
   insignificant leading/trailing zeros. Values below one use a leading `0.`.

No `normalize`, `quantize`, arithmetic rounding or float conversion participates.
Consequently `12.3400` and `12.34` have the same canonical text, and all signed
or scaled zero forms canonicalize to `0`.

### Fingerprint byte encoding

The exact semantic value is this positional array:

```text
[
  "scryntic-candle-semantic-v1",
  [venue, category, symbol],
  start_ns,
  interval_ns,
  canonical_open,
  canonical_high,
  canonical_low,
  canonical_close,
  canonical_volume,
  volume_unit,
  finalized,
  null | [source_time_value, source_time_unit],
  null | [publication_time_value, publication_time_unit],
  [quality_flag_0, quality_flag_1, ...]
]
```

It is encoded exactly as:

```python
json.dumps(
    semantic_value,
    ensure_ascii=True,
    allow_nan=False,
    separators=(",", ":"),
).encode("utf-8")
```

Only arrays, bounded ASCII strings, integers, booleans and null appear, so object
ordering is irrelevant. The first array element versions this fingerprint
contract. The revision is `sha256:` followed by lowercase SHA-256 hex over these
bytes. Fixed byte-vector tests make the representation observable and stable.

## Duplicate, revision, finality and conflict rules

Classification uses only logical key, semantic fingerprint and already
committed semantic observations:

- `accepted`: first semantic observation for the key;
- `duplicate`: the semantic revision already exists, regardless of different
  raw, receipt, schema, instrument or normalizer provenance;
- `open_revision`: a new open semantic revision before any finalized revision;
- `finalization`: the first finalized semantic revision after one or more open
  revisions;
- `conflict`: a different finalized revision when a finalized revision already
  exists, or a new open revision after finalized state;
- `rejected`: a durable structured rejection.

Every new semantic revision is inserted immutably. Existing observations are
never updated or replaced. A repeated delivery of an existing conflict revision
is a `duplicate` referencing that same revision. F06 does not select a preferred
revision for datasets; selection belongs to later dataset policy.

`Candle.raw_record` on a materialized result identifies the raw delivery for
that processing outcome. Repeated equivalent deliveries do not create repeated
semantic rows, but every raw identity and its provenance remain independently
auditable through `processing_outcomes`.

## SQLite ownership and durability

The F06 store uses the validated F04 state directory and fixed owner-only files:

- `normalization.lock`;
- `normalization.sqlite3`.

It rejects symlinks, hardlinks, unexpected owners and unsafe modes. It acquires
`flock(LOCK_EX | LOCK_NB)` before opening SQLite and holds the descriptor for the
store lifetime. This is cooperative ownership between Scryntic processes, not a
security boundary.

The connection is created, used and closed on one owning thread for the object
lifetime; the synchronous F06 store needs no internal worker queue. It uses
`autocommit=True`; production transactions use SQL `BEGIN
IMMEDIATE`, `COMMIT` and conditional `ROLLBACK`, not Python-managed implicit
transactions or `Connection.commit()`/`rollback()`.

Startup sets and reads back:

- `journal_mode=WAL`;
- `synchronous=FULL`;
- `foreign_keys=ON`.

It validates the exact owned schema, rejects unexpected schema objects, checks
`PRAGMA user_version`, runs `PRAGMA quick_check(1)`, and requires
`PRAGMA foreign_key_check` to return no rows. `quick_check` does not cover foreign
key violations, so both checks are required.

A rollback failure or residual open transaction permanently fails the store,
closes its connection and requires a clean restart. F06 never repairs or copies
live SQLite, WAL or SHM files.

## Persistent state model

All F06 tables are `STRICT`. The database metadata binds one state directory to
one producer while permitting new ingestion epochs.

### `normalization_metadata`

One singleton row stores the bound producer. `PRAGMA user_version = 1` is the
initial F06 database schema version. Normalizer and candle schema versions are
row provenance, not database-global values.

### `candle_observations`

One immutable row stores each semantic revision:

- semantic revision as the primary key;
- complete logical key;
- canonical OHLCV text;
- volume unit;
- finality;
- nullable source/publication times with units;
- canonical quality-flags JSON array.

The semantic revision includes the key and is globally unique within the F06
store. Instrument revision, normalizer version, raw identity, receipt evidence,
schema version and normalization time do not belong in this table.

### `processing_outcomes`

One row exists for each processed existing committed `RawRecord`. It stores:

- current producer, epoch and offset;
- nullable predecessor producer, epoch and offset;
- raw SHA-256;
- outcome kind;
- nullable semantic-revision foreign key;
- nullable rejection code and field;
- nullable input payload schema name/major/minor;
- nullable instrument schema name/major/minor;
- nullable output candle schema name/major/minor;
- nullable instrument revision;
- F06 normalizer version;
- receipt wall time, monotonic time, session and time-quality evidence;
- nullable `normalized_at_ns`.

Metadata-dependent fields may be null when rejection occurs before metadata
resolution. Input payload schema fields may be null when no valid declaration
was recoverable. Successful normalization requires semantic revision, all three
schema identities, instrument revision and normalized time. Rejection requires
no semantic revision and requires a rejection code; it records the input schema
identity whenever Stage 1 established one. Instrument and output candle schemas
are null before metadata resolution; once Stage 3 is attempted, its rejection
records the resolved instrument schema and the target output candle schema.

The primary identity is the full `IngestionId`; producer plus offset is also
unique because F05 offsets are producer-wide across epochs. A self-referencing
foreign key links each non-root outcome to the preceding processed outcome.
Predecessor columns are either all null or all present, predecessor offset must
be smaller than current offset, and a partial unique index permits at most one
successor per predecessor.

### `processing_checkpoint`

The producer has at most one checkpoint row containing the full identity of the
tail processing outcome. A foreign key targets that exact outcome. No row means
that no raw record has yet been processed.

The checkpoint means "last processed existing committed record", not "all
integer offsets through N".

### `processing_barrier`

The producer has at most one barrier row containing the blocker identity/hash,
the full predecessor checkpoint identity, reason, and nullable schema/instrument
details. A nullable foreign key binds its predecessor to the current processing
chain. It is not an outcome and does not change the checkpoint.

## Per-record transaction invariant

Inspection and pure normalization happen before the write transaction. For one
processible or rejectable record, the store performs:

1. `BEGIN IMMEDIATE`.
2. Read the durable checkpoint.
3. Require it to equal the runner's expected predecessor identity.
4. Require the new producer to match and the new offset to be strictly greater
   than the predecessor offset; do not require `+ 1`.
5. Determine duplicate/revision/finality/conflict classification against
   committed semantic observations.
6. Insert the semantic observation if its revision is new.
7. Insert exactly one processing outcome linked to the predecessor.
8. Insert or guarded-update the checkpoint to the current identity.
9. Delete a matching barrier, if one exists.
10. Read back and verify the outcome, semantic reference and checkpoint.
11. `COMMIT`.

The invariant is:

> A visible F06 checkpoint always references a durable outcome for that exact
> committed raw identity, and following predecessor links from the checkpoint
> reaches every earlier F06 outcome exactly once in the order F05 returned the
> existing records.

There is no invariant requiring an outcome for every integer offset below the
checkpoint.

On any failure, F06 issues SQL `ROLLBACK` only if `Connection.in_transaction`
shows an open transaction. A failed commit cannot expose an outcome or checkpoint
advance. A failed rollback permanently fails the store.

Barrier persistence uses its own single transaction while leaving the checkpoint
unchanged. When a barrier becomes processable, its deletion joins the normal
outcome/checkpoint transaction.

## Startup consistency and recovery

After schema, durability and foreign-key checks, startup validates:

- zero outcomes implies no checkpoint;
- nonzero outcomes have exactly one root with a null predecessor;
- every other outcome has one existing predecessor;
- each outcome has at most one successor;
- predecessor offsets strictly increase toward successors;
- the checkpoint identifies the only tail;
- traversing predecessors from the checkpoint reaches the total number of
  outcomes, allowing arbitrary numeric gaps;
- a barrier's predecessor is the current checkpoint, or null when no checkpoint
  exists;
- a barrier blocker offset is greater than its predecessor offset.

Internal inconsistency fails startup. F06 never infers progress from observation
rows, advances a checkpoint during recovery, or silently repairs the chain.

The runner reconciles a stored barrier with F05 by calling
`records_after(checkpoint_offset, limit=1)` and requiring the returned identity
and hash to match the barrier before retrying it.

Crash behavior is therefore:

- before a record transaction: no F06 state, so the record is returned again;
- after observation/outcome writes but before commit: SQLite rolls back all
  writes and the checkpoint remains unchanged;
- after checkpoint update but before commit: the checkpoint and outcome roll
  back together;
- after commit: the checkpoint points to the committed outcome and the next
  `limit=1` read moves beyond it;
- before barrier commit: the same blocker is rediscovered;
- after barrier commit: startup preserves and reconciles the blocked state.

## Public F06 behavior

The runner exposes bounded, synchronous operations suitable for the early slice:

- process at most the next record using caller-supplied `normalized_at_ns`;
- report no available work;
- report the durable unsupported-schema or metadata barrier;
- expose current checkpoint and fixed-message failure state;
- close idempotently and release ownership only after the connection closes.

Read methods needed for F06 tests and later composition return owned immutable
values for a checkpoint, one processing outcome and one semantic observation.
They do not expose arbitrary SQL, filesystem paths, live connections or F07
publication cursors.

## Validation

### Pure parser and normalizer

Tests cover:

- strict UTF-8 and rejection of UTF-16/32 payloads;
- invalid JSON, duplicate keys and non-finite constants;
- generic token-size limits;
- malformed or missing schema declarations;
- unknown name/major/newer minor as quarantine rather than rejection;
- unsupported bodies containing fields or numeric forms unknown to F06 without
  premature supported-schema validation;
- supported-schema JSON floats, unknown/missing fields and int64 overflow;
- bounded plain decimal strings, exact decimal preservation and invalid OHLC;
- explicit boolean finality and optional publication time;
- invalid subject rejection before metadata lookup;
- identical outputs for equal explicit inputs under different ambient Decimal
  contexts.

Fixed fingerprint vectors cover exact UTF-8 bytes and SHA-256. They verify
context-independent decimal rendering, trailing-zero equivalence, signed-zero
canonicalization and revision changes for every semantic field.

They also verify that changes only to input payload, instrument or output candle
schema versions, instrument revision, normalizer version, raw identity, receipt
evidence or normalization time do not change semantic revision.

### Ordering and metadata

Reader fakes and real F05 integration cover:

- `records_after(..., limit=1)` on gaps such as `2, 5, 9`;
- no `offset + 1` assumption;
- producer/order violations failing without progress;
- absent metadata creating a barrier without rejection or checkpoint advance;
- later metadata processing the same blocker;
- metadata resolver identity mismatch failing without progress;
- unsupported schema blocking all later records until supported.

### Duplicate, finality and conflict history

Tests verify:

- repeated semantic content creates one observation and multiple provenance
  outcomes;
- different provenance versions alone remain duplicates;
- open changes create immutable `open_revision` observations;
- first final after open is `finalization`;
- a different second final is `conflict`;
- open after final is `conflict`;
- repeated conflict content is a duplicate of the retained conflict revision;
- no accepted history is updated or overwritten.

### Transaction and process failure

Real SQLite tests inject failures at semantic insert, outcome insert, checkpoint
update, barrier deletion, commit and rollback. Subprocess tests terminate before
the transaction, after each write, after checkpoint update, immediately after
commit, and before/after barrier commit.

After restart, pre-commit work is processed again, post-commit work is not, and
the checkpoint never leads its durable outcome. A rollback failure leaves the
store fatal until restart.

### Startup validation

Tests cover WAL/FULL/foreign-key setting verification, unsupported schema
versions, unexpected schema objects, quick-check failure, foreign-key violations,
broken predecessor chains, multiple roots or tails, a checkpoint that is not the
tail, and a barrier inconsistent with the checkpoint.

Focused F06 tests use the public F05 API and real separate SQLite files. The full
project quality gate remains required at the implementation boundary, but this
design pass does not rerun it.

## Dependencies and future boundaries

F06 requires only the existing Python standard library and accepted Scryntic
contracts: `json`, `decimal`, `hashlib`, `sqlite3`, filesystem/locking primitives
already used by F05, and typed dataclasses. No dependency is added.

`limit=1` is intentional for invariant clarity, not a permanent throughput
claim. Any later batching must retain ordered no-skip processing and atomic
per-record progress or obtain separate architectural approval for a different
transaction model.

F07 remains responsible for immutable archive objects, Parquet, publication
manifests, publication checkpoints and spool release. F06 stores no archive
locator, manifest sequence, publication status or pruning decision.
