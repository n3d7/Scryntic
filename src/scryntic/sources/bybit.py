"""Read-only Bybit V5 instrument discovery and historical kline adapter."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import ssl
import threading
import time
import urllib.parse
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Protocol, cast

import aiohttp

from scryntic.application.sources import (
    BYBIT_CANDLE_SCHEMA,
    HistoryRequest,
    RawPage,
    SourceCapability,
    SourceDescriptor,
    SourceOperation,
)
from scryntic.clock.policy import is_final
from scryntic.configuration.clock import ClockLimits
from scryntic.domain.identity import InstrumentId
from scryntic.domain.market import INSTRUMENT_SCHEMA, Instrument
from scryntic.domain.raw import RawEnvelope
from scryntic.domain.time import ClockSample, SourceTime, TimeUnit

_BASE_URL = "https://api.bybit.com"
_INSTRUMENTS_PATH = "/v5/market/instruments-info"
_KLINE_PATH = "/v5/market/kline"
_CATEGORIES = ("spot", "linear", "inverse", "option")
_CANDLE_CATEGORIES = frozenset(("spot", "linear", "inverse"))
_INTERVAL_NS = {
    "1": 60_000_000_000,
    "3": 180_000_000_000,
    "5": 300_000_000_000,
    "15": 900_000_000_000,
    "30": 1_800_000_000_000,
    "60": 3_600_000_000_000,
    "120": 7_200_000_000_000,
    "240": 14_400_000_000_000,
    "360": 21_600_000_000_000,
    "720": 43_200_000_000_000,
    "D": 86_400_000_000_000,
    "W": 604_800_000_000_000,
}
_MS_NS = 1_000_000
_API_MAX_LIMIT = 1_000
_HISTORY_PAGE_MAX = _API_MAX_LIMIT - 1
_INSTRUMENT_PAGE_SIZE = 1_000
_MAX_DISCOVERY_PAGES = 32
_MAX_RESPONSE_BYTES = 1_048_576
_MAX_EVENT_BYTES = 8_192
_REQUEST_TIMEOUT_S = 5.0
_REQUEST_DEADLINE_S = 6.0
_REQUEST_INTERVAL_S = 0.5
_CURSOR_LIMIT = 4_096
_DECIMAL = re.compile(r"(?:0|[1-9][0-9]*)(?:\.[0-9]+)?", re.ASCII)
_INTEGER = re.compile(r"[0-9]+", re.ASCII)


class BybitError(ValueError):
    """Sanitized public-data failure; never includes response text or URLs."""


class _JsonError(ValueError):
    pass


class _Client(Protocol):
    async def get(
        self, path: str, params: dict[str, str | int]
    ) -> dict[str, object]: ...


class _Clock(Protocol):
    def sample(self) -> ClockSample: ...


class _RateGate:
    """Serialize requests and keep this client well below public IP limits."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._next = 0.0

    def reserve_delay(self) -> float:
        with self._lock:
            now = time.monotonic()
            start = max(now, self._next)
            self._next = start + _REQUEST_INTERVAL_S
            return start - now


_PUBLIC_RATE_GATE = _RateGate()


def _pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise _JsonError
        result[key] = value
    return result


