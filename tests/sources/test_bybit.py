from __future__ import annotations

import asyncio
import json
import ssl
import time
import urllib.request
from collections import deque
from email.message import Message
from pathlib import Path
from typing import Any, Literal, cast
from urllib.parse import urlsplit

import pytest

import scryntic.sources.bybit as bybit_module
from scryntic.application.sources import (
    BYBIT_CANDLE_SCHEMA,
    HistoryRequest,
    StreamRequest,
)
from scryntic.clock.policy import is_final
from scryntic.configuration.clock import ClockLimits
from scryntic.domain.identity import InstrumentId
from scryntic.domain.time import ClockSample, TimeQuality
from scryntic.sources.bybit import (
    BybitError,
    BybitHistoricalSource,
    BybitPublicClient,
    _instrument,
    _NoRedirect,
    _RateGate,
)
from scryntic.sources.bybit_checkpoint import BybitCursorStore

_FIXTURES = Path(__file__).parents[1] / "fixtures" / "bybit"
_BASE_MS = 1_700_000_040_000
_BASE_NS = _BASE_MS * 1_000_000
_INTERVAL_NS = 60_000_000_000


def fixture(name: str) -> dict[str, Any]:
    return cast(
        dict[str, Any], json.loads((_FIXTURES / name).read_text(encoding="utf-8"))
    )


class FixtureClient:
    def __init__(self, responses: dict[str, list[dict[str, Any]]]) -> None:
        self.responses = {path: deque(values) for path, values in responses.items()}
        self.calls: list[tuple[str, dict[str, str | int]]] = []

    def get(self, path: str, params: dict[str, str | int]) -> dict[str, Any]:
        self.calls.append((path, params))
        response = self.responses[path].popleft()
        return cast(dict[str, Any], response["result"])


class FixedClock:
    def __init__(self, sample: ClockSample) -> None:
        self.value = sample

    def sample(self) -> ClockSample:
        return self.value


def sample(
    *, wall_ns: int, status: Literal["unknown", "degraded", "healthy"] = "healthy"
) -> ClockSample:
    quality = (
        TimeQuality("clock-epoch-1", "healthy", 0, 1_000_000, 0)
        if status == "healthy"
        else TimeQuality("clock-epoch-1", status)
    )
    return ClockSample(wall_ns, 1_000_000, "clock-session-1", quality)


def request(
    *,
    category: str = "spot",
    symbol: str = "BTCUSDT",
    page_size: int = 2,
    cursor: bytes | None = None,
) -> HistoryRequest:
    return HistoryRequest(
        StreamRequest(BYBIT_CANDLE_SCHEMA, InstrumentId("bybit", category, symbol)),
        _BASE_NS,
        _BASE_NS + 3 * _INTERVAL_NS,
        page_size,
        cursor,
    )


def test_instrument_discovery_paginates_categories_and_maps_distinct_units() -> None:
    client = FixtureClient(
        {
            "/v5/market/instruments-info": [
                fixture("linear-instruments-1.json"),
                fixture("linear-instruments-2.json"),
                fixture("inverse-instruments.json"),
                fixture("spot-instruments.json"),
                fixture("option-instruments.json"),
            ]
        }
    )
    source = BybitHistoricalSource(
        client=client, clock=FixedClock(sample(wall_ns=_BASE_NS))
    )

    linear = asyncio.run(source.discover("linear"))
    inverse = asyncio.run(source.discover("inverse"))
    spot = asyncio.run(source.discover("spot"))
    options = asyncio.run(source.discover("option"))

    assert [item.identity.symbol for item in linear] == ["BTCUSDT-30JUN24", "ETHUSDT"]
    assert linear[1].identity.category == "linear"
    assert linear[1].volume_unit == "ETH"
    assert linear[1].settlement_asset == "USDT"
    assert linear[0].expiry_ns == 1_719_734_400_000_000_000
    assert inverse[0].identity.category == "inverse"
    assert inverse[0].volume_unit == "USD"
    assert inverse[0].settlement_asset == "BTC"
    assert spot[0].volume_unit == "BTC"
    assert spot[0].identity.category == "spot"
    assert options[0].identity.category == "option"
    assert options[0].expiry_ns == 1_719_734_400_000_000_000
    assert options[0].settlement_asset == "USDC"
    assert [call[1].get("cursor") for call in client.calls[:2]] == [
        None,
        "linear-page-2",
    ]
    assert "limit" not in client.calls[-2][1] and "cursor" not in client.calls[-2][1]
    assert client.calls[-1][1]["baseCoin"] == "All"


