"""Bounded same-installation CLI control; the durable store keeps one owner."""

from __future__ import annotations

import asyncio
import json
import os
import stat
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any

from scryntic.archive.canonical import canonical_json_bytes
from scryntic.configuration.paths import Installation, directory
from scryntic.research.baselines import ALGORITHMS, REQUEST_BYTES

_NAME = "pi03-control.sock"
type Reply = tuple[dict[str, Any], int]
type Dispatch = Callable[[str, str], Awaitable[Reply]]


def _socket(parent: int, uid: int) -> os.stat_result:
    info = os.stat(_NAME, dir_fd=parent, follow_symlinks=False)
    if (
        not stat.S_ISSOCK(info.st_mode)
        or info.st_uid != uid
        or stat.S_IMODE(info.st_mode) != 0o600
    ):
        raise ValueError("Unsafe PI03 control socket")
    return info


async def active_owner(
    root: Installation, operation: str, algorithm: str
) -> Reply | None:
    if operation not in ("status", "cancel") or algorithm not in ALGORITHMS:
        raise ValueError("Unregistered PI03 control operation")
    with directory(root.state_dir, root.owner_uid, private=True) as parent:
        try:
            _socket(parent, root.owner_uid)
            # An fd-relative path also handles long installation paths on Linux.
            reader, writer = await asyncio.open_unix_connection(
                path=f"/proc/self/fd/{parent}/{_NAME}", limit=REQUEST_BYTES + 1
            )
        except (FileNotFoundError, ConnectionRefusedError):
            return None
        try:
            async with asyncio.timeout(30):
                writer.write(canonical_json_bytes([operation, algorithm]) + b"\n")
                await writer.drain()
                data = await reader.readline()
                if not data.endswith(b"\n") or len(data) > REQUEST_BYTES:
                    raise ValueError("PI03 control response bound")
                reply = json.loads(data)
                if (
                    type(reply) is not dict
                    or set(reply) != {"report", "code"}
                    or type(reply["report"]) is not dict
                    or type(reply["code"]) is not int
                    or reply["code"] not in (0, 1)
                ):
                    raise ValueError("Invalid PI03 control response")
                return reply["report"], reply["code"]
        finally:
            writer.close()
            await writer.wait_closed()


@asynccontextmanager
async def owner_control(root: Installation, dispatch: Dispatch) -> AsyncIterator[None]:
    """Called only while holding the installation's exclusive durable-store lock."""
    clients: set[asyncio.Task[Any]] = set()

    async def handle(
        reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        task = asyncio.current_task()
        assert task is not None
        clients.add(task)
        try:
            async with asyncio.timeout(2):
                request = json.loads(await reader.readline())
            if (
                type(request) is not list
                or len(request) != 2
                or request[0] not in ("status", "cancel")
                or request[1] not in ALGORITHMS
            ):
                raise ValueError("Unregistered PI03 control operation")
            async with asyncio.timeout(25):
                report, code = await dispatch(*request)
            data = canonical_json_bytes({"report": report, "code": code}) + b"\n"
            if len(data) > REQUEST_BYTES:
                raise ValueError("PI03 control response bound")
            async with asyncio.timeout(2):
                writer.write(data)
                await writer.drain()
        except (ValueError, OSError, TimeoutError):
            pass  # A malformed control request cannot mutate the workload policy.
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except OSError:
                pass
            clients.discard(task)

    with directory(root.state_dir, root.owner_uid, private=True) as parent:
        try:
            _socket(parent, root.owner_uid)
        except FileNotFoundError:
            pass
        else:
            os.unlink(_NAME, dir_fd=parent)  # Stale owner; exclusive lock is held.
        server = await asyncio.start_unix_server(
            handle, path=f"/proc/self/fd/{parent}/{_NAME}", limit=4096
        )
        os.chmod(_NAME, 0o600, dir_fd=parent, follow_symlinks=False)
        info = _socket(parent, root.owner_uid)
        try:
            yield
        finally:
            server.close()
            await server.wait_closed()
            if clients:
                pending = tuple(clients)
                try:
                    async with asyncio.timeout(5):
                        await asyncio.gather(*pending, return_exceptions=True)
                finally:
                    for task in pending:
                        if not task.done():
                            task.cancel()
                    await asyncio.gather(*pending, return_exceptions=True)
            current = _socket(parent, root.owner_uid)
            if (current.st_dev, current.st_ino) != (info.st_dev, info.st_ino):
                raise ValueError("PI03 control socket changed")
            os.unlink(_NAME, dir_fd=parent)
