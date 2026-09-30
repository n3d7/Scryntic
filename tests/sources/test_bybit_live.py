from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from dataclasses import replace
from decimal import Decimal
from typing import Any, cast

import aiohttp
import pytest
from aiohttp import web

import scripts.check_bybit_live as live_check
import scryntic.sources.bybit_live as live_module
from scryntic.application.sources import BYBIT_CANDLE_SCHEMA, StreamRequest
from scryntic.clock.monitor import ClockMonitor
from scryntic.domain.identity import InstrumentId
from scryntic.domain.market import Instrument
from scryntic.domain.raw import IngestionId, RawRecord
from scryntic.domain.time import ClockSample, TimeQuality
from scryntic.ingestion.sqlite_spool import DurableIngestor
from scryntic.normalization.bybit_candle import inspect_bybit_candle
from scryntic.normalization.candle import ParsedFakeCandle
from scryntic.sources.bybit_live import (
    BybitLiveError,
    BybitLiveSource,
    BybitPublicWebSocketClient,
    LiveLimits,
)

_START = 1_700_000_100_000
_SUBJECT = InstrumentId("bybit", "spot", "BTCUSDT")
_REQUEST = StreamRequest(BYBIT_CANDLE_SCHEMA, _SUBJECT)


class FixedClock:
    def __init__(self, status: str = "healthy") -> None:
        self.status = status
        self.epoch = "epoch-1"
        self.calls = 0

    def sample(self) -> ClockSample:
        self.calls += 1
        quality = (
            TimeQuality(self.epoch, "healthy", 0, 1_000_000, 0)
            if self.status == "healthy"
            else TimeQuality(self.epoch, "unknown")
        )
        return ClockSample((_START + 120_000) * 1_000_000, 1, "session-1", quality)


def candle(*, confirm: bool = False, close: str = "101") -> dict[str, object]:
    return {
        "start": _START,
        "end": _START + 59_999,
        "interval": "1",
        "open": "100",
        "high": "102",
        "low": "99",
        "close": close,
        "volume": "2",
        "turnover": "202",
        "confirm": confirm,
        "timestamp": _START + 30_000,
    }


def frame(*, confirm: bool = False, close: str = "101") -> dict[str, object]:
    return {
        "topic": "kline.1.BTCUSDT",
        "type": "snapshot",
        "ts": _START + 60_000,
        "data": [candle(confirm=confirm, close=close)],
    }


class LocalClient:
    def __init__(self, url: str) -> None:
        self.url = url
        self.session = aiohttp.ClientSession()
        self.connections = 0

    async def connect(self, category: str) -> aiohttp.ClientWebSocketResponse:
        assert category == "spot"
        self.connections += 1
        return await self.session.ws_connect(self.url, max_msg_size=65_536, compress=0)

    async def close(self) -> None:
        await self.session.close()


async def with_server(
    handler: Callable[[web.WebSocketResponse], Awaitable[None]],
    run: Callable[[LocalClient], Awaitable[None]],
) -> None:
    async def endpoint(request: web.Request) -> web.WebSocketResponse:
        assert "Sec-WebSocket-Extensions" not in request.headers
        socket = web.WebSocketResponse(autoping=True)
        await socket.prepare(request)
        await handler(socket)
        return socket

    app = web.Application()
    app.router.add_get("/ws", endpoint)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    assert site._server is not None
    port = cast(asyncio.Server, site._server).sockets[0].getsockname()[1]
    client = LocalClient(f"http://127.0.0.1:{port}/ws")
    try:
        await run(client)
    finally:
        await client.close()
        await runner.cleanup()


async def subscribed(socket: web.WebSocketResponse) -> None:
    request = await socket.receive_json(timeout=1)
    assert request == {"op": "subscribe", "args": ["kline.1.BTCUSDT"]}
    await socket.send_json({"op": "subscribe", "success": True, "ret_msg": ""})


