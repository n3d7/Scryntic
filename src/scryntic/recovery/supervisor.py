"""One generic supervised source; same durable raw intake for live and repair."""

import asyncio
import random
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from enum import StrEnum
from functools import partial
from typing import Literal, Protocol

from scryntic.application.sources import (
    HistoricalSource,
    HistoryRequest,
    LossReportingSource,
    ResnapshotSource,
    SourceLoss,
    SourceOperation,
    SourceTransientError,
    StreamingSource,
    StreamRequest,
)
from scryntic.clock.policy import is_final
from scryntic.configuration.clock import ClockLimits
from scryntic.domain.raw import RawEnvelope, RawRecord
from scryntic.domain.time import ClockSample
from scryntic.ingestion.sqlite_spool import DurableIngestor, IngestionError, IntakeFull
from scryntic.recovery.budget import SharedBudget
from scryntic.recovery.coverage import (
    BookEvidence,
    CandleEvidence,
    CoverageLedger,
    CoverageSpan,
    Family,
)


class Clock(Protocol):
    def sample(self) -> ClockSample: ...


class SupervisorState(StrEnum):
    STARTING = "starting"
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    STOPPING = "stopping"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class RepairLimits:
    page_records: int = 4
    max_pages: int = 64
    max_candles: int = 256
    max_rejections: int = 16
    request_timeout_s: float = 10.0
    retry_initial_s: float = 1.0
    retry_max_s: float = 30.0
    shutdown_timeout_s: float = 5.0

    def __post_init__(self) -> None:
        if any(
            type(value) is not int or not 1 <= value <= limit
            for value, limit in (
                (self.page_records, 16),
                (self.max_pages, 64),
                (self.max_candles, 256),
                (self.max_rejections, 64),
            )
        ):
            raise ValueError("Invalid bounded repair limits")
        if not 0 < self.shutdown_timeout_s <= 60:
            raise ValueError("Invalid supervisor shutdown deadline")
        if (
            not 0 < self.request_timeout_s <= 60
            or not 0 < self.retry_initial_s <= self.retry_max_s <= 60
        ):
            raise ValueError("Invalid source deadlines")


async def _io[T](operation: Callable[[], T]) -> T:
    """Cancellation cannot stop a SQLite worker; join the submitted operation."""
    task = asyncio.create_task(asyncio.to_thread(operation))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        await _settle(task)
        raise


async def _settle[T](task: asyncio.Task[T]) -> None:
    cancelled = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            cancelled = True
        except Exception:
            break
    if not task.cancelled():
        task.exception()  # Observe failure without replacing caller cancellation.
    if cancelled:
        raise asyncio.CancelledError


@asynccontextmanager
async def _stream(
    source: StreamingSource, request: StreamRequest
) -> AsyncIterator[AsyncIterator[RawEnvelope]]:
    iterator = source.stream(request)
    try:
        yield iterator
    finally:
        close = getattr(iterator, "aclose", None)
        if close is not None:
            await close()


