"""Execution-owned composition of existing durable services for F23."""

import asyncio
import inspect
import os
import sqlite3
import threading
import time
from collections.abc import Awaitable, Callable
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import ExitStack
from dataclasses import dataclass, replace
from functools import partial
from typing import Protocol, TypedDict, cast, overload, runtime_checkable

from scryntic.application.clock import SyncEvidence, SynchronizationStatus
from scryntic.application.sources import (
    BYBIT_CANDLE_SCHEMA,
    HistoricalSource,
    HistoryRequest,
    SourceConfigurationError,
    SourceTransientError,
    StreamingSource,
    StreamRequest,
)
from scryntic.archive.normalized_parquet import NormalizedParquetArchive
from scryntic.archive.raw_parquet import ParquetRawArchive
from scryntic.clock.chrony import ChronyStatus
from scryntic.clock.host import SystemClockReader
from scryntic.clock.monitor import ClockMonitor
from scryntic.composition import _publication_limits
from scryntic.configuration.clock import ClockLimits
from scryntic.configuration.paths import directory, validate_directories
from scryntic.daemon.config import DaemonConfig
from scryntic.daemon.health import (
    ClockStatus,
    Fault,
    HealthCache,
    HealthSnapshot,
    ShutdownPhase,
    State,
    StreamHealth,
)
from scryntic.domain.identity import InstrumentId
from scryntic.domain.market import Instrument
from scryntic.domain.raw import RawEnvelope
from scryntic.domain.time import ClockSample
from scryntic.ingestion.sqlite_spool import DurableIngestor
from scryntic.jobs.contracts import JobRecord, JobRequest, JobState
from scryntic.jobs.service import JobService
from scryntic.jobs.store import JobStore
from scryntic.normalization.runner import Blocked, NoWork, process_next
from scryntic.normalization.sqlite_store import NormalizationStore
from scryntic.publication.coordinator import (
    NoPublishableWork,
    PublicationCoordinator,
    WaitingForNormalization,
)
from scryntic.publication.manifest import ManifestStorage
from scryntic.publication.sqlite_store import PublicationStore
from scryntic.recovery.budget import SharedBudget
from scryntic.recovery.coverage import CoverageLedger, Family, RecoveryStream
from scryntic.recovery.supervisor import SourceSupervisor, SupervisorState
from scryntic.sources.bybit import BybitHistoricalSource
from scryntic.sources.bybit_live import BybitLiveSource
from scryntic.sources.bybit_recovery import candle_evidence
from scryntic.sources.fake import (
    END_NS,
    FAKE_CANDLE_SCHEMA,
    FAKE_INSTRUMENT_ID,
    FIRST_START_NS,
    DeterministicCandleSource,
    fake_instrument,
)
from scryntic.sync.catalog import PullCatalog
from scryntic.sync.model import ReadOnlyRemote
from scryntic.sync.pull import pull

_JOB_ADMISSION_UNAVAILABLE = "Daemon job admission unavailable"
_OWNER_FAILURES = (
    Exception,
    asyncio.CancelledError,
    KeyboardInterrupt,
    SystemExit,
    GeneratorExit,
)


class ComponentError(RuntimeError):
    """Fixed diagnostics; upstream text never enters health or logs."""


class _PipelineError(ComponentError):
    def __init__(self, fault: Fault) -> None:
        super().__init__("Daemon durable pipeline unavailable")
        self.fault = fault


@dataclass(frozen=True, slots=True)
class PullBinding:
    """Trusted injection of an existing enrolled, authenticated F17 connection.

    The async factory runs on the synchronization owner thread. It creates the
    catalog and remote there; no daemon configuration can enroll an endpoint.
    """

    catalog: PullCatalog
    enrollment_name: str
    remote: ReadOnlyRemote
    aclose: Callable[[], Awaitable[None]]


class DiscoveringHistoricalSource(HistoricalSource, Protocol):
    async def discover(self, category: str) -> tuple[Instrument, ...]: ...


@runtime_checkable
class _TransportHealth(Protocol):
    @property
    def connected(self) -> bool: ...

    @property
    def last_heartbeat_monotonic_ns(self) -> int: ...


class _CachedStatus:
    def __init__(self) -> None:
        self.evidence: SyncEvidence | None = None

    def read(self) -> SyncEvidence | None:
        return self.evidence


