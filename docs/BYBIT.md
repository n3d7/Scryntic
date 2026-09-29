# Bybit public historical and live collection

F13 adds a read-only V5 adapter at `BybitHistoricalSource`. It calls only
`https://api.bybit.com` and the public instrument and kline endpoints. The
adapter does not accept, load, or send account credentials. HTTPS certificate
and hostname verification stay enabled, and redirects are rejected. A
six-second asynchronous deadline covers rate waiting, connection, headers,
and the complete response body; five-second connection and read-inactivity
timeouts also apply. Cancelling an in-flight read closes its HTTP response.
Response bodies are capped at 1 MiB, and one process-wide gate starts at most
two requests per second. Metadata discovery is bounded to 32 pages of 1,000
instruments.

Instrument discovery supports spot, linear, inverse, and option metadata. The
option request uses `baseCoin=All` so discovery includes more than Bybit's
default BTC options. V5 does not provide instrument pagination for spot;
other categories follow `nextPageCursor`. Historical candles are supported for spot, linear, and
inverse. The V5 kline endpoint returns newest candles first, so results are
validated, de-overlapped, and returned chronologically. `start_ns` is inclusive
and `end_ns` is exclusive in the domain request. The opaque `RawPage` cursor
contains the range and continuation boundary. `BybitCursorStore` saves a
query-bound checkpoint with file and directory fsync. The caller must accept
each envelope in a page before saving its `next_cursor`, then reload the
checkpoint after restart. A crash between acceptance and checkpoint save can
replay a page; stable source event IDs support downstream reconciliation.
A saved terminal page is marked complete so restart does not begin the range again.

The adapter supports fixed-duration intervals exposed by V5 (`1`, `3`, `5`,
`15`, `30`, `60`, `120`, `240`, `360`, `720`, `D`, and `W`). Calendar-month
`M` is excluded because the current domain contract represents intervals as
fixed nanoseconds. A page returns at most 999 candles so it can safely request
one extra row to detect continuation under Bybit's 1,000-row cap.

Bybit's V5 kline row retains the original string tuple in the raw payload.
Normalization records the provider schema and maps spot/linear base-asset
volume versus inverse quote-asset volume; turnover uses the opposite unit.
The REST endpoint may include the still-forming current candle. The adapter
keeps it for safe capture and marks finality using the F12 clock monitor at
the candle's closing boundary. Unknown, degraded, stale, or excessive clock
evidence leaves it unfinalized; downstream finality decisions must use the
same F12 policy and defer when evidence does not establish the boundary.

Run the bounded public check with:

```sh
rtk run '.venv/bin/python scripts/check_bybit_public.py'
```

It makes at most two public requests (spot discovery and one 10-row BTCUSDT
minute page), performs no time-setting operation, and prints only the observed
clock quality and minimal retrieval evidence. It requires a reachable
`chronyc` binary for host synchronization evidence; without evidence F12
correctly reports clock quality as unknown and candles remain unfinalized.

## Live kline collection (F14)

`BybitLiveSource` implements `StreamingSource` for one configured fixed-duration
interval and one instrument per connection. It connects only to Bybit's public
`spot`, `linear`, or `inverse` V5 WebSocket endpoints, validates TLS, and refuses
redirects. It has no account credentials or financial execution methods.
The subscription topic is `kline.{interval}.{symbol}`. A subscription must be
acknowledged on the current socket before candles are accepted; mismatched
topics, malformed frames, and disconnects close that socket and trigger a
bounded reconnect with a fresh subscription. Exact repeated candle revisions
are suppressed in a 1,024-entry cache across reconnects; changed values,
source confirmation, F12 finality, and clock epochs remain distinct evidence.

The adapter accepts at most 65,536 bytes per WebSocket message (including
decompressed content), eight candles per frame, and 8,192 bytes per persisted
candle. WebSocket compression is not requested. One reader maintains heartbeat
and captures receipts before handing envelopes to a queue of at most 32 records
(262,144 payload bytes). The caller commits each envelope through the existing
durable ingestor. Queue overflow closes the socket and records a safe pressure
error; queued records drain before reconnect, and the overflow record is not
added to replay deduplication. A remote stream cannot promise lossless delivery
during sustained backpressure.
There is no separate live schema or normalization path. The existing
`bybit_candle` payload preserves the kline values in the historical row format,
source candle start in
milliseconds, and WebSocket publication `ts` with an explicit millisecond unit.
The normalizer accepts that publication time in the same schema used by REST.

Bybit's `confirm=true` is necessary but not sufficient for finality. F12 must
also establish that the entire uncertainty interval is past the candle's close.
Unknown, degraded, stale, or excessive clock evidence keeps capture safe and
the candle unfinalized. The adapter sends an application ping every 20 seconds,
requires its pong within 10 seconds, and reconnects after 90 seconds without
candle data. Acknowledgement timeout is eight seconds; reconnect backoff is
one to 30 seconds. Public connection starts share a process-wide one-second
gate; writes have a three-second deadline. `close()` interrupts the current
socket, connection attempt, reader task and backoff. Cancellation and explicit
generator closure join the reader; callers should explicitly close a partially
consumed async generator. `last_error`
contains only bounded error categories, never a frame or URL.

Run the bounded public live check with:

```sh
rtk run '.venv/bin/python scripts/check_bybit_live.py'
```

It discovers BTCUSDT metadata, accepts at most eight live envelopes within a
75-second capture deadline, then checks committed journal records, normalization, Parquet
publication, and a reopened dataset in a temporary installation. With poor
host clock evidence, the dataset may contain an open candle but must not claim
F12-qualified finality. This is an acceptance probe, not a long-running
collector service; daemon lifecycle and explicit loss/coverage recovery belong
to F15 and F23.
