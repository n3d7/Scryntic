"""Fixed effective-control probes, run before native decoding on every launch."""

import ctypes
import errno
import os
import resource
import socket
from pathlib import Path


def check() -> None:
    if os.geteuid() != 65534 or os.getegid() != 65534:
        raise RuntimeError("Worker identity not isolated")
    if ctypes.CDLL(None).prctl(39, 0, 0, 0, 0) != 1:  # PR_GET_NO_NEW_PRIVS
        raise RuntimeError("Privileges not restricted")
    for path in ("/home", "/root", "/etc", "/dev", "/proc", "/run", "/sys"):
        if Path(path).exists():
            raise RuntimeError("Host authority exposed")
    if set(os.environ) != {
        "OPENBLAS_NUM_THREADS",
        "OMP_NUM_THREADS",
        "ARROW_NUM_THREADS",
        "LC_ALL",
        "PWD",
        "MALLOC_CONF",
        "ARROW_DEFAULT_MEMORY_POOL",
    }:
        raise RuntimeError("Environment not isolated")
    _network()
    _process()
    _filesystem()
    _descriptors()
    _memory()


def _network() -> None:
    for family in (socket.AF_INET, socket.AF_INET6, socket.AF_UNIX):
        try:
            connection = socket.socket(family)
        except OSError as error:
            if error.errno != errno.EPERM:
                raise
        else:
            connection.close()
            raise RuntimeError("Network authority exposed")


def _process() -> None:
    try:
        child = os.fork()
    except OSError as error:
        if error.errno != errno.EPERM:
            raise
    else:
        if child == 0:
            os._exit(1)
        os.waitpid(child, 0)
        raise RuntimeError("Process spawning exposed")


def _filesystem() -> None:
    try:
        fd = os.open("/escape", os.O_CREAT | os.O_WRONLY, 0o600)
    except OSError as error:
        if error.errno not in (errno.EROFS, errno.EACCES):
            raise
    else:
        os.close(fd)
        raise RuntimeError("Writable filesystem exposed")
    if resource.getrlimit(resource.RLIMIT_CORE) != (0, 0) or resource.getrlimit(
        resource.RLIMIT_NPROC
    ) != (0, 0):
        raise RuntimeError("Resource limits absent")


def _descriptors() -> None:
    for fd in range(3, 64):
        try:
            os.fstat(fd)
        except OSError as error:
            if error.errno != errno.EBADF:
                raise
        else:
            raise RuntimeError("Inherited authority exposed")
    for fd in (0, 1, 2):
        import stat

        if not stat.S_ISFIFO(os.fstat(fd).st_mode):
            raise RuntimeError("Unexpected IPC channel")


def _memory() -> None:
    import mmap

    try:
        allocated = mmap.mmap(-1, resource.getrlimit(resource.RLIMIT_AS)[0] + 4096)
    except (OSError, MemoryError):
        pass
    else:
        allocated.close()
        raise RuntimeError("Address-space limit absent")
