import asyncio
import os
import subprocess
import sys
import threading
import time
from dataclasses import replace
from pathlib import Path

import pytest

from scryntic.application.sources import (
    SourceConfigurationError,
    SourceTransientError,
    StreamRequest,
)
from scryntic.clock.monitor import ClockMonitor
from scryntic.daemon.components import ComponentError, PullBinding, Services
from scryntic.daemon.config import DaemonConfig
from scryntic.daemon.health import Fault, State
from scryntic.domain.identity import InstrumentId
from scryntic.domain.raw import RawEnvelope
from scryntic.forecast.artifact import ImmutableForecastStore
from scryntic.ingestion.sqlite_spool import DurableIngestor, IngestionError
from scryntic.jobs.contracts import JobAttempt, JobState
from scryntic.jobs.service import JobService
from scryntic.jobs.store import JobError, JobStore
from scryntic.sources.bybit_live import BybitLiveSource
from scryntic.sources.fake import fake_instrument
from scryntic.sync.catalog import PullCatalog
from tests.daemon.test_config import installation
from tests.jobs.helpers import job, policy, review
from tests.jobs.test_execution import FixedInputs
from tests.publication.helpers import configured_coordinator
from tests.sync.helpers import PathRemote, enrollment


@pytest.mark.parametrize("stage", ("store", "asyncgens", "executor"))
def test_job_owner_cleanup_failure_is_propagated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stage: str
) -> None:
    target = installation(tmp_path)
    original_close = JobStore.close

    def broken_close(store: JobStore) -> None:
        original_close(store)
        if stage == "store":
            raise RuntimeError("private cleanup failure")

    async def broken_shutdown() -> None:
        raise RuntimeError("private cleanup failure")

    monkeypatch.setattr(JobStore, "close", broken_close)

    async def run() -> None:
        services = Services(DaemonConfig(target, profile="fixture"))
        await services.start()
        loop = services._job_host._loop
        assert loop is not None
        if stage != "store":
            method = (
                "shutdown_asyncgens"
                if stage == "asyncgens"
                else "shutdown_default_executor"
            )
            monkeypatch.setattr(loop, method, broken_shutdown)
        services.stop_intake()
        await services.drain()
        resources_closed: list[bool] = []
        services._resources.callback(lambda: resources_closed.append(True))
        with pytest.raises(RuntimeError, match="private cleanup failure"):
            await services.close()
        assert not services._job_host._thread.is_alive()
        assert services._job_host._finished.done()
        assert loop.is_closed()
        assert resources_closed == [True]
        assert not services._closed

        # All cleanup stages ran: the exclusive durable job owner was released.
        recovered = JobStore(target)
        original_close(recovered)

    asyncio.run(run())


def test_foreground_job_cleanup_failure_is_sanitized(tmp_path: Path) -> None:
    program = """
import asyncio
import os
import signal
import sys
from pathlib import Path
from scryntic.daemon.components import Services
from scryntic.daemon.config import DaemonConfig
from scryntic.daemon.foreground import run_foreground
from scryntic.jobs.store import JobStore
from tests.daemon.test_config import installation

original_close = JobStore.close
def broken_close(store):
    original_close(store)
    raise RuntimeError('SENTINEL-private-cleanup-payload')
JobStore.close = broken_close
class StoppingServices(Services):
    async def start(self):
        await super().start()
        asyncio.get_running_loop().call_later(.1, os.kill, os.getpid(), signal.SIGTERM)
raise SystemExit(run_foreground(StoppingServices(
    DaemonConfig(installation(Path(sys.argv[1])), profile='fixture')
)))
"""
    result = subprocess.run(
        [sys.executable, "-c", program, str(tmp_path)],
        capture_output=True,
        text=True,
        timeout=10,
        env={**os.environ, "PYTHONPATH": "src"},
        check=False,
    )
    assert result.returncode == 1
    output = result.stdout + result.stderr
    assert "SENTINEL" not in output
    assert "Traceback" not in output
    assert '"event":"shutdown"' in output
    assert '"state":"failed"' in output


