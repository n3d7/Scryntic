# F07 Recoverable Immutable Archive Publication Design

## Status and authority

This document records the approved architectural direction for KER-11 / F07,
"Publish the first immutable archive," and is pending design-spec review. It is
bounded by the approved `ARCHITECTURE.md`, Linear KER-11, and the accepted
F03-F06 contracts and implementations.

F07 implements the first local raw and normalized Parquet archive, immutable
content-addressed objects, recoverable publication state, and the first
versioned canonical manifest chain. It does not implement dataset selection,
codec benchmarking, remote synchronization, exhaustive publication hardening,
or retention deletion.

The terms **must**, **must not**, and **only** in this document define F07
requirements.

## Goals

F07 must:

- preserve every committed raw envelope byte-for-byte in an immutable raw
  Parquet object;
- publish every durable F06 processing outcome, including rejections,
  duplicates, revisions, finalizations, and conflicts, in normalized Parquet;
- retain ingestion identity, semantic identity, observation revision, and
  processing provenance as separate dimensions;
- publish bounded batches through a recoverable state machine that never puts
  local publication progress ahead of a committed manifest;
- use a canonical, versioned, SHA-256-linked manifest chain per producer epoch;
- keep the publication checkpoint producer-global and monotonically increasing
  across epoch changes;
- make a committed manifest the external visibility marker only after every
  referenced object is durable;
- recover an exact prepared manifest without regenerating or changing its
  bytes;
- implement the accepted `RawArchive` contract without allowing archived data
  to select code or arbitrary paths;
- retain all F05 spool rows throughout F07.

## Non-goals

F07 does not:

- select or export datasets (F08);
- benchmark Parquet against a framed or compressed raw codec, choose the final
  deployment codec, or establish operating-envelope sizes (F10);
- implement the full fsync/link/transaction fault matrix, exhaustive orphan
  classification, cross-version codec fixtures, or comprehensive epoch and
  sequence conflict handling (F11);
- import remote archives or run decoders under hostile-input isolation (F16 and
  F17);
- delete spool rows, archive objects, or manifests (F29);
- add networking, credentials, source discovery, live collection, or financial
  execution authority.

## Existing boundaries

### F05 raw input

F07 consumes committed raw records only through the public F05 method:

```python
records_after(offset: int, *, limit: int) -> tuple[RawRecord, ...]
```

F07 never opens or queries `ingestion.sqlite3`. F05 offsets are monotonically
increasing for a producer across all of that producer's epochs, but numeric
gaps are valid. F07 therefore treats an offset as an ordered cursor, never as a
gap-free sequence.

### F06 normalized input

F07 uses only owned immutable values returned by the public F06 store:

```python
status() -> NormalizationStatus
outcome(identity: IngestionId) -> ProcessingOutcome | None
observation(revision: str) -> CandleSemantics | None
```

F07 never opens or queries `normalization.sqlite3`. The existing point reads
are sufficient because the ordered F05 records provide the publication
candidate sequence. No F06 schema or publication cursor is added.

For every raw record selected for publication, F07 verifies:

- the outcome identity equals the raw ingestion identity;
- the outcome raw hash equals `RawEnvelope.content_sha256`;
- non-rejected outcomes name an existing semantic observation;
- the observation recomputes to the stored semantic revision;
- rejected outcomes have no semantic observation;
- all public values have their expected concrete contract types.

A missing outcome means normalization has not reached that raw record. It is a
normal waiting condition and stops candidate selection before that record. A
mismatched or internally inconsistent result is a publication failure and does
not advance state.

### F03 archive port

`ParquetRawArchive` implements the existing asynchronous `RawArchive.seal` and
`RawArchive.read` methods and returns the existing `RawSegment` and
`RawRecordRef` values. No F03 contract extension is required.

The implementation performs synchronous local I/O inside the bounded early
slice. F10 may change scheduling after measurement, but it must not change the
archive identity or read semantics.

## Component boundaries

F07 consists of four responsibilities.

### Parquet raw archive

The raw archive converts a non-empty tuple of ordered `RawRecord` values into
one immutable Parquet object and reconstructs one exact `RawRecord` from a
`RawRecordRef`. It owns raw Parquet schema validation, object hashing, archive
limits, and raw-record reconstruction.

### Normalized Parquet archive

The normalized archive writes one self-contained row per F06 processing
outcome. It owns the normalized Parquet schema and validates the complete
outcome/semantics projection before writing.

### Publication store

A separate `publication.sqlite3` owns reservations, exact pending inputs,
prepared manifest bytes, the local committed catalog, and the producer-global
publication checkpoint. It does not own the raw or normalized evidence.

### Publication coordinator and reader

The coordinator selects an eligible prefix, reserves it, seals objects,
prepares and installs a manifest, and registers the commit. The reader discovers
only committed manifests and verifies their canonical representation, identity,
chain relationship, and referenced objects before returning them.

## Identities, ordering, and epoch behavior

### Producer-global ingestion progress

The publication checkpoint is either absent or one complete `IngestionId`.
For a given producer, every new checkpoint offset must be greater than the old
checkpoint offset even when the raw epoch changes. The checkpoint is the last
existing committed ingestion record included in a committed manifest, not the
last integer in a gap-free range.

Candidate selection always starts with:

```python
reader.records_after(checkpoint.offset if checkpoint else 0, limit=batch_limit)
```

The coordinator verifies strictly increasing returned offsets and reserves only
a prefix of that exact tuple. It never requires `next.offset == previous + 1`.