def test_revisions_duplicates_and_f12_finality_replay_same_schema() -> None:
    healthy = asyncio.Event()

    async def handler(socket: web.WebSocketResponse) -> None:
        await subscribed(socket)
        await socket.send_json(frame())
        await socket.send_json(frame())
        await socket.send_json(frame(confirm=True))
        await healthy.wait()
        await socket.send_json(frame(confirm=True))
        await socket.send_json(frame(confirm=True, close="102"))
        await asyncio.sleep(0.2)

    async def run(client: LocalClient) -> None:
        clock = FixedClock("unknown")
        source = BybitLiveSource(clock=clock, client=client)
        stream = source.stream(_REQUEST)
        try:
            first = await asyncio.wait_for(anext(stream), 2)
            assert json.loads(first.payload)["finalized"] is False
            second = await asyncio.wait_for(anext(stream), 2)
            assert json.loads(second.payload)["finalized"] is False
            clock.status = "healthy"
            healthy.set()
            third = await asyncio.wait_for(anext(stream), 2)
            assert json.loads(third.payload)["finalized"] is True
            assert third.source_event_id == first.source_event_id
            record = RawRecord(IngestionId("test", "epoch", 1), third)
            parsed = inspect_bybit_candle(record)
            assert isinstance(parsed, ParsedFakeCandle)
            assert parsed.publication_time is not None
            assert parsed.publication_time.value == _START + 60_000
            fourth = await asyncio.wait_for(anext(stream), 2)
            assert json.loads(fourth.payload)["close"] == "102"
        finally:
            await stream.aclose()
            await source.close()

    asyncio.run(with_server(handler, run))


def test_malformed_wrong_topic_and_reconnect_do_not_deliver_stale_data() -> None:
    count = 0

    async def handler(socket: web.WebSocketResponse) -> None:
        nonlocal count
        count += 1
        await subscribed(socket)
        if count == 1:
            bad = frame()
            bad["topic"] = "kline.1.ETHUSDT"
            await socket.send_json(bad)
        elif count == 2:
            await socket.send_str('{"topic": "x", "topic": "y"}')
        else:
            await socket.send_json(frame(confirm=True))
        await asyncio.sleep(0.1)

    async def run(client: LocalClient) -> None:
        source = BybitLiveSource(
            clock=FixedClock(),
            client=client,
            live_limits=LiveLimits(reconnect_initial_s=0.01, reconnect_max_s=0.02),
        )
        stream = source.stream(_REQUEST)
        try:
            result = await asyncio.wait_for(anext(stream), 3)
            assert json.loads(result.payload)["symbol"] == "BTCUSDT"
            assert client.connections == 3
        finally:
            await stream.aclose()
            await source.close()

    asyncio.run(with_server(handler, run))


@pytest.mark.parametrize("responsive", [False, True])
def test_stall_heartbeat_and_close_interrupt_reconnect(responsive: bool) -> None:
    async def handler(socket: web.WebSocketResponse) -> None:
        await subscribed(socket)
        async for message in socket:
            if responsive and message.type is aiohttp.WSMsgType.TEXT:
                if json.loads(message.data).get("op") == "ping":
                    await socket.send_json({"op": "pong", "args": [str(_START)]})

    async def run(client: LocalClient) -> None:
        source = BybitLiveSource(
            clock=FixedClock(),
            client=client,
            live_limits=LiveLimits(
                ping_interval_s=0.03,
                pong_timeout_s=0.03,
                data_stall_s=0.2,
                reconnect_initial_s=0.01,
                reconnect_max_s=0.02,
            ),
        )
        stream = source.stream(_REQUEST)
        pending = asyncio.create_task(anext(stream))
        try:
            async with asyncio.timeout(2):
                while client.connections < 2:
                    await asyncio.sleep(0.01)
            assert source.last_error == (
                "Bybit candle stream stalled"
                if responsive
                else "Bybit heartbeat timed out"
            )
            await source.close()
            try:
                await pending
            except StopAsyncIteration:
                pass
            else:
                raise AssertionError("Closed source delivered a candle")
        finally:
            pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)
            await stream.aclose()
            await source.close()

    asyncio.run(with_server(handler, run))


