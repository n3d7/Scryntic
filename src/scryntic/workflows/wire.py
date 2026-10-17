"""Bounded presentation encoding outside pure application contracts."""

import json
from collections.abc import Mapping

from scryntic.application.operations import (
    MAX_RESULT_BYTES,
    ApplicationError,
    ApplicationResult,
    ErrorCode,
    Operation,
)


def _encode(fields: Mapping[str, object]) -> bytes:
    try:
        encoded = json.dumps(
            dict(fields), sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
    except (TypeError, ValueError, OverflowError, RecursionError):
        raise ApplicationError(ErrorCode.FAILED) from None
    if len(encoded) > MAX_RESULT_BYTES:
        raise ApplicationError(ErrorCode.INCOMPLETE)
    return encoded


def result(
    operation: Operation, fields: Mapping[str, object], *, complete: bool = True
) -> ApplicationResult:
    value = ApplicationResult(operation, _encode(fields), complete)
    encode_result(value)
    return value


def encode_result(value: ApplicationResult) -> bytes:
    try:
        fields = json.loads(value.payload)
        if not isinstance(fields, dict) or _encode(fields) != value.payload:
            raise ValueError("noncanonical result")
    except (ValueError, TypeError, RecursionError):
        raise ApplicationError(ErrorCode.FAILED) from None
    return _encode(
        {
            "version": 1,
            "ok": True,
            "operation": value.operation.value,
            "complete": value.complete,
            "data": fields,
        }
    )


def encode_error(error: ApplicationError, operation: Operation | None = None) -> bytes:
    return _encode(
        {
            "version": 1,
            "ok": False,
            "operation": operation.value if operation else None,
            "error": {"code": error.code.value, "message": str(error)},
        }
    )