### Per-epoch manifest chains

Manifest identity is:

```text
(producer, epoch, sequence, manifest_hash)
```

Each producer epoch has its own sequence and hash chain:

- the first manifest observed for an epoch uses sequence 1 and the explicit
  genesis predecessor of 64 ASCII zeroes;
- a later manifest for that epoch uses the preceding sequence and that
  manifest body's SHA-256;
- switching epochs does not reset or lower the producer-global publication
  checkpoint;
- if a previously seen epoch occurs again at a higher producer-global offset,
  its own chain continues at its next sequence;
- one publication batch never crosses an epoch boundary.

This separates chain identity from global processing order. Every manifest also
records the producer-global checkpoint immediately before and after its batch,
so epoch transitions remain auditable without creating a cross-epoch hash
chain.

### Logical and physical identities

- A raw record remains identified by `IngestionId`.
- A raw object is identified by SHA-256 of its complete Parquet bytes.
- A raw record locator remains `(segment_sha256, record_index, IngestionId)`.
- A semantic candle remains identified by its F06 semantic revision.
- A normalized row is a provenance outcome identified by `IngestionId`; a
  duplicate row does not create a new semantic observation.
- A manifest body is identified by its canonical-body SHA-256.

Committed identities are never overwritten or assigned different bytes.

## Batch selection

The coordinator obtains a bounded tuple of raw records after the global
checkpoint and walks it in order. A candidate record is eligible only when its
complete F06 outcome projection is durable and valid.

The reserved prefix ends before the first of:

- a record without a durable F06 outcome;
- a producer or ordering violation;
- an epoch change;
- a change in source, event-family, or coarse-date partition;
- the configured record or decoded-byte budget.

F07 currently publishes only the candle normalization pipeline. Its event
family is therefore the fixed local value `candle`, not a value selected by raw
payload bytes. The source comes from the validated envelope. The coarse date is
the UTC calendar date derived deterministically from receipt wall time. Batches
may contain multiple instruments.

If the first record is not eligible, the coordinator returns a typed no-work or
waiting result without creating a pending publication. Once a non-empty prefix
is reserved, its membership and sequence cannot be changed. A configuration
limit that makes the reserved batch unwritable leaves the pending publication
blocked; it does not silently shrink, skip, or reassign that sequence.

Each reserved input stores its ordinal, full ingestion identity, raw payload
hash, outcome kind, semantic revision when present, and a SHA-256 fingerprint of
the complete canonical raw/outcome/semantics projection. Recovery must reread
the same prefix through F05/F06 and reproduce every fingerprint before work may
continue.

### Canonical publication-input fingerprints

F07 defines two independently reproducible algorithms. Their identifiers are
`scryntic-publication-input-v1` and
`scryntic-publication-ordered-input-v1`. Both serialize with
`canonical-json-v1`, as defined under **Canonical bytes and hash chain**, and
produce a lowercase, unprefixed 64-character SHA-256 hexadecimal digest. A
future algorithm must use a new identifier; it must not change either v1
projection.

The reusable value projections are exact-field objects:

- an ingestion identity is `{"producer": string, "epoch": string,
  "offset": int64}`;
- a schema reference is null or `{"name": string, "major": int64,
  "minor": int64}`;
- a source time is null or `{"value": int64, "unit": "s" | "ms" |
  "us" | "ns"}`;
- time quality is `{"epoch": string, "status": string, "offset_ns":
  int64 | null, "uncertainty_ns": int64 | null, "evidence_age_ns": int64 |
  null}`;
- a receipt is `{"wall_time_ns": int64, "monotonic_ns": int64,
  "session_id": string, "quality": time-quality}`;
- a subject is null, `{"type": "instrument", "venue": string,
  "category": string, "symbol": string}`, or `{"type": "entity",
  "kind": string, "namespace": string, "value": string}`.

The `raw` object contains exactly `identity`, `source`, `stream`, `channel`,
`adapter_version`, `receipt`, `subject`, `source_time`, `source_event_id`,
`source_sequence`, `payload_hex`, and `content_sha256`. `payload_hex` is the
lowercase, even-length hexadecimal encoding of the complete payload, with no
prefix; empty bytes encode as the empty string. Before hashing, F07 recomputes
`content_sha256` from those payload bytes and requires the lowercase stored
value to match.

The `outcome` object contains exactly `identity`, `predecessor`, `raw_sha256`,
`kind`, `semantic_revision`, `rejection_code`, `rejection_field`,
`input_schema`, `instrument_schema`, `output_schema`, `instrument_revision`,
`normalizer_version`, `receipt`, and `normalized_at_ns`. Nullable values remain
explicit JSON nulls; they are never omitted.

For a rejected outcome, `semantics` is null. Otherwise it is an object with
exactly `key`, `open`, `high`, `low`, `close`, `volume`, `volume_unit`,
`finalized`, `source_time`, `publication_time`, and `quality_flags`. `key` is
exactly `{"instrument": {"venue": string, "category": string, "symbol":
string}, "start_ns": int64, "interval_ns": int64}`. Decimal values and the
semantic revision are the already-canonical F06 strings; quality flags retain
their sorted tuple order as a JSON array.

For one input, form exactly:

```json
{"algorithm":"scryntic-publication-input-v1","outcome":{...},"raw":{...},"semantics":null}
```

where the final member is either null or the exact semantics object. The input
fingerprint is SHA-256 of that object's canonical JSON bytes. Identity and
receipt deliberately occur in both owned projections: a mismatch is an error,
not a value to normalize away.

