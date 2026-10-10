"""Bounded public Bybit V5 kline stream using the historical candle contract."""

from __future__ import annotations

import asyncio
import json
import re
import ssl
import threading
import time
from collections import OrderedDict
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import aclosing
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Literal, Protocol, cast

import aiohttp

from scryntic.application.sources import (
    BYBIT_CANDLE_SCHEMA,
    SourceBufferLease,
    SourceCapability,
    SourceConfigurationError,
    SourceDescriptor,
    SourceLoss,
    SourceOperation,
    StreamRequest,
)
from scryntic.clock.policy import is_final
from scryntic.configuration.clock import ClockLimits
from scryntic.domain.identity import InstrumentId
from scryntic.domain.raw import RawEnvelope
from scryntic.domain.time import ClockSample, SourceTime, TimeUnit
from scryntic.sources.bybit import _INTERVAL_NS

_SUBSCRIPTION_REJECTED = "Bybit subscription rejected"
_INVALID_CANDLE_DECIMAL = "Invalid Bybit candle decimal"

_URLS = {
    "spot": "wss://stream.bybit.com/v5/public/spot",
    "linear": "wss://stream.bybit.com/v5/public/linear",
    "inverse": "wss://stream.bybit.com/v5/public/inverse",
}
_FRAME_LIMIT = 65_536
_EVENT_LIMIT = 8_192
_INTAKE_RECORD_LIMIT = 32
_WRITE_TIMEOUT_S = 3.0
_DECIMAL = re.compile(r"(?:0|[1-9]\d*)(?:\.\d+)?", re.ASCII)


def _is_pong(document: dict[str, object]) -> bool:
    args = document.get("args")
    return (
        document.get("op") == "ping"
        and document.get("success") is True
        and document.get("ret_msg") == "pong"
    ) or (
        document.get("op") == "pong"
        and isinstance(args, list)
        and len(args) == 1
        and type(args[0]) is str
        and 1 <= len(args[0]) <= 20
        and args[0].isascii()
        and args[0].isdigit()
    )


