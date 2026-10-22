"""Offline, bounded Binance Vision 1h archive reader for PI02 reconstruction."""

from __future__ import annotations

import csv
import re
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from pathlib import Path
from typing import cast
from zipfile import BadZipFile, ZipFile

from scryntic.archive.canonical import JsonObject, canonical_json_bytes
from scryntic.research.targets import (
    HOUR_NS,
    MAX_HOURLY_ROWS,
    HourlyBar,
    HourlySeries,
    SeriesKey,
)

_MAX_ARCHIVE_BYTES = 4 * 1024 * 1024
_MAX_CSV_BYTES = 2 * 1024 * 1024
_MAX_MONTHS = 81
_SYMBOLS = frozenset(("BTCUSDT", "ETHUSDT"))
_MONTH = re.compile(r"20\d\d-(0[1-9]|1[0-2])\Z")
_HEADER = (
    "open_time",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "close_time",
    "quote_volume",
    "count",
    "taker_buy_volume",
    "taker_buy_quote_volume",
    "ignore",
)


def _checked_archive(path: Path, member_name: str) -> tuple[str, list[list[str]]]:
    sidecar = path.with_name(path.name + ".CHECKSUM")
    if (
        not path.is_file()
        or path.is_symlink()
        or not sidecar.is_file()
        or sidecar.is_symlink()
    ):
        raise ValueError("Missing archive or checksum sidecar")
    if path.stat().st_size > _MAX_ARCHIVE_BYTES or sidecar.stat().st_size > 200:
        raise ValueError("Archive exceeds PI02 bound")
    archive_sha256 = sha256(path.read_bytes()).hexdigest()
    checksum = sidecar.read_text(encoding="ascii").split()
    if len(checksum) != 2 or checksum != [archive_sha256, path.name]:
        raise ValueError("Archive checksum mismatch")
    try:
        with ZipFile(path) as archive:
            members = archive.infolist()
            if len(members) != 1 or members[0].filename != member_name:
                raise ValueError("Unexpected archive member")
            info = members[0]
            if info.file_size > _MAX_CSV_BYTES or info.flag_bits & 1:
                raise ValueError("Unsafe archive member")
            with archive.open(info) as stream:
                payload = stream.read(_MAX_CSV_BYTES + 1)
            if len(payload) > _MAX_CSV_BYTES:
                raise ValueError("Expanded archive exceeds PI02 bound")
    except BadZipFile as exc:
        raise ValueError("Invalid ZIP archive") from exc
    try:
        lines = payload.decode("ascii").splitlines()
        return archive_sha256, list(csv.reader(lines, strict=True))
    except (UnicodeError, csv.Error) as exc:
        raise ValueError("Invalid archive CSV") from exc


def load_monthly_hourly(
    root: Path, symbol: str, months: tuple[str, ...]
) -> HourlySeries:
    """Verify local monthly ZIPs and retain row/ZIP hashes; never download data."""
    if (
        not isinstance(root, Path)
        or symbol not in _SYMBOLS
        or type(months) is not tuple
        or not 1 <= len(months) <= _MAX_MONTHS
        or months != tuple(sorted(set(months)))
        or any(
            type(month) is not str or _MONTH.fullmatch(month) is None
            for month in months
        )
    ):
        raise ValueError("Unsupported bounded Vision archive selection")
    bars: list[HourlyBar] = []
    archives: list[dict[str, str]] = []
    for month in months:
        base = f"{symbol}-1h-{month}"
        archive_sha256, rows = _checked_archive(root / f"{base}.zip", f"{base}.csv")
        archives.append({"name": f"{base}.zip", "sha256": archive_sha256})
        if not rows:
            raise ValueError("Empty monthly archive")
        if tuple(rows[0]) == _HEADER:
            rows = rows[1:]
            if not rows:
                raise ValueError("Empty monthly archive")
        for row_number, row in enumerate(rows, 1):
            if len(row) != 12:
                raise ValueError("Invalid Vision row width")
            try:
                start_ms = int(row[0])
                close_ms = int(row[6])
                opening, high, low, closing = (Decimal(row[i]) for i in (1, 2, 3, 4))
                volume = Decimal(row[5])
                trades = int(row[8])
            except (ValueError, InvalidOperation) as exc:
                raise ValueError("Invalid Vision row value") from exc
            if (
                start_ms < 0
                or start_ms % 3_600_000
                or close_ms != start_ms + 3_600_000 - 1
            ):
                raise ValueError("Invalid hourly close time")
            if datetime.fromtimestamp(start_ms / 1000, UTC).strftime("%Y-%m") != month:
                raise ValueError("Row outside declared archive month")
            if (
                not all(
                    value.is_finite() for value in (opening, high, low, closing, volume)
                )
                or low <= 0
                or volume < 0
                or trades < 0
                or not low <= min(opening, closing) <= max(opening, closing) <= high
            ):
                raise ValueError("Invalid Vision OHLC or volume")
            bars.append(
                HourlyBar(
                    start_ns=start_ms * 1_000_000,
                    close=closing,
                    raw_sha256=sha256(
                        canonical_json_bytes(
                            cast(
                                JsonObject,
                                {
                                    "archive_sha256": archive_sha256,
                                    "row_number": row_number,
                                    "row": row,
                                },
                            )
                        )
                    ).hexdigest(),
                )
            )
            if len(bars) > MAX_HOURLY_ROWS:
                raise ValueError("Unbounded Vision input")
    snapshot_sha256 = sha256(
        canonical_json_bytes(cast(JsonObject, {"archives": archives}))
    ).hexdigest()
    series = HourlySeries(
        SeriesKey(
            source_id="binance-vision-usdm-1h",
            venue="Binance",
            category="USD-M",
            instrument=symbol,
            snapshot_sha256=snapshot_sha256,
            availability_plane="historical-reconstruction",
        ),
        bars,
    )
    if any(bar.start_ns % HOUR_NS for bar in bars):
        raise ValueError("Invalid hourly grid")
    return series
