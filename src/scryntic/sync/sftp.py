"""Pinned, credential-local SFTP list/read adapter.

AsyncSSH 2.24.0's convenience SFTP opener does not bound incoming packet lengths.
The narrowly pinned ``asyncssh.sftp.start_sftp_client`` factory permits a bounded
reader without replacing library globals. Its exact signature and reader usage
must be rechecked when upgrading AsyncSSH. SFTP v3 cannot atomically prove that a
remote pathname stayed unchanged between lstat and open; final object hashes and
manifest continuity remain the coordinator's responsibility.
"""

import asyncio
import stat
from collections.abc import AsyncGenerator, Awaitable
from types import TracebackType
from typing import cast

import asyncssh
from asyncssh.logging import SSHLogger
from asyncssh.sftp import start_sftp_client

from scryntic.configuration.credentials import FileCredentials
from scryntic.configuration.paths import Installation
from scryntic.configuration.values import Capability, Configuration
from scryntic.sync.model import (
    Enrollment,
    PullError,
    PullLimits,
    RemoteEntry,
    relative_path,
)

_PACKET_BYTES = 256 * 1024
_INVALID_REMOTE_ENTRY = "Invalid remote entry"


class _BoundedReader:
    """Only the two methods used by the pinned SFTP client handler."""

    def __init__(self, reader: asyncssh.SSHReader[bytes]) -> None:
        self._reader = reader

    @property
    def logger(self) -> SSHLogger:
        return self._reader.logger

    async def readexactly(self, count: int) -> bytes:
        if count < 0 or count > _PACKET_BYTES:
            raise asyncssh.SFTPBadMessage("SFTP packet exceeds local limit")
        return await self._reader.readexactly(count)

    def get_extra_info(self, name: str, default: object = None) -> object:
        return cast(object, self._reader.get_extra_info(name, default))


