"""Recovery uses existing Bybit fixtures and the real raw spool, never an alternate sink."""

import asyncio
from collections.abc import AsyncIterator, Callable, Coroutine
from dataclasses import replace
from functools import wraps
from pathlib import Path
from typing import Any, Literal

import pytest

from scryntic.application.sources import (
    BYBIT_CANDLE_SCHEMA,
    HistoricalSource,
    SourceCapability,
    SourceDescriptor,
    SourceLoss,
    SourceOperation,
    StreamRequest,
)
from scryntic.domain.raw import RawEnvelope
from scryntic.domain.time import ClockSample
from scryntic.ingestion.sqlite_spool import DurableIngestor, IngestionError
from scryntic.recovery.budget import SharedBudget
from scryntic.recovery.coverage import (
    BookEvidence,
    CoverageLedger,
    Family,
    RecoveryStream,
)
from scryntic.recovery.supervisor import RepairLimits, SourceSupervisor, SupervisorState
from scryntic.sources.bybit import BybitHistoricalSource
from scryntic.sources.bybit_live import BybitLiveSource
from scryntic.sources.bybit_recovery import candle_evidence
from tests.ingestion.test_sqlite_spool import installation
from tests.sources.test_bybit import FixtureClient, fixture
from tests.sources.test_bybit_live import _REQUEST, _START, _SUBJECT, FixedClock, candle

INTERVAL = 60_000_000_000
BASE = 1_700_000_040_000 * 1_000_000


def run_async(
    function: Callable[..., Coroutine[Any, Any, None]],
) -> Callable[..., None]:
    @wraps(function)
    def run(*args: Any, **kwargs: Any) -> None:
        asyncio.run(function(*args, **kwargs))

    return run


class IdleSource:
    def __init__(
        self,
        *,
        family: Family = Family.CANDLE,
        recovery: Literal["backfill", "resnapshot", "coverage-loss"] = "backfill",
    ) -> None:
        self.closed = asyncio.Event()
        self.snapshots_requested = 0
        self.descriptor = SourceDescriptor(
            "bybit-public",
            "1.0",
            (
                SourceCapability(
                    BYBIT_CANDLE_SCHEMA,
                    "instrument",
                    "spot",
                    frozenset({SourceOperation.STREAM}),
                    "none",
                    recovery,
                ),
            ),
            8192,
            1,
        )

    async def stream(self, request: StreamRequest) -> AsyncIterator[RawEnvelope]:
        await self.closed.wait()
        if False:
            yield

    async def close(self) -> None:
        self.closed.set()

    async def request_snapshot(self) -> None:
        self.snapshots_requested += 1


class LaterClock(FixedClock):
    def sample(self) -> ClockSample:
        sample = super().sample()
        return replace(sample, wall_time_ns=sample.wall_time_ns + INTERVAL * 4)


def envelope(clock: FixedClock, start: int, *, finalized: bool = True) -> RawEnvelope:
    data = candle(confirm=finalized)
    data["start"] = start // 1_000_000
    data["end"] = start // 1_000_000 + 59_999
    if start == BASE:
        data.update(
            open="101.00",
            high="103.00",
            low="100.00",
            close="102.00",
            volume="1.000",
            turnover="102.0",
        )
    return BybitLiveSource(clock=clock)._envelope(
        data, {"ts": _START + 200_000}, _SUBJECT
    )[0]


def build(
    spool: DurableIngestor,
    clock: FixedClock,
    *,
    history: HistoricalSource | None = None,
    family: Family = Family.CANDLE,
    budget: SharedBudget | None = None,
) -> SourceSupervisor:
    policy: Literal["backfill", "resnapshot", "coverage-loss"] = (
        "backfill"
        if family is Family.CANDLE
        else "resnapshot"
        if family is Family.BOOK
        else "coverage-loss"
    )
    ledger = CoverageLedger(
        spool,
        RecoveryStream(
            "btc-1", "bybit-public", "market-kline", "kline-1", _SUBJECT, family, policy
        ),
        baseline_ns=BASE,
    )
    source = IdleSource(family=family, recovery=policy)
    return SourceSupervisor(
        source=source,
        request=_REQUEST,
        spool=spool,
        ledger=ledger,
        budget=budget or SharedBudget(request_interval_s=0),
        clock=clock,
        history=history,
        candle_parser=candle_evidence if family is Family.CANDLE else None,
        book_parser=(
            lambda record: BookEvidence(
                record.envelope.source_sequence or 0,
                record.envelope.source_event_id == "snapshot",
            )
        )
        if family is Family.BOOK
        else None,
        interval_ns=INTERVAL if family is Family.CANDLE else None,
        limits=RepairLimits(retry_initial_s=0.01, retry_max_s=0.02),
    )


