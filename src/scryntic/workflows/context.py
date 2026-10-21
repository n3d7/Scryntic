"""Protected local artifacts and retained, single-owner blocking operations."""

import asyncio
import json
import os
import stat
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
from pathlib import Path
from typing import cast

from scryntic.application.operations import ApplicationError, ErrorCode
from scryntic.configuration.paths import Installation, directory, read_file
from scryntic.configuration.values import BoundaryError, reference_name


class WorkflowContext:
    def __init__(self, installation: Installation | None = None) -> None:
        self.installation = installation or Installation.workstation()
        self._executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="application-owner"
        )
        self._pending: set[asyncio.Future[object]] = set()
        self._closing = False
        self._close_task: asyncio.Task[None] | None = None
        self.code_revision = "f24-source-" + self._source_digest()
        self.dependency_lock_sha256 = LOCK_SHA256

    @staticmethod
    def _source_digest() -> str:
        root = Path(__file__).resolve().parents[1]
        digest = sha256()
        for path in sorted(root.rglob("*.py")):
            digest.update(path.relative_to(root).as_posix().encode())
            digest.update(path.read_bytes())
        return digest.hexdigest()

    async def run_io[T](self, operation: Callable[[], T]) -> T:
        if self._closing or self._pending:
            raise ApplicationError(ErrorCode.BUSY)
        future = asyncio.get_running_loop().run_in_executor(self._executor, operation)
        retained = cast(asyncio.Future[object], future)
        self._pending.add(retained)

        def completed(_: asyncio.Future[T]) -> None:
            self._pending.discard(retained)
            if not future.cancelled():
                future.exception()

        future.add_done_callback(completed)
        return await asyncio.shield(future)

    async def _close(self) -> None:
        self._closing = True
        while self._pending:
            await asyncio.wait(tuple(self._pending))
        self._executor.shutdown(wait=True)

    async def aclose(self) -> None:
        if self._close_task is None:
            self._close_task = asyncio.create_task(self._close())
        cancellation = None
        while not self._close_task.done():
            try:
                await asyncio.shield(self._close_task)
            except asyncio.CancelledError as interrupted:
                cancellation = interrupted
        self._close_task.result()
        if cancellation is not None:
            raise cancellation

    def read_config_json(self, leaf: str, maxbytes: int = 65536) -> dict[str, object]:
        if type(maxbytes) is not int or not 1 <= maxbytes <= 1024 * 1024:
            raise ApplicationError(ErrorCode.INVALID)
        try:
            data = read_file(
                self.installation.config_dir,
                leaf,
                self.installation.config_uid,
                private=not self.installation.service_mode,
                secret=True,
                limit=maxbytes,
            )
            document = json.loads(data, object_pairs_hook=_unique_fields)
            if not isinstance(document, dict):
                raise ValueError("expected object")
            return cast(dict[str, object], document)
        except (OSError, ValueError, RecursionError):
            raise ApplicationError(ErrorCode.CONFIGURATION) from None

    def export_bundle(self, leaf: str, files: dict[str, bytes]) -> dict[str, object]:
        try:
            reference_name(leaf)
        except BoundaryError:
            raise ApplicationError(ErrorCode.INVALID) from None
        allowed = {
            "dataset.manifest.json",
            "dataset.parquet",
            "replay.json",
            "source-use-policy.json",
            "preparation.json",
        }
        if (
            not files
            or set(files) - allowed
            or any(type(v) is not bytes for v in files.values())
            or sum(map(len, files.values())) > 96 * 1024 * 1024
        ):
            raise ApplicationError(ErrorCode.INVALID)
        try:
            with directory(
                self.installation.state_dir, self.installation.owner_uid, private=True
            ) as state:
                try:
                    os.mkdir("exports", mode=0o700, dir_fd=state)
                    os.fsync(state)
                except FileExistsError:
                    pass
                root = os.open(
                    "exports",
                    os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                    dir_fd=state,
                )
                try:
                    _private_directory(root, self.installation.owner_uid)
                    os.mkdir(leaf, mode=0o700, dir_fd=root)
                    os.fsync(root)
                    target = os.open(
                        leaf,
                        os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                        dir_fd=root,
                    )
                    try:
                        _private_directory(target, self.installation.owner_uid)
                        hashes = {
                            name: sha256(data).hexdigest()
                            for name, data in files.items()
                        }
                        for name, data in files.items():
                            _write(target, name, data)
                        _write(
                            target,
                            "complete.json",
                            json.dumps(
                                {"version": 1, "sha256": hashes},
                                sort_keys=True,
                                separators=(",", ":"),
                            ).encode(),
                        )
                        os.fsync(target)
                    finally:
                        os.close(target)
                    os.fsync(root)
                    return {"destination": leaf, "complete": True, "sha256": hashes}
                finally:
                    os.close(root)
        except FileExistsError:
            raise ApplicationError(ErrorCode.BUSY) from None
        except (OSError, BoundaryError):
            # Any partial destination remains explicitly incomplete without a committed marker.
            raise ApplicationError(ErrorCode.INCOMPLETE) from None


def _unique_fields(pairs: list[tuple[str, object]]) -> dict[str, object]:
    fields: dict[str, object] = {}
    for key, value in pairs:
        if key in fields:
            raise ValueError("duplicate field")
        fields[key] = value
    return fields


def _private_directory(fd: int, uid: int) -> None:
    info = os.fstat(fd)
    if (
        info.st_uid != uid
        or not stat.S_ISDIR(info.st_mode)
        or stat.S_IMODE(info.st_mode) != 0o700
    ):
        raise ApplicationError(ErrorCode.CONFIGURATION)


def _write(parent: int, name: str, data: bytes) -> None:
    fd = os.open(
        name,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
        0o600,
        dir_fd=parent,
    )
    try:
        view = memoryview(data)
        while view:
            written = os.write(fd, view)
            if written <= 0:
                raise OSError("incomplete export")
            view = view[written:]
        os.fsync(fd)
    finally:
        os.close(fd)


LOCK_SHA256 = "4f28c9827902e55d6d704b6e34e2bc87383e4179ae296ae2488c397105b1efa9"
