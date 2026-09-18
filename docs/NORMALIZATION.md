# Recoverable normalization (F06)

`scryntic.normalization` turns committed F05 `RawRecord` values into immutable
candle observations or bounded rejection evidence. Each processed delivery and
its checkpoint commit in one transaction in a separate F06 SQLite database.
F06 currently understands only the explicit `fake_candle` 1.0 payload below.

## Public API and staged processing

The package exports these supported names:

| Area | Exports |
|---|---|
| Constants | `FAKE_CANDLE_SCHEMA`, `NORMALIZER_VERSION`, `PAYLOAD_LIMIT` |
| Pure values | `ParsedFakeCandle`, `UnsupportedSchema`, `NormalizationRejection`, `RejectionCode`, `RejectionField`, `CandleSemantics`, `CandleNormalization` |
| Pure functions | `inspect_fake_candle`, `normalize_parsed_candle`, `canonical_decimal` |
| Store | `NormalizationStore`, `NormalizationError`, `NormalizerOwned`, `NormalizationStatus`, `ProcessingOutcome`, `ProcessingBarrier`, `OutcomeKind`, `BarrierReason` |
| Runner | `RawRecordReader`, `process_next`, `NoWork`, `Processed`, `Blocked` |

`inspect_fake_candle(record)` needs no instrument metadata. It returns a parsed
supported payload, a deterministic rejection, or `UnsupportedSchema`. Only a
parsed supported candle triggers instrument lookup. The caller supplies an
already-populated `Mapping[InstrumentId, Instrument]`; metadata resolution must
not perform networking or credential discovery. Missing metadata blocks progress;
an instrument whose identity differs from the requested key raises an error.

`normalize_parsed_candle(record, parsed, instrument, normalized_at_ns=...)` is
pure: it reads no clock, performs no I/O and uses exact `Decimal` values. Equal
explicit inputs produce equal values, independently of ambient Decimal context.
Replay requiring identical provenance must also supply the same normalization
time. A successful `CandleNormalization` contains the F03 `Candle`, input and
instrument schemas, separate instrument revision and `CandleSemantics`.

`process_next(reader, store, instruments, normalized_at_ns=...)` processes at most
one committed delivery. Its explicit normalization time must be a signed 64-bit
integer. It returns `Processed(outcome)`, `Blocked(barrier)`, or
`NoWork(checkpoint)`. It never requests another record within that call.

Construct `NormalizationStore(installation, producer=...)` with a validated F04
`Installation`. Its `checkpoint()`, `barrier()`, `outcome(identity)`,
`observation(revision)` and `status()` methods return owned immutable values
(or `None` for absent records). `observation` returns semantic content;
per-delivery provenance belongs to the outcome. The store also exposes
`process(record, normalization, expected_predecessor=...)` and
`block(record, expected_predecessor=..., reason=..., schema=..., instrument=...)`
for explicit composition. The runner supplies the F05 ordering contract when
using these operations. The store is a context manager with idempotent `close()`.

SQL, row decoders, tagged JSON tokens, connections, cursors, file descriptors,
paths and test crash helpers are private implementation details. There is no
public arbitrary-query API.

## Payload and bounds

The supported identity is `SchemaRef("fake_candle", Version(1, 0))` and the fixed
normalizer version is `f06.fake_candle.v1`. This is the complete body shape; all
fields are required, including `publication_time`, which may be `null`:

```json
{
  "schema": {"name": "fake_candle", "major": 1, "minor": 0},
  "start_ns": 1700000000000000000,
  "interval_ns": 60000000000,
  "open": "100.10",
  "high": "101.25",
  "low": "99.90",
  "close": "100.75",
  "volume": "12.3400",
  "finalized": true,
  "publication_time": {"value": 1700000060000, "unit": "ms"}
}
```

Instrument identity comes from the envelope's typed `InstrumentId` subject;
volume unit comes from resolved metadata. Source time and receipt-clock evidence
come from the raw envelope. `fake_candle` 1.0 emits `quality_flags=()` and accepts
no payload quality-flag field.

Inspection enforces the following bounds before resolving metadata:

- At most 4096 payload bytes, even if the F05 envelope limit is larger; strict
  UTF-8 decoding followed by JSON decoding. UTF-16/32 are not alternate formats.
