"""Local no-replace storage for generated dataset artifacts."""

import os
import stat
import tempfile
from hashlib import sha256
from pathlib import Path

import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]

from scryntic.configuration.paths import Installation, directory
from scryntic.domain.validation import digest


class DatasetStorageError(RuntimeError):
    """A generated dataset artifact could not be stored or verified."""


class DatasetStorage:
    def __init__(self, installation: Installation) -> None:
        self.root = installation.state_dir / "datasets"
        self._owner_uid = installation.owner_uid

    def _ensure_directory(self, path: Path) -> None:
        try:
            path.mkdir(mode=0o700)
        except FileExistsError:
            pass
        info = path.stat(follow_symlinks=False)
        if (
            not stat.S_ISDIR(info.st_mode)
            or info.st_uid != self._owner_uid
            or stat.S_IMODE(info.st_mode) != 0o700
        ):
            raise DatasetStorageError("Unsafe dataset directory")

    def _prepare(self, installation: Installation) -> Path:
        with directory(installation.state_dir, installation.owner_uid, private=True):
            self._ensure_directory(self.root)
        staging = self.root / "staging"
        self._ensure_directory(staging)
        return staging

    @staticmethod
    def _fsync_directory(path: Path) -> None:
        fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

    def _read(self, path: Path, *, max_bytes: int) -> bytes:
        fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
        try:
            info = os.fstat(fd)
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_uid != self._owner_uid
                or not 0 < info.st_size <= max_bytes
            ):
                raise DatasetStorageError("Invalid dataset artifact")
            data = bytearray()
            while chunk := os.read(fd, min(1024 * 1024, max_bytes + 1 - len(data))):
                data.extend(chunk)
                if len(data) > max_bytes:
                    raise DatasetStorageError("Dataset artifact exceeds limit")
            if len(data) != info.st_size:
                raise DatasetStorageError("Dataset artifact size changed")
            return bytes(data)
        finally:
            os.close(fd)

    def _install(self, staging: Path, suffix: str, max_bytes: int) -> tuple[str, int]:
        fd = os.open(staging, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
        try:
            os.fsync(fd)
            size = os.fstat(fd).st_size
        finally:
            os.close(fd)
        if not 0 < size <= max_bytes:
            raise DatasetStorageError("Dataset artifact exceeds limit")
        staging.chmod(0o400, follow_symlinks=False)
        data = self._read(staging, max_bytes=max_bytes)
        object_sha256 = sha256(data).hexdigest()
        folder = self.root / ("objects" if suffix == "parquet" else "manifests")
        self._ensure_directory(folder)
        folder = folder / "sha256"
        self._ensure_directory(folder)
        folder = folder / object_sha256[:2]
        self._ensure_directory(folder)
        target = folder / f"{object_sha256}.{suffix}"
        try:
            os.link(staging, target, follow_symlinks=False)
        except FileExistsError:
            if (
                sha256(self._read(target, max_bytes=max_bytes)).hexdigest()
                != object_sha256
            ):
                raise DatasetStorageError(
                    "Conflicting immutable dataset artifact"
                ) from None
        self._fsync_directory(folder)
        staging.unlink()
        self._fsync_directory(staging.parent)
        return object_sha256, size

    def write_table(
        self, installation: Installation, table: pa.Table, *, max_bytes: int
    ) -> tuple[str, int]:
        staging_dir = self._prepare(installation)
        fd, name = tempfile.mkstemp(
            prefix=".dataset-", suffix=".parquet", dir=staging_dir
        )
        os.close(fd)
        path = Path(name)
        try:
            pq.write_table(table, path, compression="zstd")
            return self._install(path, "parquet", max_bytes)
        finally:
            path.unlink(missing_ok=True)

    def write_manifest(
        self, installation: Installation, data: bytes, *, max_bytes: int
    ) -> tuple[str, int]:
        staging_dir = self._prepare(installation)
        fd, name = tempfile.mkstemp(
            prefix=".manifest-", suffix=".json", dir=staging_dir
        )
        path = Path(name)
        try:
            with os.fdopen(fd, "wb") as file:
                file.write(data)
                file.flush()
                os.fsync(file.fileno())
            return self._install(path, "json", max_bytes)
        finally:
            path.unlink(missing_ok=True)

    def read_artifact(self, value: str, suffix: str, *, max_bytes: int) -> bytes:
        digest(value)
        if suffix not in ("parquet", "json"):
            raise DatasetStorageError("Unsupported dataset artifact")
        folder = "objects" if suffix == "parquet" else "manifests"
        path = self.root / folder / "sha256" / value[:2] / f"{value}.{suffix}"
        data = self._read(path, max_bytes=max_bytes)
        if sha256(data).hexdigest() != value:
            raise DatasetStorageError("Dataset artifact hash mismatch")
        return data
