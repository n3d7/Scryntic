"""Pure normalization contracts."""

from scryntic.normalization.candle import (
    FAKE_CANDLE_SCHEMA,
    NORMALIZER_VERSION,
    PAYLOAD_LIMIT,
    NormalizationRejection,
    ParsedFakeCandle,
    RejectionCode,
    RejectionField,
    UnsupportedSchema,
    inspect_fake_candle,
)

__all__ = [
    "FAKE_CANDLE_SCHEMA",
    "NORMALIZER_VERSION",
    "PAYLOAD_LIMIT",
    "NormalizationRejection",
    "ParsedFakeCandle",
    "RejectionCode",
    "RejectionField",
    "UnsupportedSchema",
    "inspect_fake_candle",
]
