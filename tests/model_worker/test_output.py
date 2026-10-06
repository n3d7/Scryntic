"""Actual descriptor/filesystem boundary; fixtures are untrusted output."""

import os
from pathlib import Path

import pytest

from scryntic.model_worker.output import read_result
from scryntic.model_worker.profile import OUTPUT_BYTES, IsolationError


def read(path: Path) -> bytes:
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        return read_result(fd)
    finally:
        os.close(fd)


@pytest.mark.parametrize("kind", ["symlink", "hardlink", "fifo", "directory", "large"])
def test_untrusted_file_types_and_bounds(tmp_path: Path, kind: str) -> None:
    target = tmp_path / "result.json"
    secret = tmp_path / "secret"
    secret.write_bytes(b"host-secret")
    if kind == "symlink":
        target.symlink_to(secret)
    elif kind == "hardlink":
        os.link(secret, target)
    elif kind == "fifo":
        os.mkfifo(target)
    elif kind == "directory":
        target.mkdir()
    else:
        target.write_bytes(b"x" * (OUTPUT_BYTES + 1))
    with pytest.raises((OSError, IsolationError)):
        read(tmp_path)


def test_pinned_parent_survives_directory_replacement(tmp_path: Path) -> None:
    output = tmp_path / "output"
    output.mkdir()
    (output / "result.json").write_bytes(b"bounded")
    fd = os.open(output, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        output.rename(tmp_path / "old")
        output.symlink_to("/etc")
        assert read_result(fd) == b"bounded"
    finally:
        os.close(fd)


def test_link_swap_during_read_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "result.json"
    target.write_bytes(b"bounded")
    real_read = os.read

    def swap(fd: int, count: int) -> bytes:
        if target.is_file() and not target.is_symlink():
            target.unlink()
            target.symlink_to("/etc/shadow")
        return real_read(fd, count)

    monkeypatch.setattr(os, "read", swap)
    with pytest.raises(IsolationError):
        read(tmp_path)