def test_instrument_revision_changes_with_mapped_contract_multiplier() -> None:
    row = dict(fixture("linear-instruments-2.json")["result"]["list"][0])
    row["contractSize"] = "1"
    first = _instrument("linear", row)
    row["contractSize"] = "10"
    second = _instrument("linear", row)

    assert first.contract_multiplier != second.contract_multiplier
    assert first.revision != second.revision


def test_history_returns_chronological_page_and_keeps_unfinished_raw_candle() -> None:
    client = FixtureClient({"/v5/market/kline": [fixture("klines-page-1.json")]})
    clock = FixedClock(sample(wall_ns=_BASE_NS + 2 * _INTERVAL_NS + 30_000_000_000))
    source = BybitHistoricalSource(client=client, clock=clock)

    page = asyncio.run(source.fetch(request()))

    assert [item.source_time.value for item in page.envelopes if item.source_time] == [
        1_700_000_100_000,
        1_700_000_160_000,
    ]
    assert [json.loads(item.payload)["finalized"] for item in page.envelopes] == [
        True,
        False,
    ]
    assert page.envelopes[0].payload.find(b'"row"') >= 0
    assert page.next_cursor is not None
    assert client.calls[0][1]["category"] == "spot"
    assert client.calls[0][1]["interval"] == "1"
    assert client.calls[0][1]["limit"] == 3


def test_opaque_cursor_survives_restart_and_overlapping_page_is_deduplicated(
    tmp_path: Path,
) -> None:
    client = FixtureClient(
        {
            "/v5/market/kline": [
                fixture("klines-page-1.json"),
                fixture("klines-page-2-overlap.json"),
            ]
        }
    )
    clock = FixedClock(sample(wall_ns=_BASE_NS + 3 * _INTERVAL_NS))
    first = BybitHistoricalSource(client=client, clock=clock)
    page1 = asyncio.run(first.fetch(request()))
    assert page1.next_cursor is not None
    checkpoint_path = tmp_path / "bybit.cursor"
    checkpoint = BybitCursorStore(checkpoint_path)
    checkpoint.save(request(), page1.next_cursor)

    restarted = BybitHistoricalSource(client=client, clock=clock)
    progress = BybitCursorStore(checkpoint_path).load(request())
    assert progress is not None and not progress.complete
    page2 = asyncio.run(restarted.fetch(request(cursor=progress.cursor)))
    checkpoint.save(request(), page2.next_cursor)
    complete = BybitCursorStore(checkpoint_path).load(request())

    assert [item.source_time.value for item in page2.envelopes if item.source_time] == [
        1_700_000_040_000
    ]
    assert complete is not None and complete.complete and complete.cursor is None
    with pytest.raises(BybitError, match="does not match"):
        BybitCursorStore(checkpoint_path).load(request(symbol="ETHUSDT"))
    with pytest.raises(BybitError, match="cannot regress"):
        checkpoint.save(request(), page1.next_cursor)
    assert page2.next_cursor is None
    assert client.calls[1][1]["end"] == 1_700_000_100_000
    assert (
        len({item.source_event_id for item in page1.envelopes + page2.envelopes}) == 3
    )


def test_f12_unknown_clock_preserves_candles_but_never_claims_finality() -> None:
    client = FixtureClient({"/v5/market/kline": [fixture("klines-page-1.json")]})
    clock = FixedClock(sample(wall_ns=_BASE_NS + 10 * _INTERVAL_NS, status="unknown"))
    source = BybitHistoricalSource(client=client, clock=clock)

    page = asyncio.run(source.fetch(request()))

    assert len(page.envelopes) == 2
    assert all(not json.loads(item.payload)["finalized"] for item in page.envelopes)
    assert all(item.receipt.quality.status == "unknown" for item in page.envelopes)


def test_f12_boundary_uses_existing_interval_finality_policy() -> None:
    clock_sample = sample(wall_ns=_BASE_NS + _INTERVAL_NS)
    limits = ClockLimits()

    assert not is_final(clock_sample, _BASE_NS + _INTERVAL_NS, limits)
    assert is_final(clock_sample, _BASE_NS + _INTERVAL_NS - 1_000_001, limits)