def test_job_owner_startup_failure_retains_completion_error(tmp_path: Path) -> None:
    target = installation(tmp_path)

    def broken_factory(store: JobStore) -> JobService:
        raise RuntimeError("private startup failure")

    async def run() -> None:
        services = Services(
            DaemonConfig(target, profile="fixture"), job_factory=broken_factory
        )
        with pytest.raises(ComponentError, match="Daemon service startup failed"):
            await services.start()
        assert services.health().state is State.FAILED
        assert services._job_host._ready.exception() is not None
        await services.drain()
        with pytest.raises(RuntimeError, match="private startup failure"):
            await services.close()
        assert not services._job_host._thread.is_alive()
        loop = services._job_host._loop
        assert loop is not None
        assert loop.is_closed()
        with JobStore(target):
            pass

    asyncio.run(run())


async def wait_committed(services: Services, count: int = 2) -> None:
    async with asyncio.timeout(5):
        while services.health().durably_committed < count:
            await asyncio.sleep(0.01)


def test_fixture_durable_pipeline_and_restart(tmp_path: Path) -> None:
    config = DaemonConfig(
        installation(tmp_path), profile="fixture", pipeline_interval_s=0.01
    )

    async def run() -> None:
        first = Services(config)
        await first.start()
        await wait_committed(first)
        first.stop_intake()
        await first.drain()
        health = first.health()
        assert health.durably_committed == 2
        assert health.normalized == 2
        assert health.published == 2
        await first.close()
        second = Services(config)
        second.stop_intake()
        await second.start()
        assert second.health().durably_committed == 2
        assert second.health().published == 2
        await second.drain()
        await second.close()

    asyncio.run(run())
    assert list(config.installation.state_dir.rglob("*.parquet"))


def test_trusted_sync_factory_uses_existing_enrolled_pull_and_closes_owner(
    tmp_path: Path,
) -> None:
    bundle = configured_coordinator(
        tmp_path / "server", offsets=(2,), epochs=("epoch-a",)
    )
    target = installation(tmp_path / "client")
    endpoint = enrollment()
    closed = threading.Event()
    try:
        bundle.coordinator.publish_next()
        with PullCatalog(target) as catalog:
            catalog.enroll(endpoint)

        async def factory() -> PullBinding:
            catalog = PullCatalog(target)
            remote = PathRemote(bundle.root.state_dir / "archive", endpoint)

            async def close_remote() -> None:
                closed.set()

            return PullBinding(catalog, endpoint.name, remote, close_remote)

        async def run() -> None:
            services = Services(
                DaemonConfig(target, profile="fixture"), sync_factory=factory
            )
            await services.start()
            await wait_committed(services)
            services.stop_intake()
            await services.drain()
            assert services.health().sync_enabled
            assert services.health().sync_healthy
            await services.close()

        asyncio.run(run())
        assert closed.is_set()
        with PullCatalog(target) as catalog:
            assert catalog.anchor(endpoint.name).sequence == 1
            assert catalog.count() == 1
    finally:
        bundle.close()


def test_clock_monitor_capture_has_one_owner_and_probe_runs_off_loop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner = threading.get_ident()
    captures: list[int] = []
    probes: list[int] = []
    sample = ClockMonitor.sample

    def capture(clock: ClockMonitor):  # type: ignore[no-untyped-def]
        captures.append(threading.get_ident())
        return sample(clock)

    class MissingStatus:
        def read(self):  # type: ignore[no-untyped-def]
            probes.append(threading.get_ident())
            return None

    monkeypatch.setattr(ClockMonitor, "sample", capture)
    metadata = replace(
        fake_instrument(), identity=InstrumentId("bybit", "spot", "BTCUSDT")
    )

    async def run() -> None:
        services = Services(
            DaemonConfig(installation(tmp_path), clock_interval_s=0.01),
            synchronization_status=MissingStatus(),
            instruments=(metadata,),
            source_factory=lambda clock: FaultyBybit(
                clock, SourceTransientError("offline")
            ),
        )
        await services.start()
        async with asyncio.timeout(5):
            while len(captures) < 4:
                await asyncio.sleep(0.01)
        services.stop_intake()
        await services.drain()
        await services.close()

    asyncio.run(run())
    assert captures
    assert set(captures) == {owner}
    assert probes
    assert all(thread != owner for thread in probes)


