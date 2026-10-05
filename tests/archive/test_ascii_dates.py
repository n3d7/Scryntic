"""Canonical archive dates retain an ASCII-only namespace."""

import pytest

from scryntic.archive.model import Partition


@pytest.mark.parametrize("utc_date", ["٢٠٢٦-١٠-٠٣", "２０２６-１０-０３"])
def test_unicode_decimal_dates_are_rejected(utc_date: str) -> None:
    with pytest.raises(ValueError, match="canonical UTC date"):
        Partition("source-a", "candle", utc_date)


def test_ascii_date_remains_accepted() -> None:
    assert Partition("source-a", "candle", "2026-10-03").utc_date == "2026-10-03"