For a reserved batch, form exactly:

```json
{"algorithm":"scryntic-publication-ordered-input-v1","inputs":[{"identity":{...},"input_fingerprint":"<64 lowercase hex>"}],"record_count":1}
```

The `inputs` array contains every reserved input in ordinal order and no other
entry. The ordered-input digest is SHA-256 of these canonical JSON bytes.
Ordered-input v1 requires every member fingerprint to use publication-input v1;
it does not permit a per-entry algorithm choice.
Offsets must be strictly increasing but need not be consecutive, and adjacent
entries may use different epochs only in a conformance vector: an actual F07
publication remains single-epoch. The reservation stores the fingerprint
algorithm identifier, every identity/fingerprint pair, the ordered-input
algorithm identifier, and the resulting digest. The manifest stores the
ordered-input algorithm identifier and digest. Reservation recovery and
manifest reading rebuild these values from the same public F05/F06 projections;
neither may trust a stored digest without recomputation.

The following fixed vectors are normative. For the input vector, the payload
bytes are hexadecimal `00ff41`, whose SHA-256 is
`a90a10503fbfc95789ff38a1bb5039cb71869ab9c0eb1cb51c4a9099f2933c6b`.
The canonical input bytes are the single line:

```text
{"algorithm":"scryntic-publication-input-v1","outcome":{"identity":{"epoch":"epoch-a","offset":7,"producer":"prod-a"},"input_schema":null,"instrument_revision":null,"instrument_schema":null,"kind":"rejected","normalized_at_ns":null,"normalizer_version":"f06.fake_candle.v1","output_schema":null,"predecessor":null,"raw_sha256":"a90a10503fbfc95789ff38a1bb5039cb71869ab9c0eb1cb51c4a9099f2933c6b","receipt":{"monotonic_ns":90,"quality":{"epoch":"quality-a","evidence_age_ns":null,"offset_ns":null,"status":"unknown","uncertainty_ns":null},"session_id":"session-a","wall_time_ns":100},"rejection_code":"invalid_utf8","rejection_field":null,"semantic_revision":null},"raw":{"adapter_version":"v1","channel":"public","content_sha256":"a90a10503fbfc95789ff38a1bb5039cb71869ab9c0eb1cb51c4a9099f2933c6b","identity":{"epoch":"epoch-a","offset":7,"producer":"prod-a"},"payload_hex":"00ff41","receipt":{"monotonic_ns":90,"quality":{"epoch":"quality-a","evidence_age_ns":null,"offset_ns":null,"status":"unknown","uncertainty_ns":null},"session_id":"session-a","wall_time_ns":100},"source":"fake","source_event_id":null,"source_sequence":null,"source_time":null,"stream":"candles","subject":null},"semantics":null}
```

Its input fingerprint is
`aab4bbe154acd69d3bf0b535de0dd15741a4f870a8c5cb7d40246745ca7f7838`.
Using that value plus the deliberately synthetic second fingerprint
`0000000000000000000000000000000000000000000000000000000000000001`,
the ordered vector is:

```text
{"algorithm":"scryntic-publication-ordered-input-v1","inputs":[{"identity":{"epoch":"epoch-a","offset":7,"producer":"prod-a"},"input_fingerprint":"aab4bbe154acd69d3bf0b535de0dd15741a4f870a8c5cb7d40246745ca7f7838"},{"identity":{"epoch":"epoch-b","offset":11,"producer":"prod-a"},"input_fingerprint":"0000000000000000000000000000000000000000000000000000000000000001"}],"record_count":2}
```

Its ordered-input digest is
`fa95b24754be337ca3f78d08123abfbee75d4b92f81c05f7456db592a1f1e842`.

### Local publication limits

The coordinator receives an explicit immutable `PublicationLimits` value with:

- `max_batch_records`;
- separate raw and normalized `ArchiveLimits`;
- `max_manifest_bytes`;
- `max_manifests_per_read`.

Every field is a positive integer validated before filesystem or SQLite work.
These are local safety and operating limits, not product retention or
instrument-count limits. Manifest reads check `max_manifest_bytes` before JSON
decoding, and discovery never returns more than `max_manifests_per_read` in one
call. Codec v1 requests at most 1,024 rows from `iter_batches` at a time; this
fixed working size controls iteration granularity, not a promised peak-memory
ceiling.

## Parquet formats

F07 adds `pyarrow==25.0.1` to the collector dependency group and lock. The first
codec identifier is `parquet-pyarrow-v1`. It maps only to locally installed,
reviewed code; a file or manifest value cannot import or download a codec.

Writer behavior is explicit rather than inherited from PyArrow defaults:

- Parquet format version 2.6;
- one bounded row group per F07 object;
- explicit Arrow schemas and nullability;
- no schema inference from payload values;
- no compression for the first simple codec;
- dictionary encoding disabled;
- no deprecated INT96 timestamps;
- fixed schema metadata naming the F07 format and codec versions.

F10 may select a different codec for new segments. It must not rewrite or make
old `parquet-pyarrow-v1` objects unreadable.

### Raw Parquet schema

The raw format schema is `scryntic.raw-record.parquet` version 1.0. It contains
one row per raw record in ascending producer-global offset order and has
explicit columns for:

- producer, epoch, and offset;
- source, stream, channel, and adapter version;
- receipt wall time, monotonic time, session ID, and every time-quality field;
- tagged subject kind plus all instrument/entity identity components;
- nullable source time value and unit;
- nullable source event ID and sequence;
- payload as Arrow binary;
- lowercase payload SHA-256.

