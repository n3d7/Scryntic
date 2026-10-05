"""Inbound fixture parser must preserve wire-byte bounds before handler checks."""

import asyncio
import gzip

import aiohttp
from aiohttp import web

from scryntic.jobs.http import HttpTestDouble
from tests.jobs.helpers import NOW, review


def test_compressed_request_is_not_inflated_before_fixture_rejection() -> None:
    captured: list[bytes] = []
    compressed = gzip.compress(b"safe-small-compression-sentinel" * 20)

    class RecordingDouble(HttpTestDouble):
        async def _handle(self, request: web.Request) -> web.Response:
            captured.append(await request.read())
            return await super()._handle(request)

    async def exercise() -> None:
        async with RecordingDouble(review("remote"), clock=lambda: NOW) as server:
            async with aiohttp.ClientSession(
                trust_env=False, auto_decompress=False
            ) as client:
                async with client.post(
                    f"http://127.0.0.1:{server.port}/v1/forecast",
                    data=compressed,
                    headers={
                        "Content-Type": "application/json",
                        "Content-Encoding": "gzip",
                    },
                    allow_redirects=False,
                ) as response:
                    assert response.status == 400
                    await response.read()
        assert captured == [compressed]

    asyncio.run(exercise())
