"""The same durable writer reserves a bounded lane for recovery metadata."""

import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from scryntic.domain.identity import InstrumentId
from scryntic.domain.raw import RawEnvelope, RawRecord
from scryntic.ingestion.sqlite_spool import DurableIngestor, IngestionError
from tests.ingestion.test_sqlite_spool import envelope, installation


def test_metadata_commit_cas_and_restart(tmp_path: Path) -> None:
    target = installation(tmp_path)
    with DurableIngestor(
        target, producer="a", epoch="a", capacity=1, max_payload_bytes=1024
    ) as spool:
        assert spool.recovery_get("stream-a") is None
        spool.recovery_put("stream-a", b"active", expected=None)
        with pytest.raises(IngestionError, match="changed"):
            spool.recovery_put("stream-a", b"stale", expected=None)
    with DurableIngestor(
        target, producer="a", epoch="b", capacity=1, max_payload_bytes=1024
    ) as spool:
        assert spool.recovery_get("stream-a") == b"active"
        spool.recovery_put("stream-a", b"closed", expected=b"active")
        with pytest.raises(ValueError):
            spool.recovery_put("stream-b", b"x" * 65537, expected=None)


def test_old_spool_migrates_without_changing_raw_history(tmp_path: Path) -> None:
    target = installation(tmp_path)
    with DurableIngestor(
        target, producer="a", epoch="a", capacity=1, max_payload_bytes=1024
    ):
        pass
    with sqlite3.connect(target.state_dir / "ingestion.sqlite3") as database:
        database.execute("DROP TABLE recovery_state")
        database.execute("PRAGMA user_version=1")
    with DurableIngestor(
        target, producer="a", epoch="b", capacity=1, max_payload_bytes=1024
    ) as spool:
        spool.recovery_put("stream-a", b"recovered", expected=None)
        assert spool.status().accepted_offset == 0


def test_disk_headroom_stops_payload_but_allows_loss_metadata(tmp_path: Path) -> None:
    with DurableIngestor(
        installation(tmp_path),
        producer="a",
        epoch="a",
        capacity=1,
        max_payload_bytes=1024,
        metadata_headroom_bytes=2**60,
    ) as spool:
        with pytest.raises(IngestionError, match="headroom"):
            spool.accept(envelope(InstrumentId("bybit", "spot", "BTCUSDT")))
        spool.recovery_put("stream-a", b"unknown", expected=None)
        assert spool.recovery_get("stream-a") == b"unknown"


def test_metadata_admission_survives_full_payload_lane(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    entered = threading.Event()
    release = threading.Event()
    real = DurableIngestor._accept

    def blocked(
        self: DurableIngestor, connection: sqlite3.Connection, value: RawEnvelope
    ) -> RawRecord:
        entered.set()
        assert release.wait(3)
        return real(self, connection, value)

    monkeypatch.setattr(DurableIngestor, "_accept", blocked)
    with (
        DurableIngestor(
            installation(tmp_path),
            producer="a",
            epoch="a",
            capacity=1,
            max_payload_bytes=1024,
        ) as spool,
        ThreadPoolExecutor(max_workers=3) as workers,
    ):
        first = workers.submit(
            spool.accept, envelope(InstrumentId("bybit", "spot", "BTCUSDT"))
        )
        assert entered.wait(1)
        second = workers.submit(
            spool.accept, envelope(InstrumentId("bybit", "spot", "BTCUSDT"))
        )
        # Admit metadata independently even while one accepted payload is queued.
        third = workers.submit(spool.recovery_put, "stream-a", b"loss", expected=None)
        release.set()
        first.result(3)
        second.result(3)
        third.result(3)
        assert spool.recovery_get("stream-a") == b"loss"