The payload is never decoded as text. On read, the implementation reconstructs
the domain value, checks the stored hash against the exact bytes, and requires
the reconstructed identity to equal the `RawRecordRef` identity and ordinal.

### Normalized Parquet schema

The normalized format schema is `scryntic.normalization-outcome.parquet`
version 1.0. It contains exactly one row for every raw row in the same order.
Each row contains:

- outcome ingestion identity and nullable predecessor identity;
- raw SHA-256 and outcome kind;
- nullable semantic revision;
- nullable rejection code and field;
- input, instrument, and output schema name/major/minor triples;
- nullable instrument revision;
- normalizer version;
- complete receipt and time-quality evidence;
- nullable normalization time;
- for non-rejected outcomes, the complete `CandleSemantics`: instrument key,
  start and interval, OHLCV, volume unit, finality, nullable source and
  publication times, and quality flags.

Canonical OHLCV values remain strings. F06 permits up to 96 significant digits,
which does not fit Parquet Decimal256, and a Decimal-to-float conversion is not
allowed. Quality flags use a bounded Arrow list of non-null strings.

A duplicate processing outcome repeats the semantic values under the same
semantic revision so that every normalized row is independently readable. It is
still one logical observation identity with multiple provenance outcomes.

### ArchiveLimits during reads

For codec v1, `decoded_bytes` is a deterministic logical-size measure, not an
estimate of Arrow heap use. A raw row's logical bytes are the length of the
`canonical-json-v1` encoding of `{"raw": <exact raw projection>}`. A normalized
row's logical bytes are the length of the same encoding of
`{"outcome": <exact outcome projection>, "semantics": <exact semantics or
null>}`. A segment's `decoded_bytes` is the sum for its rows in storage order.
The projections are exactly those defined for
`scryntic-publication-input-v1`; no Parquet metadata, padding, dictionary, or
Arrow buffer size contributes to this value.

Seal and read use this same measure. Before constructing Arrow arrays, sealing
validates every domain value, computes each row's logical size, and rejects a
batch whose record count or cumulative logical bytes exceed the supplied
`ArchiveLimits`. After writing and closing the staging file, it rejects an
encoded file over `max_encoded_bytes` before object installation. The exact
row count and logical decoded bytes are stored as canonical ASCII decimal in
the fixed schema-metadata keys `scryntic.record_count` and
`scryntic.logical_decoded_bytes` and returned in the descriptor.

`ArchiveLimits` is enforced before and during Parquet decoding, not merely after
materializing a table. Before constructing a PyArrow reader, reading must:

1. open the generated object path without following symlinks;
2. require a regular owned immutable file;
3. compare `fstat().st_size` with `max_encoded_bytes`;
4. stream and verify the complete object SHA-256;
5. read the fixed Parquet trailer directly, verify the magic, and reject a
   footer length outside the already bounded encoded file.

After opening only the bounded metadata, but before decoding row values, it
must verify:

- exact expected schema, column count, codec metadata, and one-row-group layout;
- metadata row count no greater than `max_records`;
- codec-v1's required absence of compression and dictionary encoding;
- column-chunk offsets and encoded sizes contained in the already-bounded file;
- nonnegative footer compressed and uncompressed size values, without using
  the uncompressed value as a decode-memory bound;
- the declared logical decoded bytes no greater than `max_decoded_bytes`;
- descriptor encoded/decoded sizes and row count when a manifest descriptor is
  available.

Rows are then decoded with `ParquetFile.iter_batches`, not an unbounded
whole-table API. The requested batch row count is capped by the codec-v1
working size of 1,024 and the remaining `max_records`. Each decoded row is
validated and reconstructed under the existing F03/F05/F06 per-field bounds;
raw reconstruction supplies a payload limit no greater than the remaining
logical-byte budget. The reader recomputes the canonical logical size row by
row and stops with a fixed archive error as soon as accepting the next row
would exceed `max_records` or `max_decoded_bytes`.

Metadata claims are not treated as sufficient: final observed row count must
equal both metadata and descriptor row counts, and cumulative
logical bytes must equal both the schema-metadata declaration and descriptor
`decoded_bytes` when a descriptor is available. Any mismatch fails the read
with a fixed archive error.

Parquet footer `total_uncompressed_size` values describe encoded column chunks;
they are neither a guaranteed upper bound on Arrow allocations nor this
logical-size measure. F07 validates them only as structurally valid metadata.
Likewise, `iter_batches` limits output batch rows but does not guarantee a
strict peak-memory cap: an implementation may decode a row group before
slicing it into batches. F07 therefore promises enforceable limits on input
file bytes, footer bytes, row count, domain-field sizes, and accepted logical
decoded bytes, but does not claim an RSS or allocator peak. The single bounded
row group, explicit schema, no-compression codec, pre-decode metadata checks,
and incremental domain validation are the F07 in-process controls. F16 owns
hostile-import isolation and hard process-memory containment.

## Object storage and partition views

Authoritative objects use a content-addressed namespace under the private
archive root:

```text
objects/sha256/<first-two-hex>/<object-sha256>.parquet
```

All path components are generated from fixed literals and validated hashes.
Raw source strings, payloads, schemas, and manifest fields never become path
components.

To meet the source/family/date organization requirement without changing the
hash-only F03 archive locator, the publisher creates immutable hard-link views:

```text
partitions/<role>/<source-digest>/candle/<YYYY-MM-DD>/<object-sha256>.parquet
```

