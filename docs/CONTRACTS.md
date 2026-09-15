# Foundation contract ownership and compatibility

F03 implements the contract surface approved in `ARCHITECTURE.md`. It adds no
integrations, state stores, job runner, deserializer, registry or financial
operations. Python and dependency policies remain those of F01/F02.

| Owner/module | Contract and responsibility |
| --- | --- |
| `domain.identity` | Namespaced instrument/entity identity; Python contract and external schema versions. |
| `domain.time` | UTC POSIX wall time, original source timestamp units, session-scoped monotonic samples and time-quality claims. |
| `domain.market` | Exact instrument metadata and candle observation values, logical candle key and revision provenance. |
| `domain.raw` | Bounded original bytes and locally assigned ingestion identity. |
| `domain.dataset` | Immutable dataset manifest digest, row-count claim and input schema; no filesystem paths. |
| `application.sources` | Source declarations and optional streaming/history ports, bounded pages and opaque cursors. |
| `application.clock` | Read-only clock evidence port. |
| `application.archive` | Immutable raw segment/record references, caller-owned limits and raw reader/writer port. |
| `application.providers` | Minimal forecast descriptor, request/result correlation and capability checks. |
| `application.dto` | Interface-neutral collection/dataset request and result values for the early slice. |
| `tests/contracts` | Trusted fake source/clock/provider, conformance examples and type/import boundary tests. |

Source/provider implementations will live outside these contracts and import
inward. The proposed architecture's `sources`/`providers` boundaries retain
adapter ownership; their shared ports are application-owned so the core need
not import an implementation package. Domain modules use an explicit, small
stdlib allowlist and other domain values. Application contracts may additionally
import other application contracts. Root package imports must remain leaf-safe.
No SDK, CLI, network, storage-engine, secret-resolution or code-loading API is
permitted. Tests inspect imports, including dormant/type-only imports, and
recursively inspect data annotations. These fences are development checks, not
a sandbox for hostile Python code.

## Value and identity decisions

- Dataclasses are frozen and slotted. Collections are tuples/frozensets; mutable
  alternatives are rejected. Constructors check scalar invariants without
  silent coercion. They are typed Python boundaries, not parsers for arbitrary
  JSON, objects or byte streams; future adapters must validate untrusted input
  before constructing values and again at acceptance boundaries.
- Opaque identifiers are case-sensitive ASCII tokens, at most 256 characters,
  containing letters/digits and `_.:-`. There is no implicit lowercasing or
  concatenation of composite identities. Adapters map native names explicitly.
  Category and entity-kind vocabularies are extensible tokens, not spot-only
  enums; capabilities match them explicitly. Venue/category/symbol all contribute
  to instrument equality. Entity kind/namespace/value all contribute to identity.
- Candle logical identity is instrument + UTC start nanoseconds + positive
  fixed interval nanoseconds. Calendar-dependent bars need a deliberate schema
  extension; F03 does not pretend a month has a fixed duration. Ingestion identity
  is producer + epoch + nonnegative offset, independent of logical identity and
  observation revision. Repeated deliveries may have distinct ingestion IDs.
- `Decimal` is required for prices, quantities, increments and multipliers.
  Nonfinite/negative values and inconsistent OHLC ranges fail. No constructor
  converts floats, quantizes or rounds against ambient Decimal precision.
  Decimal equality is numeric; trailing-zero representations remain intact but
  do not make otherwise equal values unequal. Float forecasts occur only after
  the model transformation boundary and must be finite.
- Source timestamps retain integer value and explicit resolution; missing times
  remain `None`. Normalized/receipt wall times use integer POSIX nanoseconds.
  Monotonic samples are comparable only within the same host boot/session; no
  cross-session ordering is provided. Quality defaults to unknown; healthy
  claims need offset, uncertainty and evidence age. F12 owns thresholds, drift
  monitoring and uncertainty-aware decision policy, not these value objects.

## Bounds, provenance and capabilities

`RawEnvelope` takes a caller-supplied `payload_limit` at construction, preserves
immutable bytes and derives their SHA-256. The limit is local policy and is not
part of event identity. Payloads and opaque cursors are omitted from repr;
channel/stream fields are labels, not credential-bearing transport URLs. There
is no arbitrary transport-header dictionary. A digest proves byte identity,
not truthful source content. Producers must still avoid putting secrets in
payloads; this object is not a redactor.

