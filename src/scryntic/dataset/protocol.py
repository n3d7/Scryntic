"""Closed primitive analytical protocol, independently validated at both ends."""

import json
from dataclasses import asdict
from decimal import Decimal
from typing import Any, cast

from scryntic.archive.canonical import canonical_json_bytes
from scryntic.configuration.clock import ClockLimits
from scryntic.domain.time import ClockSample, TimeQuality
from scryntic.domain.validation import digest, identifier, integer
from scryntic.imports.protocol import ImportError

MAX_RESULT_BYTES = 64 * 1024 * 1024
MAX_DATASET_BYTES = 32 * 1024 * 1024
MAX_PROVENANCE_BYTES = 24 * 1024 * 1024
MAX_REQUEST_BYTES = 1024 * 1024
MAX_INSPECT_ROWS = 128


def primitive_document(data: bytes, limit: int) -> dict[str, Any]:
    try:
        if type(data) is not bytes or not 0 < len(data) <= limit:
            raise ValueError("Message limit")
        value = json.loads(data)
        if type(value) is not dict or canonical_json_bytes(value) != data:
            raise ValueError("Noncanonical primitive message")
        return value
    except Exception:
        raise ImportError("Invalid analytical message") from None


def clock_from(value: dict[str, Any] | None) -> ClockSample | None:
    if value is None:
        return None
    return ClockSample(
        value["wall_time_ns"],
        value["monotonic_ns"],
        value["session_id"],
        TimeQuality(**value["quality"]),
    )


def environment(
    code_revision: str, lock_digest: str, limits: ClockLimits
) -> dict[str, Any]:
    return {
        "code_revision": code_revision,
        "dependency_lock_sha256": lock_digest,
        "clock_limits": asdict(limits),
    }


def validate_rows(rows: object, *, evolved: bool) -> list[dict[str, Any]]:
    """Check the closed inspection result; parser trust never crosses IPC."""
    if type(rows) is not list or len(rows) > MAX_INSPECT_ROWS:
        raise ImportError("Invalid inspection rows")
    checked = cast(list[dict[str, Any]], rows)
    for row in checked:
        _validate_row(row, evolved)
    return checked


def _validate_row(row: dict[str, Any], evolved: bool) -> None:
    text_fields = {"venue", "category", "symbol", "volume_unit", "producer", "epoch"}
    hashes = {
        "manifest_sha256",
        "raw_object_sha256",
        "normalized_object_sha256",
        "raw_sha256",
    }
    decimals = {"open", "high", "low", "close", "volume"}
    integers = {
        "start_ns",
        "interval_ns",
        "offset",
        "input_ordinal",
        "receipt_wall_time_ns",
        "normalized_at_ns",
    }
    nullable = {
        "source_time_value",
        "source_time_unit",
        "publication_time_value",
        "publication_time_unit",
    }
    fields = (
        text_fields
        | hashes
        | decimals
        | integers
        | nullable
        | {"finalized", "quality_flags", "semantic_revision"}
    )
    if evolved:
        fields |= {"lag_close", "label_close", "label_end_ns"}
    if type(row) is not dict or set(row) != fields:
        raise ImportError("Invalid inspection fields")
    for name in text_fields:
        identifier(row[name])
    for name in hashes:
        digest(row[name])
    revision = row["semantic_revision"]
    if type(revision) is not str or not revision.startswith("sha256:"):
        raise ImportError("Invalid inspection revision")
    digest(revision[7:])
    for name in integers:
        integer(row[name])
    for name in decimals:
        _decimal(row[name])
    _optional_columns(row, evolved)
    if (
        type(row["finalized"]) is not bool
        or type(row["quality_flags"]) is not list
        or len(row["quality_flags"]) > 64
    ):
        raise ImportError("Invalid inspection quality")
    for flag in row["quality_flags"]:
        identifier(flag)


def _decimal(value: object) -> None:
    if type(value) is not str or len(value) > 96 or not Decimal(value).is_finite():
        raise ImportError("Invalid inspection decimal")


def _optional_columns(row: dict[str, Any], evolved: bool) -> None:
    for name in ("source_time_value", "publication_time_value"):
        if row[name] is not None:
            integer(row[name])
    for name in ("source_time_unit", "publication_time_unit"):
        if row[name] is not None and row[name] not in ("s", "ms", "us", "ns"):
            raise ImportError("Invalid inspection time unit")
    if evolved:
        for name in ("lag_close", "label_close"):
            if row[name] is not None:
                _decimal(row[name])
        if row["label_end_ns"] is not None:
            integer(row["label_end_ns"])
