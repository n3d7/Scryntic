"""Seeded synthetic market shapes and exact replay of committed public candles.

Candles use minute intervals and decimal OHLCV strings. Trade messages use a
70/25/5 percent small/medium/large batch mix. Books send an initial 100-level
(configurable) snapshot per instrument, snapshots every 100 messages, and
1-8-level deltas with occasional zero quantities. Bursts share millisecond
timestamps. These shapes model serialization load, not a venue's market law.
Recorded candles replay fixed committed response bytes with synthetic receipts;
their replay order is not a live timeline and source_time is left unparsed.
"""

import json
import random
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

from scryntic.archive.canonical import canonical_json_bytes, raw_projection
from scryntic.domain.identity import InstrumentId
from scryntic.domain.raw import IngestionId, RawEnvelope, RawRecord
from scryntic.domain.time import ClockSample, SourceTime, TimeQuality, TimeUnit


@dataclass(frozen=True, slots=True)
class WorkloadSpec:
    kind: str = "candle"
    instruments: int = 5
    records: int = 512
    seed: int = 14
    levels: int = 100
    max_trade_batch: int = 40
    payload_limit: int = 262_144
    max_total_payload_bytes: int = 67_108_864
    recorded_fixture_path: str | None = None

    def __post_init__(self) -> None:
        if self.kind not in {
            "candle",
            "recorded-candle",
            "trade",
            "orderbook",
            "mixed",
        }:
            raise ValueError("Unknown workload")
        for value, maximum in (
            (self.instruments, 20),
            (self.records, 100_000),
            (self.levels, 2_000),
            (self.max_trade_batch, 500),
            (self.payload_limit, 4_194_304),
            (self.max_total_payload_bytes, 536_870_912),
        ):
            if type(value) is not int or not 1 <= value <= maximum:
                raise ValueError("Workload parameter outside benchmark bounds")
        if type(self.seed) is not int or not 0 <= self.seed <= 2**32 - 1:
            raise ValueError("Seed must be an unsigned 32-bit integer")


def recorded_fixture_path(spec: WorkloadSpec) -> Path:
    return (
        Path(spec.recorded_fixture_path)
        if spec.recorded_fixture_path is not None
        else Path(__file__).resolve().parents[3] / "benchmarks/fixtures/candles.json"
    )


def _recorded_candles(spec: WorkloadSpec) -> tuple[tuple[str, bytes], ...]:
    with recorded_fixture_path(spec).open("rb") as fixture:
        content = fixture.read(2_097_153)
    if len(content) > 2_097_152:
        raise ValueError("Recorded fixture exceeds 2 MiB benchmark budget")
    corpus = json.loads(content)
    if not isinstance(corpus, dict) or not isinstance(corpus.get("records"), list):
        raise ValueError("Invalid recorded fixture")
    result = []
    for entry in corpus["records"]:
        if not isinstance(entry, dict) or not all(
            isinstance(entry.get(key), str)
            for key in ("symbol", "payload", "sha256", "url", "captured_at_utc")
        ):
            raise ValueError("Invalid recorded fixture entry")
        payload = entry["payload"].encode("utf-8")
        if sha256(payload).hexdigest() != entry["sha256"]:
            raise ValueError("Recorded fixture payload hash mismatch")
        result.append((str(entry["symbol"]), payload))
    if (
        len({symbol for symbol, _ in result}) != len(result)
        or len(result) < spec.instruments
    ):
        raise ValueError("Recorded fixture has insufficient unique instruments")
    return tuple(result[: spec.instruments])


