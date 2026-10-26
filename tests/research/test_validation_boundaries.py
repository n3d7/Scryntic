"""Synthetic admission failures preserve guard priority and client cleanup."""

import asyncio
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
from zipfile import ZipFile

import pytest

from scryntic.research.baseline_control import _finish_clients
from scryntic.research.feasibility import Split, assess_horizon
from scryntic.research.targets import HOUR_NS, HourlyBar, HourlySeries, SeriesKey
from scryntic.research.vision import load_monthly_hourly


def synthetic_series() -> HourlySeries:
    return HourlySeries(
        SeriesKey(
            "synthetic",
            "fixture",
            "spot",
            "BTCUSDT",
            "a" * 64,
            "historical-reconstruction",
        ),
        [HourlyBar(0, Decimal("100"), "b" * 64)],
    )


def archive(root: Path, rows: list[str]) -> None:
    name = "BTCUSDT-1h-2020-01"
    path = root / f"{name}.zip"
    with ZipFile(path, "w") as stream:
        stream.writestr(f"{name}.csv", "\n".join(rows))
    path.with_name(path.name + ".CHECKSUM").write_text(
        f"{sha256(path.read_bytes()).hexdigest()}  {path.name}\n"
    )


def test_feasibility_rejects_non_hourly_block_before_price_access(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    series = synthetic_series()
    split = Split("development", 0, 3 * HOUR_NS, 0)

    def unexpected(*args: object, **kwargs: object) -> None:
        pytest.fail("Invalid feasibility grid accessed price origins")

    monkeypatch.setattr(HourlySeries, "origin", unexpected)
    with pytest.raises(ValueError, match="^Unsupported bounded PI02 feasibility grid$"):
        assess_horizon(series, split, 3600, 3600, 1, 3601, "c" * 64)


def test_feasibility_policy_digest_precedes_unsupported_grid() -> None:
    series = synthetic_series()
    split = Split("development", 0, 3 * HOUR_NS, 0)
    with pytest.raises(ValueError, match="^Expected a lowercase SHA-256 digest$"):
        assess_horizon(series, split, 7200, 3600, 1, 86400, "invalid")


def test_structural_reasons_keep_embargo_then_history_then_endpoint_priority() -> None:
    series = synthetic_series()
    split = Split("validation", 0, 3 * HOUR_NS, 2 * HOUR_NS)
    counts = assess_horizon(series, split, 3600, 3600, 4, 86400, "c" * 64)
    assert counts.candidates == 3
    assert counts.structural_exclusions == {"embargo": 2, "missing-history-boundary": 1}
    assert counts.structurally_eligible == 0
    assert counts.matured == 0
    assert counts.completeness == 0.0
    assert counts.data_exclusions == {}


def test_duplicate_archive_selection_fails_before_missing_archive(
    tmp_path: Path,
) -> None:
    with pytest.raises(
        ValueError, match="^Unsupported bounded Vision archive selection$"
    ):
        load_monthly_hourly(tmp_path, "BTCUSDT", ("2020-01", "2020-01"))


@pytest.mark.parametrize(
    ("opening", "message"),
    [
        ("not-a-number", "Invalid Vision row value"),
        ("NaN", "Invalid Vision OHLC or volume"),
    ],
)
def test_checksum_valid_archive_rejects_malformed_price(
    tmp_path: Path, opening: str, message: str
) -> None:
    row = f"1577836800000,{opening},101,99,100,1,1577840399999,100,1,1,1,0"
    archive(tmp_path, [row])
    with pytest.raises(ValueError, match=f"^{message}$"):
        load_monthly_hourly(tmp_path, "BTCUSDT", ("2020-01",))


def test_checksum_valid_archive_cannot_repeat_a_historical_hour(tmp_path: Path) -> None:
    row = "1577836800000,100,101,99,100,1,1577840399999,100,1,1,1,0"
    archive(tmp_path, [row, row])
    with pytest.raises(
        ValueError, match="^Hourly input must be ordered with distinct revisions$"
    ):
        load_monthly_hourly(tmp_path, "BTCUSDT", ("2020-01",))


def test_header_only_archive_cannot_admit_an_empty_series(tmp_path: Path) -> None:
    archive(
        tmp_path,
        [
            "open_time,open,high,low,close,volume,close_time,quote_volume,count,taker_buy_volume,taker_buy_quote_volume,ignore"
        ],
    )
    with pytest.raises(ValueError, match="^Empty monthly archive$"):
        load_monthly_hourly(tmp_path, "BTCUSDT", ("2020-01",))


def test_cancelled_owner_waits_for_pending_client_cleanup_and_observes_failed_client() -> (
    None
):
    async def scenario() -> None:
        closed = asyncio.Event()

        async def pending_client() -> None:
            try:
                await asyncio.Event().wait()
            finally:
                await asyncio.sleep(0)
                closed.set()

        async def failed_client() -> None:
            raise OSError("synthetic disconnected client")

        pending = asyncio.create_task(pending_client())
        failed = asyncio.create_task(failed_client())
        await asyncio.sleep(0)
        owner = asyncio.create_task(_finish_clients({pending, failed}))
        await asyncio.sleep(0)
        owner.cancel()
        with pytest.raises(asyncio.CancelledError):
            await owner
        assert closed.is_set()
        assert pending.cancelled()
        assert failed.done()
        assert isinstance(failed.exception(), OSError)

    asyncio.run(scenario())
