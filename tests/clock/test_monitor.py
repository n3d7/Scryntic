from dataclasses import replace
from pathlib import Path

import pytest

from scryntic.application.clock import Clock, SyncEvidence
from scryntic.clock.monitor import ClockMonitor
from scryntic.domain.raw import RawEnvelope
from scryntic.ingestion.sqlite_spool import DurableIngestor
from tests.archive.helpers import installation
from tests.clock.helpers import LIMITS, FakeHost, FakeStatus, healthy_monitor


def test_warmup_bounded_age_growth_and_stable_epoch() -> None:
    host, _, monitor = healthy_monitor()
    clock: Clock = monitor
    first = clock.sample()
    # Holdover also widens the upper age: ceil((1000 + 5 - 990) / .9).
    assert first.quality.evidence_age_ns == 17
    assert first.quality.uncertainty_ns == 7
    host.advance(10)
    second = clock.sample()
    assert second.quality.status == "healthy"
    assert second.quality.evidence_age_ns == 28
    assert second.quality.uncertainty_ns == 8
    assert second.quality.epoch == first.quality.epoch


@pytest.mark.parametrize("step", [30, -30])
def test_clock_steps_invalidate_epoch_until_a_post_step_measurement(step: int) -> None:
    host, status, monitor = healthy_monitor()
    previous = monitor.sample()
    host.advance(10, step=step)
    stepped = monitor.sample()
    assert stepped.quality.status == "degraded"
    assert monitor.health is not None and monitor.health.reason == "clock_step"
    assert stepped.quality.epoch != previous.quality.epoch
    assert not monitor.finality_ready(0)
    assert monitor.health is not None and monitor.health.reason == "awaiting_refresh"
    host.advance(50)
    status.value = SyncEvidence(host.value.wall_time_ns, 0, 5, True)
    recovered = monitor.sample()
    assert recovered.quality.status == "healthy"
    assert recovered.quality.epoch != stepped.quality.epoch


@pytest.mark.parametrize(
    "evidence,want,reason",
    [
        (None, "unknown", "missing_evidence"),
        (SyncEvidence(800, 2, 5, True), "unknown", "reference_regression"),
        (SyncEvidence(1100, 2, 5, True), "unknown", "future_evidence"),
        (SyncEvidence(990, 11, 15, True), "degraded", "excess_offset"),
        (SyncEvidence(990, 2, 51, True), "degraded", "excess_uncertainty"),
        (SyncEvidence(990, 2, 5, False), "degraded", "unsynchronized"),
    ],
)
def test_unusable_evidence_never_grants_timing_authority(
    evidence: SyncEvidence | None, want: str, reason: str
) -> None:
    host, status, monitor = healthy_monitor()
    old = monitor.sample()
    status.value = evidence
    sample = monitor.sample()
    assert sample.quality.status == want
    assert monitor.health is not None and monitor.health.reason == reason
    assert sample.quality.epoch != old.quality.epoch
    assert not monitor.finality_ready(0)
    if reason == "reference_regression":
        host.advance(1)
        status.value = SyncEvidence(host.value.wall_time_ns, 0, 5, True)
    else:
        status.value = SyncEvidence(990, 2, 5, True)
    assert monitor.sample().quality.status == "healthy"


def test_evidence_becomes_stale_even_when_daemon_keeps_reporting_synchronized() -> None:
    host, _, monitor = healthy_monitor()
    host.advance(86)
    assert monitor.sample().quality.status == "unknown"
    assert monitor.health is not None and monitor.health.reason == "stale_evidence"


def test_regressing_source_measurement_invalidates_timing_epoch() -> None:
    host, status, monitor = healthy_monitor()
    old = monitor.sample()
    status.value = SyncEvidence(985, 2, 5, True)
    regressed = monitor.sample()
    assert regressed.quality.status == "unknown"
    assert regressed.quality.epoch != old.quality.epoch
    assert (
        monitor.health is not None and monitor.health.reason == "reference_regression"
    )
    host.advance(1)
    status.value = SyncEvidence(host.value.wall_time_ns, 0, 5, True)
    assert monitor.sample().quality.status == "healthy"


@pytest.mark.parametrize("suspend", [60, 1_000_000])
def test_suspend_requires_refresh_even_with_small_wall_step_budget(
    suspend: int,
) -> None:
    host, status, monitor = healthy_monitor()
    host.advance(10, suspend=suspend)
    assert monitor.sample().quality.status == "degraded"
    assert monitor.health is not None and monitor.health.reason == "suspend"
    assert monitor.sample().quality.status == "unknown"
    host.advance(1)
    status.value = SyncEvidence(host.value.wall_time_ns, 0, 5, True)
    assert monitor.sample().quality.status == "healthy"


