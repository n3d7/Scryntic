"""Pure normalization contracts."""

from scryntic.normalization.candle import (
    FAKE_CANDLE_SCHEMA,
    NORMALIZER_VERSION,
    PAYLOAD_LIMIT,
    CandleNormalization,
    CandleSemantics,
    NormalizationRejection,
    ParsedFakeCandle,
    RejectionCode,
    RejectionField,
    UnsupportedSchema,
    canonical_decimal,
    inspect_fake_candle,
    normalize_parsed_candle,
)

__all__ = [
    "FAKE_CANDLE_SCHEMA",
    "NORMALIZER_VERSION",
    "PAYLOAD_LIMIT",
    "CandleNormalization",
    "CandleSemantics",
    "NormalizationRejection",
    "ParsedFakeCandle",
    "RejectionCode",
    "RejectionField",
    "UnsupportedSchema",
    "canonical_decimal",
    "inspect_fake_candle",
    "normalize_parsed_candle",
]
