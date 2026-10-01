"""Private, inode-pinned opaque downloads; no parser or credential authority."""

import os
import re
import stat
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from hashlib import sha256
from pathlib import Path

from scryntic.configuration.paths import Installation, directory
from scryntic.domain.validation import digest, integer
from scryntic.imports.catalog import _child
from scryntic.imports.filesystem import _regular, snapshot_file, write_all
from scryntic.sync.model import PullError, PullLimits

_UNSAFE_STAGING = "Unsafe pull staging"
_STAGING_QUOTA = "Pull staging quota exceeded"
_PART = ".part"


class Staging:
    def __init__(
        self, installation: Installation, limits: PullLimits | None = None
    ) -> None:
        self.limits = PullLimits() if limits is None else limits
        self.path: Path = installation.state_dir / "pull-staging"
        self._resources = ExitStack()
        try:
            parent = self._resources.enter_context(
                directory(installation.state_dir, installation.owner_uid, private=True)
            )
            self.fd = _child(parent, "pull-staging")
            self._resources.callback(os.close, self.fd)
            self._recover_links()
            self._usage()
        except BaseException as error:
            self._resources.close()
            if isinstance(error, Exception):
                raise PullError(_UNSAFE_STAGING) from None
            raise

    def __enter__(self) -> "Staging":
        return self

    def __exit__(self, *args: object) -> None:
        self._resources.close()

    def _names(self) -> tuple[str, ...]:
        result: list[str] = []
        with os.scandir(self.fd) as entries:
            for entry in entries:
                if (
                    len(result) >= self.limits.max_entries
                    or re.fullmatch(r"[0-9a-f]{64}(?:\.part)?", entry.name) is None
                ):
                    raise PullError(_UNSAFE_STAGING)
                result.append(entry.name)
        return tuple(result)

    def _pin(self, name: str) -> int:
        return os.open(name, os.O_PATH | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=self.fd)

    def _recover_links(self) -> None:
        for name in self._names():
            if not name.endswith(_PART):
                continue
            pin = self._pin(name)
            try:
                info = os.fstat(pin)
                if info.st_nlink != 2:
                    continue
                if (
                    not stat.S_ISREG(info.st_mode)
                    or info.st_uid != os.geteuid()
                    or stat.S_IMODE(info.st_mode) != 0o400
                ):
                    raise PullError(_UNSAFE_STAGING)
                final = os.stat(name[:-5], dir_fd=self.fd, follow_symlinks=False)
                if (info.st_dev, info.st_ino) != (final.st_dev, final.st_ino):
                    raise PullError(_UNSAFE_STAGING)
                os.unlink(name, dir_fd=self.fd)
                os.fsync(self.fd)
            finally:
                os.close(pin)

    def _usage(self) -> int:
        total = 0
        for name in self._names():
            pin = self._pin(name)
            try:
                info = os.fstat(pin)
                _regular(info)
                total += info.st_size
                if total > self.limits.max_staging_bytes:
                    raise PullError(_STAGING_QUOTA) from None
            finally:
                os.close(pin)
        return total

    def has(self, name: str, size: int) -> bool:
        digest(name)
        try:
            os.stat(name, dir_fd=self.fd, follow_symlinks=False)
        except FileNotFoundError:
            return False
        try:
            snapshot = snapshot_file(self.fd, name, size, self.limits.max_staging_bytes)
            os.close(snapshot)
            return True
        except Exception:
            raise PullError("Conflicting staged object") from None

    @contextmanager
    def partial(self, name: str, size: int) -> Iterator[int]:
        pin = opened = -1
        try:
            digest(name)
            integer(size, 1)
            partial_name = name + _PART
            try:
                pin = self._pin(partial_name)
            except FileNotFoundError:
                if self._usage() + size > self.limits.max_staging_bytes:
                    raise PullError(_STAGING_QUOTA) from None
                free = os.fstatvfs(self.fd)
                if free.f_bavail * free.f_frsize < size + 1024 * 1024:
                    raise PullError("Pull staging disk headroom unavailable") from None
                opened = os.open(
                    partial_name,
                    os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                    0o600,
                    dir_fd=self.fd,
                )
                os.fsync(self.fd)
            else:
                info = os.fstat(pin)
                _regular(info)
                if (
                    info.st_size > size
                    or self._usage() + size - info.st_size
                    > self.limits.max_staging_bytes
                ):
                    raise PullError(_STAGING_QUOTA)
                # Pin first; changing the name cannot redirect even FIFO/device I/O.
                opened = os.open(
                    f"/proc/self/fd/{pin}", os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC
                )
                os.fchmod(opened, 0o600)
                writable = os.open(
                    f"/proc/self/fd/{opened}", os.O_RDWR | os.O_NONBLOCK | os.O_CLOEXEC
                )
                os.close(opened)
                opened = writable
            _regular(os.fstat(opened))
            os.lseek(opened, 0, os.SEEK_END)
            yield opened
        except Exception:
            raise PullError("Unable to stage pull object") from None
        finally:
            if opened >= 0:
                try:
                    os.fsync(opened)
                finally:
                    os.close(opened)
            if pin >= 0:
                os.close(pin)

    @staticmethod
    def append(fd: int, data: bytes) -> None:
        write_all(fd, data)
        os.fsync(fd)

    def finish(self, fd: int, name: str, size: int) -> None:
        digest(name)
        _regular(os.fstat(fd))
        hashed = sha256()
        offset = 0
        while offset < size:
            data = os.pread(fd, min(self.limits.chunk_bytes, size - offset), offset)
            if not data:
                break
            hashed.update(data)
            offset += len(data)
        if offset != size or os.fstat(fd).st_size != size or hashed.hexdigest() != name:
            os.unlink(name + _PART, dir_fd=self.fd)
            os.fsync(self.fd)
            raise PullError("Downloaded object integrity mismatch")
        os.fchmod(fd, 0o400)
        os.fsync(fd)
        os.link(
            name + _PART,
            name,
            src_dir_fd=self.fd,
            dst_dir_fd=self.fd,
            follow_symlinks=False,
        )
        final = os.stat(name, dir_fd=self.fd, follow_symlinks=False)
        pinned = os.fstat(fd)
        if (final.st_dev, final.st_ino) != (pinned.st_dev, pinned.st_ino):
            os.unlink(name, dir_fd=self.fd)
            os.fsync(self.fd)
            raise PullError("Staging inode changed")
        os.fsync(self.fd)
        os.unlink(name + _PART, dir_fd=self.fd)
        os.fsync(self.fd)

    def cache(self, source: int, name: str, size: int) -> None:
        with self.partial(name, size) as fd:
            os.ftruncate(fd, 0)
            os.lseek(fd, 0, os.SEEK_SET)
            offset = 0
            while offset < size:
                data = os.pread(
                    source, min(self.limits.chunk_bytes, size - offset), offset
                )
                if not data:
                    raise PullError("Invalid cached object")
                self.append(fd, data)
                offset += len(data)
            self.finish(fd, name, size)

    def discard(self, name: str) -> None:
        """Remove only an imported object's disposable local staging copy."""
        digest(name)
        pin = self._pin(name)
        try:
            _regular(os.fstat(pin), immutable=True)
            os.unlink(name, dir_fd=self.fd)
            os.fsync(self.fd)
        finally:
            os.close(pin)
