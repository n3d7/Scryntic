"""Bounded validation of committed and exact external publications."""

from pathlib import Path

import pytest

from scryntic.domain.raw import IngestionId
from scryntic.publication.coordinator import Published
from scryntic.publication.manifest import ManifestError
from scryntic.publication.reader import (
    Continuity,
    PublicationReader,
    PublicationReaderError,
)
from tests.publication.helpers import CoordinatorBundle, configured_coordinator, limits


def _reader(bundle: CoordinatorBundle) -> PublicationReader:
    return PublicationReader(
        bundle.store,
        bundle.root,
        limits(),
    )


def test_interleaved_epoch_page_validates_global_and_epoch_continuity(
    tmp_path: Path,
) -> None:
    bundle = configured_coordinator(
        tmp_path,
        offsets=(2, 5, 9, 14),
        epochs=("epoch-a", "epoch-b", "epoch-a", "epoch-b"),
    )
    try:
        for _ in range(4):
            assert isinstance(bundle.coordinator.publish_next(), Published)
        page = _reader(bundle).catalog_page(anchor=None, limit=4)
        assert page[-1].continuity is Continuity.GLOBAL
        assert [item.document.body.epoch for item in page] == [
            "epoch-a",
            "epoch-b",
            "epoch-a",
            "epoch-b",
        ]
        assert [item.document.body.checkpoint_after.offset for item in page] == [
            2,
            5,
            9,
            14,
        ]
    finally:
        bundle.close()


def test_exact_hash_uses_catalog_and_page_is_bounded(tmp_path: Path) -> None:
    bundle = configured_coordinator(
        tmp_path, offsets=(2, 5), epochs=("epoch-a", "epoch-b")
    )
    try:
        first = bundle.coordinator.publish_next()
        second = bundle.coordinator.publish_next()
        assert isinstance(first, Published) and isinstance(second, Published)
        reader = _reader(bundle)
        assert (
            reader.resolve_exact(first.manifest.manifest_hash).document
            == first.document
        )
        assert len(reader.catalog_page(anchor=None, limit=1)) == 1
        with pytest.raises(PublicationReaderError, match="limit"):
            reader.catalog_page(anchor=None, limit=limits().max_manifests_per_read + 1)
        with pytest.raises(PublicationReaderError, match="anchor"):
            reader.catalog_page(
                anchor=IngestionId("collector-a", "epoch-a", 3), limit=1
            )
    finally:
        bundle.close()


def test_complete_identity_is_readable_before_catalog_commit(tmp_path: Path) -> None:
    class StopAfterManifest(RuntimeError):
        pass

    def fault(stage: str) -> None:
        if stage == "after_manifest_directory_fsync":
            raise StopAfterManifest

    bundle = configured_coordinator(tmp_path, fault=fault)
    try:
        with pytest.raises(ManifestError):
            bundle.coordinator.publish_next()
        pending = bundle.store.pending()
        assert pending is not None and pending.manifest_ref is not None
        assert bundle.store.status().checkpoint is None
        validated = _reader(bundle).resolve_exact(pending.manifest_ref)
        assert validated.document.ref == pending.manifest_ref
        assert validated.continuity is Continuity.SELF
    finally:
        bundle.close()


def test_reader_rejects_second_sequence_entry_and_object_corruption(
    tmp_path: Path,
) -> None:
    bundle = configured_coordinator(tmp_path)
    try:
        result = bundle.coordinator.publish_next()
        assert isinstance(result, Published)
        path = bundle.root.state_dir / "archive" / "manifests"
        manifest_path = next(path.rglob(f"{result.manifest.manifest_hash}.json"))
        conflict = manifest_path.parent / f"{'d' * 64}.json"
        conflict.write_bytes(result.document_bytes)
        conflict.chmod(0o400)
        with pytest.raises(PublicationReaderError, match="manifest"):
            _reader(bundle).resolve_exact(result.manifest)
        conflict.unlink()

        raw = result.document.body.objects[0]
        object_path = (
            bundle.root.state_dir
            / "archive"
            / "objects"
            / "sha256"
            / raw.sha256[:2]
            / f"{raw.sha256}.parquet"
        )
        object_path.chmod(0o600)
        object_path.write_bytes(b"corrupt")
        object_path.chmod(0o400)
        with pytest.raises(PublicationReaderError, match="object"):
            _reader(bundle).resolve_exact(result.manifest)
    finally:
        bundle.close()


def test_reader_requires_exact_per_epoch_predecessor(tmp_path: Path) -> None:
    bundle = configured_coordinator(
        tmp_path,
        offsets=(2, 5, 9),
        epochs=("epoch-a", "epoch-b", "epoch-a"),
    )
    try:
        first = bundle.coordinator.publish_next()
        bundle.coordinator.publish_next()
        third = bundle.coordinator.publish_next()
        assert isinstance(first, Published) and isinstance(third, Published)
        manifest_root = bundle.root.state_dir / "archive" / "manifests"
        first_path = next(manifest_root.rglob(f"{first.manifest.manifest_hash}.json"))
        first_path.unlink()
        with pytest.raises(PublicationReaderError, match="predecessor"):
            _reader(bundle).resolve_exact(third.manifest)
    finally:
        bundle.close()