def test_subthreshold_step_widens_interval_until_reference_refresh() -> None:
    host, status, monitor = healthy_monitor()
    host.advance(10, step=10)
    sample = monitor.sample()
    assert sample.quality.status == "healthy"
    assert sample.quality.uncertainty_ns == 20
    assert not monitor.finality_ready(1010)
    status.value = SyncEvidence(host.value.wall_time_ns, 0, 5, True)
    assert monitor.sample().quality.uncertainty_ns == 6


def test_large_sampling_gap_invalidates_continuity() -> None:
    host, status, monitor = healthy_monitor()
    host.advance(201)
    status.value = SyncEvidence(host.value.wall_time_ns, 0, 5, True)
    assert monitor.sample().quality.status == "degraded"
    assert monitor.health is not None and monitor.health.reason == "sampling_gap"
    assert monitor.sample().quality.status == "unknown"
    host.advance(1)
    status.value = SyncEvidence(host.value.wall_time_ns, 0, 5, True)
    assert monitor.sample().quality.status == "healthy"


def test_session_change_and_monotonic_regression_cannot_reuse_old_evidence() -> None:
    host, status, monitor = healthy_monitor()
    old = monitor.sample()
    host.value = replace(
        host.value, session_id="session-b", monotonic_ns=1, boottime_ns=1
    )
    restarted = monitor.sample()
    assert restarted.quality.status == "unknown"
    assert restarted.session_id != old.session_id
    assert restarted.quality.epoch != old.quality.epoch
    assert not monitor.finality_ready(0)
    host.advance(1)
    status.value = SyncEvidence(host.value.wall_time_ns, 0, 5, True)
    assert monitor.sample().quality.status == "healthy"
    host.value = replace(host.value, monotonic_ns=0)
    assert monitor.sample().quality.status == "degraded"
    assert (
        monitor.health is not None and monitor.health.reason == "monotonic_regression"
    )


def test_process_restart_has_unique_epochs_and_warms_up() -> None:
    host, status, first = healthy_monitor()
    second = ClockMonitor(host, status, LIMITS)
    assert second.sample().quality.status == "unknown"
    assert second.sample().quality.epoch != first.sample().quality.epoch


def test_unsupported_suspend_clock_keeps_capture_but_never_claims_healthy() -> None:
    host, status = FakeHost(), FakeStatus()
    host.value = replace(host.value, boottime_ns=None)
    monitor = ClockMonitor(host, status, LIMITS)
    monitor.sample()
    assert monitor.sample().quality.status == "unknown"
    assert not monitor.finality_ready(0)


def test_extreme_holdover_evidence_stays_persistable_unknown() -> None:
    host, status = FakeHost(), FakeStatus()
    host.value = replace(host.value, wall_time_ns=1_700_000_000_000_000_000)
    status.value = SyncEvidence(1, 0, 5, True)
    limits = replace(LIMITS, holdover_drift_ppb=999_999_999)
    monitor = ClockMonitor(host, status, limits)
    sample = monitor.sample()
    assert sample.quality.status == "unknown"
    assert sample.quality.uncertainty_ns is None
    assert (
        monitor.health is not None
        and monitor.health.reason == "unrepresentable_evidence"
    )


def test_step_during_status_query_is_detected() -> None:
    host, _, monitor = healthy_monitor()

    class StepStatus:
        def read(self) -> SyncEvidence:
            host.advance(1, step=30)
            return SyncEvidence(host.value.wall_time_ns, 0, 5, True)

    querying = ClockMonitor(host, StepStatus(), LIMITS)
    assert querying.sample().quality.status == "degraded"
    assert querying.health is not None and querying.health.reason == "clock_step"
    assert monitor.sample().quality.status == "degraded"


def test_quality_transitions_emit_sanitized_status(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level("INFO", logger="scryntic.clock.monitor")
    _, status, monitor = healthy_monitor()
    status.value = None
    monitor.sample()
    assert "missing_evidence" in caplog.text
    assert "healthy" in caplog.text and "unknown" in caplog.text


def test_durable_capture_preserves_unhealthy_clock_evidence_after_restart(
    tmp_path: Path,
) -> None:
    host, status, monitor = healthy_monitor()
    samples = [monitor.sample()]
    host.advance(1, step=30)
    samples.append(monitor.sample())
    status.value = None
    samples.append(monitor.sample())
    root = installation(tmp_path)
    with DurableIngestor(
        root, producer="collector", epoch="ingest-a", capacity=4, max_payload_bytes=1024
    ) as writer:
        for sample in samples:
            writer.accept(
                RawEnvelope(
                    source="public",
                    stream="candles",
                    channel="history",
                    adapter_version="1",
                    receipt=sample,
                    payload=b"original",
                    payload_limit=1024,
                )
            )
    with DurableIngestor(
        root, producer="collector", epoch="ingest-b", capacity=4, max_payload_bytes=1024
    ) as reader:
        records = reader.records_after(0, limit=4)
        assert tuple(record.envelope.receipt for record in records) == tuple(samples)
        assert all(record.envelope.payload == b"original" for record in records)
        assert len({record.identity.offset for record in records}) == 3
