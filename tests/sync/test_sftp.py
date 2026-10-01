"""Real in-process SSH/SFTP checks for the credential-bearing adapter."""

import asyncio
import dataclasses
import os
import stat
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path

import asyncssh
import pytest

from scryntic.configuration.paths import Installation
from scryntic.configuration.values import (
    Capability,
    Configuration,
    Profile,
    SecretReference,
)
from scryntic.sync.model import Enrollment, PullError, PullLimits
from scryntic.sync.sftp import SFTPRemote, _BoundedReader


class Server(asyncssh.SSHServer):
    def __init__(self, key: asyncssh.SSHKey, auth: list[str]) -> None:
        self.key = key
        self.auth = auth

    def begin_auth(self, username: str) -> bool:
        self.auth.append(username)
        return True

    def public_key_auth_supported(self) -> bool:
        return True

    def validate_public_key(self, username: str, key: asyncssh.SSHKey) -> bool:
        return username == "reader" and key == self.key


class Files(asyncssh.SFTPServer):
    def open(self, path: bytes, pflags: int, attrs: asyncssh.SFTPAttrs) -> object:
        if pflags != asyncssh.FXF_READ:
            raise asyncssh.SFTPPermissionDenied("read-only server")
        return super().open(path, pflags, attrs)

    def write(self, file_obj: object, offset: int, data: bytes) -> int:
        raise asyncssh.SFTPPermissionDenied("read-only server")

    def setstat(self, path: bytes, attrs: asyncssh.SFTPAttrs) -> None:
        raise asyncssh.SFTPPermissionDenied("read-only server")

    lsetstat = setstat
    mkdir = setstat

    def fsetstat(self, file_obj: object, attrs: asyncssh.SFTPAttrs) -> None:
        raise asyncssh.SFTPPermissionDenied("read-only server")

    def remove(self, path: bytes) -> None:
        raise asyncssh.SFTPPermissionDenied("read-only server")

    rmdir = remove

    def rename(self, oldpath: bytes, newpath: bytes) -> None:
        raise asyncssh.SFTPPermissionDenied("read-only server")

    posix_rename = rename
    link = rename
    symlink = rename


@asynccontextmanager
async def endpoint(
    tmp_path: Path, *, delay: bool = False
) -> AsyncIterator[tuple[SFTPRemote, list[str], Path, asyncssh.SSHKey]]:
    client_key = asyncssh.generate_private_key("ssh-ed25519")
    host_key = asyncssh.generate_private_key("ssh-ed25519")
    key_dir = tmp_path / "credentials"
    key_dir.mkdir(mode=0o700)
    key_path = key_dir / "pull-key"
    key_path.write_bytes(client_key.export_private_key())
    key_path.chmod(0o400)
    installation = Installation(
        tmp_path, tmp_path, tmp_path, key_dir, os.getuid(), os.getuid(), False
    )
    configuration = Configuration(
        Profile.WORKSTATION,
        "info",
        frozenset({Capability.SYNCHRONIZATION}),
        (SecretReference(Capability.SYNCHRONIZATION, "file", "pull-key"),),
    )
    jail = tmp_path / "remote"
    root = jail / "published"
    root.mkdir(parents=True)
    (root / "object").write_bytes(b"abcdefghij")
    auth: list[str] = []

    class DelayedFiles(Files):
        async def read(self, file_obj: object, offset: int, size: int) -> bytes:
            if delay:
                await asyncio.sleep(30)
            result = super().read(file_obj, offset, size)
            assert isinstance(result, bytes)
            return result

    listener = await asyncssh.create_server(
        lambda: Server(client_key.convert_to_public(), auth),
        "127.0.0.1",
        0,
        server_host_keys=[host_key],
        sftp_factory=lambda channel: DelayedFiles(channel, chroot=os.fsencode(jail)),
        encoding=None,
    )
    enrollment = Enrollment(
        "test",
        "127.0.0.1",
        listener.get_port(),
        "reader",
        "/published",
        host_key.export_public_key().decode().strip(),
        "producer",
        "epoch",
    )
    remote = SFTPRemote(enrollment, installation, configuration)
    try:
        yield remote, auth, root, client_key
    finally:
        listener.close()
        await listener.wait_closed()