def test_public_http_client_pins_tls_host_and_sends_no_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:

    class Response:
        headers = Message()

        def __init__(self, url: str) -> None:
            self._url = url

        def __enter__(self) -> Response:
            return self

        def __exit__(self, *args: object) -> None:
            del args

        def geturl(self) -> str:
            return self._url

        def read(self, limit: int) -> bytes:
            assert limit == 1_048_577
            return b'{"retCode":0,"result":{"category":"spot"}}'

    class Opener:
        request = None
        timeout = None

        def open(self, request: urllib.request.Request, *, timeout: float) -> Response:
            self.request = request
            self.timeout = timeout
            return Response(request.full_url)

    opener = Opener()
    handlers: tuple[object, ...] = ()

    def build_opener(*values: object) -> Opener:
        nonlocal handlers
        handlers = values
        return opener

    monkeypatch.setattr(urllib.request, "build_opener", build_opener)
    client = BybitPublicClient()
    result = client.get("/v5/market/kline", {"category": "spot", "symbol": "BTCUSDT"})

    assert result == {"category": "spot"}
    assert opener.request is not None
    assert urlsplit(opener.request.full_url).scheme == "https"
    assert urlsplit(opener.request.full_url).hostname == "api.bybit.com"
    assert opener.timeout == 5.0
    headers = {key.casefold() for key, _ in opener.request.header_items()}
    assert not headers.intersection({"authorization", "x-bapi-api-key", "api-key"})
    tls = next(
        item for item in handlers if isinstance(item, urllib.request.HTTPSHandler)
    )
    tls_context = cast(ssl.SSLContext, getattr(tls, "_" + "context"))
    assert tls_context.verify_mode == ssl.CERT_REQUIRED
    assert tls_context.check_hostname
    assert any(isinstance(item, _NoRedirect) for item in handlers)


def test_public_http_client_rejects_redirects_oversize_and_non_allowlisted_paths(
    monkeypatch: pytest.MonkeyPatch,
) -> None:

    class Response:
        headers = {"Content-Length": "1048577"}

        def __enter__(self) -> Response:
            return self

        def __exit__(self, *args: object) -> None:
            del args

        def geturl(self) -> str:
            return "https://api.bybit.com/v5/market/kline?category=spot"

        def read(self, limit: int) -> bytes:
            del limit
            return b"secret-response-body"

    class Opener:
        def open(self, request: object, *, timeout: float) -> Response:
            del request, timeout
            return Response()

    monkeypatch.setattr(urllib.request, "build_opener", lambda *_: Opener())
    client = BybitPublicClient()
    with pytest.raises(BybitError, match="configured limit") as large:
        client.get("/v5/market/kline", {"category": "spot"})
    assert "secret-response-body" not in str(large.value)
    with pytest.raises(BybitError, match="endpoint"):
        client.get("https://example.invalid/private", {})
    with pytest.raises(BybitError, match="redirect"):
        _NoRedirect().redirect_request(
            None, None, 302, "found", None, "https://evil.invalid"
        )


def test_rate_gate_enforces_conservative_two_requests_per_second(
    monkeypatch: pytest.MonkeyPatch,
) -> None:

    times = iter((10.0, 10.0, 10.1, 10.5))
    sleeps: list[float] = []
    monkeypatch.setattr(time, "monotonic", lambda: next(times))
    monkeypatch.setattr(time, "sleep", sleeps.append)
    gate = _RateGate()

    gate.wait()
    gate.wait()

    assert sleeps == [pytest.approx(0.4)]


def test_fixed_duration_contract_rejects_calendar_month_interval() -> None:
    with pytest.raises(ValueError, match="fixed-duration"):
        BybitHistoricalSource(clock=FixedClock(sample(wall_ns=_BASE_NS)), interval="M")


def test_history_page_size_cannot_exceed_one_api_response() -> None:
    client = FixtureClient({"/v5/market/kline": []})
    source = BybitHistoricalSource(
        client=client, clock=FixedClock(sample(wall_ns=_BASE_NS))
    )

    with pytest.raises(BybitError, match="page"):
        asyncio.run(source.fetch(request(page_size=1_000)))

    assert not client.calls


def test_request_deadline_is_bounded(monkeypatch: pytest.MonkeyPatch) -> None:

    class SlowClient:
        def get(self, path: str, params: dict[str, str | int]) -> dict[str, object]:
            del path, params
            time.sleep(0.1)
            return {}

    monkeypatch.setattr(bybit_module, "_REQUEST_DEADLINE_S", 0.01)
    source = BybitHistoricalSource(
        client=SlowClient(), clock=FixedClock(sample(wall_ns=_BASE_NS))
    )

    with pytest.raises(BybitError, match="timed out"):
        asyncio.run(source.discover("spot"))