- Duplicate keys at every depth, non-finite JSON constants, invalid JSON and
  excessive nesting are rejected. Generic integer token text is at most 20
  characters, including a minus; float token text is at most 128 characters.
- The top level is an object with a schema object containing exactly `name`,
  `major`, `minor`. The name is a valid opaque identifier of at most 256 ASCII
  characters. Major is an integer from 1 to `2**63 - 1`; minor is from 0 to
  `2**63 - 1`. Booleans and floats do not count as integers.
- A well-formed unknown schema name, major or newer minor blocks processing
  before the supported candle-body rules are applied. Generic JSON limits still
  apply. Missing or malformed schema declarations are rejections.
- For the supported schema, field sets are exact. Start and interval are signed
  64-bit JSON integers; start is nonnegative and interval is positive.
- OHLCV values are JSON strings of 1–128 ASCII characters with at most 96 digits,
  matching `[0-9]+(?:\.[0-9]+)?`. Signs, exponents, whitespace and JSON numeric
  values are rejected. Values are finite and nonnegative, with
  `low <= min(open, close) <= max(open, close) <= high`.
- Finality is an explicit JSON boolean. Publication time is either `null` or
  exactly a signed 64-bit integer `value` plus a `unit` of `s`, `ms`, `us` or `ns`.

Supported input that fails domain construction produces a fixed rejection code.
Programming, resource, reader and storage failures stop processing instead of
being relabeled as malformed market data.

## Semantics, revisions and provenance

`CandleSemantics.canonical_bytes()` encodes this positional value:

```text
[
  "scryntic-candle-semantic-v1",
  [venue, category, symbol], start_ns, interval_ns,
  canonical_open, canonical_high, canonical_low, canonical_close,
  canonical_volume, volume_unit, finalized,
  null | [source_time_value, source_time_unit],
  null | [publication_time_value, publication_time_unit],
  [quality_flag_0, quality_flag_1, ...]
]
```

Encoding is exactly
`json.dumps(value, ensure_ascii=True, allow_nan=False, separators=(",", ":")).encode("utf-8")`.
Quality flags are sorted and unique. `revision()` returns `sha256:` followed by
lowercase SHA-256 hex over these bytes. Original timestamp units participate in
the fingerprint, even when two timestamps denote the same instant.

`canonical_decimal` renders finite nonnegative `Decimal` values from their sign,
digits and exponent without rounding or ambient-context arithmetic. Trailing
coefficient zeros disappear; output uses plain notation and a leading `0.` below
one. All signed or scaled zero values become `"0"`. Thus `12.3400` and `12.34`
produce the same semantic text.

The fingerprint includes the complete candle key, OHLCV, volume unit, finality,
source/publication timestamps with original units and quality flags. It excludes
input/instrument/output schema versions, instrument revision, normalizer version,
raw identity/hash, receipt-clock evidence and normalization time. Changes only in
that provenance create additional auditable outcomes, without new semantic rows.
A provenance change that actually changes a semantic field creates a new revision.

Classification checks existing semantic revisions before finality history:

| Outcome | Meaning |
|---|---|
| `accepted` | First observation for a logical key, whether open or finalized. |
| `duplicate` | An already-stored semantic revision, including a repeated conflict. |
| `open_revision` | A new open revision before any finalized observation. |
| `finalization` | First finalized revision after one or more open observations. |
| `conflict` | A different finalized revision, or a new open revision, after finalized state. |
| `rejected` | Durable structured rejection of the delivery. |

Each new revision is inserted immutably; accepted history is never overwritten.
Duplicate handling compares the complete stored semantic value with the incoming
value and fails closed on mismatch, rather than trusting the revision key alone.
F06 does not choose a preferred observation for a dataset.

Rejections preserve raw identity/hash, normalizer version, a `RejectionCode` and
optional `RejectionField`, plus provenance established by that processing stage.
They never copy payload bytes, unknown field names or arbitrary exception text.
The finite codes are `payload_too_large`, `invalid_utf8`, `invalid_json`,
`duplicate_json_key`, `json_token_too_long`, `invalid_schema`, `invalid_subject`,
`field_set_mismatch`, `invalid_field_type`, `integer_out_of_range`,
`invalid_decimal`, `inconsistent_ohlc`, `invalid_time`, `invalid_domain_value`.
Fields are limited to `schema`, `start_ns`, `interval_ns`, `open`, `high`, `low`,
`close`, `volume`, `finalized`, `publication_time`, `subject`. Original evidence
remains in F05. Rejections commit and advance progress like other outcomes.