def test_explicit_job_dispatch_admission_is_bounded(tmp_path: Path) -> None:
    from scryntic.jobs.fake import LocalFakeProvider

    target = installation(tmp_path)
    model = review()

    def create_jobs(store: JobStore) -> JobService:
        return JobService(
            store,
            (LocalFakeProvider(model),),
            policy(model),
            ImmutableForecastStore(target),
            FixedInputs(),
        )

    async def run() -> None:
        services = Services(
            DaemonConfig(target, profile="fixture"), job_factory=create_jobs
        )
        await services.start()
        results = await asyncio.gather(
            *(services.run_job(f"unknown-{index}") for index in range(17)),
            return_exceptions=True,
        )
        assert any(isinstance(result, ComponentError) for result in results)
        assert all(isinstance(result, (ComponentError, JobError)) for result in results)
        services.stop_intake()
        await services.drain()
        await services.close()

    asyncio.run(run())


def test_normalization_barrier_preserves_raw_and_recovers_with_metadata(
    tmp_path: Path,
) -> None:
    config = DaemonConfig(
        installation(tmp_path), profile="fixture", pipeline_interval_s=0.01
    )

    async def run() -> None:
        blocked = Services(config, instruments=())
        await blocked.start()
        async with asyncio.timeout(5):
            while Fault.NORMALIZATION not in blocked.health().faults:
                await asyncio.sleep(0.01)
        blocked.stop_intake()
        with pytest.raises(ComponentError):
            await blocked.drain()
        committed = blocked.health().durably_committed
        assert committed > 0
        assert blocked.health().normalized == blocked.health().published == 0
        await blocked.close()
        recovered = Services(config)
        recovered.stop_intake()
        await recovered.start()
        await recovered.drain()
        assert recovered.health().published == committed
        await recovered.close()

    asyncio.run(run())


def test_health_reads_only_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def run() -> None:
        services = Services(DaemonConfig(installation(tmp_path), profile="fixture"))
        await services.start()
        await wait_committed(services)
        services.stop_intake()
        await services.drain()

        def unexpected(_: DurableIngestor) -> None:
            raise AssertionError("health attempted journal I/O")

        monkeypatch.setattr(DurableIngestor, "status", unexpected)
        for _ in range(100):
            assert services.health().published == 2
        await services.close()

    asyncio.run(run())


def test_health_includes_unknown_tail_after_recovery_span_quota(tmp_path: Path) -> None:
    from scryntic.recovery.coverage import CoverageLedger
    from tests.recovery.test_supervisor import BASE, build
    from tests.sources.test_bybit_live import FixedClock

    async def run() -> None:
        services = Services(DaemonConfig(installation(tmp_path), profile="fixture"))
        services.stop_intake()
        await services.start()

        def prepare_restarted_supervisor():  # type: ignore[no-untyped-def]
            clock = FixedClock()
            spool = services._required_spool()
            supervisor = build(spool, clock)
            ledger = supervisor.ledger
            sample = clock.sample()
            ledger.begin(sample)
            for index in range(63):
                ledger.loss(index, index + 1, "overflow", sample)
            with pytest.raises(IngestionError, match="quota"):
                ledger.loss(100, 101, "overflow", sample)
            supervisor.ledger = CoverageLedger(spool, ledger.stream, baseline_ns=BASE)
            supervisor.ledger.begin(sample)
            return supervisor

        services._supervisor = await services._work(prepare_restarted_supervisor)
        assert services._supervisor is not None
        assert services._supervisor.ledger.snapshot.unknown_since_ns is not None
        services._update_health()
        stream = services.health().streams[0]
        assert stream.coverage_pending == 63
        assert stream.coverage_unknown == 1
        await services.drain()
        await services.close()

    asyncio.run(run())


