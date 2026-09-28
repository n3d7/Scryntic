"""Deterministic bounded benchmark inputs, never exchange recordings."""

import json
from hashlib import sha256
from pathlib import Path

import pytest

from scryntic.benchmarks.workloads import WorkloadSpec, build_workload, workload_digest


@pytest.mark.parametrize("kind", ["candle", "trade", "orderbook", "mixed"])
def test_workload_is_reproducible_bounded_and_unique_across_segments(kind: str) -> None:
    spec = WorkloadSpec(kind=kind, instruments=5, records=24, seed=19, levels=10)
    records = build_workload(spec)
    assert records == build_workload(spec)
    assert len(records) == 24
    assert len({record.envelope.subject for record in records}) == 5
    assert all(len(record.envelope.payload) <= spec.payload_limit for record in records)
    assert [record.identity.offset for record in records] == list(range(1, 25))
    assert workload_digest(records) != workload_digest(build_workload(spec, segment=1))
    assert records != build_workload(
        WorkloadSpec(kind=kind, records=24, seed=20, levels=10)
    )


def test_trade_batches_and_orderbook_snapshot_delta_are_realistic_synthetic_shapes() -> (
    None
):
    trades = build_workload(WorkloadSpec(kind="trade", records=100, max_trade_batch=40))
    batches = [json.loads(record.envelope.payload)["trades"] for record in trades]
    assert min(map(len, batches)) == 1
    assert max(map(len, batches)) > 1
    assert max(map(len, batches)) <= 40
    books = build_workload(WorkloadSpec(kind="orderbook", records=20, levels=12))
    events = [json.loads(record.envelope.payload) for record in books]
    assert {event["type"] for event in events} == {"snapshot", "delta"}
    assert all(
        len(event["bids"]) == 12 for event in events if event["type"] == "snapshot"
    )
    assert all(
        1 <= len(event["bids"]) <= 8 for event in events if event["type"] == "delta"
    )
    assert all(record.envelope.source == "synthetic-benchmark" for record in books)


def test_recorded_candle_replay_preserves_exact_public_response_bytes() -> None:
    fixture = Path("benchmarks/fixtures/candles.json")
    entries = json.loads(fixture.read_text())["records"]
    spec = WorkloadSpec(kind="recorded-candle", instruments=20, records=40)
    records = build_workload(spec)
    assert len({record.envelope.subject for record in records}) == 20
    for index, record in enumerate(records):
        entry = entries[index % 20]
        assert record.envelope.payload == entry["payload"].encode("utf-8")
        assert sha256(record.envelope.payload).hexdigest() == entry["sha256"]
        assert record.envelope.source == "public-recording-replay"
    assert records == build_workload(spec)
    assert workload_digest(records) != workload_digest(build_workload(spec, segment=1))


@pytest.mark.parametrize(
    "values",
    [
        {"records": 0},
        {"records": 100_001},
        {"instruments": 21},
        {"levels": 2001},
        {"max_trade_batch": 501},
        {"kind": "unknown"},
    ],
)
def test_workload_rejects_unbounded_parameters(values: dict[str, object]) -> None:
    with pytest.raises((ValueError, TypeError)):
        WorkloadSpec(**values)  # type: ignore[arg-type]
