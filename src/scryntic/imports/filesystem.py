"""Pin untrusted regular entries and publish only generated, opaque snapshots."""

import fcntl
import os
import stat
from hashlib import sha256

from scryntic.domain.validation import digest
from scryntic.imports.protocol import ImportError

# Linux UAPI constants are not exposed by the pinned CPython 3.12 fcntl module.
_ADD_SEALS = 1033
_SEALS = 0x01 | 0x02 | 0x04 | 0x08


def write_all(fd: int, data: bytes) -> None:
    view = memoryview(data)
    while view:
        count = os.write(fd, view)
        if count <= 0:
            raise ImportError("Unable to stage import")
        view = view[count:]


def sealed_bytes(data: bytes) -> int:
    fd = os.memfd_create("scryntic-import", os.MFD_CLOEXEC | os.MFD_ALLOW_SEALING)
    try:
        write_all(fd, data)
        os.fchmod(fd, 0o400)
        fcntl.fcntl(fd, _ADD_SEALS, _SEALS)
        os.lseek(fd, 0, os.SEEK_SET)
        return fd
    except BaseException:
        os.close(fd)
        raise


def _regular(info: os.stat_result, *, immutable: bool = False) -> None:
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_uid != os.geteuid()
        or info.st_nlink != 1
        or stat.S_IMODE(info.st_mode) not in ((0o400,) if immutable else (0o400, 0o600))
    ):
        raise ImportError("Unsafe import entry")


def _identity(info: os.stat_result) -> tuple[int, ...]:
    return (
        info.st_dev,
        info.st_ino,
        info.st_mode,
        info.st_uid,
        info.st_nlink,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
    )


def snapshot_file(parent: int, name: str, expected_bytes: int, limit: int) -> int:
    """O_PATH pins before checking type; even device/FIFO races cannot trigger IO."""
    pinned = source = snapshot = -1
    try:
        digest(name)
        if type(expected_bytes) is not int or not 0 < expected_bytes <= limit:
            raise ValueError("Size limit")
        pinned = os.open(name, os.O_PATH | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=parent)
        before = os.fstat(pinned)
        _regular(before)
        if before.st_size != expected_bytes:
            raise ValueError("Length mismatch")
        # This is the validated inode, never another lookup of untrusted name.
        source = os.open(
            f"/proc/self/fd/{pinned}", os.O_RDONLY | os.O_CLOEXEC | os.O_NONBLOCK
        )
        snapshot = os.memfd_create(
            "scryntic-import", os.MFD_CLOEXEC | os.MFD_ALLOW_SEALING
        )
        hashed = sha256()
        count = 0
        while count <= expected_bytes:
            data = os.read(source, min(65536, expected_bytes + 1 - count))
            if not data:
                break
            count += len(data)
            if count > expected_bytes:
                raise ValueError("Length mismatch")
            write_all(snapshot, data)
            hashed.update(data)
        if (
            _identity(before) != _identity(os.fstat(source))
            or count != expected_bytes
            or hashed.hexdigest() != name
        ):
            raise ValueError("Snapshot mismatch")
        os.fchmod(snapshot, 0o400)
        fcntl.fcntl(snapshot, _ADD_SEALS, _SEALS)
        os.lseek(snapshot, 0, os.SEEK_SET)
        result, snapshot = snapshot, -1
        return result
    except Exception:
        raise ImportError("Invalid import object") from None
    finally:
        for fd in (pinned, source, snapshot):
            if fd >= 0:
                os.close(fd)