def test_publication_time_rejects_invalid_unit() -> None:
    async def handler(socket: web.WebSocketResponse) -> None:
        await subscribed(socket)
        await socket.send_json(frame(confirm=True))
        await asyncio.sleep(0.1)

    async def run(client: LocalClient) -> None:
        source = BybitLiveSource(clock=FixedClock(), client=client)
        stream = source.stream(_REQUEST)
        try:
            envelope = await asyncio.wait_for(anext(stream), 2)
            payload: dict[str, Any] = json.loads(envelope.payload)
            payload["publication_time"]["unit"] = "s"
            modified = replace(
                envelope, payload=json.dumps(payload).encode(), payload_limit=8192
            )
            parsed = inspect_bybit_candle(
                RawRecord(IngestionId("test", "epoch", 2), modified)
            )
            assert not isinstance(parsed, ParsedFakeCandle)
        finally:
            await stream.aclose()
            await source.close()

    asyncio.run(with_server(handler, run))


@pytest.mark.parametrize("compressed", [False, True])
def test_oversized_or_unnegotiated_compressed_frame_reconnects(
    compressed: bool,
) -> None:
    count = 0

    async def handler(socket: web.WebSocketResponse) -> None:
        nonlocal count
        count += 1
        await subscribed(socket)
        if count == 1:
            await socket.send_str(
                "x" * (200_000 if compressed else 66_000),
                compress=15 if compressed else None,
            )
        else:
            await socket.send_json(frame(confirm=True))
        await asyncio.sleep(0.1)

    async def run(client: LocalClient) -> None:
        source = BybitLiveSource(
            clock=FixedClock(),
            client=client,
            live_limits=LiveLimits(reconnect_initial_s=0.01, reconnect_max_s=0.02),
        )
        stream = source.stream(_REQUEST)
        try:
            await asyncio.wait_for(anext(stream), 2)
            assert client.connections == 2
        finally:
            await stream.aclose()
            await source.close()

    asyncio.run(with_server(handler, run))


def test_slow_consumer_overflow_is_bounded_and_closes_socket(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entered = asyncio.Event()

    async def handler(socket: web.WebSocketResponse) -> None:
        await subscribed(socket)
        for i in range(20):
            message = frame()
            row = candle()
            row["start"] = _START + i * 60_000
            row["end"] = _START + (i + 1) * 60_000 - 1
            message["data"] = [row]
            await socket.send_json(message)
        entered.set()
        await asyncio.sleep(0.2)

    async def run(client: LocalClient) -> None:
        clock = FixedClock()
        source = BybitLiveSource(clock=clock, client=client)
        stream = source.stream(_REQUEST)
        try:
            await asyncio.wait_for(anext(stream), 2)
            await asyncio.wait_for(entered.wait(), 2)
            async with asyncio.timeout(1):
                while source.last_error != "Bybit bounded intake overflow":
                    await asyncio.sleep(0.005)
            assert clock.calls <= 5  # One consumed, three queued, one overflow.
            await source.close()
        finally:
            await stream.aclose()
            await source.close()

    monkeypatch.setattr(live_module, "_INTAKE_RECORD_LIMIT", 3)
    asyncio.run(with_server(handler, run))


def test_close_interrupts_pending_subscription_ack() -> None:
    entered = asyncio.Event()

    async def handler(socket: web.WebSocketResponse) -> None:
        request = await socket.receive_json(timeout=1)
        assert request["op"] == "subscribe"
        entered.set()
        await asyncio.sleep(0.3)  # No acknowledgement.

    async def run(client: LocalClient) -> None:
        source = BybitLiveSource(clock=FixedClock(), client=client)
        stream = source.stream(_REQUEST)
        pending = asyncio.create_task(anext(stream))
        try:
            await asyncio.wait_for(entered.wait(), 2)
            await source.close()
            try:
                await asyncio.wait_for(pending, 2)
            except StopAsyncIteration:
                pass
            else:
                raise AssertionError("Closed source delivered a candle")
        finally:
            pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)
            await stream.aclose()
            await source.close()

    asyncio.run(with_server(handler, run))


