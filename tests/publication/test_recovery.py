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
from tests.publication.faults import BOUNDARIES
from tests.publication.helpers import (
    FixedNormalizationReader,
    configured_coordinator,
    limits,
)


@pytest.mark.parametrize(
    "scenario,boundary",
    [
        (scenario, stage)
        for scenario in ("genesis", "continuation", "new_epoch")
        for stage in BOUNDARIES
    ],
)
def test_kill_restart_recovers_exact_prepared_publication(
    tmp_path: Path, boundary: str, scenario: str
) -> None:
    crashed = subprocess.run(
        [
            sys.executable,
            "-m",
            "tests.publication.crash_publisher",
            str(tmp_path),
            "publish",
            boundary,
            scenario,
        ],
        check=False,
    )
    assert crashed.returncode == 73
    root = installation(tmp_path)
    with PublicationStore(root, producer="collector-a") as store:
        pending = store.pending()
        exact_prepared = None if pending is None else pending.manifest_bytes
    recovered = subprocess.run(
        [
            sys.executable,
            "-m",
            "tests.publication.crash_publisher",
            str(tmp_path),
            "recover",
            "",
            scenario,
        ],
        check=False,
    )
    assert recovered.returncode == 0

    root = installation(tmp_path)
    with PublicationStore(root, producer="collector-a") as store:
        entries = store.catalog_page(None, 10)
        assert len(entries) == (1 if scenario == "genesis" else 2)
        entry = entries[-1]
        assert store.pending() is None
        assert store.status().checkpoint == entry.checkpoint_after
        external = ManifestStorage(root).read_exact(
            entry.ref, limits().max_manifest_bytes
        )
        assert external == entry.manifest_bytes
        if exact_prepared is not None:
            assert external == exact_prepared
        assert parse_manifest(external, limits().max_manifest_bytes).ref == entry.ref
        if len(entries) == 2:
            previous = entries[0]
            assert entry.checkpoint_before == previous.checkpoint_after
            assert (
                ManifestStorage(root).read_exact(
                    previous.ref, limits().max_manifest_bytes
                )
                == previous.manifest_bytes
            )
            assert entry.ref.sequence == (2 if scenario == "continuation" else 1)
            assert entry.previous_manifest_hash == (
                previous.ref.manifest_hash if scenario == "continuation" else "0" * 64
            )


@pytest.mark.parametrize(
    "failure",
    [
        None,
        "after_raw_object",
        "after_prepare_commit",
        "after_manifest_install",
        "before_catalog_commit",
    ],
)
def test_publication_preserves_every_real_f05_spool_record(
    tmp_path: Path, failure: str | None
) -> None:
    root = installation(tmp_path)
    injected = False

    def fault(stage: str) -> None:
        nonlocal injected
        if failure == stage and not injected:
            injected = True
            raise OSError("injected publication failure")

    with DurableIngestor(
        root,
        producer="collector-a",
        epoch="epoch-a",
        capacity=4,
        max_payload_bytes=8_192,
    ) as ingestor:
        records = (ingestor.accept(envelope()), ingestor.accept(envelope()))
        normalization = FixedNormalizationReader(records)
        with PublicationStore(root, producer="collector-a", fault=fault) as store:
            coordinator = PublicationCoordinator(
                ingestor,
                normalization,
                store,
                ParquetRawArchive(root),
                NormalizedParquetArchive(root),
                ManifestStorage(root, fault=fault),
                root,
                limits(),
                fault=fault,
            )
            if failure is not None:
                with pytest.raises((OSError, RuntimeError)):
                    coordinator.publish_next()
                assert injected
                assert ingestor.records_after(0, limit=10) == records
            result = coordinator.publish_next()
            assert isinstance(result, Published)
            assert result.document.body.record_count == 2
            assert ingestor.records_after(0, limit=10) == records


@pytest.mark.parametrize(
    "scenario,boundary",
    [
        (scenario, stage)
        for scenario in ("genesis", "continuation", "new_epoch")
        for stage in BOUNDARIES
    ],
)
def test_io_failure_retries_without_changing_prepared_bytes(
    tmp_path: Path, boundary: str, scenario: str
) -> None:
    armed = False
    injected = False

    def fault(stage: str) -> None:
        nonlocal injected
        if armed and not injected and stage == boundary:
            injected = True
            raise OSError("simulated storage failure")

    bundle = configured_coordinator(
        tmp_path,
        offsets=(2,) if scenario == "genesis" else (2, 5),
        epochs=("epoch-a",)
        if scenario == "genesis"
        else ("epoch-a", "epoch-a" if scenario == "continuation" else "epoch-b"),
        batch_records=1,
        fault=fault,
    )
    try:
        if scenario != "genesis":
            previous = bundle.coordinator.publish_next()
            assert isinstance(previous, Published)
        armed = True
        with pytest.raises((OSError, RuntimeError)):
            bundle.coordinator.publish_next()
        assert injected
        pending = bundle.store.pending()
        exact = None if pending is None else pending.manifest_bytes
        bundle.coordinator.recover()
        if bundle.store.status().checkpoint != bundle.records[-1].identity:
            bundle.coordinator.publish_next()
        entries = bundle.store.catalog_page(None, 10)
        assert len(entries) == len(bundle.records)
        entry = entries[-1]
        assert bundle.store.pending() is None
        assert entry.checkpoint_after == bundle.records[-1].identity
        assert bundle.raw_reader.records == bundle.records
        if exact is not None:
            assert entry.manifest_bytes == exact
        assert (
            ManifestStorage(bundle.root).read_exact(
                entry.ref, limits().max_manifest_bytes
            )
            == entry.manifest_bytes
        )
        before = bundle.store.status()
        bundle.coordinator.recover()
        assert bundle.store.status() == before
    finally:
        bundle.close()


def test_uncertain_orphans_survive_repeated_recovery_failure(tmp_path: Path) -> None:
    def fault(stage: str) -> None:
        if stage == "after_prepare_commit":
            raise OSError("stop")

    bundle = configured_coordinator(tmp_path, fault=fault)
    try:
        with pytest.raises(OSError):
            bundle.coordinator.publish_next()
        pending = bundle.store.pending()
        assert pending is not None
        archive = bundle.root.state_dir / "archive"
        orphan = archive / "staging" / ".uncertain.parquet"
        orphan.write_bytes(b"uncertain interrupted file")
        uncertain_manifest = archive / "manifest-staging" / ".uncertain.json"
        uncertain_manifest.write_bytes(b"incomplete manifest input")
        objects = tuple((archive / "objects").rglob("*.parquet"))
        original = bundle.raw_reader.records
        bundle.raw_reader.records = ()
        for _ in range(2):
            with pytest.raises(RuntimeError):
                bundle.coordinator.recover()
            assert bundle.store.pending() == pending
            assert bundle.store.status().checkpoint is None
            assert all(path.exists() for path in objects)
            assert orphan.read_bytes() == b"uncertain interrupted file"
            assert uncertain_manifest.read_bytes() == b"incomplete manifest input"
        bundle.raw_reader.records = original
        bundle.coordinator.recover()
        assert orphan.exists()
        assert uncertain_manifest.exists()
    finally:
        bundle.close()
