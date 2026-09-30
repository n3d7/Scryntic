"""Approved data-only identifiers; importing these never loads native decoders."""

from scryntic.domain.identity import SchemaRef, Version

RAW_PARQUET_SCHEMA = SchemaRef("scryntic.raw-record.parquet", Version(1, 0))
NORMALIZED_PARQUET_SCHEMA = SchemaRef(
    "scryntic.normalization-outcome.parquet", Version(1, 0)
)
PARQUET_CODEC = "parquet-pyarrow-v1"
