"""Trusted F09 composition over existing durable production boundaries."""

from __future__ import annotations

import asyncio
import time
from contextlib import ExitStack
from dataclasses import dataclass
from typing import Literal

from scryntic.application.analysis import (
    ForecastAnalysis,
    ForecastArtifactRef,
    VerifiedDataset,
)
from scryntic.application.archive import ArchiveLimits
from scryntic.application.dto import BuildDatasetRequest
from scryntic.application.providers import ForecastRequest
from scryntic.application.sources import HistoryRequest, StreamRequest
from scryntic.archive.normalized_parquet import NormalizedParquetArchive
from scryntic.archive.raw_parquet import ParquetRawArchive
from scryntic.configuration.paths import Installation, validate_directories
from scryntic.dataset.snapshot import (
    CANDLE_RECIPE_SCHEMA,
    DatasetBuilder,
    DatasetReader,
)
from scryntic.domain.dataset import DatasetRef
from scryntic.forecast.artifact import ImmutableForecastStore
from scryntic.ingestion.sqlite_spool import DurableIngestor
from scryntic.normalization.runner import Blocked, NoWork, process_next
from scryntic.normalization.sqlite_store import NormalizationStore
from scryntic.providers.fake import PersistenceFakeProvider, TrendFakeProvider
from scryntic.publication.coordinator import (
    NoPublishableWork,
    PublicationCoordinator,
    WaitingForNormalization,
)
from scryntic.publication.manifest import ManifestStorage
from scryntic.publication.reader import PublicationReader
from scryntic.publication.sqlite_store import PublicationLimits, PublicationStore
from scryntic.sources.fake import (
    END_NS,
    FAKE_CANDLE_SCHEMA,
    FAKE_INSTRUMENT_ID,
    FIRST_START_NS,
    MAX_PAYLOAD_BYTES,
    DeterministicCandleSource,
    fake_instrument,
)

_PRODUCER = "f09-fake-producer"
_MAX_INPUTS = 4_096


@dataclass(frozen=True, slots=True)
class SliceResult:
    dataset: DatasetRef
    forecast: ForecastArtifactRef


def run_fake_slice(
    installation: Installation,
    *,
    epoch: str,
    code_revision: str,
    dependency_lock_sha256: str,
    provider_name: Literal["persistence", "trend"] = "persistence",
    horizon: int = 2,
) -> SliceResult:
    """Compose F05-F08 on disk, then forecast through the application port."""
    if provider_name not in ("persistence", "trend"):
        raise ValueError("Unknown trusted F09 fake provider")
    dataset = build_fake_dataset(
        installation,
        epoch=epoch,
        code_revision=code_revision,
        dependency_lock_sha256=dependency_lock_sha256,
    )
    reader = DatasetReader(installation)
    table = reader.read_table(dataset)
    if table.num_rows != dataset.row_count or not 1 <= table.num_rows <= 1_024:
        raise ValueError("F09 requires a bounded nonempty validated dataset")
    rows = table.select(
        ["start_ns", "interval_ns", "venue", "category", "symbol", "finalized"]
    ).to_pylist()
    interval = rows[0]["interval_ns"]
    identity = (rows[0]["venue"], rows[0]["category"], rows[0]["symbol"])
    if interval < 1 or any(
        row["interval_ns"] != interval
        or not row["finalized"]
        or (row["venue"], row["category"], row["symbol"]) != identity
        or row["start_ns"] != rows[0]["start_ns"] + index * interval
        for index, row in enumerate(rows)
    ):
        raise ValueError("F09 requires one contiguous finalized candle series")
    verified = VerifiedDataset(dataset, rows[-1]["start_ns"], interval)
    provider = (
        PersistenceFakeProvider(reader)
        if provider_name == "persistence"
        else TrendFakeProvider(reader)
    )
    request = ForecastRequest(
        request_id=f"f09-{dataset.manifest_sha256[:20]}-{provider_name}",
        dataset=dataset,
        target="close",
        frequency_ns=interval,
        horizon=horizon,
    )
    forecast = asyncio.run(
        ForecastAnalysis(provider, ImmutableForecastStore(installation)).run(
            request, verified
        )
    )
    return SliceResult(dataset, forecast)


