"""Single-owner SQLite raw spool with explicit transaction control."""

import fcntl
import os
import queue
import sqlite3
import stat
import threading
from concurrent.futures import Future
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from typing import Literal, cast

from scryntic.configuration.paths import Installation, directory
from scryntic.domain.identity import EntityId, InstrumentId, SubjectId
from scryntic.domain.raw import IngestionId, RawEnvelope, RawRecord
from scryntic.domain.time import ClockSample, SourceTime, TimeQuality, TimeUnit
from scryntic.domain.validation import identifier, integer

_DATABASE_NAME = "ingestion.sqlite3"
_LOCK_NAME = "ingestion.lock"
_SCHEMA_VERSION = 1


class IngestionError(RuntimeError):
    """Fixed-message durable ingestion failure."""


class WriterOwned(IngestionError):
    """Another cooperative Scryntic writer owns this state directory."""


class IntakeFull(IngestionError):
    """The bounded intake could not admit another request."""


class _WriterFatal(IngestionError):
    """The sole connection can no longer safely process requests."""


@dataclass(frozen=True, slots=True)
class IngestionStatus:
    producer: str
    epoch: str
    accepted_offset: int
    queue_capacity: int
    journal_mode: str
    synchronous: str
    sqlite_version: str


@dataclass(slots=True)
class _StatusRequest:
    result: Future[IngestionStatus]


@dataclass(slots=True)
class _AcceptRequest:
    envelope: RawEnvelope
    result: Future[RawRecord]


@dataclass(slots=True)
class _ReadRequest:
    offset: int
    limit: int
    result: Future[tuple[RawRecord, ...]]


type _Request = _StatusRequest | _AcceptRequest | _ReadRequest

_RECORD_COLUMNS = """
offset, producer, epoch, source, stream, channel, adapter_version,
receipt_wall_time_ns, receipt_monotonic_ns, receipt_session_id,
quality_epoch, quality_status, quality_offset_ns, quality_uncertainty_ns,
quality_evidence_age_ns, subject_type, subject_first, subject_second,
subject_third, source_time_value, source_time_unit, source_event_id,
source_sequence, payload, content_sha256
"""

_CREATE_METADATA = """
CREATE TABLE spool_metadata (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    producer TEXT NOT NULL
) STRICT
""".strip()

_CREATE_RAW_RECORDS = """
CREATE TABLE raw_records (
    offset INTEGER PRIMARY KEY AUTOINCREMENT,
    producer TEXT NOT NULL,
    epoch TEXT NOT NULL,
    source TEXT NOT NULL,
    stream TEXT NOT NULL,
    channel TEXT NOT NULL,
    adapter_version TEXT NOT NULL,
    receipt_wall_time_ns INTEGER NOT NULL,
    receipt_monotonic_ns INTEGER NOT NULL,
    receipt_session_id TEXT NOT NULL,
    quality_epoch TEXT NOT NULL,
    quality_status TEXT NOT NULL,
    quality_offset_ns INTEGER,
    quality_uncertainty_ns INTEGER,
    quality_evidence_age_ns INTEGER,
    subject_type TEXT,
    subject_first TEXT,
    subject_second TEXT,
    subject_third TEXT,
    source_time_value INTEGER,
    source_time_unit TEXT,
    source_event_id TEXT,
    source_sequence INTEGER,
    payload BLOB NOT NULL,
    content_sha256 TEXT NOT NULL,
    CHECK (subject_type IS NULL OR subject_type IN ('instrument', 'entity')),
    CHECK (quality_status IN ('unknown', 'degraded', 'healthy'))
) STRICT
""".strip()


