import asyncio
import json
from pathlib import Path

import pytest

from scryntic.forecast.ledger import ForecastLedger
from scryntic.forecast.ledger_contracts import ForecastSlot, ForwardAdmission, Stream
from scryntic.forecast.live import BinanceHourly, kline
from tests.normalization.helpers import installation
from tests.research.test_forecast_ledger import Clock

LIVE_T = 1792800000 * 10**9  # 2026-10-24 00:00 UTC.


class Transport:
    close = "100"

    async def request(self, path: str, params: dict[str, str]) -> bytes:
        if path == "/fapi/v1/time":
            body: object = {"serverTime": LIVE_T // 10**6 + 1000}
        elif path == "/fapi/v1/exchangeInfo":
            body = {
                "timezone": "UTC",
                "symbols": [
                    {
                        "symbol": s,
                        "pair": s,
                        "contractType": "PERPETUAL",
                        "status": "TRADING",
                        "baseAsset": s[:-4],
                        "quoteAsset": "USDT",
                        "marginAsset": "USDT",
                        "onboardDate": 1567965300000,
                    }
                    for s in ("BTCUSDT", "ETHUSDT")
                ],
            }
        else:
            assert path == "/fapi/v1/klines"
            assert params["interval"] == "1h"
            assert params["startTime"] == str(LIVE_T // 10**6 - 3600000)
            assert params["endTime"] == str(LIVE_T // 10**6 - 1)
            body = [
                [
                    LIVE_T // 10**6 - 3600000,
                    self.close,
                    self.close,
                    self.close,
                    self.close,
                    "1",
                    LIVE_T // 10**6 - 1,
                    "100",
                    1,
                    "1",
                    "100",
                    "0",
                ]
            ]
        return json.dumps(body).encode()


@pytest.mark.parametrize(
    "change",
    [
        "timezone",
        "not-list",
        "empty",
        "duplicate",
        "identity",
        "onboard-type",
        "onboard-range",
    ],
)
def test_unqualified_native_metadata_never_becomes_a_durable_input(
    tmp_path: Path,
    change: str,
) -> None:
    class InvalidMetadata(Transport):
        async def request(self, path: str, params: dict[str, str]) -> bytes:
            data = await super().request(path, params)
            if path != "/fapi/v1/exchangeInfo":
                return data
            body = json.loads(data)
            if change == "timezone":
                body["timezone"] = "unknown"
            elif change == "not-list":
                body["symbols"] = None
            elif change == "empty":
                body["symbols"] = []
            elif change == "duplicate":
                body["symbols"].append(body["symbols"][0])
            elif change == "identity":
                body["symbols"][0]["contractType"] = "CURRENT_QUARTER"
            else:
                body["symbols"][0]["onboardDate"] = (
                    True if change == "onboard-type" else 0
                )
            return json.dumps(body).encode()

    with ForecastLedger(
        installation(tmp_path), Clock(LIVE_T + 10**9), admission=ForwardAdmission()
    ) as ledger:
        operation = BinanceHourly(ledger, InvalidMetadata()).fetch_origin(
            "BTCUSDT", LIVE_T
        )
        with pytest.raises(ValueError):
            asyncio.run(operation)
        assert not ledger.events("input")


def test_forward_class_needs_owner_grant_and_durable_input_not_worker_declaration(
    tmp_path: Path,
) -> None:
    target, clock = installation(tmp_path), Clock(LIVE_T + 10**9)
    stream = Stream("binance-zero", "forward-shadow", "binance-usdm-public-hourly-v1")
    with ForecastLedger(target, clock, admission=ForwardAdmission()) as ledger:
        source = BinanceHourly(ledger, Transport())
        value = asyncio.run(source.fetch_origin("BTCUSDT", LIVE_T))
        slot = ForecastSlot(stream, "Binance", "USD-M-futures", "BTCUSDT", LIVE_T, 3600)
        forecast = source.content(slot, value)
        receipt = ledger.publish(forecast, attempt_id="1" * 32)
        assert receipt["evidence_class"] == "genuine-forward"
        assert receipt["timeliness"] == "on-time"
        assert len(ledger.events("input")) == 1
        assert len(ledger.events("input-receipt")) == 1
    with ForecastLedger(target, clock) as ledger:
        assert ledger.receipt(slot.sha256) == receipt


def test_long_forecast_is_recordable_but_final_outcome_path_stays_sealed() -> None:
    stream = Stream("binance-zero", "forward-shadow", "binance-usdm-public-hourly-v1")
    slot = ForecastSlot(stream, "Binance", "USD-M-futures", "BTCUSDT", LIVE_T, 2419200)
    assert slot.endpoint_ns > 1793491200 * 10**9


@pytest.mark.parametrize("delay", [20, 120])
def test_new_observation_can_qualify_input_without_promoting_old_publication(
    tmp_path: Path,
    delay: int,
) -> None:
    clock = Clock(LIVE_T + 10**9)
    clock.quality = "unknown"
    with ForecastLedger(
        installation(tmp_path), clock, admission=ForwardAdmission()
    ) as ledger:
        source = BinanceHourly(ledger, Transport())
        first = asyncio.run(source.fetch_origin("BTCUSDT", LIVE_T))
        slot = ForecastSlot(
            Stream("reobserve", "forward-shadow", "binance-usdm-public-hourly-v1"),
            "Binance",
            "USD-M-futures",
            "BTCUSDT",
            LIVE_T,
            3600,
        )
        original = source.content(slot, first)
        receipt = ledger.publish(original, attempt_id="1" * 32)
        assert receipt["evidence_class"] == "forward-unclassified"
        assert source.cached_origin("BTCUSDT", LIVE_T) is None
        clock.quality = "healthy"
        clock.now += delay * 10**9
        later = asyncio.run(source.fetch_origin("BTCUSDT", LIVE_T))
        assert later["clock_qualified"]
        assert later["input_sha256"] == first["input_sha256"]
        assert later["latest_available_ns"] == clock.now
        assert len(ledger.events("input")) == 1
        assert len(ledger.events("input-receipt")) == 2
        assert ledger.events("input-receipt")[0]["body"]["timeliness"] == "unknown"
        assert ledger.publish(original, attempt_id="2" * 32) == receipt
        if delay < 60:
            assert (
                ledger.publish(source.content(slot, later), attempt_id="3" * 32)[
                    "disposition"
                ]
                == "conflict"
            )
        else:
            with pytest.raises(ValueError, match="unavailable at the cutoff"):
                source.content(slot, later)
        clock.now += 10**9
        assert asyncio.run(source.fetch_origin("BTCUSDT", LIVE_T)) == later


def test_source_reversion_is_a_new_vintage_even_when_content_is_reused(
    tmp_path: Path,
) -> None:
    clock, transport = Clock(LIVE_T + 10**9), Transport()
    with ForecastLedger(
        installation(tmp_path), clock, admission=ForwardAdmission()
    ) as ledger:
        source = BinanceHourly(ledger, transport)
        a = asyncio.run(source.fetch_origin("BTCUSDT", LIVE_T))
        clock.now += 10**9
        transport.close = "101"
        b = asyncio.run(source.fetch_origin("BTCUSDT", LIVE_T))
        clock.now += 10**9
        transport.close = "100"
        reverted = asyncio.run(source.fetch_origin("BTCUSDT", LIVE_T))
        assert reverted["input_sha256"] == a["input_sha256"] != b["input_sha256"]
        assert reverted["observation_sha256"] != a["observation_sha256"]
        assert reverted["latest_available_ns"] == clock.now
        assert source.cached_origin("BTCUSDT", LIVE_T) == reverted
        assert len(ledger.events("input")) == 2


def test_interrupted_source_certificate_requires_a_fresh_observation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target, clock = installation(tmp_path), Clock(LIVE_T + 10**9)
    with ForecastLedger(target, clock, admission=ForwardAdmission()) as ledger:
        append = ledger._append

        def interrupted(kind: str, subject: str, body: dict[str, object]) -> str:
            if kind == "input-receipt":
                raise OSError("fixture crash before certificate")
            return append(kind, subject, body)

        monkeypatch.setattr(ledger, "_append", interrupted)
        operation = BinanceHourly(ledger, Transport()).fetch_origin("BTCUSDT", LIVE_T)
        with pytest.raises(OSError, match="fixture crash"):
            asyncio.run(operation)
    clock.now += 120 * 10**9
    with ForecastLedger(target, clock, admission=ForwardAdmission()) as ledger:
        source = BinanceHourly(ledger, Transport())
        assert source.cached_origin("BTCUSDT", LIVE_T) is None
        value = asyncio.run(source.fetch_origin("BTCUSDT", LIVE_T))
        assert value["latest_available_ns"] == clock.now
        assert len(ledger.events("input-observation")) == 2
        assert len(ledger.events("input-receipt")) == 1


@pytest.mark.parametrize(
    "bad", [True, False, 100.0, None, "bogus", "NaN", "1e999999999", "١٠٠", "１００"]
)
def test_native_decimal_schema_rejects_non_string_or_invalid_prices(
    bad: object,
) -> None:
    row = [
        LIVE_T // 10**6 - 3600000,
        bad,
        bad,
        bad,
        bad,
        "1",
        LIVE_T // 10**6 - 1,
        "100",
        1,
        "1",
        "100",
        "0",
    ]
    data = json.dumps([row]).encode()
    with pytest.raises(ValueError):
        kline(data, LIVE_T)