def test_supervisor_ingest_reports_committed_duplicate_and_stopped_results(
    tmp_path: Path,
) -> None:
    from tests.recovery.test_supervisor import (
        BASE,
        INTERVAL,
        LaterClock,
        build,
        envelope,
    )

    async def run() -> None:
        with DurableIngestor(
            installation(tmp_path),
            producer="daemon",
            epoch="daemon-v1",
            capacity=2,
            max_payload_bytes=8192,
        ) as spool:
            clock = LaterClock()
            supervisor = build(spool, clock)
            await supervisor.start()
            candle = envelope(clock, BASE)
            committed = await supervisor.ingest(candle)
            assert committed is True
            assert spool.status().accepted_offset == 1
            duplicate = await supervisor.ingest(candle)
            assert duplicate is True
            assert spool.status().accepted_offset == 1
            supervisor.stop_intake()
            stopped = await supervisor.ingest(envelope(clock, BASE + INTERVAL))
            assert stopped is False
            assert spool.status().accepted_offset == 1

    asyncio.run(run())


class FaultyBybit(BybitLiveSource):
    def __init__(self, clock: ClockMonitor, error: Exception) -> None:
        super().__init__(clock=clock)
        self.error = error

    async def stream(self, request: StreamRequest):  # type: ignore[no-untyped-def]
        await asyncio.sleep(0)
        raise self.error
        yield RawEnvelope  # pragma: no cover - keep the existing streaming port.


@pytest.mark.parametrize(
    ("error", "fault", "failed"),
    [
        (SourceConfigurationError("bad subscription"), Fault.SOURCE, False),
        (SourceTransientError("transport down"), Fault.SOURCE, False),
        (IngestionError("journal unavailable"), Fault.STORAGE, True),
    ],
)
def test_source_failures_isolate_config_and_transient_but_fail_storage(
    tmp_path: Path, error: Exception, fault: Fault, failed: bool
) -> None:
    metadata = replace(
        fake_instrument(), identity=InstrumentId("bybit", "spot", "BTCUSDT")
    )

    async def run() -> None:
        services = Services(
            DaemonConfig(installation(tmp_path), pipeline_interval_s=0.01),
            instruments=(metadata,),
            source_factory=lambda clock: FaultyBybit(clock, error),
        )
        await services.start()
        async with asyncio.timeout(5):
            while fault not in services.health().faults:
                await asyncio.sleep(0.01)
        assert (services.health().state is State.FAILED) is failed
        if not failed:
            assert services.health().state is State.DEGRADED
            assert services.health().live
        services.stop_intake()
        await services.drain()
        await services.close()

    asyncio.run(run())


def test_clock_probe_failure_keeps_capture_and_degrades_clock(tmp_path: Path) -> None:
    class BrokenStatus:
        def read(self):  # type: ignore[no-untyped-def]
            raise OSError("probe unavailable")

    async def run() -> None:
        services = Services(
            DaemonConfig(installation(tmp_path), profile="fixture"),
            synchronization_status=BrokenStatus(),
        )
        await services.start()
        await wait_committed(services)
        assert services.health().state is State.DEGRADED
        assert Fault.CLOCK in services.health().faults
        services.stop_intake()
        await services.drain()
        await services.close()

    asyncio.run(run())


