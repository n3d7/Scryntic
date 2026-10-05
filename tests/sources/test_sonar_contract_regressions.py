"""Preserve parser alphabets and transport authentication during Sonar refactors."""

import asyncio
import ssl
from collections.abc import Callable
from types import SimpleNamespace
from unittest.mock import AsyncMock

import aiohttp
import pytest

import scryntic.sources.bybit as historical
import scryntic.sources.bybit_live as live
from scryntic.clock.chrony import parse_tracking
from tests.clock.test_chrony import TRACKING
from tests.sources.test_bybit_live import _REQUEST, FixedClock


@pytest.mark.parametrize("value", ["1٢", "１２", "١.٢", "1.２"])
@pytest.mark.parametrize("parse", [historical._decimal, live._decimal])
def test_source_decimals_reject_unicode_digits(
    value: str, parse: Callable[[object], object]
) -> None:
    with pytest.raises(ValueError):
        parse(value)


def test_chrony_stratum_remains_ascii_only() -> None:
    report = TRACKING.replace(",4,", ",٤,")
    with pytest.raises(ValueError):
        parse_tracking(report)


def test_public_websocket_uses_certificate_and_hostname_verification(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    contexts: list[ssl.SSLContext] = []
    original_connector = aiohttp.TCPConnector

    def connector(
        *, ssl: ssl.SSLContext, limit: int, limit_per_host: int
    ) -> aiohttp.TCPConnector:
        contexts.append(ssl)
        return original_connector(ssl=ssl, limit=limit, limit_per_host=limit_per_host)

    monkeypatch.setattr(aiohttp, "TCPConnector", connector)
    monkeypatch.setattr(
        aiohttp.ClientSession, "ws_connect", AsyncMock(side_effect=OSError)
    )
    monkeypatch.setattr(live._CONNECTION_GATE, "delay", lambda: 0.0)

    async def exercise() -> None:
        client = live.BybitPublicWebSocketClient()
        try:
            with pytest.raises(live.BybitLiveError, match="connect failed"):
                await client.connect("spot")
        finally:
            await client.close()

    asyncio.run(exercise())
    assert len(contexts) == 1
    assert contexts[0].verify_mode == ssl.CERT_REQUIRED
    assert contexts[0].check_hostname


def test_explicit_owner_cancellation_propagates_during_source_close() -> None:
    async def exercise() -> None:
        started = asyncio.Event()

        async def connect(_category: str) -> aiohttp.ClientWebSocketResponse:
            started.set()
            await asyncio.Event().wait()
            raise AssertionError("Connection unexpectedly resumed")

        client = SimpleNamespace(connect=connect, close=AsyncMock())
        source = live.BybitLiveSource(clock=FixedClock(), client=client)
        stream = source.stream(_REQUEST)
        owner = asyncio.create_task(anext(stream))
        try:
            async with asyncio.timeout(1):
                await started.wait()
                owner.cancel()
                await source.close()
                with pytest.raises(asyncio.CancelledError):
                    await owner
        finally:
            owner.cancel()
            await asyncio.gather(owner, return_exceptions=True)
            await stream.aclose()
            await source.close()

    asyncio.run(exercise())