def test_pinned_authenticated_list_and_bounded_read(tmp_path: Path) -> None:
    async def run() -> None:
        async with endpoint(tmp_path) as (remote, auth, _, _):
            async with remote:
                entries = await remote.entries(".")
                assert [(e.name, e.size) for e in entries] == [("object", 10)]
                assert stat.S_ISREG(entries[0].mode)
                assert await remote.read("object", 3, 4) == b"defg"
                assert await remote.read("object", 10, 4) == b""
                assert not hasattr(remote, "write")
                assert not hasattr(remote, "run")
            assert auth == ["reader"]
            with pytest.raises(PullError, match="not connected"):
                await remote.read("object", 0, 1)

    asyncio.run(run())


def test_wrong_host_pin_rejected_before_authentication(tmp_path: Path) -> None:
    async def run() -> None:
        async with endpoint(tmp_path) as (remote, auth, _, _):
            remote = SFTPRemote(
                dataclasses.replace(
                    remote.enrollment,
                    host_key=asyncssh.generate_private_key("ssh-ed25519")
                    .export_public_key()
                    .decode()
                    .strip(),
                ),
                remote._installation,
                remote._configuration,
            )
            with pytest.raises(PullError, match="connection failed") as exc:
                async with remote:
                    pytest.fail("incorrect host accepted")
            assert exc.value.__suppress_context__
            assert auth == []

    asyncio.run(run())


def test_wrong_private_key_is_rejected(tmp_path: Path) -> None:
    async def run() -> None:
        async with endpoint(tmp_path) as (remote, auth, _, _):
            key = tmp_path / "credentials" / "pull-key"
            key.chmod(0o600)
            key.write_bytes(
                asyncssh.generate_private_key("ssh-ed25519").export_private_key()
            )
            key.chmod(0o400)
            with pytest.raises(PullError, match="connection failed"):
                async with remote:
                    pytest.fail("incorrect client key accepted")
            assert auth == ["reader"]

    asyncio.run(run())


@pytest.mark.parametrize("unsafe", ["mode", "symlink", "invalid"])
def test_unsafe_or_invalid_key_rejected_and_redacted(
    tmp_path: Path, unsafe: str
) -> None:
    async def run() -> None:
        async with endpoint(tmp_path) as (remote, auth, _, _):
            key = tmp_path / "credentials" / "pull-key"
            if unsafe == "mode":
                key.chmod(0o644)
            elif unsafe == "symlink":
                key.rename(key.with_name("other"))
                key.symlink_to("other")
            else:
                key.chmod(0o600)
                key.write_text("SECRET_BAD_KEY_SENTINEL")
                key.chmod(0o400)
            with pytest.raises(PullError) as exc:
                async with remote:
                    pytest.fail("unsafe key accepted")
            assert "SECRET_BAD_KEY_SENTINEL" not in str(exc.value)
            assert "pull-key" not in str(exc.value)
            assert auth == []

    asyncio.run(run())


@pytest.mark.parametrize(
    "relative", ["../object", "/published/object", "a//b", "a/../b"]
)
def test_paths_validated_before_network(tmp_path: Path, relative: str) -> None:
    async def run() -> None:
        async with endpoint(tmp_path) as (remote, _, _, _):
            async with remote:
                with pytest.raises(PullError, match="path"):
                    await remote.read(relative, 0, 1)

    asyncio.run(run())


def test_symlinks_and_nonregular_files_are_rejected(tmp_path: Path) -> None:
    async def run() -> None:
        async with endpoint(tmp_path) as (remote, _, root, _):
            (root / "link").symlink_to("object")
            (root / "directory").mkdir()
            (root / "directory" / "link").symlink_to("../object")
            async with remote:
                for name in ("link", "directory", "directory/link"):
                    with pytest.raises(PullError, match="remote"):
                        await remote.read(name, 0, 1)
                with pytest.raises(PullError, match="remote"):
                    await remote.entries(".")
                (root / "link").unlink()
                (root / "directory" / "link").unlink()
                os.mkfifo(root / "fifo")
                with pytest.raises(PullError, match="remote"):
                    await remote.read("fifo", 0, 1)

    asyncio.run(run())


