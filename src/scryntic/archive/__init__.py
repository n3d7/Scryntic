"""Lazy public exports; metadata imports never load native decoders."""

from importlib import import_module
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from scryntic.archive.canonical import (
        INPUT_FINGERPRINT_ALGORITHM,
        ORDERED_INPUT_ALGORITHM,
        JsonObject,
        JsonValue,
        PublicationInput,
        canonical_json_bytes,
        input_fingerprint,
        normalized_projection,
        ordered_digest_from_pairs,
        ordered_input_digest,
        raw_projection,
        semantics_projection,
    )
    from scryntic.archive.model import ArchiveObject, ArchiveRole, Partition
    from scryntic.archive.normalized_parquet import (
        NORMALIZED_PARQUET_SCHEMA,
        ArchivedNormalization,
        NormalizedArchiveError,
        NormalizedParquetArchive,
        archived_normalization,
    )
    from scryntic.archive.raw_parquet import (
        PARQUET_CODEC,
        RAW_PARQUET_SCHEMA,
        ArchiveError,
        ParquetRawArchive,
    )
    from scryntic.archive.storage import ArchiveStorageError, ImmutableArchiveStorage

_EXPORTS = {
    name: "scryntic.archive." + module
    for module, names in (
        (
            "canonical",
            (
                "INPUT_FINGERPRINT_ALGORITHM",
                "ORDERED_INPUT_ALGORITHM",
                "JsonObject",
                "JsonValue",
                "PublicationInput",
                "canonical_json_bytes",
                "input_fingerprint",
                "normalized_projection",
                "ordered_digest_from_pairs",
                "ordered_input_digest",
                "raw_projection",
                "semantics_projection",
            ),
        ),
        ("model", ("ArchiveObject", "ArchiveRole", "Partition")),
        (
            "normalized_parquet",
            (
                "NORMALIZED_PARQUET_SCHEMA",
                "ArchivedNormalization",
                "NormalizedArchiveError",
                "NormalizedParquetArchive",
                "archived_normalization",
            ),
        ),
        (
            "raw_parquet",
            (
                "PARQUET_CODEC",
                "RAW_PARQUET_SCHEMA",
                "ArchiveError",
                "ParquetRawArchive",
            ),
        ),
        ("storage", ("ArchiveStorageError", "ImmutableArchiveStorage")),
    )
    for name in names
}

__all__ = [
    "ArchiveError",
    "ArchiveObject",
    "ArchiveRole",
    "ArchiveStorageError",
    "ArchivedNormalization",
    "INPUT_FINGERPRINT_ALGORITHM",
    "ImmutableArchiveStorage",
    "NORMALIZED_PARQUET_SCHEMA",
    "NormalizedArchiveError",
    "NormalizedParquetArchive",
    "ORDERED_INPUT_ALGORITHM",
    "PARQUET_CODEC",
    "Partition",
    "ParquetRawArchive",
    "RAW_PARQUET_SCHEMA",
    "archived_normalization",
    "JsonObject",
    "JsonValue",
    "PublicationInput",
    "canonical_json_bytes",
    "input_fingerprint",
    "normalized_projection",
    "ordered_digest_from_pairs",
    "ordered_input_digest",
    "raw_projection",
    "semantics_projection",
]


def __getattr__(name: str) -> Any:
    module = _EXPORTS.get(name)
    if module is None:
        raise AttributeError(name)
    value = getattr(import_module(module), name)
    globals()[name] = value
    return value
