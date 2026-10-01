"""Retry must durably link directories created before an interrupted fsync."""

from pathlib import Path

import pytest

from scryntic.archive.storage import ArchiveStorageError, ImmutableArchiveStorage
from scryntic.publication.manifest import ManifestError, ManifestStorage
from tests.archive.helpers import installation


def test_existing_object_prefix_parent_is_synced_on_install_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    armed = False
    injected = False

    def fault(stage: str) -> None:
        nonlocal injected
        if armed and not injected and stage == "before_archive_directory_fsync":
            injected = True
            raise OSError("before parent sync")

    root = installation(tmp_path)
    storage = ImmutableArchiveStorage(root, fault=fault)
    staging = storage.create_staging("test")
    staging.write_bytes(b"fixed immutable bytes")
    _, object_hash = storage.prepare_staging(staging)
    armed = True
    with pytest.raises(OSError):
        storage.install_staging(staging, object_hash)
    target = storage.object_path(object_hash)
    assert target.parent.is_dir()
    assert not target.exists()
    synced: list[Path] = []
    actual = ImmutableArchiveStorage._fsync_directory

    def sync(path: Path) -> None:
        synced.append(path)
        actual(path)

    monkeypatch.setattr(ImmutableArchiveStorage, "_fsync_directory", staticmethod(sync))
    storage.install_staging(staging, object_hash)
    assert target.parent.parent in synced
    assert target.read_bytes() == b"fixed immutable bytes"


@pytest.mark.parametrize("kind", ["archive", "manifest"])
def test_constructor_retry_syncs_existing_archive_root_link(
    tmp_path: Path, kind: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = installation(tmp_path)

    def stop(stage: str) -> None:
        if stage in (
            "before_archive_directory_fsync",
            "before_manifest_parent_directory_fsync",
        ):
            raise OSError("before initial parent sync")

    if kind == "archive":
        with pytest.raises(ArchiveStorageError):
            ImmutableArchiveStorage(root, fault=stop)
    else:
        with pytest.raises(ManifestError):
            ManifestStorage(root, fault=stop)
    assert (root.state_dir / "archive").is_dir()
    synced: list[Path] = []
    if kind == "archive":
        actual_archive = ImmutableArchiveStorage._fsync_directory

        def sync_archive(path: Path) -> None:
            synced.append(path)
            actual_archive(path)

        monkeypatch.setattr(
            ImmutableArchiveStorage, "_fsync_directory", staticmethod(sync_archive)
        )
        ImmutableArchiveStorage(root)
    else:
        actual_manifest = ManifestStorage._fsync_directory

        def sync_manifest(path: Path) -> None:
            synced.append(path)
            actual_manifest(path)

        monkeypatch.setattr(
            ManifestStorage, "_fsync_directory", staticmethod(sync_manifest)
        )
        ManifestStorage(root)
    assert root.state_dir in synced