## Ownership, progress and atomicity

F06 owns `normalization.lock` and `normalization.sqlite3` in the private F04 state
directory. These fixed files must be owner-only regular files with mode `0600`,
the expected owner and one hard link; symlinks and unsafe modes are rejected.
An exclusive nonblocking `flock` is acquired before SQLite opens and held for the
store lifetime. `NormalizerOwned` reports a competing cooperative owner. The
lock coordinates Scryntic processes; it is not protection against root or
malicious code running as the service UID.

One owning thread creates, uses and closes the single synchronous connection.
There is no worker queue. The connection uses `autocommit=True` with explicit SQL
`BEGIN IMMEDIATE`, `COMMIT` and conditional `ROLLBACK`. Startup sets and verifies
WAL journaling, FULL synchronization and enabled foreign keys.

The version-1 owned schema contains `normalization_metadata`,
`candle_observations`, `processing_outcomes`, `processing_checkpoint` and
`processing_barrier`, all `STRICT` tables. Metadata binds state to one producer
while allowing new ingestion epochs. Outcomes record full ingestion identity,
predecessor, raw hash, classification, semantic reference or rejection evidence,
schema and instrument provenance, normalizer version, receipt evidence and
normalization time when available.

The runner reads only through `reader.records_after(checkpoint_offset, limit=1)`;
`DurableIngestor` satisfies that protocol without modification. With no checkpoint,
the starting offset is zero. The returned record must have the configured producer
and a strictly greater offset. Gaps such as `2, 5, 9` are valid: the checkpoint
means the last processed existing committed record, never all integer offsets
through that point. An empty read reports no work currently available.

For each processable or rejectable record, one transaction checks the expected
predecessor, inserts any new semantic observation, inserts the linked outcome,
advances the checkpoint, removes a matching barrier and verifies readback before
commit. A visible checkpoint always references its durable outcome, and following
predecessors reaches every earlier outcome exactly once in F05 order. Failed or
interrupted pre-commit work cannot put progress ahead of results.

## Barriers and restart

Unsupported schemas and unavailable metadata persist one bounded singleton
barrier, with blocker identity/hash, predecessor, reason and applicable schema or
instrument identity. A barrier is not a rejection or an outcome, contains no raw
payload, and leaves the checkpoint unchanged. Later records are not requested.

Each retry reads from that unchanged checkpoint and requires the next F05 record
to match the stored blocker identity and hash. Supplying missing metadata can
make the same record processable. An unsupported schema requires compatible
payload inspection and normalization support; the current implementation adds no
future schema reader. Successful
processing deletes the matching barrier atomically with outcome and checkpoint.

Startup rejects incompatible versions, unexpected schema objects, mismatched
producer metadata, failed `quick_check(1)` or foreign-key checks, invalid rows,
broken or disconnected predecessor chains, a checkpoint other than the only
tail, and barriers inconsistent with the checkpoint. It never infers progress
from observations or repairs inconsistent history.

SQLite recovers uncommitted writes after a crash. Pre-commit processing is retried
from the unchanged checkpoint; post-commit restart moves beyond the durable
outcome without duplicating success. A crash before barrier commit rediscovers
the blocker; a crash after it preserves the blocked state. If rollback fails or
leaves an open transaction, the store permanently fails and closes its connection;
subsequent operations raise the fixed `NormalizationError` message
`Durable normalization store failed`. Close the owner and restart cleanly.
`close()` releases ownership only after connection cleanup. Do not copy or repair
live SQLite, WAL or SHM files.

## F05 and F07 boundaries

F06 consumes only committed raw records through the public F05 reader. It never
opens or queries `ingestion.sqlite3`, changes its schema, normalizes uncommitted
envelopes or modifies the F03 domain contracts. Input bytes cannot select code,
imports, paths, SQL, network destinations or credentials. Foundation v1 gives
models no financial execution authority.

F07 archive objects, raw/normalized Parquet, publication manifests, publication
checkpoints, archive locators and spool pruning are outside this implementation.
Normalization progress is not archive publication or authorization to release raw
evidence. F06 adds no dependency and performs no networking or credential lookup.
