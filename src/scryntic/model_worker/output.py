"""Bounded primitive output, opened relative to an already pinned directory."""

import os
import stat

from scryntic.model_worker.profile import OUTPUT_BYTES, RESULT_NAME, IsolationError


def read_result(directory: int) -> bytes:
    """Never follow links, open special files, or reopen a checked pathname."""
    descriptor = os.open(
        RESULT_NAME, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory
    )
    try:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_nlink != 1
            or before.st_uid != os.getuid()
            or not 0 < before.st_size <= OUTPUT_BYTES
        ):
            raise IsolationError("Invalid worker output")
        data = bytearray()
        while len(data) <= OUTPUT_BYTES:
            chunk = os.read(descriptor, min(4096, OUTPUT_BYTES + 1 - len(data)))
            if not chunk:
                break
            data.extend(chunk)
        after = os.fstat(descriptor)
        if (
            len(data) != before.st_size
            or after.st_nlink != 1
            or (after.st_size, after.st_mtime_ns, after.st_ctime_ns)
            != (before.st_size, before.st_mtime_ns, before.st_ctime_ns)
        ):
            raise IsolationError("Worker output changed or exceeded bounds")
        return bytes(data)
    finally:
        os.close(descriptor)
