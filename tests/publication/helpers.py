"""Public F05/F06 fixtures for F07 publication tests."""

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from scryntic.archive.normalized_parquet import NormalizedParquetArchive
from scryntic.archive.raw_parquet import ParquetRawArchive
from scryntic.configuration.paths import Installation
from scryntic.domain.raw import IngestionId, RawRecord
from scryntic.domain.time import ClockSample
from scryntic.normalization.candle import CandleNormalization, CandleSemantics
from scryntic.normalization.sqlite_store import OutcomeKind, ProcessingOutcome
from scryntic.publication.coordinator import PublicationCoordinator
from scryntic.publication.manifest import ManifestStorage
from scryntic.publication.sqlite_store import PublicationLimits, PublicationStore
from tests.archive.helpers import installation
from tests.normalization.helpers import (
    DEFAULT_RECEIPT,
    FixedRawReader,
    normalization,
    raw_record,
)


class FixedNormalizationReader:
    def __init__(self, records: tuple[RawRecord, ...]) -> None:
        self.outcomes: dict[IngestionId, ProcessingOutcome] = {}
        self.semantics: dict[str, CandleSemantics] = {}
        predecessor: IngestionId | None = None
        revisions: set[str] = set()
        for record in records:
            result = normalization(record)
            assert isinstance(result, CandleNormalization)
            revision = result.semantics.revision()
            kind = (
                OutcomeKind.DUPLICATE if revision in revisions else OutcomeKind.ACCEPTED
            )
            revisions.add(revision)
            outcome = ProcessingOutcome(
                identity=record.identity,
                predecessor=predecessor,
                raw_sha256=record.envelope.content_sha256,
                kind=kind,
                semantic_revision=revision,
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
            self.outcomes[record.identity] = outcome
            self.semantics[revision] = result.semantics
            predecessor = record.identity

    def outcome(self, identity: IngestionId) -> ProcessingOutcome | None:
        return self.outcomes.get(identity)

    def observation(self, revision: str) -> CandleSemantics | None:
        return self.semantics.get(revision)


def limits(records: int = 10) -> PublicationLimits:
    from scryntic.application.archive import ArchiveLimits

    archive = ArchiveLimits(records, 2_000_000, 2_000_000)
    return PublicationLimits(records, archive, archive, 200_000, 100)


@dataclass(slots=True)
class CoordinatorBundle:
    root: Installation
    records: tuple[RawRecord, ...]
    raw_reader: FixedRawReader
    normalization_reader: FixedNormalizationReader
    store: PublicationStore
    coordinator: PublicationCoordinator

    def close(self) -> None:
        self.store.close()


def configured_coordinator(
    tmp_path: Path,
    *,
    offsets: tuple[int, ...] = (2,),
    epochs: tuple[str, ...] = ("epoch-a",),
    fault: Callable[[str], None] | None = None,
    batch_records: int = 10,
    receipts: tuple[ClockSample, ...] | None = None,
) -> CoordinatorBundle:
    if receipts is not None and len(receipts) != len(offsets):
        raise ValueError("Each fixture input needs a receipt")
    records = tuple(
        raw_record(
            offset=offset,
            epoch=epoch,
            receipt=receipts[index] if receipts is not None else DEFAULT_RECEIPT,
        )
        for index, (offset, epoch) in enumerate(zip(offsets, epochs, strict=True))
    )
    root = installation(tmp_path)
    raw_reader = FixedRawReader(records)
    normalized = FixedNormalizationReader(records)
    store = PublicationStore(root, producer="collector-a", fault=fault)
    coordinator = PublicationCoordinator(
        raw_reader,
        normalized,
        store,
        ParquetRawArchive(
            root, fault=None if fault is None else lambda stage: fault(f"raw_{stage}")
        ),
        NormalizedParquetArchive(
            root,
            fault=None if fault is None else lambda stage: fault(f"normalized_{stage}"),
        ),
        ManifestStorage(root, fault=fault),
        root,
        limits(batch_records),
        fault=fault,
    )
    return CoordinatorBundle(root, records, raw_reader, normalized, store, coordinator)