def _publication_limits() -> PublicationLimits:
    objects = ArchiveLimits(64, 2_000_000, 2_000_000)
    return PublicationLimits(64, objects, objects, 200_000, 100)


def _catalog_hashes(
    reader: PublicationReader, limits: PublicationLimits
) -> tuple[str, ...]:
    hashes: list[str] = []
    anchor = None
    while True:
        page = reader.catalog_page(anchor, limits.max_manifests_per_read)
        if not page:
            break
        hashes.extend(value.document.manifest_hash for value in page)
        if len(hashes) > _MAX_INPUTS:
            raise ValueError("F09 publication catalog exceeds dataset input bound")
        anchor = page[-1].document.body.checkpoint_after
        if len(page) < limits.max_manifests_per_read:
            break
    if not hashes:
        raise RuntimeError("F09 source produced no exact publication manifests")
    return tuple(hashes)


def _ingest_fake_history(
    source: DeterministicCandleSource, ingestor: DurableIngestor
) -> None:
    cursor = None
    for _ in range(2):
        page = asyncio.run(
            source.fetch(
                HistoryRequest(
                    StreamRequest(FAKE_CANDLE_SCHEMA, FAKE_INSTRUMENT_ID),
                    FIRST_START_NS,
                    END_NS,
                    page_size=source.descriptor.max_page_records,
                    cursor=cursor,
                )
            )
        )
        for envelope in page.envelopes:
            ingestor.accept(envelope)
        cursor = page.next_cursor
        if cursor is None:
            break
    else:
        raise RuntimeError("Fake source paging exceeded its bound")


def _normalize_fake_history(
    ingestor: DurableIngestor, normalization: NormalizationStore
) -> None:
    for _ in range(64):
        outcome = process_next(
            ingestor,
            normalization,
            {FAKE_INSTRUMENT_ID: fake_instrument()},
            normalized_at_ns=time.time_ns(),
        )
        if isinstance(outcome, NoWork):
            break
        if isinstance(outcome, Blocked):
            raise RuntimeError("Fake source normalization is blocked")
    else:
        raise RuntimeError("F09 normalization exceeded its bound")


def _publish_fake_history(coordinator: PublicationCoordinator) -> None:
    for _ in range(64):
        published = coordinator.publish_next()
        if isinstance(published, NoPublishableWork):
            break
        if isinstance(published, WaitingForNormalization):
            raise RuntimeError("Fake source publication awaits normalization")
    else:
        raise RuntimeError("F09 publication exceeded its bound")


def build_fake_dataset(
    installation: Installation,
    *,
    epoch: str,
    code_revision: str,
    dependency_lock_sha256: str,
) -> DatasetRef:
    validate_directories(installation)
    source = DeterministicCandleSource()
    limits = _publication_limits()
    with ExitStack() as stack:
        try:
            ingestor = DurableIngestor(
                installation,
                producer=_PRODUCER,
                epoch=epoch,
                capacity=64,
                max_payload_bytes=MAX_PAYLOAD_BYTES,
            )
            stack.callback(ingestor.close)
            normalization = NormalizationStore(installation, producer=_PRODUCER)
            stack.callback(normalization.close)
            publication = PublicationStore(installation, producer=_PRODUCER)
            stack.callback(publication.close)

            _ingest_fake_history(source, ingestor)
            _normalize_fake_history(ingestor, normalization)
            coordinator = PublicationCoordinator(
                ingestor,
                normalization,
                publication,
                ParquetRawArchive(installation),
                NormalizedParquetArchive(installation),
                ManifestStorage(installation),
                installation,
                limits,
            )
            _publish_fake_history(coordinator)

            reader = PublicationReader(publication, installation, limits)
            manifests = _catalog_hashes(reader, limits)
            return (
                DatasetBuilder(
                    reader,
                    installation,
                    code_revision=code_revision,
                    dependency_lock_sha256=dependency_lock_sha256,
                )
                .build(BuildDatasetRequest(manifests, CANDLE_RECIPE_SCHEMA))
                .dataset
            )
        finally:
            asyncio.run(source.close())
