"""Small explicit checks shared by owned contracts; not a deserialization layer."""

import re
from decimal import Decimal


def identifier(value: str) -> None:
    if not isinstance(value, str) or not re.fullmatch(
        r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,255}", value
    ):
        raise ValueError("Expected a bounded opaque identifier")


def integer(value: int, minimum: int | None = None) -> None:
    if type(value) is not int:
        raise TypeError("Expected an integer, not a coerced number")
    if minimum is not None and value < minimum:
        raise ValueError("Integer below allowed minimum")


def decimal(value: Decimal, *, positive: bool = False) -> None:
    if not isinstance(value, Decimal):
        raise TypeError("Expected an exact Decimal")
    if not value.is_finite() or value < 0 or (positive and value == 0):
        raise ValueError(
            "Expected a finite nonnegative decimal (positive where required)"
        )


def digest(value: str) -> None:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
        raise ValueError("Expected a lowercase SHA-256 digest")


def immutable_tuple(value: tuple[object, ...], maximum: int) -> None:
    if type(value) is not tuple:
        raise TypeError("Expected an immutable tuple")
    if len(value) > maximum:
        raise ValueError("Too many contract items")