A source yields envelopes **before** durable acceptance. F05 will assign the
`IngestionId` and create `RawRecord` at the persistence boundary. Constructing
a `RawRecord` alone does not claim a successful commit. This split avoids giving
a connector authority to advance durable offsets. Candle revisions reference
raw ingestion identities; archive locators retain that identity plus segment
hash and ordinal. Instrument metadata revision is separate from candle revision;
F06 owns attachment of metadata/raw evidence during normalization.

`SourceDescriptor.require` checks family schema, operation and subject category/
kind. History availability and sequencing/recovery declarations remain source
claims. Consumers must enforce requested ranges and page/payload budgets and
preserve unknown coverage. On overflow, the declared family policy requires
backfill, invalidation/resnapshot or visible coverage loss; no silent dropping.
Separate `StreamingSource` and `HistoricalSource` protocols avoid mandatory
exchange-shaped methods. `DISCOVER` is a declared capability only at F03; its
bounded metadata paging API belongs with actual instrument discovery in F13.

`RawArchive` exposes immutable seal/read operations with explicit local encoded,
decoded and record limits. Implementations must enforce limits during decoding,
verify object hashes/record identity, and preserve original bytes and receipt
metadata. Descriptor size claims never override local limits. Segment durability
is separate from manifest publication. F07 owns the first codec/publication
implementation; F10 chooses the deployment codec after measurement. A codec
identifier never causes an import or download.

A `DatasetRef` identifies the exact immutable JSON manifest by SHA-256; the
manifest will bind input objects, recipes, environment and provenance in F08/
F18. Row count and schema are claims to verify against it, not authority to open
paths. Application DTOs contain owned values only. Bounded identifier/cursor/
collection sizes are contract safety limits, not instrument-count, frequency
or retention product limits. Runtime deployment budgets may be lower.

Provider admission is deliberately not implemented in F03. `ModelIdentity`
records origin, publisher, revision, license, usage-policy reference, loading
requirements and optional artifact hashes. Missing policy/license remains an
explicit unknown, never an implicit approval. F20 completes admission metadata
and enforcement (terms/review date, resource/loading requirements, approved use,
local/remote policy); F21 enforces OS isolation. No real model may run under the
F03 fake-provider path. Descriptors cannot resolve secrets or load code.

`ProviderDescriptor.require` checks forecast support, input schema, row/horizon
limits, optional covariates and explicit remote-disclosure intent. That intent
is necessary but not sufficient deployment authorization. Result validation
checks dataset/job/provider/revision correlation, point count, increasing time
and requested spacing. The dataset/analysis layer will validate the forecast
origin against selected timestamps; no training, text-analysis framework,
arbitrary parameters or executable outputs are invented here. Transport parsers
must enforce output limits before allocation and call result validation before
acceptance. Provider output always remains untrusted.

## Versioning and extensions

Python port compatibility (`CONTRACT_VERSION`) is separate from named external
`SchemaRef` versions, adapter/normalizer revisions, model revisions and dataset
recipe versions. F03 starts each explicit contract at 1.0. A reader accepts only
its named schema, same major and minors no newer than its declared support.
Unknown names/majors/newer minors fail explicitly; future importers quarantine
unsupported data. No dynamic schema discovery or downloaded registry exists.

Additive optional capabilities can be introduced without requiring every
adapter to support them. A consumer must request capabilities explicitly and
handle rejection. Breaking required fields, identity/equality semantics, units,
method signatures or capability meaning require a new major contract/schema
and updated conformance fixtures; existing immutable data is not rewritten.
Serialization codecs/canonical bytes are not defined by dataclass repr/asdict
and will be specified in the publication tasks. Constructors do not provide
automatic migrations. Private implementation changes need no contract bump.

Run `uv run --locked --no-sync python -m pytest tests/contracts` for focused
examples, then `bash scripts/check.sh` and the existing F02 negative controls.
Type tests run mypy against temporary valid/invalid consumers; no broken source
is retained. Context7 documentation informed
[dataclasses](https://docs.python.org/3.12/library/dataclasses.html),
[Decimal](https://docs.python.org/3.12/library/decimal.html) and
[structural protocols](https://mypy.readthedocs.io/en/stable/protocols.html).