def build_workload(spec: WorkloadSpec, *, segment: int = 0) -> tuple[RawRecord, ...]:
    """Materialize at most spec.records inputs; each segment has new identities."""
    if type(segment) is not int or not 0 <= segment <= 10_000:
        raise ValueError("Segment index outside benchmark bounds")
    rng = random.Random(spec.seed + segment * 1_000_003)
    start_ms = 1_704_067_200_000 + segment * spec.records * 60_000
    records = []
    payload_bytes = 0
    recorded = _recorded_candles(spec) if spec.kind == "recorded-candle" else ()
    for index in range(spec.records):
        instrument = index % spec.instruments
        symbol = f"ASSET{instrument:02d}-USD"
        if recorded:
            symbol = recorded[instrument][0]
        sequence = index // spec.instruments
        kind = (
            spec.kind
            if spec.kind != "mixed"
            else ("candle", "trade", "orderbook")[sequence % 3]
        )
        timestamp_ms = start_ms + (
            sequence * 60_000
            if kind in {"candle", "recorded-candle"}
            else sequence // 20
        )
        price = 100 + instrument * 10 + rng.random()
        body: object
        if recorded:
            body = None
        elif kind == "candle":
            body = [
                timestamp_ms,
                f"{price:.4f}",
                f"{price + 1:.4f}",
                f"{price - 1:.4f}",
                f"{price + 0.2:.4f}",
                f"{rng.uniform(1, 100):.6f}",
            ]
        elif kind == "trade":
            roll = rng.random()
            upper = min(
                spec.max_trade_batch,
                2 if roll < 0.70 else 10 if roll < 0.95 else spec.max_trade_batch,
            )
            count = rng.randint(1, upper)
            body = {
                "symbol": symbol,
                "timestamp_ms": timestamp_ms,
                "trades": [
                    {
                        "id": segment * 1_000_000_000 + index * 1000 + trade,
                        "price": f"{price + rng.uniform(-0.05, 0.05):.4f}",
                        "quantity": f"{rng.uniform(0.001, 5):.6f}",
                        "side": rng.choice(["buy", "sell"]),
                    }
                    for trade in range(count)
                ],
            }
        else:
            snapshot = sequence % 100 == 0 or (spec.kind == "mixed" and sequence == 2)
            levels = spec.levels if snapshot else rng.randint(1, min(8, spec.levels))
            body = {
                "symbol": symbol,
                "timestamp_ms": timestamp_ms,
                "sequence": sequence,
                "type": "snapshot" if snapshot else "delta",
                "bids": [
                    [
                        f"{price - (level + 1) * 0.01:.4f}",
                        "0"
                        if not snapshot and rng.random() < 0.1
                        else f"{rng.uniform(0.1, 20):.6f}",
                    ]
                    for level in range(levels)
                ],
                "asks": [
                    [f"{price + (level + 1) * 0.01:.4f}", f"{rng.uniform(0.1, 20):.6f}"]
                    for level in range(levels)
                ],
            }
        payload = (
            recorded[instrument][1]
            if recorded
            else json.dumps(body, separators=(",", ":"), ensure_ascii=True).encode()
        )
        payload_bytes += len(payload)
        if payload_bytes > spec.max_total_payload_bytes:
            raise ValueError("Generated workload exceeds total payload budget")
        records.append(
            RawRecord(
                IngestionId(
                    "benchmark-collector",
                    f"seed-{spec.seed}-segment-{segment}",
                    index + 1,
                ),
                RawEnvelope(
                    source="public-recording-replay"
                    if recorded
                    else "synthetic-benchmark",
                    stream="candle" if recorded else kind,
                    channel="public",
                    adapter_version="benchmark-v1",
                    receipt=ClockSample(
                        timestamp_ms * 1_000_000,
                        index * 1000,
                        "benchmark-session",
                        TimeQuality("benchmark-clock"),
                    ),
                    payload=payload,
                    payload_limit=spec.payload_limit,
                    subject=InstrumentId(
                        "bybit" if recorded else "synthetic", "spot", symbol
                    ),
                    source_time=None
                    if recorded
                    else SourceTime(timestamp_ms, TimeUnit.MILLISECOND),
                    source_sequence=index,
                ),
            )
        )
    return tuple(records)


def workload_digest(records: tuple[RawRecord, ...]) -> str:
    """Hash canonical identities/envelopes, not only generated payload bytes."""
    digest = sha256()
    for record in records:
        value = canonical_json_bytes({"raw": raw_projection(record)})
        digest.update(len(value).to_bytes(8, "big"))
        digest.update(value)
    return digest.hexdigest()
