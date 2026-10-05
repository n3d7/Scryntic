"""Secure SQLite file setup and schema comparison shared by local state owners."""

import os
import sqlite3
import stat
from collections.abc import Iterable
from contextlib import ExitStack
from pathlib import Path


def open_private_file(state_fd: int, name: str, owner_uid: int) -> int:
    fd = os.open(
        name,
        os.O_RDWR | os.O_CREAT | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK,
        0o600,
        dir_fd=state_fd,
    )
    try:
        info = os.fstat(fd)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != owner_uid
            or info.st_nlink != 1
            or stat.S_IMODE(info.st_mode) != 0o600
        ):
            raise ValueError("Unsafe SQLite state file")
        return fd
    except BaseException:
        os.close(fd)
        raise


def connect_pinned(
    state_fd: int,
    database_name: str,
    files: tuple[tuple[str, int], ...],
    resources: ExitStack,
    *,
    unsafe_file_error: Exception | None = None,
) -> sqlite3.Connection:
    """Register connection cleanup before checking the validated file identities."""
    database = Path(f"/proc/self/fd/{state_fd}/{database_name}")
    connection = sqlite3.connect(database, autocommit=True)
    resources.callback(connection.close)
    for name, fd in files:
        pinned = os.fstat(fd)
        current = os.stat(name, dir_fd=state_fd, follow_symlinks=False)
        if (pinned.st_dev, pinned.st_ino) != (current.st_dev, current.st_ino):
            if unsafe_file_error is not None:
                raise unsafe_file_error
            raise ValueError("Unsafe SQLite state file")
    return connection


def schema_matches(
    connection: sqlite3.Connection, schema: Iterable[tuple[str, str, str, str]]
) -> bool:
    rows = connection.execute(
        "SELECT type, name, tbl_name, sql FROM sqlite_schema "
        "WHERE name NOT GLOB 'sqlite_*' ORDER BY name"
    ).fetchall()
    actual = tuple((r[0], r[1], r[2], " ".join(r[3].split())) for r in rows)
    expected = tuple(
        sorted(
            (
                (kind, name, table, " ".join(sql.split()))
                for kind, name, table, sql in schema
            ),
            key=lambda row: row[1],
        )
    )
    return actual == expected
