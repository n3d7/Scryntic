"""Restart/cursor, cancellation and non-candle conformance through raw durability."""

import asyncio
import threading
from dataclasses import replace
from pathlib import Path

import pytest

from scryntic.application.sources import HistoryRequest, RawPage
from scryntic.ingestion.sqlite_spool import DurableIngestor, IngestionError
from scryntic.recovery.coverage import Family
from scryntic.recovery.supervisor import RepairLimits, _io
from scryntic.sources.bybit import BybitHistoricalSource
from tests.ingestion.test_sqlite_spool import installation
from tests.recovery.test_supervisor import (
    BASE,
    INTERVAL,
    IdleSource,
    LaterClock,
    build,
    envelope,
    run_async,
)
from tests.sources.test_bybit_live import FixedClock


@run_async
async def test_trade_sequence_loss_and_book_resnapshot_through_spool(
    tmp_path: Path,
) -> None:
    clock = LaterClock()
    for family in (Family.TRADE, Family.BOOK):
        with DurableIngestor(
            installation(tmp_path / family),
            producer="a",
            epoch="a",
            capacity=2,
            max_payload_bytes=8192,
        ) as spool:
            supervisor = build(spool, clock, family=family)
            await supervisor.start()
            base = envelope(clock, BASE)
            await supervisor.ingest(
                replace(
                    base,
                    source_sequence=10,
                    source_event_id="snapshot",
                    payload_limit=8192,
                )
            )
            await supervisor.ingest(
                replace(
                    base,
                    source_sequence=11,
                    source_event_id="delta",
                    payload_limit=8192,
                )
            )
            await supervisor.ingest(
                replace(
                    base,
                    source_sequence=13,
                    source_event_id="delta",
                    payload_limit=8192,
                )
            )
            assert supervisor.ledger.snapshot.spans[-1].status == "unrecoverable"
            if family is Family.BOOK:
                assert not supervisor.ledger.snapshot.book_valid
                assert await supervisor.repair_once()
                assert isinstance(supervisor.source, IdleSource)
                assert supervisor.source.snapshots_requested == 1
                assert not supervisor.ledger.snapshot.book_valid
                await supervisor.ingest(
                    replace(
                        base,
                        source_sequence=14,
                        source_event_id="delta",
                        payload_limit=8192,
                    )
                )
                assert not supervisor.ledger.snapshot.book_valid
                await supervisor.ingest(
                    replace(
                        base,
                        source_sequence=20,
                        source_event_id="snapshot",
                        payload_limit=8192,
                    )
                )
                await supervisor.ingest(
                    replace(
                        base,
                        source_sequence=21,
                        source_event_id="delta",
                        payload_limit=8192,
                    )
                )
                assert supervisor.ledger.snapshot.book_valid
                assert supervisor.ledger.snapshot.spans[-1].status == "unrecoverable"
            assert spool.status().accepted_offset >= 3


class PagedHistory:
    def __init__(self, clock: FixedClock) -> None:
        self.descriptor = BybitHistoricalSource(clock=clock).descriptor
        self.clock = clock
        self.cursors: list[bytes | None] = []
        self.block = False
        self.entered = asyncio.Event()

    async def fetch(self, request: HistoryRequest) -> RawPage:
        self.cursors.append(request.cursor)
        self.entered.set()
        if self.block:
            await asyncio.Event().wait()
        start = BASE + INTERVAL if request.cursor is None else BASE + INTERVAL * 2
        return RawPage(
            (envelope(self.clock, start),),
            b"next" if request.cursor is None else None,
            record_limit=request.page_size,
        )

    async def close(self) -> None:
        pass


@run_async
async def test_persisted_cursor_continues_after_restart(tmp_path: Path) -> None:
    target = installation(tmp_path)
    clock = LaterClock()
    history = PagedHistory(clock)
    with DurableIngestor(
        target, producer="a", epoch="a", capacity=2, max_payload_bytes=8192
    ) as spool:
        supervisor = build(spool, clock, history=history)
        await supervisor.start()
        await supervisor.ingest(envelope(clock, BASE))
        await supervisor.ingest(envelope(clock, BASE + INTERVAL * 3))
        assert await supervisor.repair_once()
        assert supervisor.ledger.snapshot.spans[0].cursor == b"next"
    with DurableIngestor(
        target, producer="a", epoch="b", capacity=2, max_payload_bytes=8192
    ) as spool:
        resumed = build(spool, clock, history=history)
        await resumed.start()
        assert await resumed.repair_once()
        assert history.cursors == [None, b"next"]
        assert resumed.ledger.snapshot.spans[0].status == "complete"
        assert resumed.ledger.snapshot.spans[-1].status == "unknown"


@run_async
async def test_cancelled_fetch_does_not_advance_cursor_or_leak_budget(
    tmp_path: Path,
) -> None:
    clock = LaterClock()
    history = PagedHistory(clock)
    history.block = True
    with DurableIngestor(
        installation(tmp_path),
        producer="a",
        epoch="a",
        capacity=2,
        max_payload_bytes=8192,
    ) as spool:
        supervisor = build(spool, clock, history=history)
        await supervisor.start()
        await supervisor.ingest(envelope(clock, BASE))
        await supervisor.ingest(envelope(clock, BASE + INTERVAL * 3))
        task = asyncio.create_task(supervisor.repair_once())
        await history.entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert supervisor.ledger.snapshot.spans[0].cursor is None
        assert spool.status().accepted_offset == 2
        assert supervisor.budget.used == (0, 0)


