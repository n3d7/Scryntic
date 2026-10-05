"""Recovery cannot reuse a writer whose rollback leaves a transaction open."""

import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

import pytest

from scryntic.ingestion.sqlite_spool import DurableIngestor, IngestionError
from tests.normalization.helpers import installation


class _RollbackNoopConnection(sqlite3.Connection):
    inject_failure = False
    rollback_left_transaction_open = False

    def execute(self, sql: str, parameters: Any = (), /) -> sqlite3.Cursor:
        if self.inject_failure and sql == "COMMIT":
            raise sqlite3.OperationalError("Injected recovery commit failure")
        if self.inject_failure and sql == "ROLLBACK":
            self.rollback_left_transaction_open = self.in_transaction
            return super().execute("SELECT 1")
        return super().execute(sql, parameters)


def test_recovery_rollback_remaining_in_transaction_poisoning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_connect = sqlite3.connect
    opened: list[_RollbackNoopConnection] = []

    def connect(database: Path, **kwargs: Any) -> _RollbackNoopConnection:
        connection = real_connect(database, factory=_RollbackNoopConnection, **kwargs)
        opened.append(connection)
        return connection

    monkeypatch.setattr(sqlite3, "connect", connect)
    target = installation(tmp_path)
    with DurableIngestor(
        target,
        producer="collector-a",
        epoch="epoch-a",
        capacity=2,
        max_payload_bytes=4096,
    ) as spool:
        (connection,) = opened
        connection.inject_failure = True
        with pytest.raises(IngestionError, match="roll back recovery metadata"):
            spool.recovery_put("stream-a", b"pending", expected=None)
        assert connection.rollback_left_transaction_open
        with pytest.raises(IngestionError, match="writer failed"):
            spool.status()
        with pytest.raises(IngestionError, match="writer failed"):
            spool.recovery_get("stream-a")
        with pytest.raises(IngestionError, match="writer failed"):
            spool.recovery_put("stream-a", b"later", expected=None)
    with closing(
        real_connect(target.state_dir / "ingestion.sqlite3", autocommit=True)
    ) as reader:
        assert reader.execute("SELECT count(*) FROM recovery_state").fetchone() == (0,)
        assert reader.execute("SELECT count(*) FROM raw_records").fetchone() == (0,)
