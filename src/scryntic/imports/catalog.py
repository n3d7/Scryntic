"""Transactional conformance catalog; it grants no parsing or enrollment authority."""

import fcntl
import os
import re
import sqlite3
import stat
import threading
from contextlib import ExitStack
from pathlib import Path
from uuid import uuid4

from scryntic.archive.canonical import canonical_json_bytes
from scryntic.configuration.paths import Installation, directory
from scryntic.domain.time import ClockSample
from scryntic.domain.validation import digest
from scryntic.imports.filesystem import _regular, sealed_bytes, snapshot_file, write_all
from scryntic.imports.launcher import LinuxDecoder
from scryntic.imports.protocol import (
    ImportError,
    ImportLimits,
    parse_request,
    validate_result,
)
from scryntic.publication.manifest import ManifestDocument, ManifestRef


def _child(parent: int, name: str) -> int:
    try:
        os.mkdir(name, mode=0o700, dir_fd=parent)
    except FileExistsError:
        pass
    fd = os.open(
        name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=parent
    )
    info = os.fstat(fd)
    if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700:
        os.close(fd)
        raise ImportError("Unsafe import directory")
    os.fsync(parent)
    return fd


def _fixed_file(parent: int, name: str) -> int:
    pinned = -1
    try:
        try:
            fd = os.open(
                name,
                os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                0o600,
                dir_fd=parent,
            )
        except FileExistsError:
            pinned = os.open(
                name, os.O_PATH | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=parent
            )
            _regular(os.fstat(pinned))
            fd = os.open(f"/proc/self/fd/{pinned}", os.O_RDWR | os.O_CLOEXEC)
        try:
            _regular(os.fstat(fd))
            if stat.S_IMODE(os.fstat(fd).st_mode) != 0o600:
                raise ImportError("Unsafe catalog file")
            return fd
        except BaseException:
            os.close(fd)
            raise
    finally:
        if pinned >= 0:
            os.close(pinned)


