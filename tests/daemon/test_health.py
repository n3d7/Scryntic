"""Cached daemon health remains bounded, typed and independent of data I/O."""

import json
from dataclasses import replace

import pytest

from scryntic.daemon.health import (
    ClockStatus,
    Fault,
    HealthCache,
    HealthSnapshot,
    State,
    StreamHealth,
)


def test_fresh_heartbeat_keeps_liveness_when_source_is_stale() -> None:
    cache = HealthCache(stale_after_ns=100)
    cache.publish(
        HealthSnapshot(
            state=State.HEALTHY,
            sampled_at_ns=200,
            heartbeat_ns=200,
            ready=True,
            streams=(
                StreamHealth(
                    0,
                    last_received_monotonic_ns=20,
                    connected=True,
                    freshness_limit_ns=50,
                ),
            ),
        )
    )
    health = cache.snapshot(now_ns=210)
    assert health.live
    assert not health.ready
    assert health.state is State.DEGRADED
    assert health.streams[0].received_age_ns == 190
    assert health.streams[0].stale
    assert Fault.SOURCE in health.faults


def test_source_heartbeat_cannot_hide_old_market_data() -> None:
    cache = HealthCache(stale_after_ns=100)
    cache.publish(
        HealthSnapshot(
            state=State.HEALTHY,
            sampled_at_ns=200,
            heartbeat_ns=200,
            ready=True,
            streams=(
                StreamHealth(
                    0,
                    last_received_monotonic_ns=20,
                    last_heartbeat_monotonic_ns=200,
                    connected=True,
                    freshness_limit_ns=50,
                ),
            ),
        )
    )
    health = cache.snapshot(now_ns=220)
    assert health.live
    assert not health.ready
    assert health.streams[0].stale
    assert health.streams[0].data_stale
    assert not health.streams[0].heartbeat_stale


def test_expired_daemon_heartbeat_is_neither_live_nor_ready() -> None:
    cache = HealthCache(
        HealthSnapshot(
            state=State.HEALTHY,
            sampled_at_ns=100,
            heartbeat_ns=100,
            ready=True,
        ),
        stale_after_ns=50,
    )
    health = cache.snapshot(now_ns=151)
    assert not health.live
    assert not health.ready
    assert Fault.STALE in health.faults


def test_starting_stopping_and_failed_never_claim_readiness() -> None:
    for state in (State.STARTING, State.STOPPING, State.FAILED):
        cache = HealthCache(
            HealthSnapshot(
                state=state,
                sampled_at_ns=10,
                heartbeat_ns=10,
                ready=True,
            )
        )
        health = cache.snapshot(now_ns=10)
        assert not health.ready
        assert health.live is (state is not State.FAILED)


def test_snapshot_keeps_pipeline_boundaries_and_unknown_clock_distinct() -> None:
    health = HealthSnapshot(received=9, durably_committed=7, normalized=5, published=3)
    body = json.loads(health.to_json())
    assert [
        body[name]
        for name in (
            "received",
            "durably_committed",
            "normalized",
            "published",
        )
    ] == [9, 7, 5, 3]
    assert body["clock_status"] == "unknown"
    assert body["clock_offset_ns"] is None
    assert body["clock_uncertainty_ns"] is None
    assert body["clock_evidence_age_ns"] is None


def test_health_enforces_bounded_typed_content_before_serializing() -> None:
    too_many_streams = tuple(StreamHealth(i) for i in range(65))
    with pytest.raises(ValueError):
        HealthSnapshot(streams=too_many_streams)
    with pytest.raises(ValueError):
        StreamHealth(-1)
    with pytest.raises(ValueError):
        HealthSnapshot(received=2**64)
    with pytest.raises(ValueError):
        HealthSnapshot(clock_uncertainty_ns=-1)
    with pytest.raises(ValueError):
        HealthSnapshot(state="SECRET")  # type: ignore[arg-type]
    untyped_streams = [StreamHealth(0)]
    with pytest.raises(ValueError):
        HealthSnapshot(streams=untyped_streams)  # type: ignore[arg-type]
    health = HealthSnapshot(streams=tuple(StreamHealth(i) for i in range(64)))
    assert len(health.to_json()) <= 65536


def test_cache_rejects_regression_and_preserves_immutable_sample() -> None:
    health = HealthSnapshot(sampled_at_ns=100, heartbeat_ns=100)
    cache = HealthCache(health)
    older_sample = replace(health, sampled_at_ns=99)
    with pytest.raises(ValueError):
        cache.publish(older_sample)
    assert cache.snapshot(now_ns=100).sampled_at_ns == 100
    assert health.clock_status is ClockStatus.UNKNOWN


def test_faults_are_deduplicated_and_stream_ordinals_are_unique() -> None:
    duplicate_ordinals = (StreamHealth(0), StreamHealth(0))
    with pytest.raises(ValueError):
        HealthSnapshot(streams=duplicate_ordinals)
    with pytest.raises(ValueError):
        HealthSnapshot(faults=(Fault.SOURCE, Fault.SOURCE))


def test_utc_receipt_does_not_substitute_for_monotonic_freshness() -> None:
    cache = HealthCache(
        HealthSnapshot(
            state=State.HEALTHY,
            sampled_at_ns=200,
            heartbeat_ns=200,
            ready=True,
            streams=(
                StreamHealth(
                    0,
                    last_received_monotonic_ns=20,
                    last_received_utc_ns=1_800_000_000_000_000_000,
                    connected=True,
                    freshness_limit_ns=50,
                ),
            ),
        )
    )
    health = cache.snapshot(now_ns=210)
    assert health.streams[0].stale
    assert health.streams[0].last_received_utc_ns == 1_800_000_000_000_000_000


def test_all_bounded_streams_fit_with_worst_case_numeric_values() -> None:
    maximum = 2**63 - 1
    health = HealthSnapshot(
        streams=tuple(
            StreamHealth(
                i,
                last_received_monotonic_ns=maximum,
                last_heartbeat_monotonic_ns=maximum,
                last_received_utc_ns=maximum,
                coverage_start_ns=maximum,
                coverage_end_ns=maximum,
                coverage_pending=maximum,
                coverage_unknown=maximum,
                coverage_unrecoverable=maximum,
                freshness_limit_ns=maximum,
                received_age_ns=maximum,
                heartbeat_age_ns=maximum,
            )
            for i in range(64)
        )
    )
    assert len(health.to_json()) < 65536
