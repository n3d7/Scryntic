"""Bounded, local-only Binance Vision archive admission for PI02."""

from hashlib import sha256
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

import pytest

from scryntic.research.targets import HOUR_NS
from scryntic.research.vision import load_monthly_hourly


def _archive(root: Path, symbol: str, rows: list[str]) -> Path:
    name = f"{symbol}-1h-2020-01"
    path = root / f"{name}.zip"
    with ZipFile(path, "w", compression=ZIP_DEFLATED) as zf:
        zf.writestr(f"{name}.csv", "\n".join(rows) + "\n")
    (root / f"{name}.zip.CHECKSUM").write_text(
        f"{sha256(path.read_bytes()).hexdigest()}  {path.name}\n"
    )
    return path


def _row(hour: int, close: str = "100") -> str:
    start_ms = 1_577_836_800_000 + hour * 3_600_000
    return f"{start_ms},100,101,99,{close},1,{start_ms + 3_599_999},100,1,1,1,0"


def test_local_archive_hash_and_row_lineage_are_verified(tmp_path: Path) -> None:
    _archive(tmp_path, "BTCUSDT", [_row(0), _row(1, "101")])
    series = load_monthly_hourly(tmp_path, "BTCUSDT", ("2020-01",))
    assert series.row_count == 2
    assert series.first_start_ns == 1_577_836_800_000_000_000
    assert series.last_end_ns - series.first_start_ns == 2 * HOUR_NS
    assert series.key.availability_plane == "historical-reconstruction"
    assert (
        series.origin(series.last_end_ns, 3600, "c" * 64, lookback_hours=2).known_at_ns
        is None
    )


def test_archive_checksum_mismatch_and_bad_interval_fail(tmp_path: Path) -> None:
    path = _archive(tmp_path, "BTCUSDT", [_row(0)])
    path.write_bytes(path.read_bytes() + b"tampered")
    with pytest.raises(ValueError, match="checksum"):
        load_monthly_hourly(tmp_path, "BTCUSDT", ("2020-01",))


def test_exact_optional_header_is_not_counted_as_a_bar(tmp_path: Path) -> None:
    header = (
        "open_time,open,high,low,close,volume,close_time,quote_volume,count,"
        "taker_buy_volume,taker_buy_quote_volume,ignore"
    )
    _archive(tmp_path, "BTCUSDT", [header, _row(0)])
    assert load_monthly_hourly(tmp_path, "BTCUSDT", ("2020-01",)).row_count == 1

    _archive(tmp_path, "BTCUSDT", [_row(0).replace("1577840399999", "1577840399998")])
    with pytest.raises(ValueError, match="close time"):
        load_monthly_hourly(tmp_path, "BTCUSDT", ("2020-01",))


def test_missing_month_and_zip_member_are_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="Missing archive"):
        load_monthly_hourly(tmp_path, "BTCUSDT", ("2020-01",))
    path = _archive(tmp_path, "BTCUSDT", [_row(0)])
    with ZipFile(path, "a") as zf:
        zf.writestr("unexpected.txt", "x")
    (tmp_path / f"{path.name}.CHECKSUM").write_text(
        f"{sha256(path.read_bytes()).hexdigest()}  {path.name}\n"
    )
    with pytest.raises(ValueError, match="member"):
        load_monthly_hourly(tmp_path, "BTCUSDT", ("2020-01",))