class ImportCatalog:
    """One owner, private generated paths, durable bytes before catalog commit."""

    def __init__(
        self, installation: Installation, limits: ImportLimits | None = None
    ) -> None:
        self.limits = ImportLimits() if limits is None else limits
        self._resources = ExitStack()
        self._thread = threading.get_ident()
        self._closed = False
        self._decoder = LinuxDecoder(self.limits)
        try:
            if installation.owner_uid != os.geteuid():
                raise ImportError("Unsafe catalog owner")
            state = self._resources.enter_context(
                directory(installation.state_dir, installation.owner_uid, private=True)
            )
            self._root = _child(state, "imports")
            self._resources.callback(os.close, self._root)
            self._objects = _child(self._root, "objects")
            self._resources.callback(os.close, self._objects)
            lock = _fixed_file(self._root, "catalog.lock")
            self._resources.callback(os.close, lock)
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self._recover_staging()
            db_fd = _fixed_file(self._root, "catalog.sqlite3")
            self._resources.callback(os.close, db_fd)
            for name in (
                "catalog.sqlite3-wal",
                "catalog.sqlite3-shm",
                "catalog.sqlite3-journal",
            ):
                try:
                    sidecar = os.open(
                        name,
                        os.O_PATH | os.O_NOFOLLOW | os.O_CLOEXEC,
                        dir_fd=self._root,
                    )
                except FileNotFoundError:
                    continue
                try:
                    _regular(os.fstat(sidecar))
                finally:
                    os.close(sidecar)
            self._db = sqlite3.connect(
                f"/proc/self/fd/{self._root}/catalog.sqlite3",
                autocommit=True,
                timeout=1,
            )
            self._resources.callback(self._db.close)
            for name, fd in (("catalog.lock", lock), ("catalog.sqlite3", db_fd)):
                current = os.stat(name, dir_fd=self._root, follow_symlinks=False)
                pinned = os.fstat(fd)
                if (current.st_dev, current.st_ino) != (pinned.st_dev, pinned.st_ino):
                    raise ImportError("Catalog inode changed")
            if self._db.execute("PRAGMA journal_mode=WAL").fetchone() != ("wal",):
                raise ImportError("Catalog durability unavailable")
            self._db.execute("PRAGMA synchronous=FULL")
            self._db.execute("PRAGMA trusted_schema=OFF")
            version = self._db.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1):
                raise ImportError("Unsupported import catalog")
            self._db.execute(
                "CREATE TABLE IF NOT EXISTS imports (manifest_hash TEXT PRIMARY KEY, producer TEXT NOT NULL, epoch TEXT NOT NULL, sequence INTEGER NOT NULL, manifest BLOB NOT NULL, receipt BLOB NOT NULL, UNIQUE(producer, epoch, sequence)) STRICT"
            )
            self._db.execute("PRAGMA user_version=1")
            os.fsync(self._root)
        except BaseException as error:
            self.close()
            if isinstance(error, Exception):
                raise ImportError("Unable to open import catalog") from None
            raise

    def _check(self) -> None:
        if self._closed or threading.get_ident() != self._thread:
            raise ImportError("Import catalog unavailable")

    def close(self) -> None:
        self._closed = True
        self._resources.close()

    def __enter__(self) -> "ImportCatalog":
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def count(self) -> int:
        self._check()
        return int(self._db.execute("SELECT count(*) FROM imports").fetchone()[0])

    def _snapshots(
        self, parent: int, document: ManifestDocument, resources: ExitStack
    ) -> tuple[int, int]:
        descriptors: list[int] = []
        for item in document.body.objects:
            fd = snapshot_file(
                parent, item.sha256, item.encoded_bytes, self.limits.max_encoded_bytes
            )
            resources.callback(os.close, fd)
            descriptors.append(fd)
        return descriptors[0], descriptors[1]

    def _validate(
        self, data: bytes, objects: tuple[int, int], resources: ExitStack
    ) -> ManifestDocument:
        document = parse_request(data, self.limits)
        manifest = sealed_bytes(data)
        resources.callback(os.close, manifest)
        result = self._decoder.decode((manifest, *objects))
        if len(result) > self.limits.max_message_bytes:
            raise ImportError("Decoder message limit exceeded")
        validate_result(result, document)
        return document

    def _recover_staging(self) -> None:
        # Only coordinator-generated pending names can be incomplete writes.
        # Remove their private staging link; retained digest objects are checked
        # again on every retry/inspection and never replaced or inferred valid.
        for name in os.listdir(self._objects):
            if re.fullmatch(r"pending-[0-9a-f]{32}", name) is None:
                continue
            fd = os.open(
                name, os.O_PATH | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=self._objects
            )
            try:
                info = os.fstat(fd)
                if (
                    not stat.S_ISREG(info.st_mode)
                    or info.st_uid != os.geteuid()
                    or info.st_nlink not in (1, 2)
                    or stat.S_IMODE(info.st_mode) not in (0o400, 0o600)
                ):
                    raise ImportError("Unsafe import staging")
                current = os.stat(name, dir_fd=self._objects, follow_symlinks=False)
                if (current.st_dev, current.st_ino) != (info.st_dev, info.st_ino):
                    raise ImportError("Import staging changed")
                os.unlink(name, dir_fd=self._objects)
            finally:
                os.close(fd)
        os.fsync(self._objects)

    def _quota(self, document: ManifestDocument) -> None:
        names = os.listdir(self._objects)
        if len(names) > self.limits.max_catalog_entries * 2:
            raise ImportError("Import storage quota exceeded")
        stored = 0
        for name in names:
            digest(name)
            pinned = os.open(
                name, os.O_PATH | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=self._objects
            )
            try:
                info = os.fstat(pinned)
                _regular(info, immutable=True)
                stored += info.st_size
            finally:
                os.close(pinned)
        added_bytes = sum(
            item.encoded_bytes
            for item in document.body.objects
            if item.sha256 not in names
        )
        free = os.fstatvfs(self._objects)
        if (
            stored + added_bytes > self.limits.max_storage_bytes
            or free.f_bavail * free.f_frsize < added_bytes + 1024 * 1024
        ):
            raise ImportError("Import storage quota exceeded")

    def _install(self, source: int, name: str, size: int) -> None:
        try:
            existing = snapshot_file(
                self._objects, name, size, self.limits.max_encoded_bytes
            )
        except ImportError:
            # Only true absence permits creation; corrupt/existing entries never
            # become replaceable and cannot be repaired by hostile input.
            try:
                os.stat(name, dir_fd=self._objects, follow_symlinks=False)
            except FileNotFoundError:
                pass
            else:
                raise ImportError("Conflicting stored object") from None
        else:
            os.close(existing)
            return
        temporary = f"pending-{uuid4().hex}"
        fd = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
            0o600,
            dir_fd=self._objects,
        )
        try:
            offset = 0
            while offset < size:
                block = os.pread(source, min(65536, size - offset), offset)
                if not block:
                    raise ImportError("Invalid staged object")
                write_all(fd, block)
                offset += len(block)
            os.fchmod(fd, 0o400)
            os.fsync(fd)
            os.link(
                temporary,
                name,
                src_dir_fd=self._objects,
                dst_dir_fd=self._objects,
                follow_symlinks=False,
            )
            os.fsync(self._objects)
        finally:
            os.close(fd)
            os.unlink(temporary, dir_fd=self._objects)
            os.fsync(self._objects)

    def accept(self, data: bytes, incoming: Path, receipt: ClockSample) -> ManifestRef:
        self._check()
        try:
            document = parse_request(data, self.limits)
            if not isinstance(receipt, ClockSample):
                raise TypeError("Import receipt required")
            quality = receipt.quality
            receipt_bytes = canonical_json_bytes(
                {
                    "wall_time_ns": receipt.wall_time_ns,
                    "monotonic_ns": receipt.monotonic_ns,
                    "session_id": receipt.session_id,
                    "quality": {
                        "epoch": quality.epoch,
                        "status": quality.status,
                        "offset_ns": quality.offset_ns,
                        "uncertainty_ns": quality.uncertainty_ns,
                        "evidence_age_ns": quality.evidence_age_ns,
                    },
                }
            )
            if len(receipt_bytes) > self.limits.max_message_bytes:
                raise ValueError("Receipt limit")
            previous = self._db.execute(
                "SELECT manifest_hash FROM imports WHERE producer=? AND epoch=? AND sequence=?",
                (document.body.producer, document.body.epoch, document.body.sequence),
            ).fetchone()
            if previous is not None and previous != (document.manifest_hash,):
                raise ImportError("Conflicting import identity")
            if previous is None and self.count() >= self.limits.max_catalog_entries:
                raise ImportError("Import catalog quota exceeded")
            with ExitStack() as resources:
                parent = resources.enter_context(
                    directory(incoming, os.geteuid(), private=True)
                )
                objects = self._snapshots(parent, document, resources)
                self._validate(data, objects, resources)
                if previous is not None:
                    return self.inspect(document.ref)
                self._quota(document)
                for fd, item in zip(objects, document.body.objects, strict=True):
                    self._install(fd, item.sha256, item.encoded_bytes)
                self._db.execute("BEGIN IMMEDIATE")
                try:
                    self._db.execute(
                        "INSERT INTO imports VALUES (?, ?, ?, ?, ?, ?)",
                        (
                            document.manifest_hash,
                            document.body.producer,
                            document.body.epoch,
                            document.body.sequence,
                            data,
                            receipt_bytes,
                        ),
                    )
                    self._db.execute("COMMIT")
                except BaseException:
                    self._db.execute("ROLLBACK")
                    raise
                row = self._db.execute(
                    "SELECT manifest FROM imports WHERE manifest_hash=?",
                    (document.manifest_hash,),
                ).fetchone()
                if row != (data,):
                    raise ImportError("Import catalog readback failed")
                return document.ref
        except Exception:
            raise ImportError("Import rejected") from None

    def inspect(self, reference: ManifestRef) -> ManifestRef:
        self._check()
        try:
            row = self._db.execute(
                "SELECT manifest FROM imports WHERE manifest_hash=?",
                (reference.manifest_hash,),
            ).fetchone()
            if row is None or type(row[0]) is not bytes:
                raise ImportError("Unknown import")
            data = row[0]
            document = parse_request(data, self.limits)
            if document.ref != reference:
                raise ImportError("Import identity mismatch")
            with ExitStack() as resources:
                objects = self._snapshots(self._objects, document, resources)
                self._validate(data, objects, resources)
            return document.ref
        except Exception:
            raise ImportError("Import inspection rejected") from None