These links are non-authoritative indexes. Raw archive reads use the canonical
content-addressed object. Readers discover publication only through committed
manifests, never through either object tree. All staging, object, partition, and
manifest directories must be on the same filesystem.

An object is written to a unique staging file, closed, reopened and validated,
hashed, made read-only, and fsynced. It is installed at its content-addressed
name with a same-filesystem hard link, which is atomic and fails if the name
already exists. The committed object directory is then fsynced. The partition
view is installed by the same no-replace mechanism and its directory is
fsynced. The staging link is removed and its directory is fsynced.

If the content-addressed name already exists, the publisher may reuse it only
after verifying the complete hash, size, Parquet schema, codec, and row count.
An existing identity with different bytes or metadata is an error and is never
replaced.

Objects installed before their manifest are possible crash orphans. They are
not visible publication and F07 never deletes them.

## Publication SQLite state

F07 owns `publication.sqlite3` and `publication.lock` in the approved state
directory. The cooperative lock is acquired before opening SQLite and is held
for the store lifetime. It is an ownership protocol, not a security boundary.

The store uses one owning-thread connection with:

- `autocommit=True`;
- explicit `BEGIN IMMEDIATE`, `COMMIT`, and `ROLLBACK`;
- WAL mode;
- `synchronous=FULL`;
- `foreign_keys=ON`;
- a versioned strict schema;
- fixed-message public failures.

Startup verifies the exact schema objects, producer ownership, effective
pragmas, `quick_check(1)`, foreign-key consistency, catalog relationships,
checkpoint ownership, and pending-state shape. It never repairs or fabricates
progress.

The logical tables are:

- `publication_metadata`: singleton schema/producer ownership;
- `publication_catalog`: immutable committed manifest identity, exact bytes,
  per-epoch predecessor, input coverage, and object descriptors;
- `publication_checkpoint`: singleton reference to the catalog entry that owns
  the highest producer-global published offset;
- `pending_publication`: at most one reserved or prepared publication;
- `pending_inputs`: exact ordered input identities and fingerprints belonging
  to the pending publication.

Catalog rows are unique by `(producer, epoch, sequence)` and by manifest hash.
The checkpoint foreign key targets one catalog row and its decoded ingestion
checkpoint must equal that row's ending checkpoint. Catalog insertion,
checkpoint advancement, and pending deletion occur in one SQLite transaction.

## Manifest contract

### Body

Manifest schema `scryntic.publication-manifest` version 1.0 has a body containing
exactly:

- schema name, major, and minor;
- serialization algorithm `canonical-json-v1`;
- hash algorithm `sha256`;
- producer, epoch, and positive sequence;
- previous manifest hash for this producer epoch;
- nullable producer-global ingestion checkpoint before the batch;
- producer-global ingestion checkpoint after the batch;
- first and last ingestion identities, record count, field
  `ordered_input_algorithm` set to
  `scryntic-publication-ordered-input-v1`, and field `ordered_input_digest` set
  to its lowercase SHA-256 result;
- source, fixed event family, and UTC date partition;
- exactly two object descriptors in fixed role order: `raw`, then `normalized`.

Each object descriptor contains exactly:

- role;
- object SHA-256;
- format schema name/major/minor;
- codec identifier;
- encoded bytes;
- decoded bytes;
- row count.

Object paths are not serialized. They are generated locally from validated
identities and partition fields.

### Canonical bytes and hash chain

The committed document is:

```json
{"body":{...},"manifest_hash":"<lowercase sha256>"}
```

Canonical JSON v1 is defined as:

- strict UTF-8 with no BOM;
- no trailing newline;
- only objects, arrays, strings, signed 64-bit integers, booleans, and null;
- no floats or non-finite numbers;
- exact field sets and types;
- duplicate object keys rejected;
- object keys sorted lexicographically by Unicode code point;
- Python JSON settings equivalent to `ensure_ascii=True`, `allow_nan=False`,
  `sort_keys=True`, and `separators=(",", ":")`;
- array order preserved and significant.

`manifest_hash` is lowercase SHA-256 of the canonical bytes of `body` alone.
The complete wrapper is then canonicalized using the same rules. A reader must
strictly decode, recompute the body hash, reserialize the wrapper, and require
byte-for-byte equality with the file.

For sequence 1, `previous_manifest_hash` is exactly 64 ASCII zeroes. For
sequence greater than 1, it is the preceding manifest body's hash for the same
producer and epoch.

### Committed name

The committed manifest epoch directory is derived from a digest of the producer
and epoch identities. Beneath it, a generated fixed-width decimal sequence
directory contains a filename generated only from the body hash:

```text
manifests/<producer-epoch-digest>/<fixed-width-sequence>/<manifest-hash>.json
```

Raw identifiers cannot select a path. The generated sequence directory is
created and made durable before installation. A reader inspects at most two
entries in that single directory: zero is missing, one must be the expected
regular manifest file, and a second is an obvious conflicting identity. It
never scans the complete epoch directory merely to resolve one manifest.

The same `(producer, epoch, sequence)` with a different hash, the same manifest
hash attached to a different identity, or multiple committed files claiming
one sequence is an obvious identity conflict and must be rejected. F11 expands
this into the full conflict and collision matrix.

## Publication state machine

### 1. Reserve

With no existing pending publication, the coordinator validates the exact
eligible prefix and commits one reservation transaction containing:

- the current producer-global checkpoint;
- the batch end checkpoint;
- the selected epoch;
- the next sequence and previous hash for that epoch;
- the partition;
- the two fingerprint/digest algorithm identifiers, every ordered input
  identity and fingerprint, and the computed ordered-input digest.