class _CaptureClock(ClockMonitor):
    def sample(self) -> ClockSample:
        try:
            return super().sample()
        except Exception:
            raise _PipelineError(Fault.CLOCK) from None


@dataclass(frozen=True, slots=True)
class _StoreSnapshot:
    committed: int = 0
    normalized: int = 0
    published: int = 0
    available: int = 0
    jobs: tuple[tuple[JobState, int], ...] = ()


class _ClockFields(TypedDict):
    clock_status: ClockStatus
    clock_offset_ns: int | None
    clock_uncertainty_ns: int | None
    clock_evidence_age_ns: int | None
    clock_epoch: int


class _JobHost:
    """One continuously running owner loop: async providers never block the pipeline."""

    def __init__(
        self, config: DaemonConfig, factory: Callable[[JobStore], JobService] | None
    ) -> None:
        self._config, self._factory = config, factory
        self._ready: Future[None] = Future()
        self._finished: Future[None] = Future()
        self._running = threading.Event()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._store: JobStore | None = None
        self._service: JobService | None = None
        self._closing = threading.Event()
        self._owners: set[asyncio.Task[object]] = set()
        self._pending: set[asyncio.Future[object]] = set()
        self._thread = threading.Thread(
            target=self._run, name="daemon-jobs", daemon=False
        )
        self._started = False

    def _run(self) -> None:
        failure: BaseException | None = None
        try:
            loop = asyncio.new_event_loop()
            self._loop = loop
            asyncio.set_event_loop(loop)
            self._store = JobStore(self._config.installation)
            if self._factory is not None:
                self._service = self._factory(self._store)
            self._ready.set_result(None)
            self._running.set()
            loop.run_forever()
        except _OWNER_FAILURES as error:
            failure = error
            if not self._ready.done():
                self._ready.set_exception(error)
        finally:
            self._running.clear()
            failure = self._cleanup(failure)
            if failure is None:
                self._finished.set_result(None)
            else:
                self._finished.set_exception(failure)

    def _cleanup(self, failure: BaseException | None) -> BaseException | None:
        actions: list[Callable[[], object]] = []
        loop = self._loop
        if loop is not None:
            actions.extend(
                (
                    lambda: loop.run_until_complete(loop.shutdown_asyncgens()),
                    lambda: loop.run_until_complete(loop.shutdown_default_executor()),
                )
            )
        if self._store is not None:
            actions.append(self._store.close)
        if loop is not None:
            actions.append(loop.close)
        for action in actions:
            try:
                action()
            except _OWNER_FAILURES as error:
                if failure is None:
                    failure = error
        return failure

    async def start(self) -> None:
        self._started = True
        self._thread.start()
        await asyncio.shield(asyncio.wrap_future(self._ready))

    @overload
    async def _call[T](
        self, operation: Callable[[], Awaitable[T]], *, control: bool = False
    ) -> T: ...

    @overload
    async def _call[T](
        self, operation: Callable[[], T], *, control: bool = False
    ) -> T: ...

    async def _call[T](
        self, operation: Callable[[], T | Awaitable[T]], *, control: bool = False
    ) -> T:
        if (
            self._loop is None
            or not self._ready.done()
            or self._ready.exception() is not None
        ):
            raise ComponentError("Daemon job owner unavailable")
        if not control and (self._closing.is_set() or len(self._pending) >= 16):
            raise ComponentError(_JOB_ADMISSION_UNAVAILABLE)

        async def owned() -> T:
            if not control and self._closing.is_set():
                raise ComponentError(_JOB_ADMISSION_UNAVAILABLE)
            task = cast(asyncio.Task[object], asyncio.current_task())
            if not control:
                self._owners.add(task)
            try:
                result = operation()
                if inspect.isawaitable(result):
                    return await cast(Awaitable[T], result)
                return result
            finally:
                self._owners.discard(task)

        future = asyncio.wrap_future(
            asyncio.run_coroutine_threadsafe(owned(), self._loop)
        )
        retained = cast(asyncio.Future[object], future)
        self._pending.add(retained)

        def completed(_: asyncio.Future[T]) -> None:
            self._pending.discard(retained)
            if not future.cancelled():
                future.exception()

        future.add_done_callback(completed)
        return await asyncio.shield(future)

    async def summary(self) -> tuple[tuple[JobState, int], ...]:
        def read() -> tuple[tuple[JobState, int], ...]:
            assert self._store is not None
            return tuple(self._store.summary().items())

        return await self._call(read, control=True)

    async def submit(self, job: JobRequest) -> JobRecord:
        def execute() -> JobRecord:
            if self._service is None:
                raise ComponentError(_JOB_ADMISSION_UNAVAILABLE)
            return self._service.submit_job(job)

        return await self._call(execute)

    async def execute(self, job_id: str, resume: bool) -> JobRecord:
        async def execute() -> JobRecord:
            if self._service is None:
                raise ComponentError("Daemon job execution unavailable")
            return await self._service.run(job_id, resume=resume)

        return await self._call(execute)

    def stop_intake(self) -> None:
        self._closing.set()

    async def drain(self) -> None:
        self.stop_intake()
        if not self._started:
            return
        if self._ready.done() and self._ready.exception() is not None:
            return
        await asyncio.shield(asyncio.wrap_future(self._ready))

        async def settle() -> None:
            if self._service is not None:
                await self._service.aclose()
            if self._owners:
                await asyncio.gather(*tuple(self._owners), return_exceptions=True)

        await self._call(settle, control=True)
        while self._pending:
            await asyncio.shield(
                asyncio.gather(*tuple(self._pending), return_exceptions=True)
            )

    def close(self) -> None:
        if self._loop is not None and self._running.is_set():
            self._loop.call_soon_threadsafe(self._loop.stop)
        if self._started:
            self._thread.join()
            self._finished.result()