class SFTPRemote:
    """Expose no commands, forwarding, remote writes or credential access."""

    def __init__(
        self,
        enrollment: Enrollment,
        installation: Installation,
        configuration: Configuration,
        limits: PullLimits | None = None,
    ) -> None:
        self._enrollment = enrollment
        self._installation = installation
        self._configuration = configuration
        self._credentials = FileCredentials(
            installation, configuration, Capability.SYNCHRONIZATION
        )
        self._limits = limits or PullLimits()
        self._connection: asyncssh.SSHClientConnection | None = None
        self._client: asyncssh.SFTPClient | None = None

    @property
    def enrollment(self) -> Enrollment:
        return self._enrollment

    async def __aenter__(self) -> "SFTPRemote":
        if self._connection is not None:
            raise PullError("SFTP adapter already connected")
        try:
            host_key = asyncssh.import_public_key(self.enrollment.host_key)
            with self._credentials.open() as secret:
                key = asyncssh.import_private_key(bytes(secret.view()))
            async with asyncio.timeout(self._limits.operation_seconds):
                self._connection = await asyncssh.connect(
                    self.enrollment.host,
                    self.enrollment.port,
                    username=self.enrollment.username,
                    config=None,
                    known_hosts=([host_key], [], [], [], [], [], []),
                    server_host_key_algs=["ssh-ed25519"],
                    client_keys=[key],
                    client_certs=[],
                    agent_path=None,
                    agent_identities=None,
                    agent_forwarding=False,
                    pkcs11_provider=None,
                    public_key_auth=True,
                    host_based_auth=False,
                    password_auth=False,
                    kbdint_auth=False,
                    gss_auth=False,
                    gss_kex=False,
                    preferred_auth=["publickey"],
                    disable_trivial_auth=True,
                    connect_timeout=self._limits.operation_seconds,
                    login_timeout=self._limits.operation_seconds,
                    x11_forwarding=False,
                    request_pty=False,
                    compression_algs=["none"],
                )
                # A bounded SSH receive window also limits buffering while the
                # SFTP packet reader validates the remote 32-bit frame length.
                writer, reader, _ = await self._connection.open_session(
                    subsystem="sftp",
                    encoding=None,
                    env={},
                    send_env=[],
                    window=_PACKET_BYTES,
                    max_pktsize=32768,
                )
                bounded = _BoundedReader(reader)
                self._client = await start_sftp_client(
                    self._connection,
                    asyncio.get_running_loop(),
                    "strict",
                    cast(asyncssh.SSHReader[bytes], bounded),
                    writer,
                    "utf-8",
                    "strict",
                    3,
                )
                await self._directory(self.enrollment.remote_root)
            return self
        except asyncio.CancelledError:
            await self._close()
            raise
        except Exception:
            await self._close()
            raise PullError("SFTP connection failed") from None

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        await self._close()

    async def _close(self) -> None:
        client, connection = self._client, self._connection
        self._client = None
        self._connection = None
        if client is not None:
            client.exit()
        if connection is not None:
            # Immediate transport teardown does not wait for an untrusted peer.
            connection.abort()
            try:
                async with asyncio.timeout(self._limits.operation_seconds):
                    await connection.wait_closed()
            except (asyncssh.Error, OSError):
                pass

    def _connected(self) -> asyncssh.SFTPClient:
        if self._client is None:
            raise PullError("SFTP adapter not connected")
        return self._client

    def _path(self, relative: str, *, directory: bool = False) -> str:
        if directory and relative == ".":
            return self.enrollment.remote_root
        try:
            relative_path(relative)
        except ValueError:
            raise PullError("Invalid remote path") from None
        return self.enrollment.remote_root + "/" + relative

    @staticmethod
    def _mode(attrs: asyncssh.SFTPAttrs) -> int:
        mode = attrs.permissions
        if type(mode) is not int or mode < 0:
            raise PullError("Invalid remote metadata")
        return mode

    async def _directory(self, path: str) -> None:
        client = self._connected()
        current = ""
        for component in path[1:].split("/"):
            current += "/" + component
            if not stat.S_ISDIR(self._mode(await client.lstat(current))):
                raise PullError("Unsafe remote directory")

    async def _bounded[T](self, operation: Awaitable[T]) -> T:
        task = asyncio.ensure_future(operation)
        try:
            completed, _ = await asyncio.wait(
                [task], timeout=self._limits.operation_seconds
            )
            if not completed:
                raise TimeoutError
            return task.result()
        except asyncio.CancelledError:
            await self._close()
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            raise
        except TimeoutError:
            # Abort before cancelling the operation: an async file/iterator
            # finally block may otherwise wait indefinitely for remote CLOSE.
            await self._close()
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            raise PullError("SFTP operation failed") from None
        except (asyncssh.Error, OSError, ValueError):
            raise PullError("SFTP operation failed") from None

    async def entries(self, relative: str) -> tuple[RemoteEntry, ...]:
        path = self._path(relative, directory=True)
        self._connected()
        return await self._bounded(self._entries(path))

    @staticmethod
    def _entry_name(name: object) -> str:
        if not isinstance(name, str) or "/" in name:
            raise PullError(_INVALID_REMOTE_ENTRY)
        try:
            relative_path(name)
        except ValueError:
            raise PullError(_INVALID_REMOTE_ENTRY) from None
        return name

    async def _entries(self, path: str) -> tuple[RemoteEntry, ...]:
        client = self._connected()
        await self._directory(path)
        result: list[RemoteEntry] = []
        iterator = cast(AsyncGenerator[asyncssh.SFTPName, None], client.scandir(path))
        try:
            async for entry in iterator:
                name = entry.filename
                if name in (".", ".."):
                    continue
                if len(result) >= self._limits.max_entries:
                    raise PullError("Remote listing exceeds limit")
                name = self._entry_name(name)
                attrs = await client.lstat(path + "/" + name)
                mode = self._mode(attrs)
                if not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)):
                    raise PullError("Unsafe remote entry")
                if type(attrs.size) is not int or attrs.size < 0:
                    raise PullError("Invalid remote metadata")
                result.append(RemoteEntry(name, mode, attrs.size))
        finally:
            await iterator.aclose()
        return tuple(sorted(result, key=lambda entry: entry.name))

    async def read(self, relative: str, offset: int, count: int) -> bytes:
        path = self._path(relative)
        if (
            type(offset) is not int
            or not 0 <= offset <= 2**63 - 1
            or type(count) is not int
            or not 1 <= count <= self._limits.chunk_bytes
        ):
            raise PullError("Invalid remote read bounds")
        self._connected()
        return await self._bounded(self._read(path, offset, count))

    async def _read(self, path: str, offset: int, count: int) -> bytes:
        client = self._connected()
        await self._directory(path.rsplit("/", 1)[0])
        if not stat.S_ISREG(self._mode(await client.lstat(path))):
            raise PullError("Unsafe remote file")
        async with client.open(
            path,
            "rb",
            encoding=None,
            block_size=self._limits.chunk_bytes,
            max_requests=1,
        ) as file:
            if not stat.S_ISREG(self._mode(await file.stat())):
                raise PullError("Unsafe remote file")
            data: object = await file.read(count, offset)
            if not isinstance(data, bytes) or len(data) > count:
                raise PullError("Invalid remote read response")
            return data
