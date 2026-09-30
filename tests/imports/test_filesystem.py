"""Hostile directory entries must never select parser or publication paths."""

import os
from hashlib import sha256
from pathlib import Path

import pytest

from scryntic.imports.filesystem import snapshot_file
from scryntic.imports.protocol import ImportError


def test_snapshots_are_sealed_and_independent(tmp_path: Path) -> None:
    data = b"opaque bytes"
    name = sha256(data).hexdigest()
    path = tmp_path / name
    path.write_bytes(data)
    path.chmod(0o600)
    parent = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        fd = snapshot_file(parent, name, len(data), 100)
        try:
            path.write_bytes(b"changed")
            assert os.pread(fd, 100, 0) == data
            with pytest.raises(OSError):
                os.write(fd, b"x")
        finally:
            os.close(fd)
    finally:
        os.close(parent)


@pytest.mark.parametrize("kind", ["symlink", "fifo", "directory", "hardlink", "socket"])
def test_special_entries_are_rejected_without_reading(
    tmp_path: Path, kind: str
) -> None:
    import socket

    data = b"opaque"
    name = sha256(data).hexdigest()
    path = tmp_path / name
    other = tmp_path / "other"
    other.write_bytes(data)
    other.chmod(0o600)
    sock = socket.socket(socket.AF_UNIX)
    try:
        if kind == "symlink":
            path.symlink_to(other)
        elif kind == "fifo":
            os.mkfifo(path)
        elif kind == "directory":
            path.mkdir()
        elif kind == "hardlink":
            os.link(other, path)
        else:
            parent_fd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
            try:
                sock.bind(f"/proc/self/fd/{parent_fd}/{name}")
            finally:
                os.close(parent_fd)
        parent = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
        try:
            with pytest.raises(ImportError):
                snapshot_file(parent, name, len(data), 100)
        finally:
            os.close(parent)
    finally:
        sock.close()


@pytest.mark.parametrize(
    "name", ["../outside", "/etc/passwd", "a/../../b", "0" * 63, "A" * 64]
)
def test_path_text_never_selects_a_destination(tmp_path: Path, name: str) -> None:
    parent = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        with pytest.raises(ImportError):
            snapshot_file(parent, name, 1, 100)
    finally:
        os.close(parent)


def test_swap_after_pinning_never_opens_special_replacement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data = b"opaque"
    name = sha256(data).hexdigest()
    path = tmp_path / name
    path.write_bytes(data)
    path.chmod(0o600)
    original = os.open

    def swapping_open(
        target: str | Path, flags: int, mode: int = 0o777, *, dir_fd: int | None = None
    ) -> int:
        fd = original(target, flags, mode, dir_fd=dir_fd)
        if target == name and flags & os.O_PATH:
            path.rename(tmp_path / "pinned")
            os.mkfifo(path)
        return fd

    monkeypatch.setattr(os, "open", swapping_open)
    parent = original(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        fd = snapshot_file(parent, name, len(data), 100)
        try:
            assert os.pread(fd, 100, 0) == data
        finally:
            os.close(fd)
    finally:
        os.close(parent)


def test_mutation_during_copy_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data = b"opaque"
    name = sha256(data).hexdigest()
    path = tmp_path / name
    path.write_bytes(data)
    path.chmod(0o600)
    original = os.read

    def mutating_read(fd: int, count: int) -> bytes:
        block = original(fd, count)
        path.write_bytes(b"changed")
        return block

    monkeypatch.setattr(os, "read", mutating_read)
    parent = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        with pytest.raises(ImportError):
            snapshot_file(parent, name, len(data), 100)
    finally:
        os.close(parent)
