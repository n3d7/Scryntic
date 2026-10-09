import os
from hashlib import sha256
from pathlib import Path

import pytest

from scryntic.models.artifacts import snapshot_file


def test_snapshot_is_verified_private_copy(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.write_bytes(b"reviewed-weights")
    target = tmp_path / "snapshot"
    snapshot_file(
        source, target, sha256(source.read_bytes()).hexdigest(), source.stat().st_size
    )
    source.write_bytes(b"later-tampering")
    assert target.read_bytes() == b"reviewed-weights"
    assert target.stat().st_nlink == 1
    assert target.stat().st_mode & 0o777 == 0o444


def test_bad_hash_and_symlink_never_publish_snapshot(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.write_bytes(b"host-secret")
    target = tmp_path / "output"
    with pytest.raises(ValueError):
        snapshot_file(source, target, "0" * 64, 11)
    assert not target.exists()
    link = tmp_path / "link"
    link.symlink_to(source)
    checksum = sha256(source.read_bytes()).hexdigest()
    with pytest.raises((ValueError, OSError)):
        snapshot_file(link, target, checksum, 11)
    assert not target.exists()


def test_concurrent_source_mutation_rejects_even_if_copied_bytes_hash_matches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, target = tmp_path / "source", tmp_path / "snapshot"
    source.write_bytes(b"reviewed-weights")
    checksum = sha256(source.read_bytes()).hexdigest()
    original = os.read
    mutated = False

    def raced(descriptor: int, size: int) -> bytes:
        nonlocal mutated
        chunk = original(descriptor, size)
        if not mutated:
            mutated = True
            source.write_bytes(b"hostile--weights")
        return chunk

    monkeypatch.setattr(os, "read", raced)
    with pytest.raises(ValueError, match="identity changed"):
        snapshot_file(source, target, checksum, 16)
    assert not target.exists()
