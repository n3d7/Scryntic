"""Bounded, unauthenticated Binance USD-M hourly shadow input adapter."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from dataclasses import asdict
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Protocol
from uuid import uuid4

import aiohttp

from scryntic.configuration.paths import Installation, directory
from scryntic.domain.validation import digest, integer
from scryntic.forecast.ledger import ForecastLedger
from scryntic.forecast.ledger_contracts import (
    FINAL_START_NS,
    FORWARD_SOURCE,
    SHADOW_START_NS,
    ForecastContent,
    ForecastSlot,
    Stream,
    identity,
    timeliness,
)
from scryntic.research.targets import HOUR_NS

MAX_HTTP_BYTES = 2 * 1024**2
_PATHS = {"/fapi/v1/time", "/fapi/v1/exchangeInfo", "/fapi/v1/klines"}


class Transport(Protocol):
    async def request(self, path: str, params: dict[str, str]) -> bytes: ...


class PublicTransport:
    async def request(self, path: str, params: dict[str, str]) -> bytes:
        if path not in _PATHS:
            raise ValueError("Unapproved PI04 public endpoint")
        timeout = aiohttp.ClientTimeout(total=10)
        async with aiohttp.ClientSession(timeout=timeout, trust_env=False) as session:
            async with session.get(
                "https://fapi.binance.com" + path, params=params, allow_redirects=False
            ) as response:
                if response.status != 200:
                    raise ValueError("PI04 public source unavailable")
                data = bytearray()
                async for chunk in response.content.iter_chunked(32768):
                    data.extend(chunk)
                    if len(data) > MAX_HTTP_BYTES:
                        raise ValueError("PI04 source response bound")
                return bytes(data)


class SourceRawStore:
    """Private no-replace content-addressed raw bytes, separate from the journal."""

    def __init__(self, installation: Installation) -> None:
        self.installation = installation

    def _root(self, state: int) -> int:
        try:
            os.mkdir("pi04-source", 0o700, dir_fd=state)
            os.fsync(state)
        except FileExistsError:
            pass
        fd = os.open(
            "pi04-source",
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
            dir_fd=state,
        )
        info = os.fstat(fd)
        if (
            info.st_uid != self.installation.owner_uid
            or stat.S_IMODE(info.st_mode) != 0o700
        ):
            os.close(fd)
            raise ValueError("Unsafe PI04 source directory")
        return fd

    def _read(self, root: int, checksum: str) -> bytes:
        fd = os.open(
            checksum,
            os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK,
            dir_fd=root,
        )
        try:
            info = os.fstat(fd)
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_uid != self.installation.owner_uid
                or stat.S_IMODE(info.st_mode) != 0o600
                or info.st_nlink != 1
                or not 0 < info.st_size <= MAX_HTTP_BYTES
            ):
                raise ValueError("Unsafe PI04 source object")
            data = bytearray()
            while chunk := os.read(fd, min(32768, MAX_HTTP_BYTES + 1 - len(data))):
                data.extend(chunk)
                if len(data) > MAX_HTTP_BYTES:
                    raise ValueError("PI04 source object bound")
            if (
                len(data) != info.st_size
                or hashlib.sha256(data).hexdigest() != checksum
            ):
                raise ValueError("PI04 source integrity mismatch")
            return bytes(data)
        finally:
            os.close(fd)

    def read(self, checksum: str) -> bytes:
        digest(checksum)
        with directory(
            self.installation.state_dir, self.installation.owner_uid, private=True
        ) as state:
            root = self._root(state)
            try:
                return self._read(root, checksum)
            finally:
                os.close(root)

    def write(self, data: bytes) -> str:
        if type(data) is not bytes or not 0 < len(data) <= MAX_HTTP_BYTES:
            raise ValueError("PI04 raw byte bound")
        checksum = hashlib.sha256(data).hexdigest()
        with directory(
            self.installation.state_dir, self.installation.owner_uid, private=True
        ) as state:
            root = self._root(state)
            temporary = "stage-" + uuid4().hex
            try:
                try:
                    if self._read(root, checksum) != data:
                        raise ValueError("PI04 source content conflict")
                    return checksum
                except FileNotFoundError:
                    pass
                names = os.listdir(root)
                if (
                    len(names) >= 4096
                    or sum(
                        os.stat(name, dir_fd=root, follow_symlinks=False).st_size
                        for name in names
                    )
                    + len(data)
                    > 512 * 1024**2
                ):
                    raise ValueError("PI04 raw storage quota")
                fd = os.open(
                    temporary,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                    0o600,
                    dir_fd=root,
                )
                try:
                    view = memoryview(data)
                    while view:
                        written = os.write(fd, view)
                        if written <= 0:
                            raise OSError("Incomplete PI04 raw write")
                        view = view[written:]
                    os.fsync(fd)
                finally:
                    os.close(fd)
                os.link(
                    temporary,
                    checksum,
                    src_dir_fd=root,
                    dst_dir_fd=root,
                    follow_symlinks=False,
                )
                os.unlink(temporary, dir_fd=root)
                os.fsync(root)
                return checksum
            finally:
                try:
                    os.unlink(temporary, dir_fd=root)
                except FileNotFoundError:
                    pass
                os.close(root)


def _json(data: bytes) -> Any:
    if not 0 < len(data) <= MAX_HTTP_BYTES:
        raise ValueError("PI04 response bound")

    def pairs(values: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in values:
            if key in result:
                raise ValueError("Duplicate source JSON key")
            result[key] = value
        return result

    return json.loads(data, object_pairs_hook=pairs)


def kline(data: bytes, origin_ns: int) -> tuple[list[Any], Decimal]:
    rows = _json(data)
    if (
        type(rows) is not list
        or len(rows) != 1
        or type(rows[0]) is not list
        or len(rows[0]) != 12
    ):
        raise ValueError("Unexpected PI04 hourly kline shape")
    row = rows[0]
    if (
        type(row[0]) is not int
        or type(row[6]) is not int
        or row[0] * 10**6 != origin_ns - HOUR_NS
        or (row[6] + 1) * 10**6 != origin_ns
    ):
        raise ValueError("PI04 hourly close identity mismatch")
    prices = [_decimal(row[i]) for i in (1, 2, 3, 4)]
    if (
        any(not v.is_finite() or v <= 0 for v in prices)
        or not prices[2]
        <= min(prices[0], prices[3])
        <= max(prices[0], prices[3])
        <= prices[1]
    ):
        raise ValueError("Invalid PI04 OHLC")
    if type(row[8]) is not int or row[8] < 0:
        raise ValueError("Invalid PI04 trade count")
    _validate_volumes(row)
    return row, prices[3]


def _validate_volumes(row: list[Any]) -> None:
    for i in (5, 7, 9, 10):
        value = _decimal(row[i])
        if not value.is_finite() or value < 0:
            raise ValueError("Invalid PI04 volume")


def _decimal(value: Any) -> Decimal:
    if (
        type(value) is not str
        or len(value) > 40
        or re.fullmatch(r"\d{1,20}(?:\.\d{1,18})?", value, flags=re.ASCII) is None
    ):
        raise ValueError("Invalid PI04 native decimal string")
    try:
        result = Decimal(value)
    except InvalidOperation:
        raise ValueError("Invalid PI04 native decimal string") from None
    if not result.is_finite():
        raise ValueError("Nonfinite PI04 native decimal")
    return result


class BinanceHourly:
    def __init__(
        self, ledger: ForecastLedger, transport: Transport | None = None
    ) -> None:
        if ledger.admission is None:
            raise ValueError("PI04 live source needs the owner supplement")
        self.ledger, self.transport = ledger, transport or PublicTransport()
        self.raw = SourceRawStore(ledger.installation)

    @staticmethod
    def _native_instrument(metadata: Any, instrument: str) -> dict[str, Any]:
        symbols = metadata.get("symbols") if type(metadata) is dict else None
        if (
            type(metadata) is not dict
            or metadata.get("timezone") != "UTC"
            or type(symbols) is not list
            or len(symbols) > 4096
        ):
            raise ValueError("Unqualified PI04 native instrument metadata")
        selected = [
            s for s in symbols if type(s) is dict and s.get("symbol") == instrument
        ]
        if len(selected) != 1:
            raise ValueError("Ambiguous PI04 native instrument")
        native = selected[0]
        if any(
            native.get(k) != v
            for k, v in {
                "symbol": instrument,
                "pair": instrument,
                "contractType": "PERPETUAL",
                "status": "TRADING",
                "baseAsset": instrument[:-4],
                "quoteAsset": "USDT",
                "marginAsset": "USDT",
            }.items()
        ):
            raise ValueError("PI04 contract identity changed")
        onboard = native.get("onboardDate")
        if type(onboard) is not int:
            raise ValueError("Invalid PI04 native instrument lifetime")
        integer(onboard, 1)
        return native

    async def fetch_origin(self, instrument: str, origin_ns: int) -> dict[str, Any]:
        if (
            instrument not in ("BTCUSDT", "ETHUSDT")
            or not SHADOW_START_NS <= origin_ns < FINAL_START_NS
            or origin_ns % HOUR_NS
        ):
            raise ValueError("PI04 live request outside supplement")
        time_data = await self.transport.request("/fapi/v1/time", {})
        time_body = _json(time_data)
        if type(time_body) is not dict:
            raise ValueError("Invalid PI04 source time response")
        server_time = time_body.get("serverTime")
        if type(server_time) is not int or server_time * 10**6 < origin_ns:
            raise ValueError("PI04 source close is not final")
        metadata_data = await self.transport.request("/fapi/v1/exchangeInfo", {})
        native = self._native_instrument(_json(metadata_data), instrument)
        data = await self.transport.request(
            "/fapi/v1/klines",
            {
                "symbol": instrument,
                "interval": "1h",
                "startTime": str((origin_ns - HOUR_NS) // 10**6),
                "endTime": str(origin_ns // 10**6 - 1),
                "limit": "1",
            },
        )
        row, close = kline(data, origin_ns)
        raw_sha = self.raw.write(data)
        metadata_sha, time_sha = (
            self.raw.write(metadata_data),
            self.raw.write(time_data),
        )
        reference_sha = identity(row)
        input_sha = identity(
            {
                "source": FORWARD_SOURCE,
                "adapter_sha256": hashlib.sha256(
                    Path(__file__).read_bytes()
                ).hexdigest(),
                "instrument": instrument,
                "origin_ns": origin_ns,
                "reference_sha256": reference_sha,
                "native": {
                    k: native[k]
                    for k in (
                        "symbol",
                        "contractType",
                        "onboardDate",
                        "baseAsset",
                        "quoteAsset",
                        "marginAsset",
                    )
                },
            }
        )
        existing = [
            e["body"] for e in self.ledger.events("input") if e["subject"] == input_sha
        ]
        if not existing:
            self.ledger._append(
                "input",
                input_sha,
                {
                    "origin_ns": origin_ns,
                    "instrument": instrument,
                    "source_id": FORWARD_SOURCE,
                    "close": str(close),
                    "reference_sha256": reference_sha,
                    "raw_sha256": raw_sha,
                    "metadata_raw_sha256": metadata_sha,
                    "time_raw_sha256": time_sha,
                    "server_time_ns": server_time * 10**6,
                },
            )
        cached = self.cached_origin(instrument, origin_ns)
        if cached is not None and cached["input_sha256"] == input_sha:
            return cached
        # Content may recur, but a new observation never inherits old availability.
        observation_sha = identity({"input_sha256": input_sha, "attempt": uuid4().hex})
        before = self.ledger.clock.sample()
        self.ledger._append(
            "input-observation",
            observation_sha,
            {
                "input_sha256": input_sha,
                "raw_sha256": raw_sha,
                "metadata_raw_sha256": metadata_sha,
                "time_raw_sha256": time_sha,
                "server_time_ns": server_time * 10**6,
            },
        )
        after = self.ledger.clock.sample()
        slot = ForecastSlot(
            Stream("binance-zero-v1", "forward-shadow", FORWARD_SOURCE),
            "Binance",
            "USD-M-futures",
            instrument,
            origin_ns,
            3600,
        )
        certificate = {
            "observation_sha256": observation_sha,
            "timeliness": timeliness(before, after, slot),
            "latest_available_ns": after.wall_time_ns
            + (after.quality.uncertainty_ns or 0),
            "observation": asdict(after),
        }
        self.ledger._append("input-receipt", input_sha, certificate)
        return self._observed_value(
            next(e for e in self.ledger.events("input") if e["subject"] == input_sha),
            certificate,
            observation_sha,
        )

    @staticmethod
    def _observed_value(
        event: dict[str, Any],
        certificate: dict[str, Any],
        observation_sha: str,
    ) -> dict[str, Any]:
        return {
            **event["body"],
            "input_sha256": event["subject"],
            "observation_sha256": observation_sha,
            "latest_available_ns": certificate["latest_available_ns"],
            "clock_qualified": certificate["timeliness"]
            not in ("unknown", "clock-discontinuity", "recovery-unknown"),
        }

    @staticmethod
    def content(slot: ForecastSlot, value: dict[str, Any]) -> ForecastContent:
        return ForecastContent(
            slot,
            value["input_sha256"],
            value["reference_sha256"],
            Decimal(value["close"]),
            value["latest_available_ns"],
            Decimal(0),
        )

    def cached_origin(self, instrument: str, origin_ns: int) -> dict[str, Any] | None:
        inputs = {
            e["subject"]: e
            for e in self.ledger.events("input")
            if e["body"]["instrument"] == instrument
            and e["body"]["origin_ns"] == origin_ns
        }
        observations = [
            e
            for e in self.ledger.events("input-observation")
            if e["body"]["input_sha256"] in inputs
        ]
        markers = [*inputs.values(), *observations]
        if not markers:
            return None
        latest = max(markers, key=lambda e: e["sequence"])
        observed = latest["kind"] == "input-observation"
        input_sha = latest["body"]["input_sha256"] if observed else latest["subject"]
        observation_sha = latest["subject"] if observed else None
        certificates = [
            e["body"]
            for e in self.ledger.events("input-receipt")
            if e["subject"] == input_sha
            and e["body"].get("observation_sha256") == observation_sha
        ]
        if len(certificates) != 1:
            return None
        certificate = certificates[0]
        value = self._observed_value(
            inputs[input_sha],
            certificate,
            observation_sha or identity({"legacy_input_sha256": input_sha}),
        )
        if not value["clock_qualified"]:
            return None
        _, close = kline(self.raw.read(value["raw_sha256"]), origin_ns)
        if str(close) != value["close"]:
            raise ValueError("PI04 cached source close mismatch")
        return value
