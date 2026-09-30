"""Public WebSocket buffering participates in the generic budget and loss contract."""

import asyncio

import pytest
from aiohttp import web

from scryntic.application.sources import SourceLoss
from scryntic.ingestion.sqlite_spool import IngestionError
from scryntic.recovery.budget import SharedBudget
from scryntic.sources.bybit_live import BybitLiveSource, LiveLimits
from tests.recovery.test_supervisor import run_async
from tests.sources.test_bybit_live import (
    _REQUEST,
    FixedClock,
    LocalClient,
    frame,
    with_server,
)


@run_async
async def test_shared_websocket_overflow_reports_before_retry_and_releases_leases() -> (
    None
):
    async def handler(socket: web.WebSocketResponse) -> None:
        await socket.receive_json()
        await socket.send_json({"op": "subscribe", "success": True})
        for index in range(12):
            await socket.send_json(frame(close=f"101.{index:02d}"))
        await socket.receive()

    async def check(client: LocalClient) -> None:
        budget = SharedBudget(
            records=4,
            bytes_=32768,
            live_records=1,
            live_bytes=8192,
            request_interval_s=0,
        )
        source = BybitLiveSource(
            clock=FixedClock(),
            client=client,
            live_limits=LiveLimits(reconnect_initial_s=0.01, reconnect_max_s=0.02),
        )
        losses: list[SourceLoss] = []

        async def loss(notice: SourceLoss) -> None:
            losses.append(notice)
            raise IngestionError("injected loss marker failure")

        source.set_loss_handler(loss)
        source.set_start_gate(budget.admit_request)
        source.set_buffer_admission(lambda size: budget.try_acquire("live", 1, size))
        stream = source.stream(_REQUEST)
        try:
            await anext(stream)
            await asyncio.sleep(0.03)
            assert budget.used[0] == 4
            with pytest.raises(IngestionError, match="marker"):
                async with asyncio.timeout(1):
                    while True:
                        await anext(stream)
            assert losses[0].reason == "overflow"
            assert client.connections == 1
            assert budget.used == (0, 0)
        finally:
            await stream.aclose()
            await source.close()

    await with_server(handler, check)


@run_async
async def test_rejected_subscription_requires_configuration_correction() -> None:
    async def handler(socket: web.WebSocketResponse) -> None:
        await socket.receive_json()
        await socket.send_json({"op": "subscribe", "success": False})
        await socket.receive()

    async def check(client: LocalClient) -> None:
        source = BybitLiveSource(
            clock=FixedClock(),
            client=client,
            live_limits=LiveLimits(reconnect_initial_s=0.01, reconnect_max_s=0.02),
        )
        stream = source.stream(_REQUEST)
        try:
            with pytest.raises(ValueError, match="subscription rejected"):
                async with asyncio.timeout(0.2):
                    await anext(stream)
            assert client.connections == 1
        finally:
            await stream.aclose()
            await source.close()

    await with_server(handler, check)
