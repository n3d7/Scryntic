"""F07 restart reconciliation at externally durable boundaries."""

import subprocess
import sys
from pathlib import Path

import pytest

from scryntic.archive.normalized_parquet import NormalizedParquetArchive
from scryntic.archive.raw_parquet import ParquetRawArchive
from scryntic.ingestion.sqlite_spool import DurableIngestor
from scryntic.publication.coordinator import PublicationCoordinator, Published
from scryntic.publication.manifest import ManifestStorage, parse_manifest
from scryntic.publication.sqlite_store import PublicationStore
from tests.archive.helpers import installation
from tests.normalization.helpers import envelope
from tests.publication.helpers import FixedNormalizationReader, limits


@pytest.mark.parametrize(
    "boundary",
    (
        "after_reservation_commit",
        "after_raw_object",
        "after_normalized_object",
        "after_prepare_commit",
        "after_manifest_file_fsync",
        "after_manifest_install",
        "after_manifest_directory_fsync",
        "after_catalog_commit",
    ),
)
def test_kill_restart_recovers_exact_prepared_publication(
    tmp_path: Path, boundary: str
) -> None:
    crashed = subprocess.run(
        [
            sys.executable,
            "-m",
            "tests.publication.crash_publisher",
            str(tmp_path),
            "publish",
            boundary,
        ],
        check=False,
    )
    assert crashed.returncode == 73
    recovered = subprocess.run(
        [
            sys.executable,
            "-m",
            "tests.publication.crash_publisher",
            str(tmp_path),
            "recover",
        ],
        check=False,
    )
    assert recovered.returncode == 0

    root = installation(tmp_path)
    with PublicationStore(root, producer="collector-a") as store:
        entries = store.catalog_page(None, 10)
        assert len(entries) == 1
        entry = entries[0]
        assert store.pending() is None
        assert store.status().checkpoint == entry.checkpoint_after
        external = ManifestStorage(root).read_exact(
            entry.ref, limits().max_manifest_bytes
        )
        assert external == entry.manifest_bytes
        assert parse_manifest(external, limits().max_manifest_bytes).ref == entry.ref


def test_publication_preserves_every_real_f05_spool_record(tmp_path: Path) -> None:
    root = installation(tmp_path)
    with DurableIngestor(
        root,
        producer="collector-a",
        epoch="epoch-a",
        capacity=4,
        max_payload_bytes=8_192,
    ) as ingestor:
        records = (ingestor.accept(envelope()), ingestor.accept(envelope()))
        normalization = FixedNormalizationReader(records)
        with PublicationStore(root, producer="collector-a") as store:
            coordinator = PublicationCoordinator(
                ingestor,
                normalization,
                store,
                ParquetRawArchive(root),
                NormalizedParquetArchive(root),
                ManifestStorage(root),
                root,
                limits(),
            )
            result = coordinator.publish_next()
            assert isinstance(result, Published)
            assert result.document.body.record_count == 2
            assert ingestor.records_after(0, limit=10) == records
