"""Data-only artifact verification and private, immutable per-job snapshots."""

import os
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from hashlib import sha256
from pathlib import Path

CHUNK = 512 * 1024


@contextmanager
def regular_file(path: Path) -> Iterator[int]:
    """Pin each ancestor with no symlink traversal, including a replaced parent."""
    if not path.is_absolute() or ".." in path.parts:
        raise ValueError("Expected absolute artifact path")
    parent = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    descriptor: int | None = None
    try:
        for part in path.parts[1:-1]:
            child = os.open(
                part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent
            )
            os.close(parent)
            parent = child
        descriptor = os.open(
            path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent
        )
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise ValueError("Artifact is not a regular file")
        yield descriptor
    finally:
        if descriptor is not None:
            os.close(descriptor)
        os.close(parent)


def _identity(value: os.stat_result) -> tuple[int, ...]:
    return (
        value.st_dev,
        value.st_ino,
        value.st_size,
        value.st_mtime_ns,
        value.st_ctime_ns,
        value.st_nlink,
    )


def snapshot_file(
    source: Path, target: Path, expected_sha256: str, expected_size: int
) -> None:
    """Copy verified bytes; the worker never receives the mutable source path."""
    if type(expected_size) is not int or not 0 <= expected_size <= 4 * 1024**3:
        raise ValueError("Artifact exceeds approved byte bounds")
    with regular_file(source) as incoming:
        before = os.fstat(incoming)
        if before.st_size != expected_size:
            raise ValueError("Artifact size changed")
        outgoing = os.open(
            target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600
        )
        try:
            checksum = sha256()
            total = 0
            with os.fdopen(outgoing, "wb", closefd=False) as stream:
                while chunk := os.read(incoming, CHUNK):
                    total += len(chunk)
                    if total > expected_size:
                        raise ValueError("Artifact exceeds approved size")
                    checksum.update(chunk)
                    stream.write(chunk)
                stream.flush()
            if (
                total != expected_size
                or checksum.hexdigest() != expected_sha256
                or _identity(before) != _identity(os.fstat(incoming))
            ):
                raise ValueError("Artifact hash or identity changed")
            os.fchmod(outgoing, 0o444)
        except BaseException:
            target.unlink(missing_ok=True)
            raise
        finally:
            os.close(outgoing)


def file_identity(path: Path, maximum: int = 4 * 1024**3) -> tuple[str, int]:
    with regular_file(path) as descriptor:
        before = os.fstat(descriptor)
        if not 0 <= before.st_size <= maximum:
            raise ValueError("File exceeds inventory bounds")
        checksum = sha256()
        total = 0
        while chunk := os.read(descriptor, CHUNK):
            total += len(chunk)
            if total > maximum:
                raise ValueError("File exceeds inventory bounds")
            checksum.update(chunk)
        if total != before.st_size or _identity(before) != _identity(
            os.fstat(descriptor)
        ):
            raise ValueError("File changed during inventory")
        return checksum.hexdigest(), total