@run_async
async def test_page_ack_failure_replays_committed_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = installation(tmp_path)
    clock = LaterClock()
    history = PagedHistory(clock)
    with DurableIngestor(
        target, producer="a", epoch="a", capacity=2, max_payload_bytes=8192
    ) as spool:
        supervisor = build(spool, clock, history=history)
        await supervisor.start()
        await supervisor.ingest(envelope(clock, BASE))
        await supervisor.ingest(envelope(clock, BASE + INTERVAL * 3))
        with monkeypatch.context() as patch:
            patch.setattr(
                supervisor.ledger,
                "repair_page",
                lambda *args, **kwargs: (_ for _ in ()).throw(
                    IngestionError("injected page ack")
                ),
            )
            with pytest.raises(IngestionError):
                await supervisor.repair_once()
        assert spool.status().accepted_offset == 3
        assert supervisor.ledger.snapshot.spans[0].cursor is None
    with DurableIngestor(
        target, producer="a", epoch="b", capacity=2, max_payload_bytes=8192
    ) as spool:
        resumed = build(spool, clock, history=history)
        await resumed.start()
        assert await resumed.repair_once()
        assert history.cursors == [None, None]
        assert spool.status().accepted_offset == 4


@run_async
async def test_backfill_inflight_keeps_live_reserve_available(tmp_path: Path) -> None:
    clock = LaterClock()
    history = PagedHistory(clock)
    history.block = True
    with DurableIngestor(
        installation(tmp_path),
        producer="a",
        epoch="a",
        capacity=2,
        max_payload_bytes=8192,
    ) as spool:
        supervisor = build(spool, clock, history=history)
        await supervisor.start()
        await supervisor.ingest(envelope(clock, BASE))
        await supervisor.ingest(envelope(clock, BASE + INTERVAL * 3))
        task = asyncio.create_task(supervisor.repair_once())
        await history.entered.wait()
        assert supervisor.budget.used == (4, 32768)
        assert await supervisor.ingest(envelope(clock, BASE + INTERVAL * 4))
        assert supervisor.budget.used == (4, 32768)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert supervisor.budget.used == (0, 0)


@run_async
async def test_repeated_cancellation_joins_uncancellable_writer_work() -> None:
    entered = threading.Event()
    released = threading.Event()
    completed = threading.Event()

    def operation() -> int:
        entered.set()
        assert released.wait(3)
        completed.set()
        return 1

    task = asyncio.create_task(_io(operation))
    await asyncio.to_thread(entered.wait, 1)
    task.cancel()
    await asyncio.sleep(0.01)
    task.cancel()
    await asyncio.sleep(0.01)
    assert not task.done()
    released.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert completed.is_set()


@run_async
async def test_explicit_close_drains_and_persists_clean_checkpoint(
    tmp_path: Path,
) -> None:
    clock = LaterClock()
    with DurableIngestor(
        installation(tmp_path),
        producer="a",
        epoch="a",
        capacity=2,
        max_payload_bytes=8192,
    ) as spool:
        supervisor = build(spool, clock)
        task = asyncio.create_task(supervisor.run())
        await asyncio.sleep(0.04)
        await supervisor.close()
        assert task.done()
        assert not task.cancelled()
        assert not supervisor.ledger.snapshot.active
        assert supervisor.budget.used == (0, 0)


@run_async
async def test_timed_out_repair_attempt_is_bounded_and_persistent(
    tmp_path: Path,
) -> None:
    clock = LaterClock()
    history = PagedHistory(clock)
    history.block = True
    with DurableIngestor(
        installation(tmp_path),
        producer="a",
        epoch="a",
        capacity=2,
        max_payload_bytes=8192,
    ) as spool:
        supervisor = build(spool, clock, history=history)
        supervisor.limits = RepairLimits(max_pages=1, request_timeout_s=0.02)
        await supervisor.start()
        await supervisor.ingest(envelope(clock, BASE))
        await supervisor.ingest(envelope(clock, BASE + INTERVAL * 3))
        with pytest.raises(TimeoutError):
            await supervisor.repair_once()
        assert supervisor.budget.used == (0, 0)
        assert supervisor.ledger.snapshot.spans[0].attempts == 1
        assert supervisor.ledger.snapshot.spans[0].cursor is None
        assert await supervisor.repair_once()
        assert supervisor.ledger.snapshot.spans[0].status == "unknown"
        assert len(history.cursors) == 1


@run_async
async def test_unfinished_candle_cannot_disappear_at_next_interval(
    tmp_path: Path,
) -> None:
    clock = LaterClock()
    with DurableIngestor(
        installation(tmp_path),
        producer="a",
        epoch="a",
        capacity=2,
        max_payload_bytes=8192,
    ) as spool:
        supervisor = build(spool, clock)
        await supervisor.start()
        await supervisor.ingest(envelope(clock, BASE, finalized=False))
        await supervisor.ingest(envelope(clock, BASE + INTERVAL))
        gap = supervisor.ledger.snapshot.spans[0]
        assert gap.start_ns == BASE
        assert gap.end_ns == BASE + INTERVAL
        assert gap.status == "pending"


@run_async
async def test_quarantine_rejections_stop_at_explicit_quota(tmp_path: Path) -> None:
    clock = LaterClock()
    with DurableIngestor(
        installation(tmp_path),
        producer="a",
        epoch="a",
        capacity=2,
        max_payload_bytes=8192,
    ) as spool:
        supervisor = build(spool, clock)
        supervisor.limits = RepairLimits(max_rejections=2)
        await supervisor.start()
        malformed = replace(envelope(clock, BASE), payload=b"{}", payload_limit=8192)
        assert await supervisor.ingest(malformed)
        with pytest.raises(IngestionError, match="quota"):
            await supervisor.ingest(malformed)
        assert spool.status().accepted_offset == 2
        assert all(
            span.status == "unknown" for span in supervisor.ledger.snapshot.spans
        )
