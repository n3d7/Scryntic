"""F20 loopback HTTP test boundary, not a configurable cloud integration."""

import asyncio
import re
import time
from collections import OrderedDict
from collections.abc import Callable
from math import isfinite
from types import TracebackType
from typing import Literal

import aiohttp
from aiohttp import web

from scryntic.application.providers import ForecastResult
from scryntic.jobs.admission import ModelReview
from scryntic.jobs.codec import decode_attempt, encode_attempt, encode_response
from scryntic.jobs.contracts import MAX_JOB_BYTES, MAX_RESPONSE_BYTES, JobAttempt
from scryntic.jobs.fake import calculate, require_fixture

_PATH = "/v1/forecast"
_HOST = "127.0.0.1"
_JSON = "application/json"


async def _read_bounded(stream: aiohttp.StreamReader, maximum: int) -> bytes:
    body = bytearray()
    async for chunk in stream.iter_chunked(4096):
        if len(body) + len(chunk) > maximum:
            raise ValueError("HTTP body limit")
        body.extend(chunk)
    if not body:
        raise ValueError("Empty HTTP body")
    return bytes(body)


class HttpFakeProvider:
    def __init__(
        self,
        review: ModelReview,
        port: int,
        *,
        timeout_seconds: float = 2.0,
        algorithm: Literal["persistence", "trend"] = "persistence",
    ) -> None:
        if type(port) is not int or not 1 <= port <= 65535:
            raise ValueError("Invalid loopback fixture port")
        if (
            type(timeout_seconds) is not float
            or not isfinite(timeout_seconds)
            or not 0.05 <= timeout_seconds <= 5.0
        ):
            raise ValueError("HTTP timeout outside bounds")
        if review.descriptor.execution != "remote":
            raise ValueError("HTTP fixture requires remote execution")
        require_fixture(review, algorithm)
        self.review = review
        self._port = port
        self._url = f"http://{_HOST}:{port}{_PATH}"
        self._timeout = timeout_seconds

    def _response(self, response: aiohttp.ClientResponse) -> None:
        if (
            response.status != 200
            or response.url.scheme != "http"
            or response.url.host != _HOST
            or response.url.port != self._port
            or response.url.path != _PATH
            or response.content_type != _JSON
            or response.headers.get("Content-Encoding", "identity") != "identity"
        ):
            raise ValueError("HTTP fixture response rejected")
        length = response.headers.get("Content-Length")
        if length is not None and (
            re.fullmatch(r"\d{1,10}", length, flags=re.ASCII) is None
            or not 0 < int(length) <= MAX_RESPONSE_BYTES
        ):
            raise ValueError("HTTP response length exceeds bound")

    async def execute(self, attempt: JobAttempt) -> bytes:
        job = attempt.job
        job.__post_init__()
        if job.review != self.review:
            raise ValueError("HTTP provider review mismatch")
        timeout = aiohttp.ClientTimeout(
            total=self._timeout, connect=self._timeout, sock_read=self._timeout
        )
        connector = aiohttp.TCPConnector(limit=2, force_close=True)
        async with aiohttp.ClientSession(
            connector=connector,
            timeout=timeout,
            trust_env=False,
            auto_decompress=False,
            cookie_jar=aiohttp.DummyCookieJar(),
            read_bufsize=16_384,
        ) as session:
            async with session.post(
                self._url,
                data=encode_attempt(attempt),
                allow_redirects=False,
                headers={
                    "Content-Type": _JSON,
                    "Accept": _JSON,
                    "Accept-Encoding": "identity",
                },
            ) as response:
                self._response(response)
                return await _read_bounded(response.content, MAX_RESPONSE_BYTES)


class HttpTestDouble:
    """Only bounded approved primitive fixtures; binds solely to numeric loopback."""

    def __init__(
        self,
        review: ModelReview,
        *,
        clock: Callable[[], int] = time.time_ns,
        algorithm: Literal["persistence", "trend"] = "persistence",
    ) -> None:
        if review.descriptor.execution != "remote":
            raise ValueError("HTTP test double requires a remote descriptor")
        require_fixture(review, algorithm)
        self._review = review
        self._clock = clock
        self._algorithm = algorithm
        self._cache: OrderedDict[str, tuple[str, ForecastResult]] = OrderedDict()
        self._inflight = 0
        self.executions = 0
        self.port = 0
        self._runner: web.AppRunner | None = None

    def _result(self, attempt: JobAttempt) -> ForecastResult:
        job = attempt.job
        now = self._clock()
        if type(now) is not int or not job.submitted_ns <= now < job.deadline_ns:
            raise ValueError("HTTP test job deadline rejected")
        if job.review != self._review:
            raise ValueError("HTTP test provider mismatch")
        prior = self._cache.get(job.job_id)
        if prior is not None:
            if prior[0] != job.sha256:
                raise ValueError("HTTP idempotency key conflict")
            return prior[1]
        if len(self._cache) >= 64:
            raise ValueError("HTTP test-double job quota")
        result = calculate(attempt, self._review, self._algorithm)
        self._cache[job.job_id] = (job.sha256, result)
        self.executions += 1
        return result

    async def _handle(self, request: web.Request) -> web.Response:
        if self._inflight >= 2:
            return web.Response(status=429, text="Fixture request quota")
        self._inflight += 1
        try:
            if (
                request.content_type != _JSON
                or request.headers.get("Content-Encoding", "identity") != "identity"
            ):
                raise ValueError("HTTP request encoding rejected")
            async with asyncio.timeout(2.0):
                data = await _read_bounded(request.content, MAX_JOB_BYTES)
            attempt = decode_attempt(data)
            result = self._result(attempt)
            return web.Response(
                body=encode_response(attempt, result), content_type=_JSON
            )
        except (ValueError, TypeError, TimeoutError):
            return web.Response(status=400, text="Invalid bounded fixture request")
        finally:
            self._inflight -= 1

    async def __aenter__(self) -> "HttpTestDouble":
        if self._runner is not None:
            raise ValueError("HTTP test double already running")
        application = web.Application(client_max_size=MAX_JOB_BYTES)
        application.router.add_post(_PATH, self._handle)
        runner = web.AppRunner(
            application,
            keepalive_timeout=2.0,
            handler_cancellation=True,
            auto_decompress=False,
        )
        self._runner = runner
        try:
            await runner.setup()
            await web.TCPSite(runner, _HOST, 0, backlog=8).start()
            self.port = runner.addresses[0][1]
            return self
        except BaseException:
            await runner.cleanup()
            self._runner = None
            raise

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if self._runner is not None:
            await self._runner.cleanup()
            self._runner = None