def test_close_cancels_and_joins_pending_connection() -> None:
    async def run() -> None:
        started = asyncio.Event()
        cancelled = asyncio.Event()

        class DelayedClient:
            async def connect(self, category: str) -> aiohttp.ClientWebSocketResponse:
                started.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    cancelled.set()
                raise AssertionError("Connection should be cancelled")

            async def close(self) -> None:
                pass

        source = BybitLiveSource(clock=FixedClock(), client=DelayedClient())
        stream = source.stream(_REQUEST)
        pending = asyncio.create_task(anext(stream))
        try:
            await asyncio.wait_for(started.wait(), 1)
            await source.close()
            with pytest.raises(StopAsyncIteration):
                await asyncio.wait_for(pending, 0.2)
            assert cancelled.is_set()
        finally:
            pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)
            await stream.aclose()
            await source.close()

    asyncio.run(run())


@pytest.mark.parametrize(
    "pong",
    [
        {"op": "ping", "success": True, "ret_msg": "pong"},
        {"op": "pong", "args": [str(_START)]},
    ],
)
def test_public_pong_formats_keep_connection_alive(pong: dict[str, object]) -> None:
    async def handler(socket: web.WebSocketResponse) -> None:
        await subscribed(socket)
        for _ in range(3):
            request = await socket.receive_json(timeout=1)
            assert request["op"] == "ping"
            await socket.send_json(pong)
        await socket.send_json(frame(confirm=True))
        await asyncio.sleep(0.1)

    async def run(client: LocalClient) -> None:
        source = BybitLiveSource(
            clock=FixedClock(),
            client=client,
            live_limits=LiveLimits(ping_interval_s=0.02, pong_timeout_s=0.04),
        )
        stream = source.stream(_REQUEST)
        try:
            await asyncio.wait_for(anext(stream), 1)
            assert client.connections == 1
        finally:
            await stream.aclose()
            await source.close()

    asyncio.run(with_server(handler, run))


def test_repeated_pings_do_not_extend_missing_pong_deadline() -> None:
    async def handler(socket: web.WebSocketResponse) -> None:
        await subscribed(socket)
        async for _message in socket:
            pass

    async def run(client: LocalClient) -> None:
        source = BybitLiveSource(
            clock=FixedClock(),
            client=client,
            live_limits=LiveLimits(
                ping_interval_s=0.01,
                pong_timeout_s=0.05,
                data_stall_s=0.5,
                reconnect_initial_s=0.01,
                reconnect_max_s=0.02,
            ),
        )
        stream = source.stream(_REQUEST)
        pending = asyncio.create_task(anext(stream))
        try:
            async with asyncio.timeout(0.2):
                while client.connections < 2:
                    await asyncio.sleep(0.005)
            assert source.last_error == "Bybit heartbeat timed out"
        finally:
            pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)
            await stream.aclose()
            await source.close()

    asyncio.run(with_server(handler, run))


