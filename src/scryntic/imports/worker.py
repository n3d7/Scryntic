"""Decoder authority lives only inside the fixed restricted process."""

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

from scryntic.application.archive import ArchiveLimits
from scryntic.archive.canonical import (
    PublicationInput,
    canonical_json_bytes,
    ordered_input_digest,
    raw_projection,
)
from scryntic.archive.normalized_parquet import (
    archived_normalization,
    decode_normalized_path,
)
from scryntic.archive.raw_parquet import decode_raw_path
from scryntic.imports.protocol import ImportLimits, parse_request, result_bytes
from scryntic.publication.manifest import ManifestDocument


def validated_inputs(
    manifest: Path, raw_path: Path, normalized_path: Path, limits: ImportLimits
) -> tuple[ManifestDocument, tuple[PublicationInput, ...]]:
    """Revalidate accepted bytes for every restricted analytical operation."""
    document = parse_request(manifest.read_bytes(), limits)
    raw_descriptor, normalized_descriptor = document.body.objects
    bounds = ArchiveLimits(
        limits.max_records, limits.max_encoded_bytes, limits.max_decoded_bytes
    )
    raw = decode_raw_path(raw_path, raw_descriptor.sha256, bounds, os.geteuid())
    normalized = decode_normalized_path(
        normalized_path, normalized_descriptor, bounds, os.geteuid()
    )
    if len(raw) != len(normalized) or len(raw) != document.body.record_count:
        raise ValueError("Row count mismatch")
    if (
        sum(len(canonical_json_bytes({"raw": raw_projection(item)})) for item in raw)
        != raw_descriptor.decoded_bytes
    ):
        raise ValueError("Raw logical length mismatch")
    values = tuple(
        PublicationInput(record, archived.outcome, archived.semantics)
        for record, archived in zip(raw, normalized, strict=True)
    )
    if tuple(archived_normalization(value) for value in values) != normalized:
        raise ValueError("Provenance mismatch")
    if ordered_input_digest(values) != document.body.ordered_input_digest:
        raise ValueError("Ordered digest mismatch")
    body = document.body
    if (
        raw[0].identity != body.first_ingestion
        or raw[-1].identity != body.last_ingestion
    ):
        raise ValueError("Identity mismatch")
    for item in raw:
        utc_date = (
            (
                datetime(1970, 1, 1, tzinfo=UTC)
                + timedelta(microseconds=item.envelope.receipt.wall_time_ns // 1000)
            )
            .date()
            .isoformat()
        )
        if (
            item.identity.producer != body.producer
            or item.identity.epoch != body.epoch
            or item.envelope.source != body.partition.source
            or utc_date != body.partition.utc_date
        ):
            raise ValueError("Partition mismatch")
    return document, values


def run(arguments: list[str]) -> None:
    limits = ImportLimits(
        max_encoded_bytes=int(arguments[0]),
        max_decoded_bytes=int(arguments[1]),
        max_records=int(arguments[2]),
    )
    document, _ = validated_inputs(
        Path("/input/manifest"), Path("/input/raw"), Path("/input/normalized"), limits
    )
    os.write(1, result_bytes(document))