class _ObservedSupervisor(SourceSupervisor):
    def __init__(self, services: "Services", **kwargs: object) -> None:
        # Construction stays explicit in Services; kwargs here avoid duplicating
        # the existing supervisor port solely to add local receipt observability.
        super().__init__(**kwargs)  # type: ignore[arg-type]
        self._services = services

    async def ingest(self, envelope: RawEnvelope) -> bool:
        self._services._received_envelope(envelope)
        return await super().ingest(envelope)


class Services:
    """Stores live on one owner thread; every submitted operation remains owned.

    Startup validates/reconciles journals before enabling collection. Health is
    a bounded immutable cache. Cancellation cannot claim that an executor or
    SQLite writer has stopped: drain joins actual work before close releases it.
    """

    def __init__(
        self,
        config: DaemonConfig,
        *,
        synchronization_status: SynchronizationStatus | None = None,
        source_factory: Callable[[ClockMonitor], StreamingSource] | None = None,
        history_factory: Callable[[ClockMonitor], DiscoveringHistoricalSource]
        | None = None,
        instruments: tuple[Instrument, ...] | None = None,
        publication_fault: Callable[[str], None] | None = None,
        job_factory: Callable[[JobStore], JobService] | None = None,
        sync_factory: Callable[[], Awaitable[PullBinding]] | None = None,
    ) -> None:
        self.config = config
        self._worker = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="daemon-store"
        )
        self._clock_worker = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="daemon-clock"
        )
        self._sync_worker = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="daemon-sync"
        )
        self._pending: set[asyncio.Future[object]] = set()
        self._resources = ExitStack()
        self._cache = HealthCache()
        self._store_snapshot = _StoreSnapshot()
        self._available_bytes = 0
        self._cached_status = _CachedStatus()
        self._status = synchronization_status or ChronyStatus()
        self._clock = _CaptureClock(
            SystemClockReader(), self._cached_status, ClockLimits()
        )
        self._source_factory = source_factory or (
            lambda clock: BybitLiveSource(clock=clock)
        )
        self._history_factory = history_factory or (
            lambda clock: BybitHistoricalSource(clock=clock)
        )
        self._instruments = {item.identity: item for item in instruments or ()}
        if config.profile == "fixture" and instruments is None:
            self._instruments = {FAKE_INSTRUMENT_ID: fake_instrument()}
        self._publication_fault = publication_fault
        self._sync_factory = sync_factory
        self._job_host = _JobHost(config, job_factory)
        self._sync_runner: asyncio.Runner | None = None
        self._sync_binding: PullBinding | None = None
        self._source: StreamingSource | None = None
        self._history: DiscoveringHistoricalSource | None = None
        self._fixture: DeterministicCandleSource | None = None
        self._supervisor: SourceSupervisor | None = None
        self._source_task: asyncio.Task[None] | None = None
        self._pipeline_task: asyncio.Task[None] | None = None
        self._clock_task: asyncio.Task[None] | None = None
        self._sync_task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()
        self._started = False
        self._closed = False
        self._drained = False
        self._state = State.STARTING
        self._faults: set[Fault] = set()
        self._phase = ShutdownPhase.NONE
        self._received = 0
        self._last_received_ns = 0
        self._last_received_utc_ns: int | None = None
        self._spool: DurableIngestor | None = None
        self._normalization: NormalizationStore | None = None
        self._publication: PublicationStore | None = None
        self._coordinator: PublicationCoordinator | None = None

    async def _work[T](
        self, operation: Callable[[], T], executor: ThreadPoolExecutor | None = None
    ) -> T:
        if len(self._pending) >= 16:
            raise ComponentError("Daemon execution admission full")
        future = asyncio.get_running_loop().run_in_executor(
            executor or self._worker, operation
        )
        retained = cast(asyncio.Future[object], future)
        self._pending.add(retained)

        def completed(_: asyncio.Future[T]) -> None:
            self._pending.discard(retained)
            if not future.cancelled():
                future.exception()  # Observe even if the submitting task was cancelled.
            self._update_health()

        future.add_done_callback(completed)
        self._update_health()
        return await asyncio.shield(future)

    def _disk_available(self) -> int:
        installation = self.config.installation
        with directory(
            installation.state_dir, installation.owner_uid, private=True
        ) as fd:
            info = os.fstatvfs(fd)
        available = info.f_bavail * info.f_frsize
        self._available_bytes = available
        if available < self.config.storage_reserve_bytes:
            raise _PipelineError(Fault.STORAGE)
        return available

    def _open(self) -> _StoreSnapshot:
        validate_directories(self.config.installation)
        self._disk_available()
        self._spool = self._resources.enter_context(
            DurableIngestor(
                self.config.installation,
                producer=self.config.producer,
                epoch=self.config.epoch,
                capacity=self.config.queue_capacity,
                max_payload_bytes=self.config.max_payload_bytes,
                metadata_headroom_bytes=self.config.storage_reserve_bytes,
            )
        )
        self._normalization = self._resources.enter_context(
            NormalizationStore(self.config.installation, producer=self.config.producer)
        )
        self._publication = self._resources.enter_context(
            PublicationStore(self.config.installation, producer=self.config.producer)
        )
        self._coordinator = PublicationCoordinator(
            self._spool,
            self._normalization,
            self._publication,
            ParquetRawArchive(self.config.installation),
            NormalizedParquetArchive(self.config.installation),
            ManifestStorage(self.config.installation),
            self.config.installation,
            _publication_limits(),
            fault=self._publication_fault,
        )
        try:
            self._coordinator.recover()
        except Exception:
            raise _PipelineError(Fault.PUBLICATION) from None
        return self._collect()

    def _collect(self) -> _StoreSnapshot:
        assert self._spool is not None and self._normalization is not None
        assert self._publication is not None
        normalization = self._normalization.status()
        publication = self._publication.status()
        return _StoreSnapshot(
            self._spool.status().accepted_offset,
            0 if normalization.checkpoint is None else normalization.checkpoint.offset,
            0 if publication.checkpoint is None else publication.checkpoint.offset,
            self._disk_available(),
            self._store_snapshot.jobs,
        )

    def _process(self) -> _StoreSnapshot:
        assert self._coordinator is not None
        self._disk_available()
        self._normalize_pending()
        try:
            for _ in range(64):
                published = self._coordinator.publish_next()
                if isinstance(published, (NoPublishableWork, WaitingForNormalization)):
                    break
        except Exception:
            raise _PipelineError(Fault.PUBLICATION) from None
        return self._collect()

    def _normalize_pending(self) -> None:
        assert self._spool is not None and self._normalization is not None
        if not self._instruments and self.config.profile != "fixture":
            return
        try:
            for _ in range(64):
                outcome = process_next(
                    self._spool,
                    self._normalization,
                    self._instruments,
                    normalized_at_ns=time.time_ns(),
                )
                if isinstance(outcome, NoWork):
                    break
                if isinstance(outcome, Blocked):
                    raise _PipelineError(Fault.NORMALIZATION)
        except _PipelineError:
            raise
        except Exception:
            raise _PipelineError(Fault.NORMALIZATION) from None

    async def start(self) -> None:
        if self._started or self._closed:
            raise ComponentError("Daemon services already started or closed")
        self._started = True
        try:
            self._store_snapshot = await self._work(self._open)
            await self._job_host.start()
            self._store_snapshot = replace(
                self._store_snapshot, jobs=await self._job_host.summary()
            )
            await self._refresh_clock()
        except Exception as error:
            self._fail(
                error.fault if isinstance(error, _PipelineError) else Fault.STORAGE
            )
            raise ComponentError("Daemon service startup failed") from None
        self._state = State.STOPPING if self._stop.is_set() else State.HEALTHY
        if not self._stop.is_set():
            self._source_task = asyncio.create_task(
                self._run_source(), name="daemon-source"
            )
            self._pipeline_task = asyncio.create_task(
                self._pipeline_loop(), name="daemon-pipeline"
            )
            self._clock_task = asyncio.create_task(
                self._clock_loop(), name="daemon-clock"
            )
            if self._sync_factory is not None:
                self._sync_task = asyncio.create_task(
                    self._sync_loop(), name="daemon-sync"
                )
        self._update_health()

    async def _refresh_clock(self) -> None:
        try:
            self._cached_status.evidence = await self._work(
                self._status.read, self._clock_worker
            )
        except Exception:
            self._cached_status.evidence = None
        try:
            self._clock.sample()  # Per-capture host clocks, cached synchronization evidence.
        except Exception:
            self._cached_status.evidence = None
            self._fail(Fault.CLOCK)
            raise _PipelineError(Fault.CLOCK) from None
        self._update_health()

    async def _pause(self, seconds: float) -> None:
        try:
            async with asyncio.timeout(seconds):
                await self._stop.wait()
        except TimeoutError:
            pass

    async def _clock_loop(self) -> None:
        while not self._stop.is_set():
            await self._pause(self.config.clock_interval_s)
            if not self._stop.is_set():
                await self._refresh_clock()

    async def _pipeline_loop(self) -> None:
        while not self._stop.is_set():
            try:
                self._store_snapshot = await self._work(self._process)
                self._store_snapshot = replace(
                    self._store_snapshot, jobs=await self._job_host.summary()
                )
                self._update_health()
            except Exception as error:
                self._fail(
                    error.fault if isinstance(error, _PipelineError) else Fault.STORAGE
                )
                return
            await self._pause(self.config.pipeline_interval_s)

    def _received_envelope(self, envelope: RawEnvelope) -> None:
        self._received += 1
        self._last_received_ns = time.monotonic_ns()
        self._last_received_utc_ns = envelope.receipt.wall_time_ns
        self._update_health()

    async def _run_fixture(self) -> None:
        assert self._spool is not None
        self._fixture = DeterministicCandleSource()
        page = await self._fixture.fetch(
            HistoryRequest(
                StreamRequest(FAKE_CANDLE_SCHEMA, FAKE_INSTRUMENT_ID),
                FIRST_START_NS,
                END_NS,
                page_size=2,
            )
        )
        for envelope in page.envelopes:
            if self._stop.is_set():
                break
            self._received_envelope(envelope)
            await self._work(partial(self._spool_accept, envelope))
        self._store_snapshot = await self._work(self._collect)
        self._update_health()
        await self._stop.wait()  # Explicit bounded fixture stays hosted until shutdown.

    def _spool_accept(self, envelope: RawEnvelope) -> None:
        assert self._spool is not None
        self._disk_available()
        self._spool.accept(envelope)

    async def _run_bybit(self) -> None:
        assert self._spool is not None
        self._source = self._source_factory(self._clock)
        self._history = self._history_factory(self._clock)
        subject = InstrumentId("bybit", self.config.category, self.config.symbol)
        while subject not in self._instruments and not self._stop.is_set():
            try:
                instruments = await self._history.discover(self.config.category)
                selected = next(
                    (item for item in instruments if item.identity == subject), None
                )
                if selected is None:
                    raise SourceConfigurationError("Configured instrument unavailable")
                self._instruments = {subject: selected}
            except (OSError, SourceTransientError):
                self._faults.add(Fault.SOURCE)
                self._update_health()
                await self._pause(1.0)
        if self._stop.is_set():
            return
        baseline = max(0, self._clock.sample().wall_time_ns - 120_000_000_000)
        ledger = await self._work(
            lambda: CoverageLedger(
                self._required_spool(),
                RecoveryStream(
                    "daemon-bybit-candle",
                    "bybit-public",
                    "market-kline",
                    "kline-1",
                    subject,
                    Family.CANDLE,
                    "backfill",
                ),
                baseline_ns=baseline,
            )
        )
        self._supervisor = _ObservedSupervisor(
            self,
            source=self._source,
            history=self._history,
            request=StreamRequest(BYBIT_CANDLE_SCHEMA, subject),
            spool=self._spool,
            ledger=ledger,
            budget=SharedBudget(),
            clock=self._clock,
            candle_parser=candle_evidence,
            interval_ns=60_000_000_000,
        )
        self._faults.discard(Fault.SOURCE)
        if self._stop.is_set():
            self._supervisor.stop_intake()
            return
        await self._supervisor.run()

    def _required_spool(self) -> DurableIngestor:
        assert self._spool is not None
        return self._spool

    async def _run_source(self) -> None:
        try:
            if self.config.profile == "fixture":
                await self._run_fixture()
            else:
                await self._run_bybit()
        except asyncio.CancelledError:
            raise
        except (
            ValueError
        ):  # Includes SourceConfigurationError; configuration needs correction.
            self._faults.add(
                Fault.SOURCE
            )  # Isolated source stays disabled, no config retry loop.
            self._update_health()
        except Exception as error:
            self._fail(
                error.fault if isinstance(error, _PipelineError) else Fault.STORAGE
            )

    async def _sync_loop(self) -> None:
        while not self._stop.is_set():
            try:
                sample = self._clock.sample()
                await self._work(partial(self._pull_once, sample), self._sync_worker)
                self._faults.discard(Fault.SYNC)
            except _PipelineError as error:
                self._fail(error.fault)
                return
            except Exception:
                self._faults.add(Fault.SYNC)
            self._update_health()
            await self._pause(self.config.sync_interval_s)

    def _pull_once(self, sample: ClockSample) -> None:
        assert self._sync_factory is not None
        self._check_sync_storage()
        if self._sync_runner is None:
            self._sync_runner = asyncio.Runner()
        if self._sync_binding is None:

            async def create_binding() -> PullBinding:
                assert self._sync_factory is not None
                return await self._sync_factory()

            self._sync_binding = self._sync_runner.run(create_binding())
        binding = self._sync_binding
        self._check_sync_storage()
        try:
            self._sync_runner.run(
                pull(binding.catalog, binding.enrollment_name, binding.remote, sample)
            )
        except Exception as error:
            self._check_sync_storage()
            # F17 preserves contexts while suppressing their diagnostic text.
            # SQLite failures identify local journal failure; remote OSError
            # remains a transport fault unless an explicit local check fails.
            current: BaseException | None = error
            for _ in range(8):
                if isinstance(current, sqlite3.DatabaseError):
                    raise _PipelineError(Fault.STORAGE) from None
                if current is None:
                    break
                current = current.__cause__ or current.__context__
            raise
        self._check_sync_storage()

    def _check_sync_storage(self) -> None:
        try:
            self._disk_available()
            if self._sync_binding is not None:
                self._sync_binding.catalog.count()
                self._sync_binding.catalog.anchor(self._sync_binding.enrollment_name)
        except Exception:
            raise _PipelineError(Fault.STORAGE) from None

    async def submit_job(self, job: JobRequest) -> JobRecord:
        if self._stop.is_set():
            raise ComponentError(_JOB_ADMISSION_UNAVAILABLE)
        return await self._job_host.submit(job)

    async def run_job(self, job_id: str, *, resume: bool = False) -> JobRecord:
        if self._stop.is_set():
            raise ComponentError("Daemon job execution unavailable")
        return await self._job_host.execute(job_id, resume)

    def stop_intake(self) -> None:
        self._stop.set()
        self._job_host.stop_intake()
        self._phase = ShutdownPhase.INTAKE
        if self._state is not State.FAILED:
            self._state = State.STOPPING
        if self._supervisor is not None:
            self._supervisor.stop_intake()
        self._update_health()

    async def _join_tasks(self) -> None:
        if self._source_task is not None and not self._source_task.done():
            self._source_task.cancel()
        tasks = [
            task
            for task in (
                self._source_task,
                self._pipeline_task,
                self._clock_task,
                self._sync_task,
            )
            if task is not None
        ]
        if tasks:
            await asyncio.shield(asyncio.gather(*tasks, return_exceptions=True))
        while self._pending:
            await asyncio.shield(
                asyncio.gather(*tuple(self._pending), return_exceptions=True)
            )

    async def drain(self) -> None:
        self.stop_intake()
        self._phase = ShutdownPhase.DRAIN
        self._update_health()
        await self._join_tasks()
        await self._job_host.drain()
        if self._job_host._started and self._job_host._ready.exception() is None:
            self._store_snapshot = replace(
                self._store_snapshot, jobs=await self._job_host.summary()
            )
        await self._flush_committed()
        self._drained = True
        self._update_health()

    async def _flush_committed(self) -> None:
        # A recovered/committed record can survive a failed source or previous process.
        if self._spool is None or self._coordinator is None:
            return
        try:
            while True:
                previous = self._store_snapshot
                self._store_snapshot = await self._work(self._process)
                if (
                    self._store_snapshot.normalized == self._store_snapshot.committed
                    and (
                        self._store_snapshot.published
                        == self._store_snapshot.normalized
                    )
                ):
                    break
                if self._store_snapshot == previous:
                    raise _PipelineError(Fault.NORMALIZATION)
        except Exception as error:
            self._fail(
                error.fault if isinstance(error, _PipelineError) else Fault.STORAGE
            )
            self._drained = (
                True  # Actual operations joined; failure remains observable.
            )
            raise ComponentError("Daemon durable drain incomplete") from None

    def _close_owner(self) -> None:
        try:
            self._job_host.close()
        finally:
            self._resources.close()

    def _close_sync(self) -> None:
        if self._sync_binding is not None and self._sync_runner is not None:

            async def close_binding() -> None:
                assert self._sync_binding is not None
                await self._sync_binding.aclose()

            self._sync_runner.run(close_binding())
            self._sync_binding.catalog.close()
        if self._sync_runner is not None:
            self._sync_runner.close()

    async def close(self) -> None:
        if self._closed:
            return
        if not self._drained or self._pending:
            raise ComponentError("Daemon resources still have active owners")
        self._phase = ShutdownPhase.CLOSE
        self._update_health()
        # SourceSupervisor joins its own accepted writer work before finishing run.
        for source in (self._source, self._history, self._fixture):
            if source is not None:
                await source.close()
        await self._work(self._close_sync, self._sync_worker)
        try:
            await self._work(self._close_owner)
        finally:
            self._worker.shutdown(wait=True)
            self._clock_worker.shutdown(wait=True)
            self._sync_worker.shutdown(wait=True)
        self._closed = True
        self._phase = ShutdownPhase.COMPLETE
        self._update_health()

    def _fail(self, fault: Fault) -> None:
        self._faults.add(fault)
        self._state = State.FAILED
        self.stop_intake()

    def _update_health(self) -> None:
        now = time.monotonic_ns()
        clock_fields = self._clock_fields()
        faults = set(self._faults)
        if clock_fields["clock_status"] is not ClockStatus.HEALTHY:
            faults.add(Fault.CLOCK)
        supervisor = self._supervisor
        if supervisor is not None and supervisor.state is SupervisorState.DEGRADED:
            faults.add(Fault.SOURCE)
        depth = 0 if self._spool is None else self._spool.queue_depth
        if depth >= self.config.queue_capacity:
            faults.add(Fault.BACKPRESSURE)
        counts = dict(self._store_snapshot.jobs)
        if counts.get(JobState.FAILED, 0) or counts.get(JobState.DEADLINE_EXPIRED, 0):
            faults.add(Fault.JOBS)
        unfinished_tasks = self._unfinished_tasks()
        state = self._state
        if state is State.HEALTHY and faults:
            state = State.DEGRADED
        self._cache.publish(
            HealthSnapshot(
                state=state,
                sampled_at_ns=now,
                heartbeat_ns=now,
                live=not self._closed,
                ready=state in (State.HEALTHY, State.DEGRADED),
                streams=(self._stream_health(),),
                received=self._received,
                durably_committed=self._store_snapshot.committed,
                normalized=self._store_snapshot.normalized,
                published=self._store_snapshot.published,
                **clock_fields,
                queue_depth=depth,
                queue_capacity=self.config.queue_capacity,
                backpressure=Fault.BACKPRESSURE in faults,
                storage_available_bytes=self._available_bytes,
                storage_reserve_bytes=self.config.storage_reserve_bytes,
                normalization_fault=Fault.NORMALIZATION in faults,
                publication_fault=Fault.PUBLICATION in faults,
                sync_enabled=self._sync_factory is not None,
                sync_healthy=Fault.SYNC not in faults,
                jobs_pending=counts.get(JobState.QUEUED, 0),
                jobs_running=counts.get(JobState.RUNNING, 0),
                jobs_failed=counts.get(JobState.FAILED, 0),
                jobs_interrupted=counts.get(JobState.INTERRUPTED, 0),
                jobs_cancelled=counts.get(JobState.CANCELLED, 0),
                jobs_expired=counts.get(JobState.DEADLINE_EXPIRED, 0),
                shutdown_intake_stopped=self._stop.is_set(),
                shutdown_pending=len(self._pending)
                + len(self._job_host._pending)
                + unfinished_tasks,
                shutdown_phase=self._phase,
                unfinished_operations=len(self._pending)
                + len(self._job_host._pending)
                + unfinished_tasks,
                faults=tuple(sorted(faults, key=lambda fault: fault.value)),
            )
        )

    def _clock_fields(self) -> _ClockFields:
        clock = self._clock.health
        if clock is None:
            return {
                "clock_status": ClockStatus.UNKNOWN,
                "clock_offset_ns": None,
                "clock_uncertainty_ns": None,
                "clock_evidence_age_ns": None,
                "clock_epoch": 0,
            }
        quality = clock.sample.quality
        epoch_suffix = quality.epoch.rpartition(":")[2]
        return {
            "clock_status": ClockStatus(quality.status),
            "clock_offset_ns": quality.offset_ns,
            "clock_uncertainty_ns": quality.uncertainty_ns,
            "clock_evidence_age_ns": quality.evidence_age_ns,
            "clock_epoch": int(epoch_suffix) if epoch_suffix.isdecimal() else 0,
        }

    def _stream_health(self) -> StreamHealth:
        transport = self._source if isinstance(self._source, _TransportHealth) else None
        coverage_start = coverage_end = pending = unknown = unrecoverable = 0
        if self._supervisor is not None:
            snapshot = self._supervisor.ledger.snapshot
            coverage_start = min(
                (span.start_ns for span in snapshot.spans),
                default=snapshot.checkpoint_ns,
            )
            coverage_end = snapshot.checkpoint_ns
            pending = sum(span.status == "pending" for span in snapshot.spans)
            unknown = sum(span.status == "unknown" for span in snapshot.spans) + int(
                snapshot.unknown_since_ns is not None
            )
            unrecoverable = sum(
                span.status == "unrecoverable" for span in snapshot.spans
            )
        return StreamHealth(
            ordinal=0,
            last_received_monotonic_ns=self._last_received_ns,
            last_received_utc_ns=self._last_received_utc_ns,
            last_heartbeat_monotonic_ns=0
            if transport is None
            else transport.last_heartbeat_monotonic_ns,
            connected=False if transport is None else transport.connected,
            coverage_start_ns=coverage_start,
            coverage_end_ns=coverage_end,
            coverage_pending=pending,
            coverage_unknown=unknown,
            coverage_unrecoverable=unrecoverable,
            freshness_limit_ns=self.config.freshness_limit_ns,
        )

    def _unfinished_tasks(self) -> int:
        if not self._stop.is_set():
            return 0
        return sum(
            task is not None and not task.done()
            for task in (
                self._source_task,
                self._pipeline_task,
                self._clock_task,
                self._sync_task,
            )
        )

    def health(self) -> HealthSnapshot:
        return self._cache.snapshot()
