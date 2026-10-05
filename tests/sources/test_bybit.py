from __future__ import annotations

import asyncio
import json
import ssl
import time
from collections import deque
from collections.abc import AsyncIterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Literal, cast
from urllib.parse import urlsplit

import aiohttp
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

    async def get(self, path: str, params: dict[str, str | int]) -> dict[str, Any]:
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
    assert "limit" not in client.calls[-2][1]
    assert "cursor" not in client.calls[-2][1]
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
    assert progress is not None
    assert not progress.complete
    page2 = asyncio.run(restarted.fetch(request(cursor=progress.cursor)))
    checkpoint.save(request(), page2.next_cursor)
    complete = BybitCursorStore(checkpoint_path).load(request())

    assert [item.source_time.value for item in page2.envelopes if item.source_time] == [
        1_700_000_040_000
    ]
    assert complete is not None
    assert complete.complete
    assert complete.cursor is None
    prepared_bybit_cursor_store = BybitCursorStore(checkpoint_path)
    prepared_request = request(symbol="ETHUSDT")
    with pytest.raises(BybitError, match="does not match"):
        prepared_bybit_cursor_store.load(prepared_request)
    prepared_request_2 = request()
    with pytest.raises(BybitError, match="cannot regress"):
        checkpoint.save(prepared_request_2, page1.next_cursor)
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


def _stub_http_session(
    monkeypatch: pytest.MonkeyPatch,
    *,
    status: int = 200,
    headers: dict[str, str] | None = None,
    body: bytes = b'{"retCode":0,"result":{"category":"spot"}}',
) -> dict[str, Any]:
    captured: dict[str, Any] = {}

    def connector(*, ssl: ssl.SSLContext) -> object:
        captured["tls"] = ssl
        return object()

    class Content:
        async def iter_chunked(self, size: int) -> AsyncIterator[bytes]:
            assert size == 65_536
            yield body

    class Response:
        def __init__(self, url: str) -> None:
            parts = urlsplit(url)
            self.url = SimpleNamespace(
                scheme=parts.scheme, host=parts.hostname, path=parts.path
            )
            self.status = status
            self.headers = headers or {}
            self.content = Content()

        async def __aenter__(self) -> Response:
            return self

        async def __aexit__(self, *args: object) -> None:
            del args

    class Session:
        def __init__(self, **kwargs: object) -> None:
            captured["session"] = kwargs

        async def __aenter__(self) -> Session:
            return self

        async def __aexit__(self, *args: object) -> None:
            del args

        def get(self, url: str, **kwargs: object) -> Response:
            captured["url"] = url
            captured["request"] = kwargs
            return Response(url)

    monkeypatch.setattr(aiohttp, "TCPConnector", connector)
    monkeypatch.setattr(aiohttp, "ClientSession", Session)
    monkeypatch.setattr(bybit_module, "_REQUEST_INTERVAL_S", 0.0)
    monkeypatch.setattr(bybit_module, "_PUBLIC_RATE_GATE", _RateGate())
    return captured


def test_public_http_client_pins_tls_host_and_sends_no_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = _stub_http_session(monkeypatch)
    result = asyncio.run(
        BybitPublicClient().get(
            "/v5/market/kline", {"category": "spot", "symbol": "BTCUSDT"}
        )
    )

    assert result == {"category": "spot"}
    assert urlsplit(captured["url"]).scheme == "https"
    assert urlsplit(captured["url"]).hostname == "api.bybit.com"
    assert captured["request"]["allow_redirects"] is False
    request_headers = captured["request"]["headers"]
    assert not {key.casefold() for key in request_headers}.intersection(
        {"authorization", "x-bapi-api-key", "api-key"}
    )
    tls_context = cast(ssl.SSLContext, captured["tls"])
    assert tls_context.verify_mode == ssl.CERT_REQUIRED
    assert tls_context.check_hostname
    assert captured["session"]["trust_env"] is False
    assert captured["session"]["auto_decompress"] is False
    timeout = captured["session"]["timeout"]
    assert timeout.total == 6.0
    assert timeout.sock_connect == 5.0
    assert timeout.sock_read == 5.0


