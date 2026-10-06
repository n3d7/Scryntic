"""Closed, canonical and byte-bounded primitive launcher messages."""

import json
from typing import Any

from scryntic.archive.canonical import canonical_json_bytes
from scryntic.model_worker.profile import WIRE_BYTES, IsolationError


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise IsolationError("Duplicate worker field")
        result[key] = value
    return result


def document(data: bytes, fields: str) -> dict[str, Any]:
    try:
        if type(data) is not bytes or not 0 < len(data) <= WIRE_BYTES:
            raise ValueError
        value = json.loads(data.decode("utf-8", "strict"), object_pairs_hook=_pairs)
        if (
            type(value) is not dict
            or set(value) != set(fields.split())
            or canonical_json_bytes(value) != data
        ):
            raise ValueError
        return value
    except (ValueError, TypeError, RecursionError, IsolationError):
        raise IsolationError("Invalid bounded worker message") from None
