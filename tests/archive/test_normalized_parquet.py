"""Self-contained normalized-outcome Parquet behavior."""

import asyncio
from dataclasses import replace
from pathlib import Path

import pytest

from scryntic.application.archive import ArchiveLimits
from scryntic.archive.canonical import (
    PublicationInput,
    canonical_json_bytes,
    normalized_projection,
    semantics_projection,
)
from scryntic.archive.normalized_parquet import (
    NormalizedArchiveError,
    NormalizedParquetArchive,
    archived_normalization,
)
from scryntic.normalization.candle import (
    CandleNormalization,
    NormalizationRejection,
)
from scryntic.normalization.sqlite_store import OutcomeKind, ProcessingOutcome
from tests.archive.helpers import installation
from tests.normalization.helpers import normalization, raw_record


def _limits(
    *, records: int = 20, encoded: int = 2_000_000, decoded: int = 2_000_000
) -> ArchiveLimits:
    return ArchiveLimits(records, encoded, decoded)


def _accepted(kind: OutcomeKind, *, offset: int = 1) -> PublicationInput:
    record = raw_record(offset=offset)
    result = normalization(record)
    assert isinstance(result, CandleNormalization)
    outcome = ProcessingOutcome(
        identity=record.identity,
        predecessor=None,
        raw_sha256=record.envelope.content_sha256,
        kind=kind,
        semantic_revision=result.semantics.revision(),
        rejection_code=None,
        rejection_field=None,
        input_schema=result.input_schema,
        instrument_schema=result.instrument_schema,
        output_schema=result.candle.schema,
        instrument_revision=result.instrument_revision,
        normalizer_version=result.candle.normalizer_version,
        receipt=record.envelope.receipt,
        normalized_at_ns=result.candle.normalized_at_ns,
    )
    return PublicationInput(record, outcome, result.semantics)


def _rejected(*, offset: int = 1) -> PublicationInput:
    record = raw_record(offset=offset, payload=b"not-json")
    result = normalization(record)
    assert isinstance(result, NormalizationRejection)
    outcome = ProcessingOutcome(
        identity=record.identity,
        predecessor=None,
        raw_sha256=record.envelope.content_sha256,
        kind=OutcomeKind.REJECTED,
        semantic_revision=None,
        rejection_code=result.code,
        rejection_field=result.field,
        input_schema=result.input_schema,
        instrument_schema=result.instrument_schema,
        output_schema=result.output_schema,
        instrument_revision=result.instrument_revision,
        normalizer_version=result.normalizer_version,
        receipt=record.envelope.receipt,
        normalized_at_ns=result.normalized_at_ns,
    )
    return PublicationInput(record, outcome, None)


@pytest.mark.parametrize("kind", tuple(OutcomeKind))
def test_every_outcome_kind_round_trips(kind: OutcomeKind, tmp_path: Path) -> None:
    value = _rejected() if kind is OutcomeKind.REJECTED else _accepted(kind)
    archive = NormalizedParquetArchive(installation(tmp_path))

    descriptor = asyncio.run(archive.seal((value,), _limits()))

    assert asyncio.run(archive.read(descriptor, _limits())) == (
        archived_normalization(value),
    )


def test_exact_decimal_strings_nullable_times_and_provenance_round_trip(
    tmp_path: Path,
) -> None:
    value = _accepted(OutcomeKind.ACCEPTED)
    assert value.semantics is not None
    semantics = replace(
        value.semantics,
        open="100.1",
        high="101.25",
        low="99.9",
        close="100.75",
        volume="12.34",
        source_time=None,
        publication_time=None,
        quality_flags=("estimated", "late"),
    )
    adjusted = PublicationInput(
        value.raw,
        replace(value.outcome, semantic_revision=semantics.revision()),
        semantics,
    )
    archive = NormalizedParquetArchive(installation(tmp_path))

    descriptor = asyncio.run(archive.seal((adjusted,), _limits()))

    assert asyncio.run(archive.read(descriptor, _limits())) == (
        archived_normalization(adjusted),
    )


def test_seal_and_read_enforce_logical_limits_symmetrically(tmp_path: Path) -> None:
    value = _accepted(OutcomeKind.ACCEPTED)
    logical = len(
        canonical_json_bytes(
            {
                "outcome": normalized_projection(value.outcome, value.semantics),
                "semantics": semantics_projection(value.semantics),
            }
        )
    )
    archive = NormalizedParquetArchive(installation(tmp_path))

    descriptor = asyncio.run(archive.seal((value,), _limits(decoded=logical)))
    assert descriptor.decoded_bytes == logical
    assert asyncio.run(archive.read(descriptor, _limits(decoded=logical)))
    with pytest.raises(NormalizedArchiveError, match="limits"):
        asyncio.run(archive.seal((value,), _limits(decoded=logical - 1)))
    with pytest.raises(NormalizedArchiveError, match="limits"):
        asyncio.run(archive.read(descriptor, _limits(decoded=logical - 1)))


def test_reader_rejects_descriptor_identity_or_count_mismatch(tmp_path: Path) -> None:
    value = _rejected()
    archive = NormalizedParquetArchive(installation(tmp_path))
    descriptor = asyncio.run(archive.seal((value,), _limits()))

    with pytest.raises(NormalizedArchiveError, match="descriptor"):
        asyncio.run(
            archive.read(replace(descriptor, record_count=2), _limits(records=2))
        )
    with pytest.raises(NormalizedArchiveError, match="hash"):
        asyncio.run(archive.read(replace(descriptor, sha256="0" * 64), _limits()))


def test_normalized_rows_require_strictly_increasing_input_order(
    tmp_path: Path,
) -> None:
    archive = NormalizedParquetArchive(installation(tmp_path))

    with pytest.raises(NormalizedArchiveError, match="order"):
        asyncio.run(
            archive.seal(
                (
                    _accepted(OutcomeKind.ACCEPTED, offset=5),
                    _accepted(OutcomeKind.ACCEPTED, offset=2),
                ),
                _limits(),
            )
        )