The publication checkpoint does not advance. A second publication cannot be
reserved until this one is committed.

### 2. Seal objects

The exact reserved inputs are reread through F05 and F06 and compared with the
reservation. Raw and normalized staging Parquet files are written, validated,
hashed, fsynced, installed without replacement, and directory-fsynced as
described above.

### 3. Prepare manifest

After both referenced objects and partition views are durable, the coordinator
constructs the manifest body and wrapper exactly once. One SQLite transaction
changes the pending state from `reserved` to `prepared` and stores:

- the exact complete manifest bytes as a BLOB;
- the manifest body hash;
- the two verified object descriptors.

Once this transaction commits, recovery must never reserialize the manifest,
change its sequence, select a different object, or change any byte.

### 4. Install manifest durably

The coordinator reads the exact prepared BLOB and:

1. writes it to a unique staging file in the manifest filesystem;
2. flushes and fsyncs the complete prepared manifest file;
3. makes the file read-only and fsyncs the metadata-bearing file again;
4. checks the generated sequence directory with the bounded zero/one/two-entry
   rule and installs at the generated committed name without replacement only
   when no conflicting entry exists;
5. fsyncs the committed manifest directory;
6. removes the staging link and fsyncs the staging directory.

No catalog or checkpoint transaction may begin before step 5 succeeds.

If the committed name already exists, it is idempotent success only when its
bytes exactly equal the prepared BLOB and a full manifest/object validation
succeeds. A different file, a conflicting sequence identity, or an invalid
referenced object fails closed and preserves the pending publication.

Atomic no-replace installation makes the complete manifest name visible to
concurrent readers. It is safe to validate and read at that point because the
manifest file was fsynced and all referenced objects were already durable. The
subsequent committed-directory fsync makes that installed name crash-durable.
The local catalog/checkpoint commit must wait for this second durability
boundary.

### 5. Register catalog and checkpoint

After durable manifest installation, one SQLite transaction:

- inserts the immutable catalog row and exact manifest bytes;
- updates the producer-global publication checkpoint to the batch end;
- verifies that the new offset is strictly greater than the previous offset,
  regardless of epoch;
- deletes the pending inputs and publication.

Readback validation occurs before commit. Transaction failure rolls back all
three changes. Rollback failure makes the publication store fatal until clean
restart.

F07 may report the resulting checkpoint as `eligible_through` for future
retention work, but it does not call a prune operation or delete F05 evidence.

## Reader visibility and validation

The committed manifest namespace is authoritative. Object listings, partition
views, staging files, and an optional future head hint are never evidence of a
publication.

A reader accepts a manifest only after all of the following succeed:

- safe bounded file open and encoded-size check;
- strict canonical JSON parse and byte-for-byte canonical reserialization;
- wrapper body-hash verification;
- filename identity equals manifest identity;
- schema, serialization, and hash algorithms are supported locally;
- sequence/genesis or predecessor relationship is valid for the requested
  chain step;
- producer-global before/after checkpoints are ordered;
- first/last/count coverage agrees with the checkpoints;
- object descriptors have distinct required roles and valid formats;
- every referenced object exists at its generated content-addressed path;
- every object's full hash, encoded size, Parquet schema, codec, decoded-size
  bound, and row count match its descriptor;
- raw and normalized rows have the same ordered ingestion identities and their
  independently recomputed input fingerprints and ordered-input digest match
  the manifest.

### Exact manifest resolution

An exact lookup starts from either a complete manifest identity or the unique
local catalog row for a manifest hash. Hash alone is not used to scan the
filesystem. The reader derives the one sequence directory, rejects a second
entry there as an obvious identity conflict, and validates the expected file
and objects in full. A local catalog is only an index: its exact stored manifest
bytes, identity, before/after checkpoints, object descriptors, and
ordered-input digest must all equal the externally validated values. A catalog
row with a missing or invalid manifest is a fatal inconsistency, not an
invisible or empty result.

From one exact manifest the reader can prove only bounded facts: internal
canonical identity; strictly increasing raw offsets; equality of the first,
last, count, and after checkpoint with the decoded rows; an absent before
checkpoint or a same-producer lower before offset; object integrity; and the
ordered-input digest. For sequence 1 it verifies the genesis predecessor. For a
later sequence, the reader derives the exact predecessor path from `(producer,
epoch, sequence - 1, previous_manifest_hash)`, validates that predecessor's
canonical identity and body hash without recursively walking earlier history,
and requires the local catalog entry, when present, to agree. A missing or
invalid exact predecessor means per-epoch continuity is not validated and the
chain-step read fails.

If the catalog contains a producer-global predecessor whose ending checkpoint
equals the current `checkpoint_before`, exact resolution validates that catalog
entry and the predecessor's canonical external manifest identity as a second,
independent anchor, again without recursive traversal. It may belong to a
different epoch from the per-epoch predecessor. Absence of such a local entry
is valid only when `checkpoint_before` is null; otherwise it is a fatal
local-catalog inconsistency. A manifest that is externally visible in the
intentional pre-catalog crash window can therefore be validated as a
self-contained publication, but global-history continuity remains unconfirmed
until recovery registers it or the caller supplies the global predecessor.

### Bounded producer-global coverage

Producer-global validation reads at most `max_manifests_per_read` catalog rows
in ascending ending-offset order, beginning after a validated global anchor or
at the producer's first publication. Numeric offset gaps are allowed. For each
row and its validated external manifest, the reader requires:

