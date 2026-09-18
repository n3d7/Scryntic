"""The supported normalization package boundary excludes storage internals."""

from importlib import import_module

import scryntic.normalization as normalization


def test_supported_package_imports_resolve_to_the_documented_api() -> None:
    expected = {
        "candle": (
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
        ),
        "runner": (
            "Blocked",
            "NoWork",
            "Processed",
            "RawRecordReader",
            "process_next",
        ),
        "sqlite_store": (
            "BarrierReason",
            "NormalizationError",
            "NormalizationStatus",
            "NormalizationStore",
            "NormalizerOwned",
            "OutcomeKind",
            "ProcessingBarrier",
            "ProcessingOutcome",
        ),
    }
    names = [name for exports in expected.values() for name in exports]
    assert sorted(normalization.__all__) == sorted(names)
    for module_name, exports in expected.items():
        implementation = import_module(f"scryntic.normalization.{module_name}")
        for name in exports:
            # Exercise the same package lookup used by an explicit from-import.
            package = __import__("scryntic.normalization", fromlist=[name])
            assert getattr(package, name) is getattr(implementation, name)


def test_package_does_not_export_storage_or_decoder_capabilities() -> None:
    private_names = {
        "Connection",
        "Cursor",
        "Path",
        "sqlite3",
        "_DATABASE_NAME",
        "_LOCK_NAME",
        "_CREATE_OBSERVATIONS",
        "_IntegerToken",
        "_FloatToken",
        "_parse_int",
        "_parse_float",
        "_decode_observation",
        "_decode_outcome",
        "_connection",
        "_resources",
        "crash_normalizer",
    }
    assert private_names.isdisjoint(normalization.__all__)
    assert all(not hasattr(normalization, name) for name in private_names)
    assert all(not name.startswith("_") for name in normalization.__all__)
