"""Bounded public F15 supervision using the existing archive/dataset probe."""

import asyncio
import json
import time

from scripts.check_bybit_live import main
from scryntic.application.sources import BYBIT_CANDLE_SCHEMA, StreamRequest
from scryntic.clock.monitor import ClockMonitor
from scryntic.domain.identity import InstrumentId
from scryntic.domain.market import Instrument
from scryntic.ingestion.sqlite_spool import DurableIngestor
from scryntic.recovery.budget import SharedBudget
from scryntic.recovery.coverage import CoverageLedger, Family, RecoveryStream
from scryntic.recovery.supervisor import SourceSupervisor
from scryntic.sources.bybit import BybitHistoricalSource
from scryntic.sources.bybit_live import BybitLiveSource
from scryntic.sources.bybit_recovery import candle_evidence


async def capture(
    spool: DurableIngestor, clock: ClockMonitor
) -> tuple[int, bool, Instrument]:
    history = BybitHistoricalSource(clock=clock)
    source = BybitLiveSource(clock=clock)
    task: asyncio.Task[None] | None = None
    supervisor: SourceSupervisor | None = None
    try:
        instruments = await history.discover("spot")
        instrument = next(
            value for value in instruments if value.identity.symbol == "BTCUSDT"
        )
        subject = InstrumentId("bybit", "spot", "BTCUSDT")
        baseline = max(0, clock.sample().wall_time_ns - 120_000_000_000)
        ledger = await asyncio.to_thread(
            CoverageLedger,
            spool,
            RecoveryStream(
                "public-check-btc-1",
                "bybit-public",
                "market-kline",
                "kline-1",
                subject,
                Family.CANDLE,
                "backfill",
            ),
            baseline_ns=baseline,
        )
        supervisor = SourceSupervisor(
            source=source,
            request=StreamRequest(BYBIT_CANDLE_SCHEMA, subject),
            spool=spool,
            ledger=ledger,
            budget=SharedBudget(),
            clock=clock,
            history=history,
            candle_parser=candle_evidence,
            interval_ns=60_000_000_000,
        )
        task = asyncio.create_task(supervisor.run())
        deadline = time.monotonic() + 75
        while time.monotonic() < deadline:
            if task.done():
                task.result()
                break
            if (await asyncio.to_thread(spool.status)).accepted_offset >= 8:
                break
            await asyncio.sleep(0.1)
        await supervisor.close()
        if supervisor.budget.used != (0, 0):
            raise RuntimeError("Public recovery budget did not drain")
        count = (await asyncio.to_thread(spool.status)).accepted_offset
        if count == 0:
            raise RuntimeError("Public recovery check captured no candles")
        records = await asyncio.to_thread(spool.records_after, 0, limit=count)
        final = any(
            bool(json.loads(record.envelope.payload)["finalized"]) for record in records
        )
        return count, final, instrument
    finally:
        if supervisor is not None:
            await supervisor.close()
        else:
            await source.close()
            await history.close()
        if task is not None:
            await asyncio.gather(task, return_exceptions=True)


if __name__ == "__main__":
    main(capture=capture, code_revision="f15-public-recovery-check")
