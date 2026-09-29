# Bybit public historical retrieval

F13 adds a read-only V5 adapter at `BybitHistoricalSource`. It calls only
`https://api.bybit.com` and the public instrument and kline endpoints. The
adapter does not accept, load, or send account credentials. HTTPS certificate
and hostname verification stay enabled, redirects are rejected, requests have
a five-second socket timeout and six-second overall deadline, response bodies
are capped at 1 MiB, and one process-wide gate limits requests to two per
second. Metadata discovery is bounded to 32 pages of 1,000 instruments.

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
