"""Behavioral tests for the single-owner durable SQLite spool."""

import os
import sqlite3
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from concurrent.futures import Future
from pathlib import Path
from typing import Any, cast

import pytest

from scryntic.configuration.paths import Installation
from scryntic.domain.identity import EntityId, InstrumentId, SubjectId
from scryntic.domain.raw import RawEnvelope, RawRecord
from scryntic.domain.time import ClockSample, SourceTime, TimeQuality, TimeUnit
from scryntic.ingestion.sqlite_spool import (
    DurableIngestor,
    IngestionError,
    IntakeFull,
    WriterOwned,
)


def installation(root: Path) -> Installation:
    config = root / "config"
    state = root / "state"
    runtime = root / "runtime"
    credentials = config / "credentials"
    for path in (config, state, runtime, credentials):
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
        path.chmod(0o700)
    uid = os.geteuid()
    return Installation(config, state, runtime, credentials, uid, uid, False)


def test_competing_writer_is_rejected_before_opening_sqlite(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = installation(tmp_path)
    real_connect = sqlite3.connect
    opened: list[Path] = []

    def recording_connect(database: Path, **kwargs: Any) -> sqlite3.Connection:
        opened.append(database)
        return cast(sqlite3.Connection, real_connect(database, **kwargs))

    monkeypatch.setattr(sqlite3, "connect", recording_connect)
    with DurableIngestor(
        target,
        producer="collector-a",
        epoch="boot-a",
        capacity=1,
        max_payload_bytes=1024,
    ):
        with pytest.raises(WriterOwned, match="already owned"):
            DurableIngestor(
                target,
                producer="collector-a",
                epoch="boot-b",
                capacity=1,
                max_payload_bytes=1024,
            )

    assert len(opened) == 1


def test_cooperative_lock_is_held_for_writer_lifetime(tmp_path: Path) -> None:
    target = installation(tmp_path)
    first = DurableIngestor(
        target,
        producer="collector-a",
        epoch="boot-a",
        capacity=1,
        max_payload_bytes=1024,
    )
    with pytest.raises(WriterOwned):
        DurableIngestor(
            target,
            producer="collector-a",
            epoch="boot-b",
            capacity=1,
            max_payload_bytes=1024,
        )
    first.close()

    with DurableIngestor(
        target,
        producer="collector-a",
        epoch="boot-b",
        capacity=1,
        max_payload_bytes=1024,
    ) as replacement:
        assert replacement.status().accepted_offset == 0


def test_thread_start_failure_releases_cooperative_ownership(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = installation(tmp_path)

    def fail_start(self: threading.Thread) -> None:
        raise RuntimeError("injected thread start failure")

    with monkeypatch.context() as patch:
        patch.setattr(threading.Thread, "start", fail_start)
        with pytest.raises(RuntimeError, match="injected thread start failure"):
            DurableIngestor(
                target,
                producer="collector-a",
                epoch="boot-a",
                capacity=1,
                max_payload_bytes=1024,
            )

    with DurableIngestor(
        target,
        producer="collector-a",
        epoch="boot-b",
        capacity=1,
        max_payload_bytes=1024,
    ):
        pass


def test_interrupted_startup_stops_worker_and_releases_ownership(
    tmp_path: Path,
) -> None:
    completed = subprocess.run(
        [sys.executable, "-m", "tests.ingestion.constructor_failure", str(tmp_path)],
        check=False,
        capture_output=True,
        text=True,
        timeout=3,
    )
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout == "released\n"


def test_startup_verifies_effective_durability_pragmas(tmp_path: Path) -> None:
    with DurableIngestor(
        installation(tmp_path),
        producer="collector-a",
        epoch="boot-a",
        capacity=2,
        max_payload_bytes=1024,
    ) as ingestor:
        status = ingestor.status()

    assert status.journal_mode == "wal"
    assert status.synchronous == "FULL"
    assert status.sqlite_version == sqlite3.sqlite_version
    assert status.accepted_offset == 0
    assert status.queue_capacity == 2


def envelope(subject: SubjectId) -> RawEnvelope:
    return RawEnvelope(
        source="fake-source",
        stream="market-events",
        channel="public",
        adapter_version="1.2.3",
        receipt=ClockSample(
            1_700_000_000_000_000_000,
            42,
            "session-a",
            TimeQuality(
                "clock-a",
                status="healthy",
                offset_ns=-12,
                uncertainty_ns=50,
                evidence_age_ns=10,
            ),
        ),
        payload=b'{"price":"1.2300"}',
        payload_limit=1024,
        subject=subject,
        source_time=SourceTime(1_700_000_000_123, TimeUnit.MILLISECOND),
        source_event_id="event-7",
        source_sequence=99,
    )


@pytest.mark.parametrize(
    "subject",
    [
        InstrumentId("fake", "perpetual", "BTC-USDT"),
        EntityId("document", "publisher", "story-7"),
    ],
)
def test_committed_envelopes_round_trip_and_repeats_stay_distinct(
    tmp_path: Path, subject: SubjectId
) -> None:
    raw = envelope(subject)
    with DurableIngestor(
        installation(tmp_path),
        producer="collector-a",
        epoch="boot-a",
        capacity=2,
        max_payload_bytes=1024,
    ) as ingestor:
        first = ingestor.accept(raw)
        second = ingestor.accept(raw)
        recovered = ingestor.records_after(0, limit=10)

    assert first.identity.offset == 1
    assert second.identity.offset == 2
    assert first.identity != second.identity
    assert first.envelope == second.envelope == raw
    assert recovered == (first, second)


class ObservedConnection(sqlite3.Connection):
    observations: list[tuple[str, str, bool, bool]]
    fail_commit: bool
    fail_rollback: bool

    def execute(self, sql: str, parameters: Any = (), /) -> sqlite3.Cursor:
        statement = sql.strip().upper().split(maxsplit=1)[0]
        if statement in {"BEGIN", "INSERT", "COMMIT", "ROLLBACK"}:
            self.observations.append(
                (statement, "before", bool(self.autocommit), self.in_transaction)
            )
        if statement == "COMMIT" and self.fail_commit:
            raise sqlite3.OperationalError("injected commit failure")
        if statement == "ROLLBACK" and self.fail_rollback:
            raise sqlite3.OperationalError("injected rollback failure")
        cursor = super().execute(sql, parameters)
        if statement in {"BEGIN", "INSERT", "COMMIT", "ROLLBACK"}:
            self.observations.append(
                (statement, "after", bool(self.autocommit), self.in_transaction)
            )
        return cursor


def observed_connect(
    connections: list[ObservedConnection],
    real_connect: Callable[..., sqlite3.Connection],
) -> Callable[..., ObservedConnection]:
    def connect(database: Path, **kwargs: Any) -> ObservedConnection:
        connection = real_connect(database, factory=ObservedConnection, **kwargs)
        observed = cast(ObservedConnection, connection)
        observed.observations = []
        observed.fail_commit = False
        observed.fail_rollback = False
        connections.append(observed)
        return observed

    return connect


def test_acceptance_uses_only_explicit_sql_transaction_control(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    connections: list[ObservedConnection] = []
    monkeypatch.setattr(
        sqlite3, "connect", observed_connect(connections, sqlite3.connect)
    )
    with DurableIngestor(
        installation(tmp_path),
        producer="collector-a",
        epoch="boot-a",
        capacity=1,
        max_payload_bytes=1024,
    ) as ingestor:
        connections[0].observations.clear()
        ingestor.accept(envelope(InstrumentId("fake", "spot", "BTC-USDT")))
        observations = tuple(connections[0].observations)

    assert observations == (
        ("BEGIN", "before", True, False),
        ("BEGIN", "after", True, True),
        ("INSERT", "before", True, True),
        ("INSERT", "after", True, True),
        ("COMMIT", "before", True, True),
        ("COMMIT", "after", True, False),
    )


def test_failed_commit_rolls_back_without_advancing_acceptance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    connections: list[ObservedConnection] = []
    monkeypatch.setattr(
        sqlite3, "connect", observed_connect(connections, sqlite3.connect)
    )
    with DurableIngestor(
        installation(tmp_path),
        producer="collector-a",
        epoch="boot-a",
        capacity=1,
        max_payload_bytes=1024,
    ) as ingestor:
        connection = connections[0]
        connection.observations.clear()
        connection.fail_commit = True
        with pytest.raises(IngestionError, match="commit"):
            ingestor.accept(envelope(InstrumentId("fake", "spot", "BTC-USDT")))
        assert ingestor.status().accepted_offset == 0
        assert connection.in_transaction is False
        assert ("ROLLBACK", "after", True, False) in connection.observations

        connection.fail_commit = False
        accepted = ingestor.accept(envelope(InstrumentId("fake", "spot", "BTC-USDT")))

    assert accepted.identity.offset == 1


def test_failed_rollback_permanently_fails_writer_without_exposing_progress(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    connections: list[ObservedConnection] = []
    monkeypatch.setattr(
        sqlite3, "connect", observed_connect(connections, sqlite3.connect)
    )
    ingestor = DurableIngestor(
        installation(tmp_path),
        producer="collector-a",
        epoch="boot-a",
        capacity=1,
        max_payload_bytes=1024,
    )
    connection = connections[0]
    connection.fail_commit = True
    connection.fail_rollback = True

    try:
        with pytest.raises(IngestionError, match="transaction recovery"):
            ingestor.accept(envelope(InstrumentId("fake", "spot", "BTC-USDT")))
        with pytest.raises(IngestionError, match="writer failed"):
            ingestor.status()
    finally:
        ingestor.close()


class BlockingConnection(ObservedConnection):
    block_insert: bool
    insert_started: threading.Event
    release_insert: threading.Event

    def execute(self, sql: str, parameters: Any = (), /) -> sqlite3.Cursor:
        if sql.lstrip().upper().startswith("INSERT") and self.block_insert:
            self.insert_started.set()
            if not self.release_insert.wait(timeout=5):
                raise sqlite3.OperationalError("test insert barrier timed out")
        return super().execute(sql, parameters)


def blocking_connect(
    connections: list[BlockingConnection],
    real_connect: Callable[..., sqlite3.Connection],
) -> Callable[..., BlockingConnection]:
    def connect(database: Path, **kwargs: Any) -> BlockingConnection:
        connection = real_connect(database, factory=BlockingConnection, **kwargs)
        blocking = cast(BlockingConnection, connection)
        blocking.observations = []
        blocking.fail_commit = False
        blocking.fail_rollback = False
        blocking.block_insert = False
        blocking.insert_started = threading.Event()
        blocking.release_insert = threading.Event()
        connections.append(blocking)
        return blocking

    return connect


def test_bounded_intake_rejects_without_evicting_admitted_work(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    connections: list[BlockingConnection] = []
    monkeypatch.setattr(
        sqlite3, "connect", blocking_connect(connections, sqlite3.connect)
    )
    raw = envelope(InstrumentId("fake", "spot", "BTC-USDT"))
    with DurableIngestor(
        installation(tmp_path),
        producer="collector-a",
        epoch="boot-a",
        capacity=1,
        max_payload_bytes=1024,
    ) as ingestor:
        connection = connections[0]
        connection.block_insert = True
        first: Future[RawRecord] = Future()
        second: Future[RawRecord] = Future()

        def submit(result: Future[RawRecord]) -> None:
            try:
                result.set_result(ingestor.accept(raw))
            except BaseException as error:
                result.set_exception(error)

        first_thread = threading.Thread(target=submit, args=(first,))
        first_thread.start()
        assert connection.insert_started.wait(timeout=2)
        second_thread = threading.Thread(target=submit, args=(second,))
        second_thread.start()
        deadline = time.monotonic() + 2
        while not ingestor._requests.full() and time.monotonic() < deadline:
            time.sleep(0.001)
        assert ingestor._requests.full()

        with pytest.raises(IntakeFull, match="full"):
            ingestor.accept(raw)

        connection.release_insert.set()
        first_thread.join(timeout=2)
        second_thread.join(timeout=2)
        assert not first_thread.is_alive()
        assert not second_thread.is_alive()
        accepted = (first.result(), second.result())
        stored = ingestor.records_after(0, limit=10)

    assert tuple(record.identity.offset for record in accepted) == (1, 2)
    assert stored == accepted


def test_concurrent_close_calls_wait_for_the_same_shutdown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    connections: list[BlockingConnection] = []
    monkeypatch.setattr(
        sqlite3, "connect", blocking_connect(connections, sqlite3.connect)
    )
    target = installation(tmp_path)
    ingestor = DurableIngestor(
        target,
        producer="collector-a",
        epoch="boot-a",
        capacity=1,
        max_payload_bytes=1024,
    )
    connection = connections[0]
    connection.block_insert = True
    accepted: Future[RawRecord] = Future()

    def submit() -> None:
        try:
            accepted.set_result(
                ingestor.accept(envelope(InstrumentId("fake", "spot", "BTC-USDT")))
            )
        except BaseException as error:
            accepted.set_exception(error)

    submit_thread = threading.Thread(target=submit)
    submit_thread.start()
    assert connection.insert_started.wait(timeout=2)
    closed = [threading.Event(), threading.Event()]

    def close(index: int) -> None:
        ingestor.close()
        closed[index].set()

    closers = [threading.Thread(target=close, args=(index,)) for index in range(2)]
    for closer in closers:
        closer.start()
    assert not closed[0].wait(timeout=0.05)
    assert not closed[1].wait(timeout=0.05)

    connection.release_insert.set()
    submit_thread.join(timeout=2)
    for closer in closers:
        closer.join(timeout=2)
        assert not closer.is_alive()
    assert accepted.result().identity.offset == 1

    with DurableIngestor(
        target,
        producer="collector-a",
        epoch="boot-b",
        capacity=1,
        max_payload_bytes=1024,
    ):
        pass


def test_payload_over_local_limit_is_rejected_before_acceptance(tmp_path: Path) -> None:
    raw = RawEnvelope(
        source="fake-source",
        stream="events",
        channel="public",
        adapter_version="1.0",
        receipt=ClockSample(1, 1, "session-a", TimeQuality("clock-a")),
        payload=b"12345",
        payload_limit=5,
    )
    with DurableIngestor(
        installation(tmp_path),
        producer="collector-a",
        epoch="boot-a",
        capacity=1,
        max_payload_bytes=4,
    ) as ingestor:
        with pytest.raises(IngestionError, match="payload limit"):
            ingestor.accept(raw)
        assert ingestor.status().accepted_offset == 0


def test_acknowledged_rows_survive_abrupt_process_exit_and_restart(
    tmp_path: Path,
) -> None:
    target = installation(tmp_path)
    completed = subprocess.run(
        [sys.executable, "-m", "tests.ingestion.crash_writer", str(tmp_path)],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout == "2\n"

    with DurableIngestor(
        target,
        producer="collector-a",
        epoch="boot-b",
        capacity=1,
        max_payload_bytes=1024,
    ) as recovered:
        assert recovered.status().accepted_offset == 2
        records = recovered.records_after(0, limit=10)

    assert tuple(record.identity.offset for record in records) == (1, 2)
    assert tuple(record.identity.epoch for record in records) == ("boot-a", "boot-a")


def test_unsupported_or_corrupt_spool_fails_without_fabricating_progress(
    tmp_path: Path,
) -> None:
    unsupported = installation(tmp_path / "unsupported")
    connection = sqlite3.connect(unsupported.state_dir / "ingestion.sqlite3")
    connection.execute("PRAGMA user_version=99")
    connection.close()
    (unsupported.state_dir / "ingestion.sqlite3").chmod(0o600)
    with pytest.raises(IngestionError, match="schema"):
        DurableIngestor(
            unsupported,
            producer="collector-a",
            epoch="boot-a",
            capacity=1,
            max_payload_bytes=1024,
        )

    corrupt = installation(tmp_path / "corrupt")
    database = corrupt.state_dir / "ingestion.sqlite3"
    database.write_bytes(b"not a sqlite database")
    database.chmod(0o600)
    with pytest.raises(IngestionError, match="initialize"):
        DurableIngestor(
            corrupt,
            producer="collector-a",
            epoch="boot-a",
            capacity=1,
            max_payload_bytes=1024,
        )


def test_version_one_schema_is_validated_before_use(tmp_path: Path) -> None:
    target = installation(tmp_path)
    database = target.state_dir / "ingestion.sqlite3"
    connection = sqlite3.connect(database, autocommit=True)
    connection.execute(
        "CREATE TABLE raw_records (offset INTEGER PRIMARY KEY AUTOINCREMENT) STRICT"
    )
    connection.execute("PRAGMA user_version=1")
    connection.close()
    database.chmod(0o600)
    unexpected: DurableIngestor | None = None
    try:
        with pytest.raises(IngestionError, match="schema"):
            unexpected = DurableIngestor(
                target,
                producer="collector-a",
                epoch="boot-a",
                capacity=1,
                max_payload_bytes=1024,
            )
    finally:
        if unexpected is not None:
            unexpected.close()


def test_versioned_schema_with_suppressing_trigger_is_rejected(
    tmp_path: Path,
) -> None:
    target = installation(tmp_path)
    with DurableIngestor(
        target,
        producer="collector-a",
        epoch="boot-a",
        capacity=1,
        max_payload_bytes=1024,
    ):
        pass
    database = target.state_dir / "ingestion.sqlite3"
    connection = sqlite3.connect(database, autocommit=True)
    connection.execute(
        "CREATE TRIGGER suppress_raw BEFORE INSERT ON raw_records "
        "BEGIN SELECT RAISE(IGNORE); END"
    )
    connection.close()
    unexpected: DurableIngestor | None = None
    try:
        with pytest.raises(IngestionError, match="schema"):
            unexpected = DurableIngestor(
                target,
                producer="collector-a",
                epoch="boot-b",
                capacity=1,
                max_payload_bytes=1024,
            )
    finally:
        if unexpected is not None:
            unexpected.close()


def test_state_directory_is_bound_to_one_producer(tmp_path: Path) -> None:
    target = installation(tmp_path)
    with DurableIngestor(
        target,
        producer="collector-a",
        epoch="boot-a",
        capacity=1,
        max_payload_bytes=1024,
    ) as first:
        first.accept(envelope(InstrumentId("fake", "spot", "BTC-USDT")))

    unexpected: DurableIngestor | None = None
    try:
        with pytest.raises(IngestionError, match="producer"):
            unexpected = DurableIngestor(
                target,
                producer="collector-b",
                epoch="boot-b",
                capacity=1,
                max_payload_bytes=1024,
            )
    finally:
        if unexpected is not None:
            unexpected.close()
