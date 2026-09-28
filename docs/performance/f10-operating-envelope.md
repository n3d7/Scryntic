# F10 operating envelope and raw codec decision

**Production raw codec: retain `parquet-pyarrow-v1`.** It wins sustained
seal/scan and point-read latency for the measured initial workloads, and also
uses fewer encoded bytes for recorded candles. Framed zlib saves memory and
book storage, but that saving does not justify changing the first candle-led
deployment's supported writer. The alternative remains a comparison candidate.

Scope: KER-14 and KER-52–KER-54. The prerequisite is merged F09,
`f194a896b13dc0483a5eecfba51240d867847d05` (PR #9). These measurements qualify
the named workload on the recorded host/filesystem; they are not product caps
or a release soak. F11 recovery, F13 production historical retrieval, F15
backpressure policy and F29 pruning are outside this change.

## Reproduction and evidence boundaries

Use the pinned Python/uv/collector/dev environment from `pyproject.toml` and
`uv.lock`. Measurements use the project filesystem, not `/tmp`; a short
integration run on a different filesystem is not decision evidence. Each case
and repeat starts a new Linux process. Both implementations are imported before
the RSS baseline, so startup imports (including PyArrow) are common.

The committed `benchmarks/fixtures/candles.json` contains 20 public Bybit spot
HTTP responses with exact payload text, SHA-256, URL and capture time. Each
response covers the single completed UTC minute starting 2024-01-01. Replaying
the stored bytes is deterministic; reacquiring a response can change server
response metadata and is not a byte-identical reproduction. The recorded
payloads are repeated across new synthetic ingestion identities/receipts;
replay does not claim to be a live chronology or a Bybit normalizer.

Fixture acquisition is explicitly bounded (20 GETs, one candle per response,
1 MiB/response, 10 s/request, no credentials). To capture a separate corpus:

```bash
uv run --locked --no-sync python scripts/capture_f10_candles.py --output /tmp/f10-candles-new.json
```

The request shape was checked against [Bybit's public kline documentation](https://bybit-exchange.github.io/docs/v5/market/kline).
This script is a benchmark fixture tool, not the F13 historical adapter.

Synthetic trades contain a seeded 70/25/5 small/medium/large message mixture,
up to 40 trades/message, distinct source-native IDs, sides and decimal strings.
Books contain snapshots every 100 messages per instrument and 1–8-level
deltas, including zero quantities; the ordinary snapshot has 100 bid and 100
ask levels, and stress uses 1000 on each side. Shared millisecond timestamps
exercise burst-shaped content. These are serialization shapes, not measured
venue traffic, actual book-continuity proofs or concurrent arrival tests.

All JSON results include commands, seed, complete workload parameters,
per-segment workload/object hashes, environment, limits and each repeat's
metrics. Sizes are encoded file lengths (`st_size`), not compressed filesystem
allocation; disk estimates conservatively do not credit transparent filesystem
compression. RSS is process `ru_maxrss`, including generated input and native
allocations, rather than a Python-only memory measure.

Durable seal includes canonical-size validation, encoding, file fsync,
readback, immutable installation/directory fsync and installed readback. Scan
decodes and verifies one whole segment. Sequential/random points use the real
`RawArchive.read` port; first/last segments are sampled. Both readers validate
the whole segment for each point, so point cost grows with segment size.
Generation/digest and point-read time are excluded from active seal/scan
throughput and are included in separately reported wall time. This is warm
page-cache evidence; no privileged cache drop or cold-read claim is made.

F05 is measured separately using serial synchronous `accept`, including
WAL/FULL commit and acknowledged readback, followed by `records_after`
verification. Its explicitly bounded sample has 2048 envelopes per case.
SQLite/WAL sizes are recorded while open and after close. This is a raw-only
capacity component: it does not establish F06 normalization, full publication,
dataset or source-to-model sustained throughput.

The segment sweep holds total envelope count constant but regenerates each
profile with its recorded segment/seed parameters; it is not a repack of one
identical stream. In particular, synthetic books restart snapshot cadence at
each segment, so encoded-size differences across segment sizes include a
different snapshot mix. Within each profile both codecs receive identical
workload hashes. The selected 256-row budget is justified by its measured
point-read/memory cost and explicit byte limits, not by attributing all sweep
size differences to codec overhead.

The interruption probe terminates a read-only worker after a verified read
while it repeatedly reads; the exact signal position is scheduler-dependent.
It then verifies a fresh healthy reread. Header/middle/tail truncation is tried
both with the original hash and with the truncated bytes rehashed. Rehashing
must not admit malformed structure. These probes do not claim prefix recovery
or F11 publication crash convergence.

## Codec responsibility and compatibility

The existing `parquet-pyarrow-v1` implementation writes an uncompressed,
dictionary-free binary-payload row group. The alternative `framed-zlib-v1`
uses the eight-byte magic `SCRAWZ1\n`, big-endian uint64 record count/logical
size, and independent uint32 decoded/encoded lengths plus zlib level-3 frames.
Each inflated frame is exactly canonical JSON `{"raw": raw_projection(record)}`.
The canonical representation hex-encodes payload bytes; this adds parser work
and does not share compression history between records.

The alternative caps inflation before parsing using
`decompress(..., decoded_size + 1)` and checks exact length, `eof`, unused data
and unconsumed input. See [Python 3.12 zlib](https://docs.python.org/3.12/library/zlib.html).
It verifies object hash, all record identities/order and canonical equality
before returning a point. Logical decoded limits use the same canonical
evidence-size definition as Parquet; they are not an RSS limit. It uses the
existing no-replace hard-link/file/directory-fsync storage implementation.

No archived name imports or executes code. The comparison candidate is not
registered with production publication. Production composition and
`PublicationReader` retain their fixed Parquet writer/format/codec allowlist.
Existing Parquet objects retain exactly the same reader, schema and pinned
dependency; no archive rewrite or migration occurs.

## Capacity arithmetic

`scripts/size_operating_envelope.py --config <profile.json>` computes counts and
budgets from operator-owned configuration. Rates count **raw envelopes**, not
trades or book levels within messages. Horizon, family counts/rates, byte
coefficients, burst duration, measured service, reserve fractions, queue
duration and segment limits are configurable. Ninety days is an example, not
a hard-coded retention cap.

The retained-copy budget includes raw archive, the entire unpruned F05 spool,
configured derived storage, and manifest/catalog allowance. F10 does not
delete any spool or archive data. Derived bytes are an explicit planning
allowance, not measured public-candle normalization. Staging reserves two
objects (raw + derived) per concurrent staging segment; a fixed reserve covers
small catalogs/WAL/quarantine and free-disk fraction supplies further headroom.
Future real deployments must measure those components rather than treating
the allowance as a guarantee.

Service arithmetic uses a reserve fraction of the slowest measured raw
seal/F05 rate. Queue count covers the larger of peak-rate × queue duration and
positive (peak − reserved service) × burst duration. Payload budget includes
queued envelopes, one in-flight segment and the active writer; a configurable
memory multiplier allows for Python/decoded overhead. F05 currently bounds
queue count and each payload, not a separate byte-aware F15 policy.

Segment record count and encoded/decoded byte limits apply together. Multiplying
the largest admitted payload by the record limit can exceed the decoded byte
limit: maximum-sized payloads require shorter segments. Arithmetic assuming
full segments undercounts metadata if time/source/epoch partitions or low
rates cause low fill; raise the metadata allowance or rerun with measured fill.

The result flag `sustained_within_budget` compares configured average rate with
the reserved component service rate. It is planning arithmetic, not an
application admission test or the Foundation release qualification gate.

## Measured result

Host: Intel Core i5-13600KF, approximately 48 GiB physical memory, Linux x86-64;
the repository resides on `/dev/vda3`, Btrfs with `compress=zstd:1`. Python
3.12.14, PyArrow 25.0.1 and zlib 1.3.2. Full kernel/mount/block-size/free-space
details and timed implementation/lock hashes are in each result JSON.

The four completed suites contain **78 independently verified cases and
1,204,224 sealed envelopes across both codecs**. All header/middle/tail
original-hash and rehashed truncation probes rejected input; all interrupted
read probes allowed a subsequent exact healthy reread.

For the main 20-instrument, 256-record segment profile, throughput and point
latency below are medians of three fresh-process repeats; RSS is the largest
repeat peak. Bytes/record average the encoded segment files, including metadata.

| Workload | Codec | Durable seal envelopes/s | Verified scan envelopes/s | Encoded B/envelope | Peak RSS MiB | Random point median ms |
|---|---|---:|---:|---:|---:|---:|
| Recorded candles | Parquet | 6,401 | 44,647 | 530 | 73.5 | 5.21 |
| Recorded candles | Framed zlib | 4,853 | 33,793 | 604 | 59.4 | 7.18 |
| Trades | Parquet | 5,691 | 40,192 | 661 | 84.0 | 5.69 |
| Trades | Framed zlib | 4,477 | 29,460 | 625 | 59.9 | 7.84 |
| Books, 100 levels/side | Parquet | 5,441 | 38,411 | 1,029 | 80.4 | 6.11 |
| Books, 100 levels/side | Framed zlib | 4,034 | 27,014 | 836 | 60.3 | 8.98 |

The 5-instrument profiles also completed all three repeats; their individual
numbers/ranges are preserved in the [main results](../../benchmarks/results/raw-256.json).
Raw throughput varied materially with filesystem conditions (e.g. 20-instrument
Parquet trade repeats ranged 4,152–5,939 envelopes/s). Avoid projecting a fast
repeat or a temporary-filesystem smoke result as guaranteed service.

Larger segments amortize durable-write overhead but worsen point latency:
recorded-candle Parquet random point medians are 5.21 / 19.5 / 78.7 ms at
256 / 1024 / 4096 rows. Corresponding peak RSS is 73.5 / 87.0 / 121.8 MiB;
4096-row trade/book peaks reach 128.9 MiB. See the
[1024-row](../../benchmarks/results/raw-1024.json) and
[4096-row](../../benchmarks/results/raw-4096.json) results. Real sequential
point reads and their p95/max timings are recorded separately from the efficient
whole-segment scan; the scan rate is not the application's per-reference rate.

The [1000-level/side book stress](../../benchmarks/results/book-1000.json) gives
Parquet / framed zlib respectively: 6,673 / 3,206 sealed envelopes/s,
4,471 / 2,210 encoded B/envelope, 94.0 / 64.1 MiB peak RSS and
9.98 / 16.32 ms median random points. That roughly 51% storage saving is
material for a deep-book deployment; such a deployment should revisit the
codec decision with its own observed traffic mix and intended filesystem.
It does not overturn the measured candle-led selection.

F05 committed intake across the main suite ranged **130–749 envelopes/s**;
the primary 20-instrument candle samples were 145–168 envelopes/s. The conservative
profile uses the observed minimum rounded down to **130**, then reserves 50%,
giving **65 envelopes/s component planning service**. This is approximately
195 times the 20-instrument one-minute envelope rate (0.333/s), but it remains
raw/F05 evidence, not total collector-pipeline service.

## Practical profile and 90-day sizing

Recommended starting planning profile for this measured raw/F05 workload:

- 128 queued envelopes at a declared 64 envelopes/s peak, two-second queue
  window and three-second burst duration. At the reserved 65/s service the
  arithmetic does not accumulate sustained burst backlog.
- 64 KiB maximum payload; at most 256 records, 4 MiB encoded and 8 MiB canonical
  decoded bytes per raw segment. The benchmark's representative candle/trade
  and 1000-level book fixtures fit these limits in focused tests. The largest
  stress payload across all repeats is 49,178 B; one stress segment's canonical size is about
  2.19 MiB. Worst-case 64 KiB payloads can fill the decoded budget in roughly
  63 records, so the record limit is not permission to ignore the byte limit.
- Queue plus in-flight segment/writer reserves 24.06 MiB of payloads; a 3×
  planning multiplier budgets **72.19 MiB**. Allow **512 MiB for the raw/F05
  process** as an engineering starting reserve, exceeding measured codec RSS
  plus queue arithmetic. This does not bound native allocations for every
  allowed input or budget other pipeline/model workers.
- Two concurrent staging segments (raw + derived) reserve 16 MiB; fixed
  metadata/WAL/quarantine allowance is 512 MiB; retain 30% free-disk headroom.
  These are planning parameters, not newly implemented queue/recovery/pruning
  policies or new application-wide caps.

The profile byte coefficients deliberately round above the 256-row results:

| Family | Archive B/envelope | Canonical B/envelope | Spool B/envelope | Derived B/envelope |
|---|---:|---:|---:|---:|
| Candles | 640 | 1,280 | 768 | 2,048 |
| Trades | 768 | 1,536 | 768 | 0 |
| Books, ordinary 100-level mix | 1,280 | 2,560 | 1,280 | 0 |

Measured post-close SQLite bytes were about 504 B/candle, 630–658 B/trade,
and 1,014–1,018 B/book for the primary 20-instrument samples. The coefficients
add page/index/fill allowance; derived candle storage is an unmeasured explicit
allowance for normalized SQLite plus immutable analytical output. Zero derived
bytes for trade/book means raw-only retention; those normalizers are not
implemented by F10. Metadata allowance is 24 KiB per full segment, with the
partition/fill limitation described above.

| Configured scenario, 90 days | Envelopes | Retained copies GiB | Required disk GiB including reserves |
|---|---:|---:|---:|
| 5 instruments, one candle envelope/minute | 648,000 | 2.144 | 3.799 |
| 20 instruments, one candle envelope/minute | 2,592,000 | 8.574 | **12.986** |
| 20 instruments: candles + 0.5 trade envelopes/s/instrument + 2 book envelopes/s/instrument | 391,392,000 | 896.150 | **1,280.950** |

For the 20-candle example, provision at least **16 GiB** for these modeled
retained copies/reserves; dataset copies, backups or a larger real derived
store require additional capacity. The mixed scenario is illustrative raw
cost, not an admitted real trade/book collector. Its 50.333/s average consumes
77.4% of the reserved raw/F05 component service and needs pipeline qualification.
Book depth, duplicate deliveries, page size, capture frequency, late revisions,
retained datasets and lower segment fill change these calculations.

Profiles and their exact computed outputs are committed under
[benchmarks/profiles](../../benchmarks/profiles). The calculator refuses a
spool horizon shorter than the modeled archive horizon: F10 does not implement
pruning. Retention cannot assume acknowledged input silently disappears.

## Commands and validation

All measured runs used the project Python and the repository filesystem:

```bash
uv run --locked --no-sync python scripts/benchmark_raw.py --workloads recorded-candle trade orderbook --instruments 5 20 --records 256 --segments 64 --repeats 3 --point-reads 16 --ingestion-records 2048 --storage-root "$PWD" --output benchmarks/results/raw-256.json
uv run --locked --no-sync python scripts/benchmark_raw.py --workloads recorded-candle trade orderbook --instruments 20 --records 1024 --segments 16 --repeats 3 --point-reads 8 --ingestion-records 512 --storage-root "$PWD" --output benchmarks/results/raw-1024.json
uv run --locked --no-sync python scripts/benchmark_raw.py --workloads recorded-candle trade orderbook --instruments 20 --records 4096 --segments 4 --repeats 3 --point-reads 8 --ingestion-records 512 --storage-root "$PWD" --output benchmarks/results/raw-4096.json
uv run --locked --no-sync python scripts/benchmark_raw.py --workloads orderbook --instruments 20 --levels 1000 --records 256 --segments 16 --repeats 3 --point-reads 8 --ingestion-records 512 --storage-root "$PWD" --output benchmarks/results/book-1000.json
uv run --locked --no-sync python scripts/size_operating_envelope.py --config benchmarks/profiles/candles-20-90d.json
uv run --locked --no-sync python -m pytest tests/archive tests/benchmarks tests/ingestion tests/normalization tests/publication tests/dataset tests/slice -q
bash scripts/check.sh
```

The benchmark uses 256 MiB local encoded/decoded safety ceilings; the narrower
recommended ArchiveLimits are additionally exercised by focused segment-budget
tests. Unit coverage includes binary envelopes/identity/hash fidelity,
immutable reseal/no replacement, count/encoded/decoded limits, reference
identity errors, bounded inflation, malformed/truncated frames, deterministic
workloads, exact recorded replay, benchmark timeout/cancellation and sizing.

Validation completed: all four benchmark commands succeeded (78 cases); the
focused F05–F09/archive/benchmark test command succeeded; a fresh
`bash scripts/check.sh` completed with **593 passed**, strict mypy over 108
source files, Ruff check/format, rebuilt/installed package-profile checks and
dependency audits reporting no known vulnerabilities. It used the pinned
uv 0.12.13 executable through a temporary PATH because the host's default uv
was 0.12.19; no runtime pins were changed. Paired workload/source hashes and
all three committed sizing outputs were independently recomputed and matched.

Remaining qualification: cold-cache/storage-contention behavior, live/timed
arrival and queue occupancy, sustained full F06–F09 throughput, real trade/book
traffic, actual derived storage and the release soak/offline-retention simulation.
This evidence does not grant financial execution authority or introduce network
credentials, executable codecs from metadata, recovery policy or pruning.