def test_public_http_client_rejects_redirects_oversize_and_non_allowlisted_paths(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_http_session(
        monkeypatch, status=302, headers={"Location": "https://evil.invalid"}
    )
    client = BybitPublicClient()
    prepared_operation = client.get("/v5/market/kline", {"category": "spot"})
    with pytest.raises(BybitError, match="redirect"):
        asyncio.run(prepared_operation)

    _stub_http_session(monkeypatch, headers={"Content-Length": "1048577"})
    prepared_operation = client.get("/v5/market/kline", {"category": "spot"})
    with pytest.raises(BybitError, match="configured limit") as large:
        asyncio.run(prepared_operation)
    assert "secret-response-body" not in str(large.value)

    _stub_http_session(monkeypatch, body=b"x" * 1_048_577)
    prepared_operation = client.get("/v5/market/kline", {"category": "spot"})
    with pytest.raises(BybitError, match="configured limit"):
        asyncio.run(prepared_operation)

    prepared_operation = client.get("https://example.invalid/private", {})
    with pytest.raises(BybitError, match="endpoint"):
        asyncio.run(prepared_operation)


def test_rate_gate_enforces_conservative_two_requests_per_second(
    monkeypatch: pytest.MonkeyPatch,
) -> None:

    times = iter((10.0, 10.1))
    monkeypatch.setattr(time, "monotonic", lambda: next(times))
    gate = _RateGate()

    first = gate.reserve_delay()
    second = gate.reserve_delay()

    assert first == 0
    assert second == pytest.approx(0.4)


def test_fixed_duration_contract_rejects_calendar_month_interval() -> None:
    prepared_clock = FixedClock(sample(wall_ns=_BASE_NS))
    with pytest.raises(ValueError, match="fixed-duration"):
        BybitHistoricalSource(clock=prepared_clock, interval="M")


def test_history_page_size_cannot_exceed_one_api_response() -> None:
    client = FixtureClient({"/v5/market/kline": []})
    source = BybitHistoricalSource(
        client=client, clock=FixedClock(sample(wall_ns=_BASE_NS))
    )

    prepared_operation = source.fetch(request(page_size=1000))
    with pytest.raises(BybitError, match="page"):
        asyncio.run(prepared_operation)

    assert not client.calls


def test_request_deadline_is_bounded(monkeypatch: pytest.MonkeyPatch) -> None:

    class SlowClient:
        async def get(
            self, path: str, params: dict[str, str | int]
        ) -> dict[str, object]:
            del path, params
            await asyncio.sleep(0.1)
            return {}

    monkeypatch.setattr(bybit_module, "_REQUEST_DEADLINE_S", 0.01)
    source = BybitHistoricalSource(
        client=SlowClient(), clock=FixedClock(sample(wall_ns=_BASE_NS))
    )

    prepared_operation = source.discover("spot")
    with pytest.raises(BybitError, match="timed out"):
        asyncio.run(prepared_operation)


def test_request_deadline_closes_slowly_streaming_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def exercise() -> None:
        client_closed = asyncio.Event()
        body_started = asyncio.Event()
        body_finished = asyncio.Event()
        body = b'{"retCode":0,"result":{"category":"spot"}}'

        async def stream(
            reader: asyncio.StreamReader, writer: asyncio.StreamWriter
        ) -> None:
            try:
                await reader.readuntil(b"\r\n\r\n")
                writer.write(
                    b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
                    + f"Content-Length: {len(body)}\r\n\r\n".encode("ascii")
                )
                await writer.drain()

                async def watch_disconnect() -> None:
                    if await reader.read() == b"":
                        client_closed.set()

                watcher = asyncio.create_task(watch_disconnect())
                for byte in body:
                    if watcher.done():
                        break
                    writer.write(bytes((byte,)))
                    await writer.drain()
                    body_started.set()
                    await asyncio.sleep(0.03)
                else:
                    body_finished.set()
                await asyncio.wait_for(watcher, timeout=1.0)
            except (ConnectionError, TimeoutError):
                pass
            finally:
                writer.close()
                await writer.wait_closed()

        server = await asyncio.start_server(stream, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        monkeypatch.setattr(bybit_module, "_BASE_URL", f"http://127.0.0.1:{port}")
        monkeypatch.setattr(bybit_module, "_REQUEST_DEADLINE_S", 0.2)
        monkeypatch.setattr(bybit_module, "_PUBLIC_RATE_GATE", _RateGate())
        source = BybitHistoricalSource(
            client=BybitPublicClient(), clock=FixedClock(sample(wall_ns=_BASE_NS))
        )
        try:
            with pytest.raises(BybitError, match="timed out"):
                await source.discover("spot")
            assert body_started.is_set()
            assert not body_finished.is_set()
            await asyncio.wait_for(client_closed.wait(), timeout=0.3)
        finally:
            server.close()
            await server.wait_closed()

    asyncio.run(exercise())