def test_listing_and_read_limits(tmp_path: Path) -> None:
    async def run() -> None:
        async with endpoint(tmp_path) as (remote, _, root, _):
            remote._limits = PullLimits(max_entries=1, chunk_bytes=4)
            (root / "second").write_bytes(b"second")
            async with remote:
                with pytest.raises(PullError, match="limit"):
                    await remote.entries(".")
                for offset, count in [(-1, 1), (0, 0), (0, 5), (True, 1)]:
                    with pytest.raises(PullError, match="read"):
                        await remote.read("object", offset, count)

    asyncio.run(run())


def test_stalled_read_deadline_and_cancellation_close(tmp_path: Path) -> None:
    async def run() -> None:
        async with endpoint(tmp_path, delay=True) as (remote, _, _, _):
            remote._limits = PullLimits(operation_seconds=1)
            started = time.monotonic()
            with pytest.raises(PullError, match="operation failed"):
                async with remote:
                    await remote.read("object", 0, 4)
            assert time.monotonic() - started < 3
            assert remote._connection is None
            async with remote:
                task = asyncio.create_task(remote.read("object", 0, 4))
                await asyncio.sleep(0.02)
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
            assert remote._connection is None

    asyncio.run(run())


def test_server_separately_rejects_shell_forwarding_and_writes(tmp_path: Path) -> None:
    async def run() -> None:
        async with endpoint(tmp_path) as (remote, _, _, key):
            e = remote.enrollment
            host = asyncssh.import_public_key(e.host_key)
            async with asyncssh.connect(
                e.host,
                e.port,
                username=e.username,
                config=None,
                known_hosts=([host], [], [], [], [], [], []),
                client_keys=[key],
                agent_path=None,
            ) as conn:
                with pytest.raises(asyncssh.ChannelOpenError):
                    await conn.run("echo forbidden")
                with pytest.raises(asyncssh.ChannelOpenError):
                    await conn.open_connection("127.0.0.1", 1)
                async with conn.start_sftp_client() as client:
                    with pytest.raises(asyncssh.SFTPPermissionDenied):
                        await client.open("/published/object", "wb")
                    mutations: tuple[Callable[[], Awaitable[None]], ...] = (
                        lambda: client.remove("/published/object"),
                        lambda: client.mkdir("/published/new"),
                        lambda: client.rename("/published/object", "/published/new"),
                        lambda: client.chmod("/published/object", 0o777),
                        lambda: client.symlink("object", "/published/new"),
                        lambda: client.link("/published/object", "/published/new"),
                    )
                    for mutation in mutations:
                        with pytest.raises(asyncssh.SFTPPermissionDenied):
                            await mutation()

    asyncio.run(run())


@pytest.mark.parametrize("first", [True, False])
def test_oversized_sftp_packet_rejected_without_body(
    tmp_path: Path, first: bool
) -> None:
    class PacketSession(asyncssh.SSHServerSession[bytes]):
        def connection_made(self, chan: asyncssh.SSHServerChannel[bytes]) -> None:
            self.channel = chan

        def subsystem_requested(self, subsystem: str) -> bool:
            return subsystem == "sftp"

        def session_started(self) -> None:
            if not first:
                self.channel.write(b"\x00\x00\x00\x05\x02\x00\x00\x00\x03")
            # No body follows. The length must fail before a huge allocation or
            # deadline-based rejection, both during VERSION and normal packets.
            self.channel.write(b"\xff\xff\xff\xff")

    class PacketServer(Server):
        def session_requested(self) -> PacketSession:
            return PacketSession()

    async def run() -> None:
        async with endpoint(tmp_path) as (remote, _, _, key):
            host = asyncssh.generate_private_key("ssh-ed25519")
            listener = await asyncssh.create_server(
                lambda: PacketServer(key.convert_to_public(), []),
                "127.0.0.1",
                0,
                server_host_keys=[host],
                encoding=None,
            )
            bad = SFTPRemote(
                dataclasses.replace(
                    remote.enrollment,
                    port=listener.get_port(),
                    host_key=host.export_public_key().decode().strip(),
                ),
                remote._installation,
                remote._configuration,
                PullLimits(operation_seconds=2),
            )
            started = time.monotonic()
            try:
                with pytest.raises(PullError, match="connection failed"):
                    async with bad:
                        pytest.fail("oversized packet accepted")
                assert time.monotonic() - started < 1
                assert bad._connection is None
            finally:
                listener.close()
                await listener.wait_closed()

    asyncio.run(run())