- the same producer throughout the page;
- `checkpoint_before` to equal the preceding page/anchor manifest's
  `checkpoint_after` exactly;
- `checkpoint_after.offset` to be strictly greater than the preceding ending
  offset, regardless of epoch;
- raw-row offsets to be strictly increasing, all greater than
  `checkpoint_before.offset` when present, with first/last/count and the final
  row equal to the manifest coverage fields and `checkpoint_after`;
- raw and normalized identities to match at every ordinal, with independently
  recomputed input fingerprints and ordered-input digest.

Interleaved epochs do not make globally adjacent manifests per-epoch
predecessors. For every manifest in the page, sequence 1 must use the genesis
hash. For sequence greater than 1, the reader performs one direct exact-path
lookup of `(producer, epoch, sequence - 1, previous_manifest_hash)`, validates
that external predecessor's canonical identity and body hash without recursive
history traversal, and requires any catalog row for it to agree. If the
predecessor already appears in the page, the cached validated value is reused.
Thus a page of `N` manifests requires at most `N` direct per-epoch predecessor
lookups in addition to the page, and switching away from and later back to an
epoch cannot reset or skip its chain.

The page proves continuity only from its supplied or validated global anchor
through its final manifest; it does not claim the producer's entire history was
enumerated. Exact-hash resolution similarly cannot prove that no unrelated
content has the same cryptographic digest. F11 owns exhaustive history,
collision, and orphan-conflict handling.

Readers reject obvious conflicts, including two manifests claiming the same
epoch/sequence with different hashes, a predecessor mismatch, overlapping or
non-monotonic producer-global coverage, or one content hash resolving to
different bytes. F11 adds exhaustive history and adversarial collision cases;
F07 does not silently choose one conflicting identity.

## Crash and restart recovery

Startup reconciles at most the one pending publication before allowing a new
reservation.

### No pending publication

The catalog checkpoint is the local progress authority. Committed catalog rows
and their external manifests are validated according to the F07 startup scope.
Unknown object files are retained.

### Reserved pending publication

The checkpoint must still equal the reservation predecessor. Recovery rereads
the same F05 prefix and F06 values and requires exact identity/fingerprint
matches. It then seals or reuses valid objects and proceeds to prepare.

Objects left by a crash may be reused only after complete identity validation.
Other objects remain untouched as uncertain orphans.

### Prepared pending publication

Recovery validates the exact stored bytes and the referenced durable objects.
It writes and installs only the stored BLOB. It never regenerates the manifest
or substitutes new object identities.

If the expected committed file is absent, recovery completes its durable
installation. If it already exists with identical bytes and valid objects,
recovery treats publication as externally committed. Any mismatch fails closed.

### Manifest committed, catalog behind

The pending prepared record must still exist. Recovery validates the committed
manifest and objects, then performs the catalog/checkpoint transaction. This is
the only recovery path that advances local progress, and it advances it to the
checkpoint already fixed by the exact externally committed manifest.

### Catalog committed

The pending record is absent and the checkpoint references the committed
catalog row. Restart proceeds after validation and the next candidate read uses
that producer-global offset.

### Recovery table

| Interruption point | Reader visibility | Restart action |
| --- | --- | --- |
| Before reservation commit | None | Select a new eligible prefix |
| After reservation | None | Revalidate exact reserved inputs |
| After object installation | None | Reuse verified objects or finish sealing |
| After prepare commit | None | Use only stored manifest bytes |
| During manifest staging/fsync | None | Rewrite staging from stored bytes |
| After no-replace install, before committed-directory fsync | Visible and valid, but the name is not yet proven crash-durable | If the name survived, validate and repeat directory fsync; otherwise reinstall the exact prepared bytes |
| After committed-directory fsync, before catalog commit | Visible and valid | Register exact manifest and checkpoint |
| After catalog commit | Visible and locally registered | Continue after new global checkpoint |

## Error and lifecycle behavior

The publication owner is synchronous and single-threaded. It exposes typed
results for no work, waiting for normalization, successful publication, and
recovered publication. Storage, codec, reader, and consistency failures use
fixed public error messages and retain their original causes only internally.

An exception from the current F05 `records_after` implementation is contained
at the F07 boundary and cannot advance reservation or publication state. The
known F05 invalid-TEXT decoding error boundary remains a separate F05 defect;
F07 does not modify F05 while implementing this task.

`close()` is idempotent and releases cooperative ownership only after SQLite and
owned descriptors are closed. A failed rollback or indeterminate connection
state permanently fails that store instance.

## Validation strategy

### Raw archive

Tests must cover:

- round-trip equality for every `RawRecord` field;
- payloads containing NUL bytes, arbitrary high bytes, invalid UTF-8, and empty
  bytes;
- repeated payloads with distinct ingestion identities;
- record-index and ingestion-identity mismatch;
- corrupted payload/hash, object hash, footer, schema, and row count;
- seal success at the exact `max_records` and logical `max_decoded_bytes`
  boundaries and failure one unit below each boundary before Arrow construction;
- read success at exact `max_encoded_bytes`, `max_records`, and declared logical
  `max_decoded_bytes` boundaries and failure one unit below each boundary;
- oversized or inconsistent declared logical sizes rejected before row-value
  decoding, and an actual recomputed logical-size mismatch rejected during
  iteration;
- one maximum-size row, many small rows, and a variable-length value that would
  exceed the remaining logical budget;
