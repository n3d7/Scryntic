"""F11 conflicting durable evidence must never fabricate publication progress."""

import os
import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest

from scryntic.application.archive import ArchiveLimits, RawSegment
from scryntic.archive.canonical import PublicationInput
from scryntic.archive.model import ArchiveObject
from scryntic.archive.normalized_parquet import NormalizedParquetArchive
from scryntic.archive.raw_parquet import ParquetRawArchive
from scryntic.archive.storage import ImmutableArchiveStorage
from scryntic.domain.raw import RawRecord
from scryntic.publication.coordinator import PublicationCoordinator, Published
from scryntic.publication.manifest import (
    ManifestError,
    ManifestStorage,
    parse_manifest,
    prepare_manifest,
)
from scryntic.publication.sqlite_store import PublicationError, PublicationStore
from tests.publication.helpers import configured_coordinator, limits


def test_catalog_without_checkpoint_is_not_genesis(tmp_path: Path) -> None:
    bundle = configured_coordinator(tmp_path)
    result = bundle.coordinator.publish_next()
    assert isinstance(result, Published)
    bundle.close()
    with sqlite3.connect(bundle.root.state_dir / "publication.sqlite3") as connection:
        connection.execute("DELETE FROM publication_checkpoint")
    with pytest.raises(PublicationError):
        PublicationStore(bundle.root, producer="collector-a")
    assert (
        ManifestStorage(bundle.root).read_exact(
            result.manifest, limits().max_manifest_bytes
        )
        == result.document_bytes
    )


def test_prepared_hash_column_must_match_exact_bytes(tmp_path: Path) -> None:
    def stop(stage: str) -> None:
        if stage == "after_prepare_commit":
            raise RuntimeError("stop")

    bundle = configured_coordinator(tmp_path, fault=stop)
    with pytest.raises(RuntimeError, match="stop"):
        bundle.coordinator.publish_next()
    bundle.close()
    with sqlite3.connect(bundle.root.state_dir / "publication.sqlite3") as connection:
        connection.execute(
            "UPDATE pending_publication SET manifest_hash=?", ("a" * 64,)
        )
    with pytest.raises(PublicationError):
        PublicationStore(bundle.root, producer="collector-a")


def test_prepared_normalized_object_matches_complete_reserved_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle = configured_coordinator(tmp_path)
    actual = NormalizedParquetArchive(bundle.root)

    async def changed(
        values: tuple[PublicationInput, ...], archive_limits: ArchiveLimits
    ) -> ArchiveObject:
        altered = tuple(
            replace(value, outcome=replace(value.outcome, normalized_at_ns=999))
            for value in values
        )
        return await actual.seal(altered, archive_limits)

    monkeypatch.setattr(bundle.coordinator._normalized_archive, "seal", changed)
    try:
        with pytest.raises(PublicationError, match="normalized"):
            bundle.coordinator.publish_next()
        assert bundle.store.status().checkpoint is None
        assert bundle.store.pending() is not None
        assert not tuple(
            (bundle.root.state_dir / "archive" / "manifests").rglob("*.json")
        )
    finally:
        bundle.close()


