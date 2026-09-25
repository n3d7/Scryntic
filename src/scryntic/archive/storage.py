"""Generated-path immutable local storage with no-replace installation."""

import os
import stat
import tempfile
from hashlib import sha256
from pathlib import Path

from scryntic.archive.model import ArchiveRole, Partition
from scryntic.configuration.paths import Installation, directory
from scryntic.domain.validation import digest


class ArchiveStorageError(RuntimeError):
    """Fixed-message immutable archive filesystem failure."""


class ImmutableArchiveStorage:
    """Own generated archive paths beneath an approved state directory."""

    def __init__(self, installation: Installation) -> None:
        self._owner_uid = installation.owner_uid
        self.root = installation.state_dir / "archive"
        try:
            with directory(
                installation.state_dir, installation.owner_uid, private=True
            ):
                self._ensure_directory(self.root)
            self.staging_path = self.root / "staging"
            self.objects_path = self.root / "objects" / "sha256"
            self.partitions_path = self.root / "partitions"
            for path in (
                self.staging_path,
                self.root / "objects",
                self.objects_path,
                self.partitions_path,
            ):
                self._ensure_directory(path)
        except BaseException as error:
            if isinstance(error, ArchiveStorageError):
                raise
            if isinstance(error, Exception):
                raise ArchiveStorageError(
                    "Unable to initialize archive storage"
                ) from None
            raise

    def _ensure_directory(self, path: Path) -> None:
        created = False
        try:
            path.mkdir(mode=0o700)
            created = True
        except FileExistsError:
            pass
        info = path.stat(follow_symlinks=False)
        if (
            not stat.S_ISDIR(info.st_mode)
            or info.st_uid != self._owner_uid
            or stat.S_IMODE(info.st_mode) != 0o700
        ):
            raise ArchiveStorageError("Unsafe archive directory")
        if created:
            self._fsync_directory(path.parent)

    @staticmethod
    def _fsync_directory(path: Path) -> None:
        fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

    def create_staging(self, prefix: str) -> Path:
        fd, name = tempfile.mkstemp(
            prefix=f".{prefix}-", suffix=".parquet", dir=self.staging_path
        )
        os.close(fd)
        return Path(name)

    def prepare_staging(self, path: Path) -> tuple[int, str]:
        fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
        try:
            info = os.fstat(fd)
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_uid != self._owner_uid
                or stat.S_IMODE(info.st_mode) != 0o600
            ):
                raise ArchiveStorageError("Unsafe archive staging file")
            os.fsync(fd)
        finally:
            os.close(fd)
        path.chmod(0o400, follow_symlinks=False)
        fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
        try:
            os.fsync(fd)
            size = os.fstat(fd).st_size
            hashed = sha256()
            while chunk := os.read(fd, 1024 * 1024):
                hashed.update(chunk)
        finally:
            os.close(fd)
        return size, hashed.hexdigest()

    def object_path(self, object_sha256: str) -> Path:
        digest(object_sha256)
        return self.objects_path / object_sha256[:2] / f"{object_sha256}.parquet"

    def install_staging(self, staging: Path, object_sha256: str) -> Path:
        target = self.object_path(object_sha256)
        self._ensure_directory(target.parent)
        try:
            os.link(staging, target, follow_symlinks=False)
        except FileExistsError:
            if self.file_sha256(target) != object_sha256:
                raise ArchiveStorageError(
                    "Conflicting immutable archive object"
                ) from None
        self._fsync_directory(target.parent)
        try:
            staging.unlink()
        finally:
            self._fsync_directory(self.staging_path)
        return target

    def file_sha256(self, path: Path) -> str:
        fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
        try:
            info = os.fstat(fd)
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_uid != self._owner_uid
                or stat.S_IMODE(info.st_mode) != 0o400
            ):
                raise ArchiveStorageError("Unsafe immutable archive object")
            hashed = sha256()
            while chunk := os.read(fd, 1024 * 1024):
                hashed.update(chunk)
            return hashed.hexdigest()
        finally:
            os.close(fd)

    def install_partition_view(
        self, role: ArchiveRole, partition: Partition, object_sha256: str
    ) -> Path:
        digest(object_sha256)
        source_digest = sha256(partition.source.encode("utf-8")).hexdigest()
        directory_path = (
            self.partitions_path
            / role.value
            / source_digest
            / partition.event_family
            / partition.utc_date
        )
        current = self.partitions_path
        for component in directory_path.relative_to(self.partitions_path).parts:
            current = current / component
            self._ensure_directory(current)
        source = self.object_path(object_sha256)
        target = directory_path / f"{object_sha256}.parquet"
        try:
            os.link(source, target, follow_symlinks=False)
        except FileExistsError:
            source_info = source.stat(follow_symlinks=False)
            target_info = target.stat(follow_symlinks=False)
            if (source_info.st_dev, source_info.st_ino) != (
                target_info.st_dev,
                target_info.st_ino,
            ):
                raise ArchiveStorageError(
                    "Conflicting archive partition view"
                ) from None
        self._fsync_directory(directory_path)
        return target
