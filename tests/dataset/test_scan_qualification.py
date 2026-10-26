"""Qualification uses fixed operations, independent expected rows and exact masks."""

from decimal import Decimal
from pathlib import Path

import pytest

from scryntic.analytics.benchmark import run_fixture
from scryntic.analytics.fixtures import prepare_fixture
from scryntic.analytics.scans import compare_rows, horizon_inputs


def test_fixture_is_repeatable_and_all_rows_are_independently_verified(
    tmp_path: Path,
) -> None:
    first = prepare_fixture(tmp_path / "first", count=512, gaps=True)
    second = prepare_fixture(tmp_path / "second", count=512, gaps=True)
    assert first == second
    assert first["row_count"] == 512
    rows = first["expected_rows"]
    compare_rows(rows, first)
    changed = [dict(r) for r in rows]
    changed[-1]["close"] = "999.000000000000000000"
    with pytest.raises(ValueError, match="parity"):
        compare_rows(changed, first)
    with pytest.raises(ValueError, match="parity"):
        compare_rows(rows[::-1], first)


def test_hand_derived_horizon_masks_keep_absent_and_invalid_separate() -> None:
    hour = 3600 * 10**9
    rows = [
        {
            "start_ns": i * hour,
            "close": str(Decimal(i + 1)),
            "finalized": i != 2,
            "quality_flags": [],
            "semantic_revision": str(i),
        }
        for i in (0, 1, 2, 4)
    ]
    result = horizon_inputs(rows, horizons=(1,))
    assert result["1"]["eligible"] == 1
    assert result["1"]["excluded"] == 3
    assert result["1"]["missing_endpoint"] == 2
    assert result["1"]["invalid"] == 1


def test_real_page_and_scan_jobs_have_identical_complete_results(
    tmp_path: Path,
) -> None:
    path = tmp_path / "fixture"
    prepare_fixture(path, count=129, gaps=True)
    pages = run_fixture(path, "pyarrow-pages")
    scan = run_fixture(path, "pyarrow-scan")
    assert pages["rows_sha256"] == scan["rows_sha256"]
    assert pages["horizons"] == scan["horizons"]
    assert pages["usage"]["scan_count"] == 2
    assert scan["usage"]["scan_count"] == 1
    assert pages["usage"]["rows_considered"] == 258
    assert scan["usage"]["rows_considered"] == 129


@pytest.mark.parametrize("count", [-1, True, 10001])
def test_fixture_generation_never_expands_the_admitted_scope(
    tmp_path: Path, count: int
) -> None:
    with pytest.raises((ValueError, TypeError)):
        prepare_fixture(tmp_path / "fixture", count=count, gaps=False)
