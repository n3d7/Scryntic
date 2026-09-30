"""Repaired candles take the existing normalization/publication/dataset path."""

import json
from dataclasses import replace
from pathlib import Path

import pytest

import scripts.check_bybit_live as live_check
from scryntic.clock.monitor import ClockMonitor
from scryntic.domain.market import Instrument
from scryntic.ingestion.sqlite_spool import DurableIngestor, IngestionError
from scryntic.sources.bybit import BybitHistoricalSource
from tests.ingestion.test_sqlite_spool import installation
from tests.recovery.test_supervisor import (
    BASE,
    INTERVAL,
    LaterClock,
    build,
    envelope,
    run_async,
)
from tests.sources.test_bybit import FixtureClient, fixture


def test_repaired_fixture_candles_reach_archive_dataset(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    async def capture(
        spool: DurableIngestor, clock: ClockMonitor
    ) -> tuple[int, bool, Instrument]:
        fake_clock = LaterClock()
        history = BybitHistoricalSource(
            clock=fake_clock,
            client=FixtureClient(
                {
                    "/v5/market/kline": [fixture("klines-page-1.json")],
                    "/v5/market/instruments-info": [fixture("spot-instruments.json")],
                }
            ),
        )
        instrument = next(
            value
            for value in await history.discover("spot")
            if value.identity.symbol == "BTCUSDT"
        )
        supervisor = build(spool, fake_clock, history=history)
        await supervisor.start()
        await supervisor.ingest(envelope(fake_clock, BASE))
        await supervisor.ingest(envelope(fake_clock, BASE + INTERVAL * 3))
        assert await supervisor.repair_once()
        assert supervisor.ledger.snapshot.spans[0].status == "complete"
        return spool.status().accepted_offset, True, instrument

    monkeypatch.setattr(live_check, "_capture", capture)
    live_check.main()
    result = json.loads(capsys.readouterr().out)
    assert result["accepted"] == result["normalized"] == 4
    assert result["published"] >= 1
    assert result["dataset_rows"] == 4
    assert result["dataset_finalized_rows"] == 4


@run_async
async def test_conflicting_final_overlap_is_retained_and_fails_closed(
    tmp_path: Path,
) -> None:
    clock = LaterClock()
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
        first = envelope(clock, BASE)
        payload = json.loads(first.payload)
        payload["close"] = payload["row"][4] = "101.50"
        await supervisor.ingest(
            replace(first, payload=json.dumps(payload).encode(), payload_limit=8192)
        )
        await supervisor.ingest(envelope(clock, BASE + INTERVAL * 3))
        with pytest.raises(IngestionError, match="Conflicting finalized"):
            await supervisor.repair_once()
        assert supervisor.ledger.snapshot.spans[0].status == "unknown"
        assert spool.status().accepted_offset == 3