class BybitPublicClient:
    """TLS-verified unauthenticated HTTP client pinned to api.bybit.com."""

    async def get(self, path: str, params: dict[str, str | int]) -> dict[str, object]:
        if path not in (_INSTRUMENTS_PATH, _KLINE_PATH):
            raise BybitError("Unsupported Bybit endpoint")
        delay = _PUBLIC_RATE_GATE.reserve_delay()
        if delay:
            await asyncio.sleep(delay)
        query = urllib.parse.urlencode(params)
        url = f"{_BASE_URL}{path}?{query}"
        expected = urllib.parse.urlsplit(_BASE_URL)
        timeout = aiohttp.ClientTimeout(
            total=_REQUEST_DEADLINE_S,
            sock_connect=_REQUEST_TIMEOUT_S,
            sock_read=_REQUEST_TIMEOUT_S,
        )
        connector = aiohttp.TCPConnector(ssl=ssl.create_default_context())
        try:
            async with aiohttp.ClientSession(
                connector=connector,
                timeout=timeout,
                trust_env=False,
                auto_decompress=False,
            ) as session:
                async with session.get(
                    url,
                    headers={
                        "Accept": "application/json",
                        "Accept-Encoding": "identity",
                        "User-Agent": "Scryntic/1",
                    },
                    allow_redirects=False,
                ) as response:
                    if (
                        response.url.scheme != expected.scheme
                        or response.url.host != expected.hostname
                        or response.url.path != path
                    ):
                        raise BybitError("Bybit response host changed")
                    if 300 <= response.status < 400:
                        raise BybitError("Bybit redirect refused")
                    if response.status != 200:
                        raise BybitError("Bybit public request failed")
                    length = response.headers.get("Content-Length")
                    if length is not None and (
                        not _INTEGER.fullmatch(length)
                        or int(length) > _MAX_RESPONSE_BYTES
                    ):
                        raise BybitError("Bybit response exceeds configured limit")
                    body = bytearray()
                    async for chunk in response.content.iter_chunked(65_536):
                        if len(body) + len(chunk) > _MAX_RESPONSE_BYTES:
                            raise BybitError("Bybit response exceeds configured limit")
                        body.extend(chunk)
        except BybitError:
            raise
        except (aiohttp.ClientError, OSError, ValueError):
            raise BybitError("Bybit public request failed") from None
        try:
            decoded = json.loads(
                body.decode("utf-8", "strict"),
                object_pairs_hook=_pairs,
                parse_constant=lambda _: (_ for _ in ()).throw(_JsonError()),
            )
        except (UnicodeError, json.JSONDecodeError, _JsonError, RecursionError):
            raise BybitError("Invalid Bybit response") from None
        if not isinstance(decoded, dict):
            raise BybitError("Invalid Bybit response")
        document = cast(dict[str, object], decoded)
        if type(document.get("retCode")) is not int or document["retCode"] != 0:
            raise BybitError("Bybit rejected public request")
        result = document.get("result")
        if not isinstance(result, dict):
            raise BybitError("Invalid Bybit response")
        return cast(dict[str, object], result)


def _object(value: object) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise BybitError("Invalid Bybit response")
    return value


def _text(value: object, *, max_length: int = 128) -> str:
    if type(value) is not str or not 1 <= len(value) <= max_length:
        raise BybitError("Invalid Bybit response")
    return value


def _decimal(value: object) -> Decimal:
    text = _text(value)
    if _DECIMAL.fullmatch(text) is None:
        raise BybitError("Invalid Bybit decimal")
    try:
        number = Decimal(text)
    except InvalidOperation:
        raise BybitError("Invalid Bybit decimal") from None
    if not number.is_finite() or number <= 0:
        raise BybitError("Invalid Bybit decimal")
    return number


def _nonnegative_decimal(value: object) -> Decimal:
    text = _text(value)
    if _DECIMAL.fullmatch(text) is None:
        raise BybitError("Invalid Bybit decimal")
    try:
        number = Decimal(text)
    except InvalidOperation:
        raise BybitError("Invalid Bybit decimal") from None
    if not number.is_finite() or number < 0:
        raise BybitError("Invalid Bybit decimal")
    return number


def _timestamp_ms(value: object) -> int:
    text = _text(value)
    if _INTEGER.fullmatch(text) is None:
        raise BybitError("Invalid Bybit timestamp")
    number = int(text)
    if number > ((1 << 63) - 1) // _MS_NS:
        raise BybitError("Invalid Bybit timestamp")
    return number


def _list(value: object) -> list[object]:
    if type(value) is not list:
        raise BybitError("Invalid Bybit response")
    return value