def test_clock_capture_failure_stops_intake_instead_of_losing_refresh_task(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = ClockMonitor.sample
    calls = 0

    def failed_capture(clock: ClockMonitor):  # type: ignore[no-untyped-def]
        nonlocal calls
        calls += 1
        if calls > 1:
            raise OSError("capture failed")
        return original(clock)

    monkeypatch.setattr(ClockMonitor, "sample", failed_capture)

    async def run() -> None:
        services = Services(
            DaemonConfig(
                installation(tmp_path), profile="fixture", clock_interval_s=0.01
            )
        )
        await services.start()
        async with asyncio.timeout(5):
            while services.health().state is not State.FAILED:
                await asyncio.sleep(0.01)
        assert Fault.CLOCK in services.health().faults
        assert services.health().shutdown_intake_stopped
        await services.drain()
        await services.close()

    asyncio.run(run())


@pytest.mark.parametrize("failure", ["disk", "catalog", "remote"])
def test_sync_local_storage_fails_global_and_remote_failure_is_isolated(
    tmp_path: Path, failure: str
) -> None:
    bundle = configured_coordinator(
        tmp_path / "server", offsets=(2,), epochs=("epoch-a",)
    )
    target = installation(tmp_path / "client")
    endpoint = enrollment()
    try:
        bundle.coordinator.publish_next()
        with PullCatalog(target) as catalog:
            catalog.enroll(endpoint)

        async def factory() -> PullBinding:
            catalog = PullCatalog(target)
            remote = PathRemote(bundle.root.state_dir / "archive", endpoint)
            if failure == "catalog":
                catalog.close()
            elif failure == "remote":
                remote.interrupt_after = 1

            async def close_remote() -> None:
                pass

            return PullBinding(catalog, endpoint.name, remote, close_remote)

        class DiskFailureServices(Services):
            def _disk_available(self) -> int:
                if failure == "disk" and threading.current_thread().name.startswith(
                    "daemon-sync"
                ):
                    raise OSError("local headroom unavailable")
                return super()._disk_available()

        async def run() -> None:
            services = DiskFailureServices(
                DaemonConfig(target, profile="fixture"), sync_factory=factory
            )
            await services.start()
            fault = Fault.SYNC if failure == "remote" else Fault.STORAGE
            async with asyncio.timeout(5):
                while fault not in services.health().faults:
                    await asyncio.sleep(0.01)
            assert (services.health().state is State.FAILED) is (failure != "remote")
            if failure == "remote":
                await wait_committed(services)
                assert services.health().live
            services.stop_intake()
            await services.drain()
            await services.close()

        asyncio.run(run())
        with PullCatalog(target) as catalog:
            assert catalog.anchor(endpoint.name).sequence == 0
    finally:
        bundle.close()


def test_executor_work_stays_owned_until_actual_completion(tmp_path: Path) -> None:
    entered, release = threading.Event(), threading.Event()

    class BlockedServices(Services):
        def _process(self):  # type: ignore[no-untyped-def]
            entered.set()
            release.wait()
            return super()._process()

    async def run() -> None:
        services = BlockedServices(
            DaemonConfig(installation(tmp_path), profile="fixture")
        )
        await services.start()
        async with asyncio.timeout(5):
            while not entered.is_set():
                await asyncio.sleep(0.01)
        services.stop_intake()
        draining = asyncio.create_task(services.drain())
        done, _ = await asyncio.wait((draining,), timeout=0.05)
        assert not done
        assert services.health().unfinished_operations > 0
        with pytest.raises(ComponentError):
            await services.close()
        release.set()
        await draining
        await services.close()

    try:
        asyncio.run(run())
    finally:
        release.set()


def test_jobs_recover_and_remain_visible_without_automatic_execution(
    tmp_path: Path,
) -> None:
    target = installation(tmp_path)
    now = time.time_ns()
    with JobStore(target) as store:
        for name, state in (
            ("queued", JobState.QUEUED),
            ("interrupted", JobState.RUNNING),
            ("failed", JobState.FAILED),
        ):
            request = replace(
                job(name), submitted_ns=now - 100_000, deadline_ns=now + 60_000_000_000
            )
            store.submit(request)
            if state is not JobState.QUEUED:
                active = store.claim(name)
                if state is JobState.FAILED:
                    store.finish(active, state, reason="provider_failed")

    async def run() -> None:
        services = Services(DaemonConfig(target, profile="fixture"))
        await services.start()
        assert services.health().jobs_pending == 1
        assert services.health().jobs_interrupted == 1
        assert services.health().jobs_failed == 1
        assert Fault.JOBS in services.health().faults
        assert services.health().jobs_running == 0
        services.stop_intake()
        await services.drain()
        await services.close()

    asyncio.run(run())


def test_active_job_owner_does_not_block_pipeline_and_joins_shutdown(
    tmp_path: Path,
) -> None:
    target = installation(tmp_path)
    started, release = threading.Event(), threading.Event()
    model = review()

    class ResistantProvider:
        review = model

        async def execute(self, attempt: JobAttempt) -> bytes:
            started.set()
            try:
                await asyncio.sleep(3600)
            except asyncio.CancelledError:
                await asyncio.to_thread(release.wait)
                raise
            return b""

    def create_jobs(store: JobStore) -> JobService:
        return JobService(
            store,
            (ResistantProvider(),),
            policy(model),
            ImmutableForecastStore(target),
            FixedInputs(),
        )

    async def run() -> None:
        services = Services(
            DaemonConfig(target, profile="fixture", pipeline_interval_s=0.01),
            job_factory=create_jobs,
        )
        await services.start()
        now = time.time_ns()
        request = replace(
            job(), submitted_ns=now - 100_000, deadline_ns=now + 60_000_000_000
        )
        await services.submit_job(request)
        owner = asyncio.create_task(services.run_job(request.job_id))
        async with asyncio.timeout(5):
            while not started.is_set() or services.health().published < 2:
                await asyncio.sleep(0.01)
        assert services.health().jobs_running == 1
        owner.cancel()
        with pytest.raises(asyncio.CancelledError):
            await owner
        assert services.health().unfinished_operations > 0
        services.stop_intake()
        with pytest.raises(ComponentError):
            await services.submit_job(request)
        draining = asyncio.create_task(services.drain())
        done, _ = await asyncio.wait((draining,), timeout=0.05)
        assert not done
        with pytest.raises(JobError):
            JobStore(target)
        release.set()
        await draining
        assert services.health().jobs_interrupted == 1
        await services.close()

    try:
        asyncio.run(run())
    finally:
        release.set()


def test_stop_before_start_never_admits_fixture(tmp_path: Path) -> None:
    async def run() -> None:
        services = Services(DaemonConfig(installation(tmp_path), profile="fixture"))
        services.stop_intake()
        await services.start()
        await services.drain()
        assert services.health().durably_committed == 0
        await services.close()

    asyncio.run(run())


def test_disk_reserve_stops_before_any_acceptance(tmp_path: Path) -> None:
    async def run() -> None:
        services = Services(
            DaemonConfig(
                installation(tmp_path),
                profile="fixture",
                storage_reserve_bytes=2**63 - 1,
            )
        )
        with pytest.raises(RuntimeError):
            await services.start()
        assert services.health().state is State.FAILED
        assert Fault.STORAGE in services.health().faults
        assert services.health().durably_committed == 0
        assert (
            0
            < services.health().storage_available_bytes
            < services.config.storage_reserve_bytes
        )
        services.stop_intake()
        await services.drain()
        await services.close()

    asyncio.run(run())


def test_publication_fault_retains_committed_raw_for_recovery(tmp_path: Path) -> None:
    config = DaemonConfig(installation(tmp_path), profile="fixture")

    def fail(stage: str) -> None:
        if stage == "after_reservation_commit":
            raise RuntimeError("test fault")

    async def run() -> None:
        services = Services(config, publication_fault=fail)
        await services.start()
        await wait_committed(services)
        services.stop_intake()
        with pytest.raises(RuntimeError):
            await services.drain()
        assert Fault.PUBLICATION in services.health().faults
        await services.close()
        restarted = Services(config)
        restarted.stop_intake()
        await restarted.start()
        await restarted.drain()
        assert restarted.health().durably_committed == 2
        assert restarted.health().published == 2
        await restarted.close()

    asyncio.run(run())