class _ConnectionGate:
    """Process-wide public connection starts below Bybit's 500/5min limit."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._next = 0.0

    def delay(self) -> float:
        with self._lock:
            now = time.monotonic()
            start = max(now, self._next)
            self._next = start + 1.0
            return start - now


_CONNECTION_GATE = _ConnectionGate()


class BybitLiveError(ValueError):
    """Safe, bounded public transport or wire error (never contains a frame)."""


class _Clock(Protocol):
    def sample(self) -> ClockSample: ...


class BybitLiveConfigurationError(BybitLiveError, SourceConfigurationError):
    """Rejected public subscription requires configuration correction."""


class _Socket(Protocol):
    async def send_json(self, data: object) -> None: ...
    async def receive(self, timeout: float | None = None) -> aiohttp.WSMessage: ...
    async def close(self) -> object: ...


class _Client(Protocol):
    async def connect(self, category: str) -> _Socket: ...
    async def close(self) -> None: ...


@dataclass(frozen=True, slots=True)
class LiveLimits:
    subscribe_timeout_s: float = 8.0
    ping_interval_s: float = 20.0
    pong_timeout_s: float = 10.0
    data_stall_s: float = 90.0
    reconnect_initial_s: float = 1.0
    reconnect_max_s: float = 30.0

    def __post_init__(self) -> None:
        values = (
            self.subscribe_timeout_s,
            self.ping_interval_s,
            self.pong_timeout_s,
            self.data_stall_s,
            self.reconnect_initial_s,
            self.reconnect_max_s,
        )
        if any(not 0 < value < 3600 for value in values):
            raise ValueError("Expected bounded positive live intervals")
        if self.reconnect_initial_s > self.reconnect_max_s:
            raise ValueError("Invalid reconnect interval")


class BybitPublicWebSocketClient:
    """Only fixed Bybit public TLS endpoints; no account or credential inputs."""

    def __init__(self) -> None:
        self._session: aiohttp.ClientSession | None = None
        self._closed = False

    @staticmethod
    async def _refuse_redirect(
        _session: aiohttp.ClientSession,
        _context: object,
        _params: aiohttp.TraceRequestRedirectParams,
    ) -> None:
        raise BybitLiveError("Bybit public WebSocket redirect refused")

    async def connect(self, category: str) -> aiohttp.ClientWebSocketResponse:
        if category not in _URLS:
            raise BybitLiveError("Unsupported Bybit category")
        if self._closed:
            raise BybitLiveError("Bybit public WebSocket client is closed")
        await asyncio.sleep(_CONNECTION_GATE.delay())
        if self._closed:
            raise BybitLiveError("Bybit public WebSocket client is closed")
        if self._session is None:
            trace = aiohttp.TraceConfig()
            trace.on_request_redirect.append(self._refuse_redirect)
            self._session = aiohttp.ClientSession(
                connector=aiohttp.TCPConnector(
                    ssl=ssl.create_default_context(), limit=1, limit_per_host=1
                ),
                trust_env=False,
                headers={"User-Agent": "Scryntic/1"},
                trace_configs=[trace],
            )
        try:
            async with asyncio.timeout(8.0):
                socket = await self._session.ws_connect(
                    _URLS[category],
                    autoping=True,
                    compress=0,
                    max_msg_size=_FRAME_LIMIT,
                    timeout=aiohttp.ClientWSTimeout(ws_close=3.0),
                )
        except (aiohttp.ClientError, OSError):
            raise BybitLiveError("Bybit public WebSocket connect failed") from None
        if str(socket._response.url) != _URLS[category]:
            await socket.close()
            raise BybitLiveError("Bybit public WebSocket endpoint changed")
        return socket

    async def close(self) -> None:
        self._closed = True
        if self._session is not None:
            await self._session.close()
            self._session = None


def _pairs(items: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in items:
        if key in result:
            raise BybitLiveError("Duplicate Bybit JSON key")
        result[key] = value
    return result


def _reject_json_constant(_value: str) -> object:
    raise ValueError()


def _document(message: aiohttp.WSMessage) -> dict[str, object]:
    if message.type not in (aiohttp.WSMsgType.TEXT, aiohttp.WSMsgType.BINARY):
        raise BybitLiveError("Bybit WebSocket closed or invalid frame")
    raw = message.data
    if type(raw) not in (str, bytes) or len(raw) > _FRAME_LIMIT:
        raise BybitLiveError("Bybit WebSocket frame exceeds limit")
    if isinstance(raw, str) and len(raw.encode("utf-8")) > _FRAME_LIMIT:
        raise BybitLiveError("Bybit WebSocket frame exceeds limit")
    try:
        value = json.loads(
            raw,
            object_pairs_hook=_pairs,
            parse_constant=_reject_json_constant,
        )
    except (ValueError, RecursionError):
        raise BybitLiveError("Invalid Bybit WebSocket JSON") from None
    if not isinstance(value, dict):
        raise BybitLiveError("Invalid Bybit WebSocket document")
    return cast(dict[str, object], value)


def _decimal(value: object) -> str:
    if (
        type(value) is not str
        or not 1 <= len(value) <= 128
        or not _DECIMAL.fullmatch(value)
    ):
        raise BybitLiveError(_INVALID_CANDLE_DECIMAL)
    try:
        if not Decimal(value).is_finite():
            raise BybitLiveError(_INVALID_CANDLE_DECIMAL)
    except InvalidOperation:
        raise BybitLiveError(_INVALID_CANDLE_DECIMAL) from None
    return value


def _millis(value: object) -> int:
    if type(value) is not int or not 0 <= value < (2**63 - 1) // 1_000_000:
        raise BybitLiveError("Invalid Bybit candle time")
    return value


def _loss_reason(
    error: Exception, detail: str
) -> Literal["overflow", "stall", "malformed", "disconnect"]:
    if "overflow" in detail:
        return "overflow"
    if "timed out" in detail or "stalled" in detail:
        return "stall"
    if "closed" in detail:
        return "disconnect"
    return "malformed" if isinstance(error, BybitLiveError) else "disconnect"


class BybitLiveSource:
    """One fixed topic per stream, with one reader and bounded replay deduplication."""

    def __init__(
        self,
        *,
        clock: _Clock,
        interval: str = "1",
        client: _Client | None = None,
        clock_limits: ClockLimits | None = None,
        live_limits: LiveLimits | None = None,
    ) -> None:
        if interval not in _INTERVAL_NS:
            raise ValueError("Unsupported fixed-duration Bybit interval")
        self._clock = clock
        self._interval = interval
        self._interval_ns = _INTERVAL_NS[interval]
        self._client = client or BybitPublicWebSocketClient()
        self._clock_limits = clock_limits or ClockLimits()
        self._limits = live_limits or LiveLimits()
        self._closed = asyncio.Event()
        self._socket: _Socket | None = None
        self._connecting: asyncio.Task[_Socket] | None = None
        self._reader_task: asyncio.Task[None] | None = None
        self._running = False
        self.last_error: str | None = None
        self.connected = False
        self.last_heartbeat_monotonic_ns = 0
        self._loss_handler: Callable[[SourceLoss], Awaitable[None]] | None = None
        self._start_gate: Callable[[], bool] | None = None
        self._buffer_admission: Callable[[int], SourceBufferLease | None] | None = None
        self.descriptor = SourceDescriptor(
            source_id="bybit-public",
            adapter_version="1.0",
            capabilities=tuple(
                SourceCapability(
                    schema=BYBIT_CANDLE_SCHEMA,
                    subject_kind="instrument",
                    category=category,
                    operations=frozenset({SourceOperation.STREAM}),
                    sequencing="none",
                    recovery="backfill",
                )
                for category in _URLS
            ),
            max_payload_bytes=_EVENT_LIMIT,
            max_page_records=1,
        )

    def set_loss_handler(
        self, handler: Callable[[SourceLoss], Awaitable[None]]
    ) -> None:
        if self._running:
            raise ValueError("Cannot replace active loss handler")
        self._loss_handler = handler

    def set_start_gate(self, gate: Callable[[], bool]) -> None:
        if self._running:
            raise ValueError("Cannot replace active start gate")
        self._start_gate = gate

    async def close(self) -> None:
        self._closed.set()
        if self._connecting is not None:
            self._connecting.cancel()
            await asyncio.gather(self._connecting, return_exceptions=True)
        if self._reader_task is not None:
            self._reader_task.cancel()
            await asyncio.gather(self._reader_task, return_exceptions=True)
        if self._socket is not None:
            await self._socket.close()
        await self._client.close()

    def set_buffer_admission(
        self, acquire: Callable[[int], SourceBufferLease | None]
    ) -> None:
        if self._running:
            raise ValueError("Cannot replace active buffer admission")
        self._buffer_admission = acquire

    async def _connect(self, category: str) -> _Socket:
        task = asyncio.create_task(self._client.connect(category))
        self._connecting = task
        try:
            return await task
        except asyncio.CancelledError:
            task.cancel()
            results = await asyncio.gather(task, return_exceptions=True)
            # Cancellation can race with a successful dial. Close any result
            # whose ownership never reached the stream's socket finally block.
            result = results[0]
            if not isinstance(result, BaseException):
                await result.close()
            raise
        finally:
            self._connecting = None

    @staticmethod
    async def _send(socket: _Socket, data: object) -> None:
        try:
            async with asyncio.timeout(_WRITE_TIMEOUT_S):
                await socket.send_json(data)
        except TimeoutError:
            raise BybitLiveError("Bybit WebSocket write timed out") from None

    def _envelope(
        self, data: object, document: dict[str, object], subject: InstrumentId
    ) -> tuple[RawEnvelope, tuple[object, ...]]:
        if not isinstance(data, dict):
            raise BybitLiveError("Invalid Bybit candle")
        start = _millis(data.get("start"))
        end = _millis(data.get("end"))
        published = _millis(document.get("ts"))
        if (
            data.get("interval") != self._interval
            or end != start + self._interval_ns // 1_000_000 - 1
            or type(data.get("confirm")) is not bool
        ):
            raise BybitLiveError("Invalid Bybit candle boundary")
        values = tuple(
            _decimal(data.get(name))
            for name in ("open", "high", "low", "close", "volume", "turnover")
        )
        open_, high, low, close, volume, turnover = values
        if (
            min(Decimal(open_), Decimal(high), Decimal(low), Decimal(close)) <= 0
            or Decimal(volume) < 0
            or Decimal(turnover) < 0
            or not Decimal(low)
            <= min(Decimal(open_), Decimal(close))
            <= max(Decimal(open_), Decimal(close))
            <= Decimal(high)
        ):
            raise BybitLiveError("Invalid Bybit candle values")
        receipt = self._clock.sample()
        finalized = bool(data["confirm"]) and is_final(
            receipt, start * 1_000_000 + self._interval_ns, self._clock_limits
        )
        row = [str(start), open_, high, low, close, volume, turnover]
        payload = json.dumps(
            {
                "category": subject.category,
                "close": close,
                "finalized": finalized,
                "high": high,
                "interval": self._interval,
                "interval_ns": self._interval_ns,
                "low": low,
                "open": open_,
                "publication_time": {"value": published, "unit": "ms"},
                "row": row,
                "schema": {"name": "bybit_candle", "major": 1, "minor": 0},
                "start_ns": start * 1_000_000,
                "symbol": subject.symbol,
                "volume": volume,
            },
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("ascii")
        if len(payload) > _EVENT_LIMIT:
            raise BybitLiveError("Bybit candle exceeds configured limit")
        envelope = RawEnvelope(
            source="bybit-public",
            stream="market-kline",
            channel=f"kline-{self._interval}",
            adapter_version="1.0",
            receipt=receipt,
            payload=payload,
            payload_limit=_EVENT_LIMIT,
            subject=subject,
            source_time=SourceTime(start, TimeUnit.MILLISECOND),
            source_event_id=f"{subject.category}-{subject.symbol}-{start}",
        )
        # A later publication timestamp alone is not a candle revision. Preserve
        # changes in values, source confirmation, clock epoch and F12 finality.
        key: tuple[object, ...] = (
            start,
            values,
            data["confirm"],
            finalized,
            receipt.quality.epoch,
        )
        return envelope, key

    async def _subscribe(self, socket: _Socket, topic: str) -> None:
        await self._send(socket, {"op": "subscribe", "args": [topic]})
        try:
            ack = _document(
                await socket.receive(timeout=self._limits.subscribe_timeout_s)
            )
        except TimeoutError:
            raise BybitLiveError("Bybit subscription timed out") from None
        if ack.get("op") != "subscribe":
            raise BybitLiveError("Invalid Bybit subscription acknowledgement")
        if ack.get("success") is not True:
            raise BybitLiveConfigurationError(_SUBSCRIPTION_REJECTED)
        data = ack.get("data")
        if isinstance(data, dict) and data.get("failTopics"):
            raise BybitLiveConfigurationError(_SUBSCRIPTION_REJECTED)

    @staticmethod
    def _candle_batch(document: dict[str, object], topic: str) -> list[object]:
        if document.get("topic") != topic or document.get("type") != "snapshot":
            raise BybitLiveError("Unexpected Bybit subscription message")
        candles = document.get("data")
        if not isinstance(candles, list) or not 1 <= len(candles) <= 8:
            raise BybitLiveError("Invalid Bybit candle batch")
        return candles

    async def _heartbeat_deadline(
        self,
        socket: _Socket,
        next_ping: float,
        last_data: float,
        pong_deadline: float | None,
    ) -> tuple[float, float | None, float]:
        now = time.monotonic()
        if pong_deadline is not None and now >= pong_deadline:
            raise BybitLiveError("Bybit heartbeat timed out")
        if now - last_data >= self._limits.data_stall_s:
            raise BybitLiveError("Bybit candle stream stalled")
        if now >= next_ping and pong_deadline is None:
            await self._send(socket, {"op": "ping"})
            pong_deadline = now + self._limits.pong_timeout_s
            next_ping = now + self._limits.ping_interval_s
        deadline = min(
            next_ping if pong_deadline is None else float("inf"),
            last_data + self._limits.data_stall_s,
            pong_deadline if pong_deadline is not None else float("inf"),
        )
        return next_ping, pong_deadline, deadline

    async def _connected(
        self,
        socket: _Socket,
        topic: str,
        subject: InstrumentId,
    ) -> AsyncGenerator[tuple[RawEnvelope, tuple[object, ...]], None]:
        await self._subscribe(socket, topic)
        self.connected = True
        self.last_heartbeat_monotonic_ns = time.monotonic_ns()
        next_ping = time.monotonic() + self._limits.ping_interval_s
        last_data = time.monotonic()
        pong_deadline: float | None = None
        while not self._closed.is_set():
            next_ping, pong_deadline, deadline = await self._heartbeat_deadline(
                socket, next_ping, last_data, pong_deadline
            )
            try:
                message = await socket.receive(
                    timeout=max(0.001, deadline - time.monotonic())
                )
            except TimeoutError:
                continue
            document = _document(message)
            # Buffered duplicates/control frames can otherwise bypass every
            # suspension point. Give cancellation and other collectors a turn.
            await asyncio.sleep(0)
            if _is_pong(document):
                self.last_heartbeat_monotonic_ns = time.monotonic_ns()
                pong_deadline = None
                continue
            candles = self._candle_batch(document, topic)
            last_data = time.monotonic()
            for candle in candles:
                yield self._envelope(candle, document, subject)

    def _enqueue(
        self,
        envelope: RawEnvelope,
        key: tuple[object, ...],
        seen: OrderedDict[tuple[object, ...], None],
        queue: asyncio.Queue[tuple[RawEnvelope, SourceBufferLease | None]],
    ) -> None:
        lease = (
            self._buffer_admission(len(envelope.payload))
            if self._buffer_admission is not None
            else None
        )
        if self._buffer_admission is not None and lease is None:
            raise BybitLiveError("Bybit shared intake overflow")
        try:
            queue.put_nowait((envelope, lease))
        except asyncio.QueueFull:
            if lease is not None:
                lease.release()
            raise BybitLiveError("Bybit bounded intake overflow") from None
        # Never suppress a reconnect replay of the overflow record.
        seen[key] = None
        if len(seen) > 1024:
            seen.popitem(last=False)

    async def _pump(
        self,
        socket: _Socket,
        topic: str,
        subject: InstrumentId,
        seen: OrderedDict[tuple[object, ...], None],
        queue: asyncio.Queue[tuple[RawEnvelope, SourceBufferLease | None]],
        wake: asyncio.Event,
    ) -> None:
        try:
            async for envelope, key in self._connected(socket, topic, subject):
                if self._closed.is_set():
                    break
                if key in seen:
                    seen.move_to_end(key)
                    continue
                self._enqueue(envelope, key, seen, queue)
                wake.set()
        except BybitLiveError as exc:
            self.last_error = str(exc)
            raise
        finally:
            self.connected = False
            try:
                await socket.close()
            finally:
                wake.set()

    async def _consume_intake(
        self,
        queue: asyncio.Queue[tuple[RawEnvelope, SourceBufferLease | None]],
        reader: asyncio.Task[None],
        wake: asyncio.Event,
    ) -> AsyncGenerator[RawEnvelope, None]:
        while not self._closed.is_set():
            if not queue.empty():
                envelope, lease = queue.get_nowait()
                try:
                    yield envelope
                finally:
                    if lease is not None:
                        lease.release()
                continue
            if reader.done():
                reader.result()
                return
            wake.clear()
            await wake.wait()

    @staticmethod
    def _release_intake(
        queue: asyncio.Queue[tuple[RawEnvelope, SourceBufferLease | None]],
    ) -> None:
        while not queue.empty():
            _, lease = queue.get_nowait()
            if lease is not None:
                lease.release()

    async def _bounded(
        self,
        socket: _Socket,
        topic: str,
        subject: InstrumentId,
        seen: OrderedDict[tuple[object, ...], None],
    ) -> AsyncGenerator[RawEnvelope, None]:
        # Count and bytes are bounded: 32 records, each at most 8,192 bytes.
        # A slow journal must not leave aiohttp accumulating tiny messages.
        queue: asyncio.Queue[tuple[RawEnvelope, SourceBufferLease | None]] = (
            asyncio.Queue(_INTAKE_RECORD_LIMIT)
        )
        wake = asyncio.Event()

        reader = asyncio.create_task(
            self._pump(socket, topic, subject, seen, queue, wake)
        )
        self._reader_task = reader
        try:
            async with aclosing(self._consume_intake(queue, reader, wake)) as intake:
                async for envelope in intake:
                    yield envelope
        finally:
            reader.cancel()
            await asyncio.gather(reader, return_exceptions=True)
            self._reader_task = None
            self._release_intake(queue)

    async def _stream_connection(
        self,
        topic: str,
        subject: InstrumentId,
        seen: OrderedDict[tuple[object, ...], None],
    ) -> AsyncGenerator[RawEnvelope, None]:
        socket = await self._connect(subject.category)
        self._socket = socket
        try:
            async with aclosing(self._bounded(socket, topic, subject, seen)) as intake:
                async for envelope in intake:
                    if self._closed.is_set():
                        break
                    yield envelope
        finally:
            await socket.close()
            self._socket = None
        self.last_error = "Bybit WebSocket disconnected"

    def _internal_close_cancellation(self) -> bool:
        owner = asyncio.current_task()
        return self._closed.is_set() and owner is not None and not owner.cancelling()

    async def _report_loss(
        self, exc: BybitLiveError | aiohttp.ClientError | OSError
    ) -> None:
        self.last_error = (
            str(exc)
            if isinstance(exc, BybitLiveError)
            else "Bybit WebSocket transport failed"
        )
        if self._loss_handler is not None and not self._closed.is_set():
            reason = _loss_reason(exc, self.last_error)
            await self._loss_handler(SourceLoss(reason, self._clock.sample()))

    async def _stream_attempt(
        self,
        topic: str,
        subject: InstrumentId,
        seen: OrderedDict[tuple[object, ...], None],
    ) -> AsyncGenerator[RawEnvelope, None]:
        try:
            async with aclosing(
                self._stream_connection(topic, subject, seen)
            ) as intake:
                async for envelope in intake:
                    yield envelope
        except BybitLiveConfigurationError:
            self.last_error = _SUBSCRIPTION_REJECTED
            raise
        except asyncio.CancelledError:
            if self._internal_close_cancellation():
                # close() cancels owned connection/reader tasks. End iteration only
                # when the stream owner itself has received no cancellation request.
                return
            raise
        except (
            BybitLiveError,
            aiohttp.ClientError,
            OSError,
        ) as exc:
            await self._report_loss(exc)

    def _validate_stream(self, request: StreamRequest) -> InstrumentId:
        if self._closed.is_set():
            raise BybitLiveError("Bybit live source is closed")
        if self._running:
            raise BybitLiveError("Bybit live source already streaming")
        subject = request.subject
        if not isinstance(subject, InstrumentId) or subject.venue != "bybit":
            raise ValueError("Expected Bybit instrument")
        self.descriptor.require(request.schema, SourceOperation.STREAM, subject)
        return subject

    async def _close_socket(self) -> None:
        if self._socket is not None:
            await self._socket.close()
            self._socket = None

    async def stream(self, request: StreamRequest) -> AsyncGenerator[RawEnvelope, None]:
        subject = self._validate_stream(request)
        topic = f"kline.{self._interval}.{subject.symbol}"
        self._running = True
        seen: OrderedDict[tuple[object, ...], None] = OrderedDict()
        delay = self._limits.reconnect_initial_s
        try:
            while not self._closed.is_set():
                if self._start_gate is not None and not self._start_gate():
                    try:
                        async with asyncio.timeout(0.05):
                            await self._closed.wait()
                    except TimeoutError:
                        pass
                    continue
                async with aclosing(
                    self._stream_attempt(topic, subject, seen)
                ) as intake:
                    async for envelope in intake:
                        delay = self._limits.reconnect_initial_s
                        yield envelope
                if self._closed.is_set():
                    break
                try:
                    async with asyncio.timeout(delay):
                        await self._closed.wait()
                except TimeoutError:
                    pass
                delay = min(delay * 2, self._limits.reconnect_max_s)
        finally:
            self._running = False
            await self._close_socket()