class DurableIngestor:
    """Own one cooperative lock, worker thread and SQLite connection."""

    def __init__(
        self,
        installation: Installation,
        *,
        producer: str,
        epoch: str,
        capacity: int,
        max_payload_bytes: int,
    ) -> None:
        identifier(producer)
        identifier(epoch)
        integer(capacity, 1)
        integer(max_payload_bytes, 1)
        self._producer = producer
        self._epoch = epoch
        self._capacity = capacity
        self._max_payload_bytes = max_payload_bytes
        self._requests: queue.Queue[_Request] = queue.Queue(maxsize=capacity)
        self._lifecycle_lock = threading.Lock()
        self._close_lock = threading.Lock()
        self._shutdown = threading.Event()
        self._closing = False
        self._closed = False
        self._fatal: IngestionError | None = None
        self._resources = ExitStack()
        self._ready: Future[None] = Future()
        thread_started = False
        try:
            state_fd = self._resources.enter_context(
                directory(installation.state_dir, installation.owner_uid, private=True)
            )
            self._lock_fd = self._acquire_lock(state_fd, installation.owner_uid)
            self._resources.callback(os.close, self._lock_fd)
            database = self._prepare_database(state_fd, installation.owner_uid)
            self._thread = threading.Thread(
                target=self._run,
                args=(database,),
                name="scryntic-ingestion-writer",
                daemon=False,
            )
            self._thread.start()
            thread_started = True
            self._ready.result()
        except BaseException:
            self._shutdown.set()
            try:
                while thread_started and self._thread.is_alive():
                    try:
                        self._thread.join()
                    except BaseException:
                        continue
            finally:
                self._resources.close()
            raise

    @staticmethod
    def _acquire_lock(state_fd: int, owner_uid: int) -> int:
        flags = os.O_RDWR | os.O_CREAT | os.O_CLOEXEC | os.O_NOFOLLOW
        try:
            lock_fd = os.open(_LOCK_NAME, flags, 0o600, dir_fd=state_fd)
            info = os.fstat(lock_fd)
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_uid != owner_uid
                or info.st_nlink != 1
                or stat.S_IMODE(info.st_mode) != 0o600
            ):
                raise IngestionError("Unsafe ingestion lock file")
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise WriterOwned("Durable ingestion is already owned") from None
            return lock_fd
        except BaseException:
            if "lock_fd" in locals():
                os.close(lock_fd)
            raise

    @staticmethod
    def _prepare_database(state_fd: int, owner_uid: int) -> Path:
        flags = os.O_RDWR | os.O_CREAT | os.O_CLOEXEC | os.O_NOFOLLOW
        database_fd = os.open(_DATABASE_NAME, flags, 0o600, dir_fd=state_fd)
        try:
            info = os.fstat(database_fd)
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_uid != owner_uid
                or info.st_nlink != 1
                or stat.S_IMODE(info.st_mode) != 0o600
            ):
                raise IngestionError("Unsafe ingestion database file")
        finally:
            os.close(database_fd)
        return Path(f"/proc/self/fd/{state_fd}/{_DATABASE_NAME}")

    def _run(self, database: Path) -> None:
        connection: sqlite3.Connection | None = None
        try:
            connection = sqlite3.connect(database, autocommit=True)
            self._initialize(connection)
            self._ready.set_result(None)
            while True:
                if self._shutdown.is_set() and self._requests.empty():
                    return
                try:
                    request = self._requests.get(timeout=0.05)
                except queue.Empty:
                    continue
                try:
                    if isinstance(request, _StatusRequest):
                        request.result.set_result(self._status(connection))
                    elif isinstance(request, _AcceptRequest):
                        request.result.set_result(
                            self._accept(connection, request.envelope)
                        )
                    else:
                        request.result.set_result(
                            self._read(connection, request.offset, request.limit)
                        )
                except _WriterFatal as error:
                    request.result.set_exception(error)
                    self._fail_writer()
                    return
                except BaseException as error:
                    request.result.set_exception(error)
                finally:
                    self._requests.task_done()
        except BaseException as error:
            if not self._ready.done():
                startup_error = (
                    error
                    if isinstance(error, IngestionError)
                    else IngestionError("Unable to initialize durable ingestion")
                )
                self._ready.set_exception(startup_error)
        finally:
            if connection is not None:
                connection.close()

    def _fail_writer(self) -> None:
        failure = IngestionError("Durable ingestion writer failed")
        with self._lifecycle_lock:
            self._fatal = failure
        self._shutdown.set()
        while True:
            try:
                request = self._requests.get_nowait()
            except queue.Empty:
                return
            request.result.set_exception(failure)
            self._requests.task_done()

    @staticmethod
    def _normalized_sql(statement: object) -> str:
        if type(statement) is not str:
            raise IngestionError("Invalid ingestion schema")
        return " ".join(statement.split())

    def _validate_schema(self, connection: sqlite3.Connection) -> None:
        objects = connection.execute(
            "SELECT type, name, tbl_name, sql FROM sqlite_schema "
            "WHERE name NOT LIKE 'sqlite_%' ORDER BY name"
        ).fetchall()
        expected = (
            (
                "table",
                "raw_records",
                "raw_records",
                self._normalized_sql(_CREATE_RAW_RECORDS),
            ),
            (
                "table",
                "spool_metadata",
                "spool_metadata",
                self._normalized_sql(_CREATE_METADATA),
            ),
        )
        actual = tuple(
            (row[0], row[1], row[2], self._normalized_sql(row[3])) for row in objects
        )
        if actual != expected:
            raise IngestionError("Invalid ingestion schema")

        metadata = connection.execute(
            "SELECT singleton, producer FROM spool_metadata"
        ).fetchall()
        if metadata != [(1, self._producer)]:
            raise IngestionError("Ingestion producer does not own this spool")
        foreign_rows = connection.execute(
            "SELECT COUNT(*) FROM raw_records WHERE producer != ?",
            (self._producer,),
        ).fetchone()
        if foreign_rows is None or foreign_rows[0] != 0:
            raise IngestionError("Ingestion producer does not own this spool")

    def _initialize(self, connection: sqlite3.Connection) -> None:
        journal = connection.execute("PRAGMA journal_mode=WAL").fetchone()
        connection.execute("PRAGMA synchronous=FULL")
        synchronous = connection.execute("PRAGMA synchronous").fetchone()
        if journal is None or str(journal[0]).lower() != "wal":
            raise IngestionError("SQLite WAL mode is unavailable")
        if synchronous is None or synchronous[0] != 2:
            raise IngestionError("SQLite FULL synchronization is unavailable")

        version_row = connection.execute("PRAGMA user_version").fetchone()
        version = 0 if version_row is None else int(version_row[0])
        if version == 0:
            connection.execute("BEGIN IMMEDIATE")
            try:
                connection.execute(_CREATE_METADATA)
                connection.execute(_CREATE_RAW_RECORDS)
                connection.execute(
                    "INSERT INTO spool_metadata (singleton, producer) VALUES (1, ?)",
                    (self._producer,),
                )
                connection.execute(f"PRAGMA user_version={_SCHEMA_VERSION}")
                connection.execute("COMMIT")
            except BaseException:
                if connection.in_transaction:
                    connection.execute("ROLLBACK")
                raise
        elif version != _SCHEMA_VERSION:
            raise IngestionError("Unsupported ingestion schema")

        self._validate_schema(connection)
        integrity = connection.execute("PRAGMA quick_check(1)").fetchone()
        if integrity != ("ok",):
            raise IngestionError("Ingestion database integrity check failed")

    def _status(self, connection: sqlite3.Connection) -> IngestionStatus:
        row = connection.execute(
            "SELECT COALESCE(MAX(offset), 0) FROM raw_records WHERE producer = ?",
            (self._producer,),
        ).fetchone()
        accepted_offset = 0 if row is None else int(row[0])
        journal = connection.execute("PRAGMA journal_mode").fetchone()
        synchronous = connection.execute("PRAGMA synchronous").fetchone()
        if journal is None or synchronous is None:
            raise IngestionError("Unable to read SQLite durability settings")
        return IngestionStatus(
            self._producer,
            self._epoch,
            accepted_offset,
            self._capacity,
            str(journal[0]).lower(),
            "FULL" if synchronous[0] == 2 else str(synchronous[0]),
            sqlite3.sqlite_version,
        )

    @staticmethod
    def _subject_values(
        subject: SubjectId | None,
    ) -> tuple[str | None, str | None, str | None, str | None]:
        if subject is None:
            return None, None, None, None
        if isinstance(subject, InstrumentId):
            return "instrument", subject.venue, subject.category, subject.symbol
        return "entity", subject.kind, subject.namespace, subject.value

    def _accept(
        self, connection: sqlite3.Connection, envelope: RawEnvelope
    ) -> RawRecord:
        subject_type, subject_first, subject_second, subject_third = (
            self._subject_values(envelope.subject)
        )
        source_time_value = (
            None if envelope.source_time is None else envelope.source_time.value
        )
        source_time_unit = (
            None if envelope.source_time is None else envelope.source_time.unit.value
        )
        quality = envelope.receipt.quality
        try:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                """
                INSERT INTO raw_records (
                    producer, epoch, source, stream, channel, adapter_version,
                    receipt_wall_time_ns, receipt_monotonic_ns, receipt_session_id,
                    quality_epoch, quality_status, quality_offset_ns,
                    quality_uncertainty_ns, quality_evidence_age_ns,
                    subject_type, subject_first, subject_second, subject_third,
                    source_time_value, source_time_unit, source_event_id,
                    source_sequence, payload, content_sha256
                ) VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?
                )
                """,
                (
                    self._producer,
                    self._epoch,
                    envelope.source,
                    envelope.stream,
                    envelope.channel,
                    envelope.adapter_version,
                    envelope.receipt.wall_time_ns,
                    envelope.receipt.monotonic_ns,
                    envelope.receipt.session_id,
                    quality.epoch,
                    quality.status,
                    quality.offset_ns,
                    quality.uncertainty_ns,
                    quality.evidence_age_ns,
                    subject_type,
                    subject_first,
                    subject_second,
                    subject_third,
                    source_time_value,
                    source_time_unit,
                    envelope.source_event_id,
                    envelope.source_sequence,
                    envelope.payload,
                    envelope.content_sha256,
                ),
            )
            offset = cursor.lastrowid
            if type(offset) is not int or offset < 1 or cursor.rowcount != 1:
                raise IngestionError("SQLite did not assign an ingestion offset")
            expected = RawRecord(
                IngestionId(self._producer, self._epoch, offset), envelope
            )
            stored_row = connection.execute(
                f"SELECT {_RECORD_COLUMNS} FROM raw_records "
                "WHERE producer = ? AND offset = ?",
                (self._producer, offset),
            ).fetchone()
            if stored_row is None or self._decode_record(tuple(stored_row)) != expected:
                raise IngestionError("SQLite did not store the accepted envelope")
            connection.execute("COMMIT")
        except BaseException:
            if connection.in_transaction:
                try:
                    connection.execute("ROLLBACK")
                except sqlite3.Error:
                    raise _WriterFatal(
                        "Durable ingestion transaction recovery failed"
                    ) from None
                if connection.in_transaction:
                    raise _WriterFatal(
                        "Durable ingestion transaction recovery failed"
                    ) from None
            raise IngestionError("Unable to commit raw envelope") from None
        return expected

    def _read(
        self, connection: sqlite3.Connection, offset: int, limit: int
    ) -> tuple[RawRecord, ...]:
        rows = connection.execute(
            f"SELECT {_RECORD_COLUMNS} FROM raw_records "
            "WHERE producer = ? AND offset > ? ORDER BY offset LIMIT ?",
            (self._producer, offset, limit),
        ).fetchall()
        try:
            return tuple(self._decode_record(tuple(row)) for row in rows)
        except (TypeError, ValueError, IndexError):
            raise IngestionError("Invalid durable raw record") from None

    def _decode_record(self, row: tuple[object, ...]) -> RawRecord:
        def text(index: int) -> str:
            value = row[index]
            if type(value) is not str:
                raise TypeError("Expected stored text")
            return value

        def number(index: int) -> int:
            value = row[index]
            if type(value) is not int:
                raise TypeError("Expected stored integer")
            return value

        def optional_number(index: int) -> int | None:
            value = row[index]
            if value is None:
                return None
            if type(value) is not int:
                raise TypeError("Expected optional stored integer")
            return value

        subject_type = row[15]
        subject: SubjectId | None
        if subject_type is None:
            if any(row[index] is not None for index in (16, 17, 18)):
                raise ValueError("Incomplete stored subject")
            subject = None
        elif subject_type == "instrument":
            subject = InstrumentId(text(16), text(17), text(18))
        elif subject_type == "entity":
            subject = EntityId(text(16), text(17), text(18))
        else:
            raise ValueError("Unknown stored subject")

        source_value = optional_number(19)
        source_unit = row[20]
        if source_value is None and source_unit is None:
            source_time = None
        elif source_value is not None and type(source_unit) is str:
            source_time = SourceTime(source_value, TimeUnit(source_unit))
        else:
            raise ValueError("Incomplete stored source time")

        status = text(11)
        if status not in ("unknown", "degraded", "healthy"):
            raise ValueError("Unknown stored time quality")
        quality_status = cast(Literal["unknown", "degraded", "healthy"], status)
        payload = row[23]
        if type(payload) is not bytes:
            raise TypeError("Expected stored bytes")
        envelope = RawEnvelope(
            source=text(3),
            stream=text(4),
            channel=text(5),
            adapter_version=text(6),
            receipt=ClockSample(
                number(7),
                number(8),
                text(9),
                TimeQuality(
                    text(10),
                    quality_status,
                    optional_number(12),
                    optional_number(13),
                    optional_number(14),
                ),
            ),
            payload=payload,
            payload_limit=self._max_payload_bytes,
            subject=subject,
            source_time=source_time,
            source_event_id=None if row[21] is None else text(21),
            source_sequence=optional_number(22),
        )
        if envelope.content_sha256 != text(24):
            raise ValueError("Stored payload hash mismatch")
        return RawRecord(IngestionId(text(1), text(2), number(0)), envelope)

    def status(self) -> IngestionStatus:
        request = _StatusRequest(Future())
        self._submit(request)
        return request.result.result()

    def accept(self, envelope: RawEnvelope) -> RawRecord:
        if len(envelope.payload) > self._max_payload_bytes:
            raise IngestionError("Raw envelope exceeds ingestion payload limit")
        request = _AcceptRequest(envelope, Future())
        self._submit(request)
        return request.result.result()

    def records_after(self, offset: int, *, limit: int) -> tuple[RawRecord, ...]:
        integer(offset, 0)
        integer(limit, 1)
        request = _ReadRequest(offset, limit, Future())
        self._submit(request)
        return request.result.result()

    def _submit(self, request: _Request) -> None:
        with self._lifecycle_lock:
            if self._fatal is not None:
                raise IngestionError("Durable ingestion writer failed")
            if self._closing:
                raise IngestionError("Durable ingestion is closing")
            if self._closed:
                raise IngestionError("Durable ingestion is closed")
            try:
                self._requests.put_nowait(request)
            except queue.Full:
                raise IntakeFull("Durable ingestion intake is full") from None

    def close(self) -> None:
        with self._close_lock:
            with self._lifecycle_lock:
                if self._closed:
                    return
                self._closing = True
            self._shutdown.set()
            try:
                self._thread.join()
            except BaseException:
                if not self._thread.is_alive():
                    self._resources.close()
                    with self._lifecycle_lock:
                        self._closed = True
                raise
            self._resources.close()
            with self._lifecycle_lock:
                self._closed = True

    def __enter__(self) -> "DurableIngestor":
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()