def test_live_candles_reach_real_journal_archive_and_dataset(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    async def handler(socket: web.WebSocketResponse) -> None:
        await subscribed(socket)
        await socket.send_json(frame())
        await socket.send_json(frame(confirm=True))
        await asyncio.sleep(0.1)

    instrument = Instrument(
        identity=_SUBJECT,
        base_asset="BTC",
        quote_asset="USDT",
        price_increment=Decimal("0.01"),
        quantity_increment=Decimal("0.000001"),
        volume_unit="BTC",
        revision="bybit-live-fixture",
    )

    async def capture(
        ingestor: DurableIngestor, clock: ClockMonitor
    ) -> tuple[int, bool, Instrument]:
        async def run(client: LocalClient) -> None:
            source = BybitLiveSource(clock=FixedClock(), client=client)
            stream = source.stream(_REQUEST)
            try:
                for _ in range(2):
                    ingestor.accept(await asyncio.wait_for(anext(stream), 2))
            finally:
                await stream.aclose()
                await source.close()

        await with_server(handler, run)
        return 2, True, instrument

    monkeypatch.setattr(live_check, "_capture", capture)
    monkeypatch.setattr(live_check, "ClockMonitor", lambda *_: FixedClock())
    live_check.main()
    result = json.loads(capsys.readouterr().out)
    assert result["accepted"] == result["normalized"] == 2
    assert result["published"] >= 1
    assert result["dataset_rows"] == result["dataset_finalized_rows"] == 1


def test_write_deadline_cancels_stalled_send(monkeypatch: pytest.MonkeyPatch) -> None:
    async def run() -> None:
        cleaned = asyncio.Event()

        class Socket:
            async def send_json(self, data: object) -> None:
                try:
                    await asyncio.Event().wait()
                finally:
                    cleaned.set()

            async def receive(self, timeout: float | None = None) -> aiohttp.WSMessage:
                raise AssertionError("No receive expected")

            async def close(self) -> None:
                pass

        with pytest.raises(BybitLiveError, match="write timed out"):
            await BybitLiveSource._send(Socket(), {"op": "ping"})
        assert cleaned.is_set()

    monkeypatch.setattr(live_module, "_WRITE_TIMEOUT_S", 0.02)
    asyncio.run(run())


def test_public_transport_refuses_redirect_before_contacting_target(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def run() -> None:
        contacted = False

        async def redirect(request: web.Request) -> web.Response:
            raise web.HTTPFound("/target")

        async def target(request: web.Request) -> web.Response:
            nonlocal contacted
            contacted = True
            return web.Response(text="must not be fetched")

        app = web.Application()
        app.router.add_get("/redirect", redirect)
        app.router.add_get("/target", target)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        assert site._server is not None
        port = cast(asyncio.Server, site._server).sockets[0].getsockname()[1]
        monkeypatch.setitem(
            live_module._URLS, "spot", f"http://127.0.0.1:{port}/redirect"
        )
        client = BybitPublicWebSocketClient()
        try:
            with pytest.raises(BybitLiveError, match="redirect refused"):
                await client.connect("spot")
            assert not contacted
        finally:
            await client.close()
            await runner.cleanup()

    asyncio.run(run())


def test_overflow_record_is_not_suppressed_after_reconnect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    consumed = asyncio.Event()
    connections = 0

    def at(index: int) -> dict[str, object]:
        message = frame()
        row = candle()
        row["start"] = _START + index * 60_000
        row["end"] = _START + (index + 1) * 60_000 - 1
        message["data"] = [row]
        return message

    async def handler(socket: web.WebSocketResponse) -> None:
        nonlocal connections
        connections += 1
        await subscribed(socket)
        if connections == 1:
            await socket.send_json(at(0))
            await consumed.wait()
            await socket.send_json(at(1))
            await socket.send_json(at(2))
        else:
            await socket.send_json(at(1))
            await socket.send_json(at(2))
        async for _message in socket:
            pass

    async def run(client: LocalClient) -> None:
        source = BybitLiveSource(
            clock=FixedClock(),
            client=client,
            live_limits=LiveLimits(reconnect_initial_s=0.01, reconnect_max_s=0.02),
        )
        stream = source.stream(_REQUEST)
        try:
            first = await asyncio.wait_for(anext(stream), 1)
            consumed.set()
            async with asyncio.timeout(1):
                while source.last_error != "Bybit bounded intake overflow":
                    await asyncio.sleep(0.005)
            second = await asyncio.wait_for(anext(stream), 1)
            third = await asyncio.wait_for(anext(stream), 1)
            assert [
                json.loads(item.payload)["start_ns"] for item in (first, second, third)
            ] == [(_START + i * 60_000) * 1_000_000 for i in range(3)]
            assert client.connections == 2
        finally:
            await stream.aclose()
            await source.close()

    monkeypatch.setattr(live_module, "_INTAKE_RECORD_LIMIT", 1)
    asyncio.run(with_server(handler, run))


@pytest.mark.parametrize("mode", ["cancel", "aclose"])
def test_consumer_exit_joins_reader_and_closes_socket(mode: str) -> None:
    async def handler(socket: web.WebSocketResponse) -> None:
        await subscribed(socket)
        await socket.send_json(frame())
        async for _message in socket:
            pass

    async def run(client: LocalClient) -> None:
        source = BybitLiveSource(clock=FixedClock(), client=client)
        stream = source.stream(_REQUEST)
        try:
            await asyncio.wait_for(anext(stream), 1)
            if mode == "cancel":
                pending = asyncio.create_task(anext(stream))
                await asyncio.sleep(0)
                pending.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await pending
            else:
                await stream.aclose()
            assert source._reader_task is None
            assert source._socket is None
        finally:
            await stream.aclose()
            await source.close()

    asyncio.run(with_server(handler, run))
