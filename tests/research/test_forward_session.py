import asyncio
import json
from pathlib import Path

from scryntic.forecast.forward_service import ForwardSession
from scryntic.forecast.inference import InferenceStore
from scryntic.forecast.ledger import ForecastLedger
from scryntic.forecast.ledger_contracts import ForecastSlot, ForwardAdmission, Stream
from scryntic.forecast.live import BinanceHourly
from tests.normalization.helpers import installation
from tests.research.test_forecast_ledger import Clock
from tests.research.test_forecast_live import LIVE_T, Transport


def test_forward_tick_schedules_horizons_and_recovery_preserves_identity(
    tmp_path: Path,
) -> None:
    target, clock = installation(tmp_path), Clock(LIVE_T + 10**9)
    with (
        ForecastLedger(target, clock, admission=ForwardAdmission()) as ledger,
        InferenceStore(target, ledger, clock=lambda: clock.now) as store,
    ):
        session = ForwardSession(store, ledger, BinanceHourly(ledger, Transport()))
        result = asyncio.run(session.tick(LIVE_T))
        assert result["published"] == 8
        assert ledger.status()["publications"] == 8
        assert asyncio.run(session.tick(LIVE_T))["published"] == 8
        assert ledger.status()["publications"] == 8
        clock.now = LIVE_T + 2 * 3600 * 10**9 + 61 * 10**9
        result = asyncio.run(session.tick(LIVE_T + 2 * 3600 * 10**9))
        assert result["missed"] == 2
        assert ledger.status()["publications"] == 8


def test_maturation_cursor_advances_past_pending_and_sealed_rows(
    tmp_path: Path,
) -> None:
    target, clock = installation(tmp_path), Clock(LIVE_T + 10**9)
    with (
        ForecastLedger(target, clock, admission=ForwardAdmission()) as ledger,
        InferenceStore(target, ledger, clock=lambda: clock.now) as store,
    ):
        session = ForwardSession(store, ledger, BinanceHourly(ledger, Transport()))
        asyncio.run(session.tick(LIVE_T))
        first = asyncio.run(session.mature(limit=2))["results"]
        second = asyncio.run(session.mature(limit=2))["results"]
        assert {v["slot"] for v in first}.isdisjoint(v["slot"] for v in second)


def test_week_outcome_scan_continues_across_bounded_calls(tmp_path: Path) -> None:
    clock = Clock(LIVE_T + 10**9)

    class DynamicTransport(Transport):
        async def request(self, path: str, params: dict[str, str]) -> bytes:
            if path == "/fapi/v1/time":
                return json.dumps({"serverTime": clock.now // 10**6}).encode()
            if path != "/fapi/v1/klines":
                return await super().request(path, params)
            start = int(params["startTime"])
            return json.dumps(
                [
                    [
                        start,
                        "100",
                        "110",
                        "99",
                        "110",
                        "1",
                        start + 3599999,
                        "110",
                        1,
                        "1",
                        "110",
                        "0",
                    ]
                ]
            ).encode()

    target = installation(tmp_path)
    with (
        ForecastLedger(target, clock, admission=ForwardAdmission()) as ledger,
        InferenceStore(target, ledger, clock=lambda: clock.now) as store,
    ):
        source = BinanceHourly(ledger, DynamicTransport())
        slot = ForecastSlot(
            Stream("week-zero", "forward-shadow", "binance-usdm-public-hourly-v1"),
            "Binance",
            "USD-M-futures",
            "BTCUSDT",
            LIVE_T,
            7 * 86400,
        )
        value = asyncio.run(source.fetch_origin("BTCUSDT", LIVE_T))
        ledger.publish(source.content(slot, value), attempt_id="1" * 32)
        clock.now = slot.endpoint_ns + 61 * 10**9
        session = ForwardSession(store, ledger, source)
        first = asyncio.run(session.mature(limit=1))
        second = asyncio.run(session.mature(limit=1))
        third = asyncio.run(session.mature(limit=1))
        assert [r["fetched_rows"] for r in (first, second, third)] == [64, 64, 40]
        assert [r["results"][0]["state"] for r in (first, second, third)] == [
            "pending",
            "pending",
            "scored",
        ]
        assert len(ledger.events("score")) == 1