@run_async
async def test_recorded_overlap_repairs_only_finalized_committed_candles(
    tmp_path: Path,
) -> None:
    clock = LaterClock()
    client = FixtureClient({"/v5/market/kline": [fixture("klines-page-1.json")]})
    history = BybitHistoricalSource(clock=clock, client=client)
    with DurableIngestor(
        installation(tmp_path),
        producer="a",
        epoch="a",
        capacity=2,
        max_payload_bytes=8192,
    ) as spool:
        supervisor = build(spool, clock, history=history)
        await supervisor.start()
        assert await supervisor.ingest(envelope(clock, BASE))
        await supervisor.transport_loss(SourceLoss("disconnect", clock.sample()))
        assert await supervisor.ingest(envelope(clock, BASE + INTERVAL * 3))
        assert await supervisor.repair_once()
        repaired = [
            span
            for span in supervisor.ledger.snapshot.spans
            if span.reason == "disconnect"
        ]
        assert repaired[0].status == "complete"
        assert set(repaired[0].repaired) == {BASE + INTERVAL, BASE + INTERVAL * 2}
        assert spool.status().accepted_offset == 4
        assert len(spool.records_after(0, limit=10)) == 4


@run_async
async def test_unknown_f12_evidence_does_not_complete_repair(tmp_path: Path) -> None:
    clock = FixedClock("unknown")
    history = BybitHistoricalSource(
        clock=clock,
        client=FixtureClient({"/v5/market/kline": [fixture("klines-page-1.json")]}),
    )
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
        assert await supervisor.repair_once()
        assert all(
            span.status != "complete" for span in supervisor.ledger.snapshot.spans
        )


@run_async
@pytest.mark.parametrize(
    "family", [Family.CANDLE, Family.TRADE, Family.BOOK, Family.UNKNOWN]
)
async def test_shared_overflow_never_silently_drops(
    tmp_path: Path, family: Family
) -> None:
    budget = SharedBudget(records=2, bytes_=16384, live_records=1, live_bytes=8192)
    occupied = budget.try_acquire("live", 2, 16384)
    assert occupied is not None
    clock = FixedClock()
    with DurableIngestor(
        installation(tmp_path),
        producer="a",
        epoch="a",
        capacity=2,
        max_payload_bytes=8192,
    ) as spool:
        supervisor = build(spool, clock, family=family, budget=budget)
        await supervisor.start()
        assert not await supervisor.ingest(envelope(clock, BASE))
        assert supervisor.ledger.snapshot.spans[-1].reason == "overflow"
        assert not supervisor.ledger.snapshot.book_valid
        assert spool.status().accepted_offset == 0
    occupied.release()


@run_async
async def test_failure_in_repair_task_stops_stalled_live_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = FixedClock()
    with DurableIngestor(
        installation(tmp_path),
        producer="a",
        epoch="a",
        capacity=2,
        max_payload_bytes=8192,
    ) as spool:
        supervisor = build(spool, clock)

        async def failed_repair() -> None:
            await asyncio.sleep(0.01)
            raise IngestionError("injected recovery failure")

        monkeypatch.setattr(supervisor, "_repair", failed_repair)
        operation = supervisor.run()
        with pytest.raises(IngestionError, match="injected"):
            await asyncio.wait_for(operation, timeout=0.3)
        assert supervisor.state is SupervisorState.FAILED
        assert isinstance(supervisor.source, IdleSource)
        assert supervisor.source.closed.is_set()


@run_async
async def test_caller_cancellation_closes_and_leaves_dirty_session(
    tmp_path: Path,
) -> None:
    clock = FixedClock()
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
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert supervisor.ledger.snapshot.active
        assert supervisor.budget.used == (0, 0)
