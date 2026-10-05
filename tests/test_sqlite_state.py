"""Shared SQLite setup preserves pinned files, explicit transactions and cleanup."""

import os
import sqlite3
from contextlib import ExitStack
from pathlib import Path

import pytest

from scryntic.sqlite_state import connect_pinned, open_private_file, schema_matches


@pytest.mark.parametrize("kind", ["mode", "symlink", "hardlink"])
def test_private_file_rejects_unsafe_names(tmp_path: Path, kind: str) -> None:
    target = tmp_path / "target"
    target.touch(mode=0o600)
    name = tmp_path / "state"
    if kind == "symlink":
        name.symlink_to(target)
    elif kind == "hardlink":
        os.link(target, name)
    else:
        name.touch(mode=0o644)
    directory_fd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    owner_uid = os.getuid()
    file_name = name.name
    try:
        with pytest.raises((ValueError, OSError)):
            open_private_file(directory_fd, file_name, owner_uid)
    finally:
        os.close(directory_fd)


def test_connection_uses_explicit_transactions_and_schema_comparison(
    tmp_path: Path,
) -> None:
    with ExitStack() as resources:
        directory_fd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
        resources.callback(os.close, directory_fd)
        file_fd = open_private_file(directory_fd, "state.sqlite3", os.getuid())
        resources.callback(os.close, file_fd)
        connection = connect_pinned(
            directory_fd, "state.sqlite3", (("state.sqlite3", file_fd),), resources
        )
        assert connection.autocommit is True
        sql = "CREATE TABLE evidence (value INTEGER NOT NULL)"
        connection.execute(sql)
        assert schema_matches(connection, (("table", "evidence", "evidence", sql),))
        assert not schema_matches(connection, ())
        connection.execute("BEGIN IMMEDIATE")
        connection.execute("INSERT INTO evidence VALUES (7)")
        connection.execute("ROLLBACK")
        assert connection.execute("SELECT value FROM evidence").fetchall() == []
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        connection.execute("SELECT 1")


@pytest.mark.parametrize("domain_error", [False, True])
def test_replaced_file_closes_registered_connection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, domain_error: bool
) -> None:
    real_connect = sqlite3.connect
    opened: list[sqlite3.Connection] = []

    def connect(database: Path, *, autocommit: bool) -> sqlite3.Connection:
        connection = real_connect(database, autocommit=autocommit)
        opened.append(connection)
        return connection

    monkeypatch.setattr(sqlite3, "connect", connect)
    with ExitStack() as resources:
        directory_fd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
        resources.callback(os.close, directory_fd)
        file_fd = open_private_file(directory_fd, "state.sqlite3", os.getuid())
        resources.callback(os.close, file_fd)
        (tmp_path / "state.sqlite3").unlink()
        (tmp_path / "state.sqlite3").touch(mode=0o600)
        pinned_files = (("state.sqlite3", file_fd),)
        expected_error = RuntimeError("Domain unsafe file") if domain_error else None
        error_type = RuntimeError if domain_error else ValueError
        with pytest.raises(error_type, match="unsafe|Unsafe"):
            connect_pinned(
                directory_fd,
                "state.sqlite3",
                pinned_files,
                resources,
                unsafe_file_error=expected_error,
            )
    (connection,) = opened
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        connection.execute("SELECT 1")
