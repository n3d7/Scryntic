"""Closed primitive IPC rejects malformed or coerced worker results."""

from dataclasses import asdict
from typing import Any

import pytest

from scryntic.archive.canonical import canonical_json_bytes
from scryntic.dataset.protocol import (
    MAX_INSPECT_ROWS,
    clock_from,
    primitive_document,
    validate_rows,
)
from scryntic.imports.protocol import ImportError
from tests.normalization.helpers import DEFAULT_RECEIPT


def row(*, evolved: bool = True) -> dict[str, Any]:
    result: dict[str, Any] = {
        "venue": "bybit",
        "category": "linear",
        "symbol": "BTCUSDT",
        "volume_unit": "base",
        "producer": "collector",
        "epoch": "epoch-1",
        "manifest_sha256": "a" * 64,
        "raw_object_sha256": "b" * 64,
        "normalized_object_sha256": "c" * 64,
        "raw_sha256": "d" * 64,
        "semantic_revision": "sha256:" + "e" * 64,
        "open": "100.0",
        "high": "101.0",
        "low": "99.0",
        "close": "100.5",
        "volume": "1.0",
        "start_ns": 0,
        "interval_ns": 60_000_000_000,
        "offset": 1,
        "input_ordinal": 0,
        "receipt_wall_time_ns": 1,
        "normalized_at_ns": 2,
        "source_time_value": None,
        "source_time_unit": None,
        "publication_time_value": None,
        "publication_time_unit": None,
        "finalized": True,
        "quality_flags": [],
    }
    if evolved:
        result.update(lag_close=None, label_close=None, label_end_ns=None)
    return result


@pytest.mark.parametrize(
    "data",
    [b"", b"{", b"[]", b'{"a":1,"a":1}', b'{ "a": 1 }', b'{"a":NaN}'],
)
def test_message_requires_bounded_canonical_object(data: bytes) -> None:
    with pytest.raises(ImportError, match="analytical message"):
        primitive_document(data, 128)


def test_message_limit_and_exact_canonical_roundtrip() -> None:
    expected: dict[str, Any] = {
        "policy": {"lag_steps": 1},
        "rows": [],
        "cutoff": None,
    }
    data = canonical_json_bytes(expected)
    assert primitive_document(data, len(data)) == expected
    with pytest.raises(ImportError, match="analytical message"):
        primitive_document(data, len(data) - 1)


def test_clock_roundtrip_preserves_evidence_and_absence() -> None:
    assert clock_from(None) is None
    assert clock_from(asdict(DEFAULT_RECEIPT)) == DEFAULT_RECEIPT


@pytest.mark.parametrize("evolved", [False, True])
def test_row_roundtrip_keeps_explicit_optional_values(evolved: bool) -> None:
    result = row(evolved=evolved)
    result.update(
        source_time_value=1,
        source_time_unit="ms",
        publication_time_value=2,
        publication_time_unit="ns",
        quality_flags=["late"],
    )
    if evolved:
        result.update(lag_close="100.0", label_close="101.0", label_end_ns=3)
    assert validate_rows([result], evolved=evolved) == [result]
    assert validate_rows([], evolved=evolved) == []


@pytest.mark.parametrize("rows", [None, {}, (), [None], [{}], [row()] * 129])
def test_row_shape_and_count_are_closed(rows: object) -> None:
    with pytest.raises(ImportError, match="inspection"):
        validate_rows(rows, evolved=True)


@pytest.mark.parametrize(
    ("field", "value", "exception"),
    [
        ("close", True, ImportError),
        ("close", "NaN", ImportError),
        ("close", "Infinity", ImportError),
        ("close", "1" * 97, ImportError),
        ("lag_close", False, ImportError),
        ("label_close", "NaN", ImportError),
        ("label_end_ns", True, TypeError),
        ("offset", 1.5, TypeError),
        ("symbol", "../BTC", ValueError),
        ("raw_sha256", "f" * 63, ValueError),
        ("semantic_revision", "e" * 64, ImportError),
        ("semantic_revision", True, ImportError),
        ("semantic_revision", "sha256:bad", ValueError),
        ("finalized", 1, ImportError),
        ("quality_flags", "late", ImportError),
        ("quality_flags", ["late"] * 65, ImportError),
        ("quality_flags", ["../late"], ValueError),
        ("source_time_value", True, TypeError),
        ("source_time_unit", "minutes", ImportError),
        ("publication_time_unit", 1, ImportError),
    ],
)
def test_worker_values_cannot_be_coerced(
    field: str, value: object, exception: type[Exception]
) -> None:
    result = row()
    result[field] = value
    with pytest.raises(exception):
        validate_rows([result], evolved=True)


def test_row_schema_does_not_silently_drop_unknown_columns() -> None:
    result = row()
    result["future_schema_column"] = "untrusted"
    with pytest.raises(ImportError, match="fields"):
        validate_rows([result], evolved=True)
    legacy = row(evolved=False)
    legacy["lag_close"] = "100.0"
    with pytest.raises(ImportError, match="fields"):
        validate_rows([legacy], evolved=False)
    assert MAX_INSPECT_ROWS == 128