- footer uncompressed sizes that are larger or smaller than logical decoded
  bytes without treating either as an Arrow-memory bound;
- iteration with the codec-v1 working batch size without asserting that batch
  size is a strict process-memory cap.

### Normalized archive

Fixtures must cover accepted, duplicate, open revision, finalization, conflict,
and rejected outcomes; exact decimal strings; finality; nullable times; every
provenance schema; quality evidence; and rejection before metadata resolution.

Every batch test must prove that raw and normalized rows have the same ordered
ingestion identities and that equivalent semantics retain one semantic
revision even when provenance rows repeat it.

### Canonical manifests and chains

Fixed conformance vectors must hard-code:

- the exact canonical publication-input bytes and input fingerprint specified
  above, including binary payload hex encoding;
- the exact ordered-input bytes and digest specified above, including the
  non-consecutive, cross-epoch vector entries;
- exact canonical body bytes;
- exact wrapper bytes;
- the body SHA-256;
- a sequence-1 genesis manifest;
- a sequence-2 manifest whose predecessor equals the sequence-1 body hash;
- an epoch transition where the per-epoch sequence resets to 1 while the
  producer-global ingestion checkpoint increases;
- continuation of a previously seen epoch at a later global offset.

Negative vectors must reject duplicate keys, UTF-16/32, BOMs, floats,
out-of-range integers, unknown/missing fields, noncanonical whitespace or key
order, uppercase hashes, bad predecessor hashes, conflicting sequence files,
descriptor/object mismatches, reordered or omitted fingerprint inputs, and a
payload whose recomputed content hash differs from the canonical projection.

Bounded reader tests must also cover a producer-global page whose manifests
alternate epochs and contain legitimate offset gaps, continuation of each
epoch's own sequence/hash chain after interleaving, a mismatched global anchor,
a missing or wrong direct per-epoch predecessor, a page limit smaller than the
remaining history, exact-hash lookup through the catalog, exact lookup during
the pre-catalog visibility window, and two files in one generated sequence
directory. They must distinguish self-contained exact-manifest validity from
validated global and per-epoch continuity.

### Transactions and process interruption

Focused SQLite tests inject reservation, prepare, catalog insert, checkpoint
update, pending deletion, commit, and rollback failures. No failed transaction
may advance publication progress.

Subprocess kill/restart tests cover:

- before and after reservation commit;
- after each object becomes durable;
- immediately before and after prepare commit;
- during manifest staging and after manifest file fsync;
- after no-replace installation but before committed-directory fsync;
- after committed-directory fsync but before catalog commit;
- immediately after catalog commit.

Tests must assert that:

- no reader accepts a batch before atomic installation in the committed
  namespace, and any manifest visible there is already complete and references
  durable objects;
- catalog/checkpoint state never advances before committed-directory fsync;
- referenced objects are durable before an accepted manifest;
- a prepared manifest's bytes are identical before and after restart;
- a visible manifest with a lagging catalog is registered exactly once;
- no sequence is reused for different inputs or bytes;
- numeric ingestion-offset gaps do not block valid progress;
- the global checkpoint remains monotonic across epoch changes;
- every F05 spool row remains available at every failure point.

F11 later expands these representative F07 crash points into the full
filesystem/SQLite fault matrix and prior-codec compatibility suite.

### Task-boundary validation

Implementation completion requires focused F07 tests plus the repository's full
`bash scripts/check.sh` gate. Because F07 adds a locked runtime dependency, the
boundary validation must also exercise lock integrity, dependency audit,
package build/rebuild, installation from the built wheel under the collector
profile, and an import/read-write smoke test using the locked PyArrow version.

## Material trade-offs

### Content-addressed objects plus manifest commit marker

This is selected over SQLite-only publication because manifests must be usable
as external immutable commit markers and later synchronization inputs. It is
selected over a filesystem-only journal because SQLite provides a direct atomic
reservation/checkpoint invariant and exact prepared-byte durability.

### One normalized outcome table

A self-contained row per processing outcome is selected over separate
observation and provenance Parquet objects. It duplicates semantic column values
for duplicate deliveries but keeps every ingestion outcome independently
auditable and avoids requiring a reader to locate an observation in an earlier
manifest. Semantic identity still deduplicates logically.

### No-replace hard-link installation

Same-filesystem hard links provide an atomic standard-library no-replace
operation on the supported Linux filesystem. A `rename` fallback that can
replace a committed identity is prohibited. Lack of required filesystem support
is an explicit startup/publication failure, not permission to weaken the
protocol.

### No Parquet byte-reproducibility promise

PyArrow output bytes may depend on the pinned writer version. F07 hashes the
actual closed object and persists exact manifest bytes before external commit.
Recovery before prepare may produce and retain a different unreferenced object;
recovery after prepare may not re-encode or change the referenced object or
manifest.

## Remaining risks and later work

- Cooperative locks prevent conforming concurrent writers but are not a
  security boundary.
- The durability guarantee assumes a functioning local filesystem and storage
  stack that honor file and directory fsync.
- F07 rejects evident identity conflicts but does not attempt F11's exhaustive
  conflict reconciliation or orphan proof.
- PyArrow decoding remains in-process for trusted local objects in the early
  slice. F16 owns hostile-import isolation.
- F07 never prunes. Capacity measurement is F10 and operator-controlled
  retention is F29.
- The current F05 invalid stored-TEXT decoding path can surface a raw SQLite
  error. F07 contains that exception without progress; repairing F05 itself is
  separate work.

No unresolved architectural choice blocks implementation planning after this
specification is approved.