def _instrument(category: str, value: object) -> Instrument:
    raw = _object(value)
    symbol = _text(raw.get("symbol"))
    base = _text(raw.get("baseCoin"))
    quote = _text(raw.get("quoteCoin"))
    status = _text(raw.get("status"))
    contract_type = raw.get("contractType", "Spot")
    if type(contract_type) is not str or len(contract_type) > 64:
        raise BybitError("Invalid Bybit instrument")
    price_filter = _object(raw.get("priceFilter"))
    lot_filter = _object(raw.get("lotSizeFilter"))
    price_increment = _decimal(price_filter.get("tickSize"))
    quantity_text = lot_filter.get("qtyStep")
    if category == "spot":
        quantity_text = lot_filter.get("basePrecision", quantity_text)
    quantity_increment = _decimal(quantity_text)
    launch = _timestamp_ms(raw.get("launchTime", "0"))
    delivery = _timestamp_ms(raw.get("deliveryTime", "0"))
    settlement_value = raw.get("settleCoin")
    settlement = None if settlement_value in (None, "") else _text(settlement_value)
    multiplier_value = raw.get("contractSize")
    multiplier = None if multiplier_value in (None, "") else _decimal(multiplier_value)
    if category == "inverse":
        volume_unit = quote
    else:
        volume_unit = base
    revision_body = json.dumps(
        {
            "base": base,
            "category": category,
            "contractType": contract_type,
            "contractMultiplier": None if multiplier is None else str(multiplier),
            "deliveryTime": str(delivery),
            "launchTime": str(launch),
            "priceIncrement": str(price_increment),
            "quantityIncrement": str(quantity_increment),
            "quote": quote,
            "settlement": settlement,
            "status": status,
        },
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("ascii")
    revision = "bybit-v5-" + hashlib.sha256(revision_body).hexdigest()[:24]
    return Instrument(
        identity=InstrumentId("bybit", category, symbol),
        base_asset=base,
        quote_asset=quote,
        price_increment=price_increment,
        quantity_increment=quantity_increment,
        volume_unit=volume_unit,
        revision=revision,
        settlement_asset=settlement,
        contract_multiplier=multiplier,
        expiry_ns=None if delivery == 0 else delivery * _MS_NS,
    )


def _page_rows(result: Mapping[str, object], category: str) -> list[object]:
    if result.get("category") != category:
        raise BybitError("Bybit category mismatch")
    rows = _list(result.get("list"))
    if len(rows) > _INSTRUMENT_PAGE_SIZE:
        raise BybitError("Bybit page exceeds configured limit")
    return rows


@dataclass(frozen=True, slots=True)
class _Cursor:
    category: str
    symbol: str
    interval: str
    start_ns: int
    end_ns: int
    page_size: int
    before_ms: int


def _encode_cursor(cursor: _Cursor) -> bytes:
    data = json.dumps(
        {
            "before_ms": cursor.before_ms,
            "category": cursor.category,
            "end_ns": cursor.end_ns,
            "interval": cursor.interval,
            "page_size": cursor.page_size,
            "schema": 1,
            "start_ns": cursor.start_ns,
            "symbol": cursor.symbol,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("ascii")
    if len(data) > _CURSOR_LIMIT:
        raise BybitError("Bybit cursor exceeds configured limit")
    return data


def _decode_cursor(value: bytes, request: HistoryRequest, interval: str) -> _Cursor:
    if type(value) is not bytes or not 1 <= len(value) <= _CURSOR_LIMIT:
        raise BybitError("Invalid Bybit cursor")
    try:
        data = json.loads(value.decode("ascii"), object_pairs_hook=_pairs)
    except (UnicodeError, json.JSONDecodeError, _JsonError, RecursionError):
        raise BybitError("Invalid Bybit cursor") from None
    if not isinstance(data, dict) or set(data) != {
        "before_ms",
        "category",
        "end_ns",
        "interval",
        "page_size",
        "schema",
        "start_ns",
        "symbol",
    }:
        raise BybitError("Invalid Bybit cursor")
    subject = request.stream.subject
    if not isinstance(subject, InstrumentId):
        raise BybitError("Invalid Bybit subject")
    expected = (
        type(data["schema"]) is int
        and data["schema"] == 1
        and type(data["category"]) is str
        and data["category"] == subject.category
        and type(data["symbol"]) is str
        and data["symbol"] == subject.symbol
        and type(data["interval"]) is str
        and data["interval"] == interval
        and type(data["start_ns"]) is int
        and data["start_ns"] == request.start_ns
        and type(data["end_ns"]) is int
        and data["end_ns"] == request.end_ns
        and type(data["page_size"]) is int
        and data["page_size"] == request.page_size
        and type(data["before_ms"]) is int
    )
    if not expected:
        raise BybitError("Bybit cursor does not match request")
    return _Cursor(
        subject.category,
        subject.symbol,
        interval,
        request.start_ns,
        request.end_ns,
        request.page_size,
        cast(int, data["before_ms"]),
    )


def _kline_row(value: object) -> tuple[int, list[str]]:
    row = _list(value)
    if len(row) != 7 or any(type(item) is not str for item in row):
        raise BybitError("Invalid Bybit kline")
    timestamp_ms = _timestamp_ms(row[0])
    numeric = [_text(item) for item in row[1:]]
    open_value, high, low, close = (_decimal(item) for item in numeric[:4])
    volume, turnover = (_nonnegative_decimal(item) for item in numeric[4:])
    if low > min(open_value, close) or high < max(open_value, close) or low > high:
        raise BybitError("Invalid Bybit OHLC")
    return timestamp_ms, [cast(str, row[0]), *numeric]


class BybitHistoricalSource:
    """One configured interval; tokens are stable, opaque, and process-independent."""

    def __init__(
        self,
        *,
        clock: _Clock,
        client: _Client | None = None,
        limits: ClockLimits | None = None,
        interval: str = "1",
    ) -> None:
        if interval not in _INTERVAL_NS:
            raise ValueError("Unsupported fixed-duration Bybit interval")
        self._clock = clock
        self._client = client or BybitPublicClient()
        self._limits = limits or ClockLimits()
        self._interval = interval
        self._interval_ns = _INTERVAL_NS[interval]
        self._closed = False
        capabilities = [
            SourceCapability(
                schema=INSTRUMENT_SCHEMA,
                subject_kind="instrument",
                category=category,
                operations=frozenset({SourceOperation.DISCOVER}),
                sequencing="none",
                recovery="backfill",
            )
            for category in _CATEGORIES
        ]
        capabilities.extend(
            SourceCapability(
                schema=BYBIT_CANDLE_SCHEMA,
                subject_kind="instrument",
                category=category,
                operations=frozenset({SourceOperation.HISTORY}),
                sequencing="none",
                recovery="backfill",
            )
            for category in sorted(_CANDLE_CATEGORIES)
        )
        self.descriptor = SourceDescriptor(
            source_id="bybit-public",
            adapter_version="1.0",
            capabilities=tuple(capabilities),
            max_payload_bytes=_MAX_EVENT_BYTES,
            max_page_records=_HISTORY_PAGE_MAX,
        )

    async def _get(self, path: str, params: dict[str, str | int]) -> dict[str, object]:
        try:
            async with asyncio.timeout(_REQUEST_DEADLINE_S):
                return await self._client.get(path, params)
        except TimeoutError:
            raise BybitError("Bybit public request timed out") from None

    async def close(self) -> None:
        self._closed = True

    async def discover(self, category: str) -> tuple[Instrument, ...]:
        if self._closed:
            raise BybitError("Bybit adapter is closed")
        if category not in _CATEGORIES:
            raise BybitError("Unsupported Bybit category")
        cursor: str | None = None
        seen: set[str] = set()
        found: dict[str, Instrument] = {}
        for _ in range(_MAX_DISCOVERY_PAGES):
            params: dict[str, str | int] = {"category": category}
            if category == "option":
                params["baseCoin"] = "All"
            if category != "spot":
                params["limit"] = _INSTRUMENT_PAGE_SIZE
                if cursor is not None:
                    params["cursor"] = cursor
            result = await self._get(_INSTRUMENTS_PATH, params)
            rows = _page_rows(result, category)
            for row in rows:
                instrument = _instrument(category, row)
                prior = found.get(instrument.identity.symbol)
                if prior is not None and prior != instrument:
                    raise BybitError("Conflicting Bybit instrument metadata")
                found[instrument.identity.symbol] = instrument
            next_cursor = result.get("nextPageCursor", "")
            if type(next_cursor) is not str or len(next_cursor) > 2_048:
                raise BybitError("Invalid Bybit instrument cursor")
            if category == "spot" or not next_cursor:
                return tuple(found[key] for key in sorted(found))
            if next_cursor == cursor or next_cursor in seen:
                raise BybitError("Bybit instrument cursor did not advance")
            seen.add(next_cursor)
            cursor = next_cursor
        raise BybitError("Bybit instrument discovery exceeded page limit")

    async def fetch(self, request: HistoryRequest) -> RawPage:
        if self._closed:
            raise BybitError("Bybit adapter is closed")
        subject = request.stream.subject
        if not isinstance(subject, InstrumentId) or subject.venue != "bybit":
            raise BybitError("Unsupported Bybit subject")
        if request.stream.schema != BYBIT_CANDLE_SCHEMA:
            raise BybitError("Unsupported Bybit candle schema")
        if subject.category not in _CANDLE_CATEGORIES:
            raise BybitError("Bybit category has no supported candle endpoint")
        if request.page_size > _HISTORY_PAGE_MAX:
            raise BybitError("Bybit page exceeds configured limit")
        self.descriptor.require(BYBIT_CANDLE_SCHEMA, SourceOperation.HISTORY, subject)
        start_ms = (request.start_ns + _MS_NS - 1) // _MS_NS
        end_exclusive_ms = (request.end_ns + _MS_NS - 1) // _MS_NS
        cursor = (
            None
            if request.cursor is None
            else _decode_cursor(request.cursor, request, self._interval)
        )
        before_ms = end_exclusive_ms if cursor is None else cursor.before_ms
        if before_ms <= start_ms or before_ms > end_exclusive_ms:
            raise BybitError("Invalid Bybit cursor boundary")
        end_ms = before_ms - 1 if cursor is None else before_ms
        api_limit = request.page_size + 1
        params: dict[str, str | int] = {
            "category": subject.category,
            "symbol": subject.symbol,
            "interval": self._interval,
            "start": start_ms,
            "end": end_ms,
            "limit": api_limit,
        }
        result = await self._get(_KLINE_PATH, params)
        if (
            result.get("category") != subject.category
            or result.get("symbol") != subject.symbol
        ):
            raise BybitError("Bybit kline identity mismatch")
        raw_rows = _list(result.get("list"))
        if len(raw_rows) > api_limit:
            raise BybitError("Bybit kline page exceeds configured limit")
        rows: dict[int, list[str]] = {}
        for raw_row in raw_rows:
            timestamp_ms, values = _kline_row(raw_row)
            if start_ms <= timestamp_ms < before_ms:
                prior = rows.get(timestamp_ms)
                if prior is not None and prior != values:
                    raise BybitError("Conflicting overlapping Bybit candles")
                rows[timestamp_ms] = values
        ordered = sorted(rows.items())
        selected = ordered[-request.page_size :]
        receipt = self._clock.sample()
        envelopes: list[RawEnvelope] = []
        for timestamp_ms, values in selected:
            start_ns = timestamp_ms * _MS_NS
            closing_boundary_ns = start_ns + self._interval_ns
            finalized = is_final(receipt, closing_boundary_ns, self._limits)
            payload = json.dumps(
                {
                    "category": subject.category,
                    "close": values[4],
                    "finalized": finalized,
                    "high": values[2],
                    "interval": self._interval,
                    "interval_ns": self._interval_ns,
                    "low": values[3],
                    "open": values[1],
                    "publication_time": None,
                    "row": values,
                    "schema": {"name": "bybit_candle", "major": 1, "minor": 0},
                    "start_ns": start_ns,
                    "symbol": subject.symbol,
                    "volume": values[5],
                },
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("ascii")
            if len(payload) > self.descriptor.max_payload_bytes:
                raise BybitError("Bybit candle exceeds configured limit")
            envelopes.append(
                RawEnvelope(
                    source="bybit-public",
                    stream="market-kline",
                    channel=f"kline-{self._interval}",
                    adapter_version="1.0",
                    receipt=receipt,
                    payload=payload,
                    payload_limit=self.descriptor.max_payload_bytes,
                    subject=subject,
                    source_time=SourceTime(timestamp_ms, TimeUnit.MILLISECOND),
                    source_event_id=f"{subject.category}-{subject.symbol}-{timestamp_ms}",
                )
            )
        next_cursor = None
        if len(raw_rows) >= api_limit and selected and selected[0][0] > start_ms:
            next_cursor = _encode_cursor(
                _Cursor(
                    subject.category,
                    subject.symbol,
                    self._interval,
                    request.start_ns,
                    request.end_ns,
                    request.page_size,
                    selected[0][0],
                )
            )
        return RawPage(tuple(envelopes), next_cursor, record_limit=request.page_size)