class SourceSupervisor:
    def __init__(
        self,
        *,
        source: StreamingSource,
        request: StreamRequest,
        spool: DurableIngestor,
        ledger: CoverageLedger,
        budget: SharedBudget,
        clock: Clock,
        history: HistoricalSource | None = None,
        candle_parser: Callable[[RawEnvelope], CandleEvidence | None] | None = None,
        book_parser: Callable[[RawRecord], BookEvidence | None] | None = None,
        interval_ns: int | None = None,
        limits: RepairLimits | None = None,
        clock_limits: ClockLimits | None = None,
    ) -> None:
        reporting_source = source if isinstance(source, LossReportingSource) else None
        resnapshot_source = source if isinstance(source, ResnapshotSource) else None
        self.source, self.request, self.spool = source, request, spool
        self.ledger, self.budget, self.clock = ledger, budget, clock
        self.history, self.candle_parser, self.interval_ns = (
            history,
            candle_parser,
            interval_ns,
        )
        self.limits, self.clock_limits = (
            limits or RepairLimits(),
            clock_limits or ClockLimits(),
        )
        self.book_parser = book_parser
        self.state = SupervisorState.STARTING
        self.last_error: str | None = None
        self._lock = asyncio.Lock()
        self._repair_lock = asyncio.Lock()
        self._closed = asyncio.Event()
        self._rejections = 0
        self._running = False
        self._owner: asyncio.Task[None] | None = None
        self._reporting = reporting_source is not None
        self._resnapshot_source = resnapshot_source
        self._snapshot_required = ledger.stream.family is Family.BOOK
        self._snapshot_attempts = 0
        self._validate_configuration()
        self._validate_book(resnapshot_source)
        if reporting_source is not None:
            if reporting_source is not source:
                raise ValueError("Loss reporter must be supervised source")
            reporting_source.set_loss_handler(self.transport_loss)
            reporting_source.set_start_gate(budget.admit_request)
            reporting_source.set_buffer_admission(
                lambda size: budget.try_acquire("live", 1, size)
            )

    def _validate_configuration(self) -> None:
        capability = self.source.descriptor.require(
            self.request.schema, SourceOperation.STREAM, self.request.subject
        )
        if (
            capability.recovery != self.ledger.stream.recovery
            or self.source.descriptor.source_id != self.ledger.stream.source
            or self.request.subject != self.ledger.stream.subject
        ):
            raise ValueError("Recovery capability mismatch")
        if self.ledger.stream.family is Family.CANDLE and (
            self.candle_parser is None
            or type(self.interval_ns) is not int
            or self.interval_ns <= 0
        ):
            raise ValueError("Candles require typed parser and interval")
        if self.history is not None:
            self.history.descriptor.require(
                self.request.schema, SourceOperation.HISTORY, self.request.subject
            )
            if self.history.descriptor.source_id != self.ledger.stream.source:
                raise ValueError("Recovery source mismatch")

    def _validate_book(self, resnapshot_source: ResnapshotSource | None) -> None:
        if self.ledger.stream.family is Family.BOOK and (
            self.book_parser is None or resnapshot_source is not self.source
        ):
            raise ValueError("Books require provider continuity evidence")

    async def start(self) -> None:
        async with self._lock:
            sample = self.clock.sample()
            await _io(lambda: self.ledger.begin(sample))

    async def transport_loss(self, notice: SourceLoss) -> None:
        async with self._lock:
            self.state = SupervisorState.DEGRADED
            if self.ledger.stream.family is Family.BOOK:
                self._snapshot_required = True
            await _io(
                lambda: self.ledger.loss(
                    self.ledger.snapshot.checkpoint_ns,
                    None,
                    notice.reason,
                    notice.detected,
                )
            )

    def _candle(self, envelope: RawEnvelope) -> CandleEvidence | None:
        if self.candle_parser is None:
            return None
        evidence = self.candle_parser(envelope)
        if evidence is not None and evidence.interval_ns != self.interval_ns:
            return None
        if (
            evidence is not None
            and evidence.finalized
            and not is_final(
                envelope.receipt,
                evidence.start_ns + evidence.interval_ns,
                self.clock_limits,
            )
        ):
            evidence = CandleEvidence(
                evidence.start_ns, evidence.interval_ns, False, evidence.market_sha256
            )
        return evidence

    async def ingest(self, envelope: RawEnvelope) -> bool:
        if self._closed.is_set():
            return False
        reservation = (
            None
            if self._reporting
            else self.budget.try_acquire("live", 1, len(envelope.payload))
        )
        if not self._reporting and reservation is None:
            await self.transport_loss(SourceLoss("overflow", envelope.receipt))
            return False
        try:
            evidence = self._candle(envelope)
            if (
                self.ledger.matches(envelope)
                and evidence is not None
                and evidence.market_sha256 is not None
            ):
                known = self._known_candle(evidence)
                if known == evidence.market_sha256:
                    return True
            try:
                record = await _io(lambda: self.spool.accept(envelope))
            except IntakeFull:
                await self.transport_loss(SourceLoss("overflow", envelope.receipt))
                return False
            if not self.ledger.matches(envelope):
                raise IngestionError("Source envelope identity mismatch")
            await self._observe(record)
            self.state = (
                SupervisorState.DEGRADED
                if any(span.status != "complete" for span in self.ledger.snapshot.spans)
                else SupervisorState.HEALTHY
            )
            return True
        finally:
            if reservation is not None:
                reservation.release()

    async def _observe(self, record: RawRecord) -> None:
        envelope = record.envelope
        evidence = self._candle(envelope)
        async with self._lock:
            book = None if self.book_parser is None else self.book_parser(record)
            malformed = (
                self.ledger.stream.family is Family.CANDLE and evidence is None
            ) or (self.ledger.stream.family is Family.BOOK and book is None)
            if malformed:
                self._rejections += 1
                await _io(
                    lambda: self.ledger.loss(
                        self.ledger.snapshot.checkpoint_ns,
                        None,
                        "malformed",
                        envelope.receipt,
                    )
                )
                if self._rejections >= self.limits.max_rejections:
                    raise IngestionError("Recovery rejection quota reached")
            else:
                if book is not None:
                    valid = await _io(lambda: self.ledger.book_observed(record, book))
                    self._snapshot_required = not valid
                    if valid and book.snapshot:
                        self._snapshot_attempts = 0
                else:
                    await _io(lambda: self.ledger.observed(record, evidence))

    async def repair_once(self) -> bool:
        if self._closed.is_set():
            return False
        if self.ledger.stream.family is Family.BOOK:
            return await self._resnapshot()
        if (
            self.history is None
            or self.ledger.stream.family is not Family.CANDLE
            or self.interval_ns is None
        ):
            return False
        async with self._repair_lock:
            async with self._lock:
                pending = next(
                    (
                        (i, span)
                        for i, span in enumerate(self.ledger.snapshot.spans)
                        if span.status == "pending" and span.end_ns is not None
                    ),
                    None,
                )
            if pending is None:
                return False
            index, span = pending
            assert span.end_ns is not None
            interval = self.interval_ns
            if (
                span.attempts >= self.limits.max_pages
                or span.end_ns - span.start_ns > interval * self.limits.max_candles
                or (span.end_ns - span.start_ns) % interval
            ):
                async with self._lock:
                    await _io(lambda: self.ledger.exhaust(index, "repair-limit"))
                return True
            page_size = min(
                self.limits.page_records, self.history.descriptor.max_page_records
            )
            reservation = self.budget.try_acquire(
                "backfill",
                page_size,
                page_size * self.history.descriptor.max_payload_bytes,
            )
            if reservation is None:
                return False
            try:
                if not self.budget.admit_request():
                    return False
                async with self._lock:
                    await _io(lambda: self.ledger.repair_attempt(index))
                request = HistoryRequest(
                    self.request,
                    max(0, span.start_ns - interval),
                    span.end_ns + interval,
                    page_size,
                    span.cursor,
                )
                async with asyncio.timeout(self.limits.request_timeout_s):
                    page = await self.history.fetch(request)
                if len(page.envelopes) > page_size or any(
                    len(envelope.payload) > self.history.descriptor.max_payload_bytes
                    for envelope in page.envelopes
                ):
                    raise IngestionError("Historical page exceeds reservation")
                proven, final_candles, raw_offset = await self._repair_rows(
                    index, span, page.envelopes
                )
                async with self._lock:
                    await _io(
                        lambda: self.ledger.repair_page(
                            index,
                            tuple(proven),
                            page.next_cursor,
                            interval_ns=interval,
                            exhausted=page.next_cursor is None
                            or page.next_cursor == span.cursor,
                            raw_offset=raw_offset,
                            final_candles=tuple(final_candles),
                        )
                    )
                return True
            finally:
                reservation.release()

    @staticmethod
    def _repair_action(
        span: CoverageSpan, evidence: CandleEvidence | None, known: str | None
    ) -> Literal["duplicate", "conflict", "outside", "admit", "malformed"]:
        if evidence is None:
            return "malformed"
        if known is not None:
            return "duplicate" if known == evidence.market_sha256 else "conflict"
        if span.end_ns is not None and span.start_ns <= evidence.start_ns < span.end_ns:
            return "admit"
        return "outside"

    def _known_candle(self, evidence: CandleEvidence | None) -> str | None:
        if evidence is None:
            return None
        return dict(self.ledger.snapshot.final_candles).get(evidence.start_ns)

    async def _repair_rows(
        self, index: int, span: CoverageSpan, envelopes: tuple[RawEnvelope, ...]
    ) -> tuple[list[int], list[tuple[int, str]], int]:
        assert span.end_ns is not None
        proven: list[int] = []
        final_candles: list[tuple[int, str]] = []
        raw_offset = self.ledger.snapshot.raw_offset
        for envelope in self._intake_envelopes(envelopes):
            evidence = self._candle(envelope)
            async with self._lock:
                known = self._known_candle(evidence)
            action = self._repair_action(span, evidence, known)
            if not self.ledger.matches(envelope):
                action = "malformed"
            if action == "duplicate":
                assert evidence is not None
                proven.append(evidence.start_ns)
                continue
            if action == "outside":
                continue
            record = await _io(partial(self.spool.accept, envelope))
            raw_offset = record.identity.offset
            if action in ("conflict", "malformed"):
                async with self._lock:
                    await _io(partial(self.ledger.exhaust, index, "repair-" + action))
                raise IngestionError(
                    "Conflicting finalized or malformed candle recovery"
                )
            if evidence is None or not evidence.finalized:
                continue
            proven.append(evidence.start_ns)
            if evidence.market_sha256 is not None:
                final_candles.append((evidence.start_ns, evidence.market_sha256))
        return proven, final_candles, raw_offset

    def _intake_envelopes(
        self, envelopes: tuple[RawEnvelope, ...]
    ) -> Iterator[RawEnvelope]:
        """Check the stop flag again before each historical admission."""
        for envelope in envelopes:
            if self._closed.is_set():
                return
            yield envelope

    async def _resnapshot(self) -> bool:
        if not self._snapshot_required or self._resnapshot_source is None:
            return False
        if self._snapshot_attempts >= self.limits.max_pages:
            raise IngestionError("Resnapshot request quota reached")
        reservation = self.budget.try_acquire(
            "backfill", 1, self.source.descriptor.max_payload_bytes
        )
        if reservation is None:
            return False
        try:
            if not self.budget.admit_request():
                return False
            self._snapshot_attempts += 1
            async with asyncio.timeout(self.limits.request_timeout_s):
                await self._resnapshot_source.request_snapshot()
            # A successful request does not make current book state valid.
            self._snapshot_required = False
            return True
        finally:
            reservation.release()

    async def _repair(self) -> None:
        delay = self.limits.retry_initial_s
        while not self._closed.is_set():
            try:
                progressed = await self.repair_once()
                delay = (
                    self.limits.retry_initial_s
                    if progressed
                    else min(delay * 2, self.limits.retry_max_s)
                )
            except (OSError, SourceTransientError):
                self.state = SupervisorState.DEGRADED
                self.last_error = "Transient repair failure"
                delay = min(delay * 2, self.limits.retry_max_s)
            try:
                await asyncio.wait_for(
                    self._closed.wait(), timeout=delay + random.uniform(0, delay / 4)
                )
            except TimeoutError:
                pass

    async def _live(self) -> None:
        delay = self.limits.retry_initial_s
        while not self._closed.is_set():
            if not self._reporting and not self.budget.admit_request():
                await asyncio.sleep(0.05)
                continue
            if await self._live_once():
                delay = self.limits.retry_initial_s
            if not self._closed.is_set():
                try:
                    await asyncio.wait_for(
                        self._closed.wait(),
                        timeout=delay + random.uniform(0, delay / 4),
                    )
                except TimeoutError:
                    delay = min(delay * 2, self.limits.retry_max_s)

    async def _live_once(self) -> bool:
        progressed = False
        try:
            async with _stream(self.source, self.request) as stream:
                async for envelope in stream:
                    if self._closed.is_set() or not await self.ingest(envelope):
                        break
                    progressed = True
            if not self._closed.is_set():
                await self.transport_loss(SourceLoss("disconnect", self.clock.sample()))
        except (OSError, SourceTransientError):
            await self.transport_loss(SourceLoss("disconnect", self.clock.sample()))
        return progressed

    async def run(self) -> None:
        if self._running or self._closed.is_set():
            raise ValueError("Supervisor already running or closed")
        self._running = True
        self._owner = asyncio.current_task()
        tasks: list[asyncio.Task[None]] = []
        failed = True
        try:
            await self.start()
            tasks = [
                asyncio.create_task(self._live()),
                asyncio.create_task(self._repair()),
            ]
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                task.result()
            failed = False
        except asyncio.CancelledError:
            self.state = SupervisorState.STOPPING
            raise
        except Exception:
            self.state = SupervisorState.FAILED
            self.last_error = "Source supervision failed"
            raise
        finally:
            self._closed.set()
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            try:
                await self.source.close()
                if self.history is not None:
                    await self.history.close()
                if not failed:
                    await _io(self.ledger.finish)
                    self.state = SupervisorState.STOPPING
            finally:
                self._owner = None
                self._running = False

    def stop_intake(self) -> None:
        """Prevent further admission immediately; accepted writer work still joins."""
        self._closed.set()
        if self.state is not SupervisorState.FAILED:
            self.state = SupervisorState.STOPPING

    async def close(self) -> None:
        self._closed.set()
        await self.source.close()
        owner = self._owner
        if owner is not None and owner is not asyncio.current_task():
            try:
                await asyncio.wait_for(
                    asyncio.shield(owner), timeout=self.limits.shutdown_timeout_s
                )
            except TimeoutError:
                owner.cancel()
                await asyncio.gather(owner, return_exceptions=True)
