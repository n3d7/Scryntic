# Coverage and source recovery (F15)

`scryntic.recovery` supplies policy on the existing source and raw-journal boundaries. It does not compose the daemon or implement daemon health (F23). Only Bybit candles have a real provider adapter; trade/book conformance uses synthetic evidence, not additional public feeds.

## Durable coverage

`CoverageLedger` binds a stable configured key to source, stream, channel, subject and family. Its versioned snapshots use the **same sole SQLite ingestion writer**, WAL/FULL transactions, readback and compare-and-swap. Spool schema v2 migrates a validated v1 spool without rewriting raw records. Unsupported/corrupt schemas still fail closed.

Begin persists an active session **before intake**. Raw acceptance precedes the live checkpoint; every repair page's accepted raw records precede its cursor/proof update. Interrupted page acknowledgement may replay records, never skip unacknowledged work. Restart/clean downtime cannot prove continuity: coverage since the verified checkpoint becomes unknown. If a loss-marker write fails, the supervisor stops and leaves the earlier active session intact. Even a full loss history has a separate reserved `unknown_since_ns`/detection field for conservative restart. `finish()` cannot clear a failed ledger.

Coverage spans retain best-known interval/sequence evidence, reason, detection clock quality and raw checkpoint offset. They do not invent missing counts. `coverage(start, end)` defaults to unknown, rather than deriving completeness from a reconnected socket or an empty response. Complete means exact finalized candle evidence for the affected grid, not a claim about all provider history. Unsupported completeness remains unknown. Existing unrecoverable gaps remain visible after restoration of current book state.

## Admission and bounds

- One shared event-loop-owned `SharedBudget` bounds live buffers, in-flight intake and complete backfill pages by record count and payload bytes: defaults 32 records / 262144 bytes, with 8 records / 65536 bytes unavailable to backfill.
- Bybit's optional buffer-admission hook holds leases through journal acceptance. Overflow closes the affected socket; queued records drain, loss notification precedes retry, and failed loss recording stops retry. Generic sources without a buffer hook must independently enforce their advertised transport bounds.
- A shared monotonic request/start gate defaults to one second. Provider IP/connection/HTTP limits also remain enforced. TLS, frame/decompression/response limits remain provider responsibilities; the generic budget does not replace those bounds.
- Backfill reserves its declared whole page before fetching. Defaults: four records/page, 256 candles/repair, 64 persisted attempts, ten seconds for a full fetch. Both failed requests and successful pages consume the attempt allowance; exhaustion stays unknown. Cursors remain opaque and bounded to 4096 bytes.
- Recovery metadata has a separate 32-request lane, at most 64 stream keys and 65536 bytes per snapshot. Normal updates stop at 61440 bytes to leave restart headroom. Loss history admits at most 63 spans; no old loss is silently evicted. The finalized-candle comparison cache holds at most 256 recent identities; cache eviction removes an optimization, not a loss record or accepted raw history.
- Payload intake stops below 16 MiB available filesystem space by default (`metadata_headroom_bytes`). Metadata writes still attempt a transaction. This is an admission watermark, not a physical storage reservation or a guarantee against concurrent disk exhaustion.
- Malformed/quarantined input is retained only in the existing bounded raw intake, with fixed loss reasons; 16 rejection events per supervisor session stop the source by default. There is no second unbounded payload quarantine.

## Family policy

| Family | Loss/pressure consequence | Recovery |
| --- | --- | --- |
| Candles | Finite supported gaps become pending; uncertain bounds stay unknown. Missing finalized terminal updates are gaps too. | Fixed-interval overlap fetch, exact timestamp coverage, source finality plus F12. Empty/unfinished/ambiguous results cannot complete a gap. |
| Trades/liquidations | Preserve received identity/sequence and explicit loss. | No inference of no activity. This implementation does not claim historical reconstruction of these future feeds. |
| Books | Immediately invalidate current state; reject continuity until a snapshot. | Bounded provider `request_snapshot`, then only decoder-proven consecutive sequence deltas. Sending a request does not validate a book. Restoring current state does not repair historical replay. |
| Periodic/news/on-chain/unknown | Explicit unknown coverage; preserve raw identity/cursor evidence. | No generic coalescing or invented history/reorg proof. Real family-specific adapters remain later work. |

The generic supervisor owns cancellation, source/repair task lifetimes, capped jittered retry for typed transient failures, and checkpoint ordering. Configuration/schema/storage failures stop intake. Blocking writer operations run outside the network event loop; cancellation joins them before releasing reservations. A failed repair task also stops a stalled live task.

Loss-reporting/buffer-admission and resnapshot are optional source protocols, wired when the source implements them. Explicit close stops intake and allows five seconds for cooperative task shutdown before cancellation; an interrupted session remains conservative on restart. The journal's blocking operation is still joined: daemon forced-termination policy belongs to F23. A rejected Bybit subscription fails for configuration correction rather than reconnecting indefinitely; malformed/transient transport failures keep explicit loss evidence.

## Candle overlap and ambiguity

The Bybit bridge reuses the existing pure candle parser before admission. Its bounded comparison key contains canonical candle market values, not WebSocket publication timestamps. Matching previously proven final candles are duplicates, including REST/WS overlap; this does **not** change canonical normalization hashes or rewrite historical records. Outside-range overlap is inspected but does not become a new repair write. Missing/in-range candles enter the same journal → normalizer → archive → dataset path. A conflicting previously finalized value is retained in raw storage, coverage becomes unknown and repair fails closed. Existing normalization/dataset conflict rules remain authoritative.

Finality additionally uses the immutable F12 receipt evidence. Unknown/degraded/stale/excessive/ambiguous clock evidence permits safe raw capture but cannot prove repaired completion. A clean restart alone never upgrades uncertainty.

Validation is in `tests/recovery/` and `tests/ingestion/test_recovery_metadata.py`: recorded Bybit overlap through real Parquet/dataset readback; disconnects, pressure, restart/page acknowledgement, repeated cancellation, missing markers, metadata/disk quotas, visible synthetic trade loss and book resnapshot. Service composition, ledger retention/operator retirement and dataset-wide coverage/provenance packaging remain with their later owners (F23/F29/F18).

For a bounded public probe using the same supervisor and the existing archive/dataset readback:

```sh
rtk proxy .venv/bin/python -m scripts.check_bybit_recovery
```

It requests no credentials and changes no clock. Capture stops at eight accepted records or a 75-second deadline, then joins accepted work; a final in-flight bounded page can add records. This is a public smoke check, not a demonstration of provider-wide completeness. Stale host timing evidence must produce zero qualified final rows.
