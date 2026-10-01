"""Terminal exceptions survive SQLite cleanup without acknowledging writes."""

import asyncio
import sqlite3
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from scryntic.archive.canonical import (
    INPUT_FINGERPRINT_ALGORITHM,
    ORDERED_INPUT_ALGORITHM,
    ordered_digest_from_pairs,
)
from scryntic.archive.model import Partition
from scryntic.domain.raw import IngestionId
from scryntic.ingestion.sqlite_spool import DurableIngestor, IngestionError
from scryntic.normalization.sqlite_store import NormalizationError, NormalizationStore
from scryntic.publication.sqlite_store import (
    PublicationError,
    PublicationReservation,
    PublicationStore,
)
from tests.normalization.helpers import installation, normalization, raw_record

TERMINAL_ERRORS = (KeyboardInterrupt, SystemExit, asyncio.CancelledError)


class InterruptedConnection(sqlite3.Connection):
    """Real SQLite with one-shot interruption at transaction boundaries."""

    commit_error: BaseException | None = None
    rollback_error: BaseException | None = None
    close_error: BaseException | None = None

    def execute(self, sql: str, parameters: Any = (), /) -> sqlite3.Cursor:
        if sql == "COMMIT" and self.commit_error is not None:
            error, self.commit_error = self.commit_error, None
            raise error
        if sql == "ROLLBACK" and self.rollback_error is not None:
            error, self.rollback_error = self.rollback_error, None
            raise error
        return super().execute(sql, parameters)

    def close(self) -> None:
        if self.close_error is not None:
            error, self.close_error = self.close_error, None
            raise error
        super().close()


@pytest.fixture
def connections(monkeypatch: pytest.MonkeyPatch) -> list[InterruptedConnection]:
    real_connect = sqlite3.connect
    opened: list[InterruptedConnection] = []

    def connect(database: Path, **kwargs: Any) -> InterruptedConnection:
        connection = real_connect(database, factory=InterruptedConnection, **kwargs)
        opened.append(connection)
        return connection

    monkeypatch.setattr(sqlite3, "connect", connect)
    return opened


@pytest.mark.parametrize("error_type", TERMINAL_ERRORS)
@pytest.mark.parametrize("operation", ["accept", "metadata"])
def test_ingestion_interrupted_commit_rolls_back_and_preserves_termination(
    tmp_path: Path,
    connections: list[InterruptedConnection],
    error_type: type[BaseException],
    operation: str,
) -> None:
    with DurableIngestor(
        installation(tmp_path),
        producer="collector-a",
        epoch="epoch-a",
        capacity=2,
        max_payload_bytes=4096,
    ) as spool:
        (db,) = connections
        db.commit_error = error_type()
        if operation == "accept":
            envelope = raw_record().envelope
            with pytest.raises(error_type):
                spool.accept(envelope)
        else:
            with pytest.raises(error_type):
                spool.recovery_put("stream-a", b"pending", expected=None)
        assert not db.in_transaction
        assert spool.status().accepted_offset == 0
        assert spool.records_after(0, limit=1) == ()
        assert spool.recovery_get("stream-a") is None
        # A successful rollback keeps the writer usable and reuses no lost offset.
        assert spool.accept(raw_record().envelope).identity.offset == 1


def interrupt_cleanup(
    db: InterruptedConnection, error: BaseException, stage: str
) -> None:
    db.commit_error = sqlite3.OperationalError("private commit failure")
    if stage == "rollback":
        db.rollback_error = error
    else:
        db.rollback_error = sqlite3.OperationalError("private rollback failure")
        db.close_error = error


@pytest.mark.parametrize("error_type", TERMINAL_ERRORS)
def test_ingestion_interrupted_rollback_poisoning_rejects_future_requests(
    tmp_path: Path,
    connections: list[InterruptedConnection],
    error_type: type[BaseException],
) -> None:
    target = installation(tmp_path)
    with DurableIngestor(
        target,
        producer="collector-a",
        epoch="epoch-a",
        capacity=2,
        max_payload_bytes=4096,
    ) as spool:
        (db,) = connections
        interrupt_cleanup(db, error_type(), "rollback")
        envelope = raw_record().envelope
        with pytest.raises((IngestionError, error_type)) as caught:
            spool.accept(envelope)
        # Failure to roll back must poison the writer before it can report state.
        with pytest.raises(IngestionError, match="failed"):
            spool.status()
        assert isinstance(caught.value, IngestionError)
        with pytest.raises(IngestionError, match="failed"):
            spool.accept(envelope)
        with pytest.raises(IngestionError, match="failed"):
            spool.recovery_put("stream-a", b"pending", expected=None)
    with sqlite3.connect(target.state_dir / "ingestion.sqlite3") as reader:
        assert reader.execute("SELECT count(*) FROM raw_records").fetchone()[0] == 0


def reservation() -> PublicationReservation:
    identity = IngestionId("producer-a", "epoch-a", 7)
    inputs = ((identity, "a" * 64),)
    return PublicationReservation(
        checkpoint_before=None,
        checkpoint_after=identity,
        epoch="epoch-a",
        sequence=1,
        previous_manifest_hash="0" * 64,
        partition=Partition("fake", "candle", "2026-09-23"),
        input_fingerprint_algorithm=INPUT_FINGERPRINT_ALGORITHM,
        inputs=inputs,
        ordered_input_algorithm=ORDERED_INPUT_ALGORITHM,
        ordered_input_digest=ordered_digest_from_pairs(inputs),
    )


@pytest.mark.parametrize("error_type", TERMINAL_ERRORS)
@pytest.mark.parametrize("stage", ["rollback", "close"])
@pytest.mark.parametrize("kind", ["normalization", "publication"])
def test_interrupted_store_cleanup_preserves_termination_and_disables_store(
    tmp_path: Path,
    connections: list[InterruptedConnection],
    error_type: type[BaseException],
    stage: str,
    kind: str,
) -> None:
    target = installation(tmp_path)
    store: NormalizationStore | PublicationStore
    operation: Callable[[], object]
    failure: type[Exception]
    if kind == "normalization":
        normalizer = NormalizationStore(target, producer="collector-a")
        store = normalizer
        record = raw_record()

        def process() -> object:
            return normalizer.process(
                record, normalization(record), expected_predecessor=None
            )

        operation = process
        failure = NormalizationError
        table = "processing_outcomes"
    else:
        publisher = PublicationStore(target, producer="producer-a")
        store = publisher

        def reserve() -> object:
            return publisher.reserve(reservation())

        operation = reserve
        failure = PublicationError
        table = "pending_publication"
    try:
        (db,) = connections
        interrupt_cleanup(db, error_type(), stage)
        with pytest.raises(error_type):
            operation()
        with pytest.raises(failure, match="failed"):
            store.status()
        # A separate connection observes no acknowledged outcome or reservation.
        with sqlite3.connect(target.state_dir / f"{kind}.sqlite3") as reader:
            assert reader.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0
    finally:
        store.close()