@pytest.mark.parametrize(
    "field,value",
    [("codec", "unapproved-codec"), ("encoded_bytes", 1), ("decoded_bytes", 1)],
)
def test_raw_descriptor_cannot_misrepresent_object(
    tmp_path: Path, field: str, value: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle = configured_coordinator(tmp_path)
    seal = bundle.coordinator._raw_archive.seal

    async def changed(
        records: tuple[RawRecord, ...], archive_limits: ArchiveLimits
    ) -> RawSegment:
        segment = await seal(records, archive_limits)
        if field == "codec":
            return replace(segment, codec=str(value))
        if field == "encoded_bytes":
            return replace(segment, encoded_bytes=1)
        return replace(segment, decoded_bytes=1)

    monkeypatch.setattr(bundle.coordinator._raw_archive, "seal", changed)
    try:
        with pytest.raises(PublicationError):
            bundle.coordinator.publish_next()
        assert bundle.store.status().checkpoint is None
    finally:
        bundle.close()


def test_unknown_external_history_cannot_be_adopted_or_skipped(tmp_path: Path) -> None:
    bundle = configured_coordinator(tmp_path)
    values = bundle.coordinator._select()
    assert isinstance(values, tuple)
    pending = bundle.coordinator._reserve(values)
    prepared = bundle.coordinator._seal_and_prepare(pending, values)
    assert prepared.manifest_bytes is not None
    assert prepared.manifest_ref is not None
    body = parse_manifest(prepared.manifest_bytes, limits().max_manifest_bytes).body
    unrelated = prepare_manifest(
        replace(
            body,
            epoch="epoch-other",
            checkpoint_after=replace(body.checkpoint_after, epoch="epoch-other"),
            first_ingestion=replace(body.first_ingestion, epoch="epoch-other"),
            last_ingestion=replace(body.last_ingestion, epoch="epoch-other"),
        )
    )
    storage = ManifestStorage(bundle.root)
    storage.install_exact(unrelated, parse_manifest(unrelated, len(unrelated)).ref)
    try:
        with pytest.raises(PublicationError, match="history"):
            bundle.coordinator.recover()
        assert bundle.store.pending() == prepared
        assert bundle.store.status().checkpoint is None
    finally:
        bundle.close()


def test_committed_manifest_missing_is_not_recreated(tmp_path: Path) -> None:
    bundle = configured_coordinator(tmp_path)
    result = bundle.coordinator.publish_next()
    assert isinstance(result, Published)
    storage = ManifestStorage(bundle.root)
    storage.manifest_path(result.manifest).unlink()
    try:
        with pytest.raises((PublicationError, ManifestError)):
            bundle.coordinator.recover()
        assert bundle.store.status().checkpoint == result.checkpoint
        assert not storage.manifest_path(result.manifest).exists()
    finally:
        bundle.close()


def test_pending_input_ordinals_cannot_have_holes(tmp_path: Path) -> None:
    def stop(stage: str) -> None:
        if stage == "after_reservation_commit":
            raise RuntimeError("stop")

    bundle = configured_coordinator(tmp_path, fault=stop)
    with pytest.raises(RuntimeError):
        bundle.coordinator.publish_next()
    bundle.close()
    with sqlite3.connect(bundle.root.state_dir / "publication.sqlite3") as connection:
        connection.execute("UPDATE pending_inputs SET ordinal=4")
    with pytest.raises(PublicationError):
        PublicationStore(bundle.root, producer="collector-a")


def test_rolled_back_local_catalog_cannot_reset_external_history(
    tmp_path: Path,
) -> None:
    bundle = configured_coordinator(tmp_path)
    result = bundle.coordinator.publish_next()
    assert isinstance(result, Published)
    bundle.close()
    with sqlite3.connect(bundle.root.state_dir / "publication.sqlite3") as connection:
        connection.execute("DELETE FROM publication_checkpoint")
        connection.execute("DELETE FROM publication_catalog")
    with PublicationStore(bundle.root, producer="collector-a") as store:
        restarted = PublicationCoordinator(
            bundle.raw_reader,
            bundle.normalization_reader,
            store,
            ParquetRawArchive(bundle.root),
            NormalizedParquetArchive(bundle.root),
            ManifestStorage(bundle.root),
            bundle.root,
            limits(),
        )
        with pytest.raises(PublicationError, match="history"):
            restarted.publish_next()
        assert store.pending() is None
        assert store.status().checkpoint is None
    assert (
        ManifestStorage(bundle.root).read_exact(
            result.manifest, limits().max_manifest_bytes
        )
        == result.document_bytes
    )


@pytest.mark.parametrize("target", ["raw", "normalized", "manifest"])
@pytest.mark.parametrize("operation", ["fsync", "link"])
def test_real_durability_syscall_error_preserves_reservation(
    tmp_path: Path, target: str, operation: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle = configured_coordinator(tmp_path)
    original_fsync = os.fsync
    original_link = os.link
    injected = False

    def selected(path: str) -> bool:
        return f".{target}-" in path

    def fail_fsync(fd: int) -> None:
        nonlocal injected
        if not injected and selected(os.readlink(f"/proc/self/fd/{fd}")):
            injected = True
            raise OSError("fsync failed")
        original_fsync(fd)

    def fail_link(
        source: str | bytes | os.PathLike[str] | os.PathLike[bytes],
        destination: str | bytes | os.PathLike[str] | os.PathLike[bytes],
        *,
        follow_symlinks: bool = True,
    ) -> None:
        nonlocal injected
        if not injected and selected(os.fsdecode(source)):
            injected = True
            raise OSError("link failed")
        original_link(source, destination, follow_symlinks=follow_symlinks)

    monkeypatch.setattr(
        os, operation, fail_fsync if operation == "fsync" else fail_link
    )
    try:
        with pytest.raises(RuntimeError):
            bundle.coordinator.publish_next()
        assert injected
        assert bundle.store.pending() is not None
        assert bundle.store.status().checkpoint is None
        bundle.coordinator.recover()
        assert bundle.store.status().checkpoint == bundle.records[-1].identity
    finally:
        bundle.close()


def test_catalog_cannot_hide_foreign_rows_by_filtering_producer(tmp_path: Path) -> None:
    bundle = configured_coordinator(tmp_path)
    assert isinstance(bundle.coordinator.publish_next(), Published)
    bundle.close()
    with sqlite3.connect(bundle.root.state_dir / "publication.sqlite3") as connection:
        connection.execute("DELETE FROM publication_checkpoint")
        connection.execute(
            "UPDATE publication_catalog SET producer='other-producer', after_producer='other-producer'"
        )
    with pytest.raises(PublicationError):
        PublicationStore(bundle.root, producer="collector-a")


@pytest.mark.parametrize("role", [0, 1])
def test_missing_committed_object_blocks_recovery_without_rewriting_history(
    tmp_path: Path, role: int
) -> None:
    bundle = configured_coordinator(tmp_path)
    result = bundle.coordinator.publish_next()
    assert isinstance(result, Published)
    descriptor = result.document.body.objects[role]
    object_path = (
        bundle.root.state_dir
        / "archive"
        / "objects"
        / "sha256"
        / descriptor.sha256[:2]
        / f"{descriptor.sha256}.parquet"
    )
    object_path.unlink()
    try:
        with pytest.raises(PublicationError):
            bundle.coordinator.recover()
        assert bundle.store.status().checkpoint == result.checkpoint
        assert (
            ManifestStorage(bundle.root).read_exact(
                result.manifest, limits().max_manifest_bytes
            )
            == result.document_bytes
        )
        assert not object_path.exists()
    finally:
        bundle.close()


def test_retry_resyncs_existing_object_directory_parent_before_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    armed = False
    injected = False

    def fault(stage: str) -> None:
        nonlocal injected
        if armed and not injected and stage == "raw_before_archive_directory_fsync":
            injected = True
            raise OSError("directory created but parent not synced")

    bundle = configured_coordinator(tmp_path, fault=fault)
    synced: list[Path] = []
    original_sync = ImmutableArchiveStorage._fsync_directory

    def track(path: Path) -> None:
        synced.append(path)
        original_sync(path)

    try:
        armed = True
        with pytest.raises(RuntimeError):
            bundle.coordinator.publish_next()
        assert injected
        parent = bundle.root.state_dir / "archive" / "objects" / "sha256"
        assert tuple(parent.iterdir())
        monkeypatch.setattr(
            ImmutableArchiveStorage, "_fsync_directory", staticmethod(track)
        )
        bundle.coordinator.recover()
        assert parent in synced
        assert bundle.store.status().checkpoint == bundle.records[-1].identity
    finally:
        bundle.close()


def test_failed_full_reconciliation_invalidates_cached_history(tmp_path: Path) -> None:
    bundle = configured_coordinator(
        tmp_path, offsets=(2, 5), epochs=("epoch-a", "epoch-a"), batch_records=1
    )
    try:
        first = bundle.coordinator.publish_next()
        assert isinstance(first, Published)
        body = first.document.body
        unknown = prepare_manifest(
            replace(
                body,
                epoch="unknown-epoch",
                checkpoint_after=replace(body.checkpoint_after, epoch="unknown-epoch"),
                first_ingestion=replace(body.first_ingestion, epoch="unknown-epoch"),
                last_ingestion=replace(body.last_ingestion, epoch="unknown-epoch"),
            )
        )
        document = parse_manifest(unknown, len(unknown))
        ManifestStorage(bundle.root).install_exact(unknown, document.ref)
        with pytest.raises(PublicationError, match="history"):
            bundle.coordinator.recover()
        with pytest.raises(PublicationError, match="history"):
            bundle.coordinator.publish_next()
        assert bundle.store.status().checkpoint == first.checkpoint
        assert bundle.store.pending() is None
        assert len(bundle.store.catalog_page(None, 10)) == 1
    finally:
        bundle.close()