def test_packet_bound_checked_before_reader_access() -> None:
    class Probe(asyncssh.SSHReader[bytes]):
        # No actual stream: touching it would fail. An oversized requested read
        # must be rejected without invoking the delegated reader at all.
        async def readexactly(self, n: int) -> bytes:
            pytest.fail("unbounded body read reached SSH stream")

    async def run() -> None:
        reader = _BoundedReader(Probe.__new__(Probe))
        with pytest.raises(asyncssh.SFTPBadMessage):
            await reader.readexactly(2**32 - 1)

    asyncio.run(run())


def test_symlink_artifact_root_rejected(tmp_path: Path) -> None:
    async def run() -> None:
        async with endpoint(tmp_path) as (remote, _, root, _):
            root.rename(root.with_name("actual"))
            root.symlink_to("actual")
            with pytest.raises(PullError, match="connection failed"):
                async with remote:
                    pytest.fail("symlink artifact root accepted")
            assert remote._connection is None

    asyncio.run(run())


def test_connection_handshake_has_deadline(tmp_path: Path) -> None:
    async def run() -> None:
        writers: list[asyncio.StreamWriter] = []

        def stall(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            writers.append(writer)

        async with endpoint(tmp_path) as (remote, _, _, _):
            listener = await asyncio.start_server(stall, "127.0.0.1", 0)
            port = listener.sockets[0].getsockname()[1]
            held = SFTPRemote(
                dataclasses.replace(remote.enrollment, port=port),
                remote._installation,
                remote._configuration,
                PullLimits(operation_seconds=1),
            )
            try:
                started = time.monotonic()
                with pytest.raises(PullError, match="connection failed"):
                    async with held:
                        pytest.fail("stalled handshake accepted")
                assert time.monotonic() - started < 3
                assert held._connection is None
            finally:
                listener.close()
                for writer in writers:
                    writer.close()
                    await writer.wait_closed()
                await listener.wait_closed()

    asyncio.run(run())


def test_cancel_during_authentication_closes_connection(tmp_path: Path) -> None:
    async def run() -> None:
        entered = asyncio.Event()
        closed = asyncio.Event()

        class HeldAuth(asyncssh.SSHServer):
            async def begin_auth(self, username: str) -> bool:
                entered.set()
                await asyncio.sleep(30)
                return True

            def connection_lost(self, exc: Exception | None) -> None:
                closed.set()

        async with endpoint(tmp_path) as (remote, _, _, _):
            host = asyncssh.generate_private_key("ssh-ed25519")
            listener = await asyncssh.create_server(
                HeldAuth,
                "127.0.0.1",
                0,
                server_host_keys=[host],
                encoding=None,
            )
            held = SFTPRemote(
                dataclasses.replace(
                    remote.enrollment,
                    port=listener.get_port(),
                    host_key=host.export_public_key().decode().strip(),
                ),
                remote._installation,
                remote._configuration,
            )
            try:
                task = asyncio.create_task(held.__aenter__())
                await asyncio.wait_for(entered.wait(), 2)
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
                await asyncio.wait_for(closed.wait(), 2)
                assert held._connection is None
            finally:
                listener.close()
                await listener.wait_closed()

    asyncio.run(run())
