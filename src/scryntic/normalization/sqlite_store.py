"""Single-owner F06 state with explicit transactions and strict owned reads."""

import fcntl
import json
import os
import sqlite3
import stat
import threading
from collections.abc import Iterator
from contextlib import ExitStack
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from pathlib import Path
from types import TracebackType
from typing import Literal, cast

from scryntic.configuration.paths import Installation, directory
from scryntic.domain.identity import InstrumentId, SchemaRef, Version
from scryntic.domain.market import CandleKey
from scryntic.domain.raw import IngestionId, RawRecord
from scryntic.domain.time import ClockSample, SourceTime, TimeQuality, TimeUnit
from scryntic.domain.validation import digest, identifier
from scryntic.normalization.candle import (
    CandleNormalization,
    CandleSemantics,
    NormalizationRejection,
    RejectionCode,
    RejectionField,
)

_DATABASE_NAME = "normalization.sqlite3"
_LOCK_NAME = "normalization.lock"
_SCHEMA_VERSION = 1


class NormalizationError(RuntimeError):
    """Fixed-message normalization state failure."""


class NormalizerOwned(NormalizationError):
    """Another cooperative owner already holds the normalization lock."""


class OutcomeKind(StrEnum):
    ACCEPTED = "accepted"
    DUPLICATE = "duplicate"
    OPEN_REVISION = "open_revision"
    FINALIZATION = "finalization"
    CONFLICT = "conflict"
    REJECTED = "rejected"


class BarrierReason(StrEnum):
    UNSUPPORTED_SCHEMA = "unsupported_schema"
    METADATA_UNAVAILABLE = "metadata_unavailable"


@dataclass(frozen=True, slots=True)
class ProcessingOutcome:
    identity: IngestionId
    predecessor: IngestionId | None
    raw_sha256: str
    kind: OutcomeKind
    semantic_revision: str | None
    rejection_code: RejectionCode | None
    rejection_field: RejectionField | None
    input_schema: SchemaRef | None
    instrument_schema: SchemaRef | None
    output_schema: SchemaRef | None
    instrument_revision: str | None
    normalizer_version: str
    receipt: ClockSample
    normalized_at_ns: int | None


@dataclass(frozen=True, slots=True)
class ProcessingBarrier:
    blocker: IngestionId
    predecessor: IngestionId | None
    raw_sha256: str
    reason: BarrierReason
    schema: SchemaRef | None
    instrument: InstrumentId | None


@dataclass(frozen=True, slots=True)
class NormalizationStatus:
    producer: str
    checkpoint: IngestionId | None
    barrier: ProcessingBarrier | None
    journal_mode: str
    synchronous: str
    sqlite_version: str


_CREATE_METADATA = """
CREATE TABLE normalization_metadata (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    producer TEXT NOT NULL
) STRICT
""".strip()

_CREATE_OBSERVATIONS = """
CREATE TABLE candle_observations (
    revision TEXT PRIMARY KEY NOT NULL,
    venue TEXT NOT NULL,
    category TEXT NOT NULL,
    symbol TEXT NOT NULL,
    start_ns INTEGER NOT NULL CHECK (start_ns >= 0),
    interval_ns INTEGER NOT NULL CHECK (interval_ns > 0),
    open TEXT NOT NULL,
    high TEXT NOT NULL,
    low TEXT NOT NULL,
    close TEXT NOT NULL,
    volume TEXT NOT NULL,
    volume_unit TEXT NOT NULL,
    finalized INTEGER NOT NULL CHECK (finalized IN (0, 1)),
    source_time_value INTEGER,
    source_time_unit TEXT CHECK (source_time_unit IN ('s', 'ms', 'us', 'ns')),
    publication_time_value INTEGER,
    publication_time_unit TEXT CHECK (publication_time_unit IN ('s', 'ms', 'us', 'ns')),
    quality_flags TEXT NOT NULL,
    CHECK ((source_time_value IS NULL) = (source_time_unit IS NULL)),
    CHECK ((publication_time_value IS NULL) = (publication_time_unit IS NULL))
) STRICT
""".strip()

_CREATE_OUTCOMES = """
CREATE TABLE processing_outcomes (
    producer TEXT NOT NULL,
    epoch TEXT NOT NULL,
    offset INTEGER NOT NULL CHECK (offset > 0),
    predecessor_producer TEXT,
    predecessor_epoch TEXT,
    predecessor_offset INTEGER CHECK (predecessor_offset > 0),
    raw_sha256 TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN (
        'accepted', 'duplicate', 'open_revision', 'finalization', 'conflict', 'rejected'
    )),
    semantic_revision TEXT REFERENCES candle_observations(revision),
    rejection_code TEXT CHECK (rejection_code IN (
        'payload_too_large', 'invalid_utf8', 'invalid_json', 'duplicate_json_key',
        'json_token_too_long', 'invalid_schema', 'invalid_subject',
        'field_set_mismatch', 'invalid_field_type', 'integer_out_of_range',
        'invalid_decimal', 'inconsistent_ohlc', 'invalid_time', 'invalid_domain_value'
    )),
    rejection_field TEXT CHECK (rejection_field IN (
        'schema', 'start_ns', 'interval_ns', 'open', 'high', 'low', 'close',
        'volume', 'finalized', 'publication_time', 'subject'
    )),
    input_schema_name TEXT,
    input_schema_major INTEGER CHECK (input_schema_major > 0),
    input_schema_minor INTEGER CHECK (input_schema_minor >= 0),
    instrument_schema_name TEXT,
    instrument_schema_major INTEGER CHECK (instrument_schema_major > 0),
    instrument_schema_minor INTEGER CHECK (instrument_schema_minor >= 0),
    output_schema_name TEXT,
    output_schema_major INTEGER CHECK (output_schema_major > 0),
    output_schema_minor INTEGER CHECK (output_schema_minor >= 0),
    instrument_revision TEXT,
    normalizer_version TEXT NOT NULL,
    receipt_wall_time_ns INTEGER NOT NULL,
    receipt_monotonic_ns INTEGER NOT NULL CHECK (receipt_monotonic_ns >= 0),
    receipt_session_id TEXT NOT NULL,
    quality_epoch TEXT NOT NULL,
    quality_status TEXT NOT NULL CHECK (quality_status IN ('unknown', 'degraded', 'healthy')),
    quality_offset_ns INTEGER,
    quality_uncertainty_ns INTEGER CHECK (quality_uncertainty_ns >= 0),
    quality_evidence_age_ns INTEGER CHECK (quality_evidence_age_ns >= 0),
    normalized_at_ns INTEGER,
    PRIMARY KEY (producer, epoch, offset),
    UNIQUE (producer, offset),
    FOREIGN KEY (predecessor_producer, predecessor_epoch, predecessor_offset)
        REFERENCES processing_outcomes(producer, epoch, offset),
    CHECK ((predecessor_producer IS NULL) = (predecessor_epoch IS NULL)
        AND (predecessor_producer IS NULL) = (predecessor_offset IS NULL)),
    CHECK (predecessor_offset < offset),
    CHECK ((input_schema_name IS NULL) = (input_schema_major IS NULL)
        AND (input_schema_name IS NULL) = (input_schema_minor IS NULL)),
    CHECK ((instrument_schema_name IS NULL) = (instrument_schema_major IS NULL)
        AND (instrument_schema_name IS NULL) = (instrument_schema_minor IS NULL)),
    CHECK ((output_schema_name IS NULL) = (output_schema_major IS NULL)
        AND (output_schema_name IS NULL) = (output_schema_minor IS NULL)),
    CHECK ((instrument_schema_name IS NULL) = (instrument_revision IS NULL)),
    CHECK ((instrument_schema_name IS NULL) = (output_schema_name IS NULL)),
    CHECK (quality_status != 'healthy' OR (quality_offset_ns IS NOT NULL
        AND quality_uncertainty_ns IS NOT NULL AND quality_evidence_age_ns IS NOT NULL)),
    CHECK ((kind = 'rejected' AND semantic_revision IS NULL AND rejection_code IS NOT NULL)
        OR (kind != 'rejected' AND semantic_revision IS NOT NULL
            AND rejection_code IS NULL AND rejection_field IS NULL
            AND input_schema_name IS NOT NULL AND instrument_schema_name IS NOT NULL
            AND output_schema_name IS NOT NULL AND instrument_revision IS NOT NULL
            AND normalized_at_ns IS NOT NULL))
) STRICT
""".strip()

_CREATE_SUCCESSOR_INDEX = """
CREATE UNIQUE INDEX one_successor_per_outcome ON processing_outcomes
    (predecessor_producer, predecessor_epoch, predecessor_offset)
    WHERE predecessor_producer IS NOT NULL
""".strip()

_CREATE_CHECKPOINT = """
CREATE TABLE processing_checkpoint (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    producer TEXT NOT NULL,
    epoch TEXT NOT NULL,
    offset INTEGER NOT NULL CHECK (offset > 0),
    FOREIGN KEY (producer, epoch, offset) REFERENCES processing_outcomes(producer, epoch, offset)
) STRICT
""".strip()

_CREATE_BARRIER = """
CREATE TABLE processing_barrier (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    producer TEXT NOT NULL,
    epoch TEXT NOT NULL,
    offset INTEGER NOT NULL CHECK (offset > 0),
    predecessor_producer TEXT,
    predecessor_epoch TEXT,
    predecessor_offset INTEGER CHECK (predecessor_offset > 0),
    raw_sha256 TEXT NOT NULL,
    reason TEXT NOT NULL CHECK (reason IN ('unsupported_schema', 'metadata_unavailable')),
    schema_name TEXT,
    schema_major INTEGER CHECK (schema_major > 0),
    schema_minor INTEGER CHECK (schema_minor >= 0),
    instrument_venue TEXT,
    instrument_category TEXT,
    instrument_symbol TEXT,
    FOREIGN KEY (predecessor_producer, predecessor_epoch, predecessor_offset)
        REFERENCES processing_outcomes(producer, epoch, offset),
    CHECK ((predecessor_producer IS NULL) = (predecessor_epoch IS NULL)
        AND (predecessor_producer IS NULL) = (predecessor_offset IS NULL)),
    CHECK (predecessor_offset < offset),
    CHECK ((schema_name IS NULL) = (schema_major IS NULL)
        AND (schema_name IS NULL) = (schema_minor IS NULL)),
    CHECK ((instrument_venue IS NULL) = (instrument_category IS NULL)
        AND (instrument_venue IS NULL) = (instrument_symbol IS NULL)),
    CHECK (schema_name IS NOT NULL),
    CHECK ((reason = 'unsupported_schema' AND instrument_venue IS NULL)
        OR (reason = 'metadata_unavailable' AND instrument_venue IS NOT NULL))
) STRICT
""".strip()

_SCHEMA = (
    ("table", "normalization_metadata", "normalization_metadata", _CREATE_METADATA),
    ("table", "candle_observations", "candle_observations", _CREATE_OBSERVATIONS),
    ("table", "processing_outcomes", "processing_outcomes", _CREATE_OUTCOMES),
    (
        "index",
        "one_successor_per_outcome",
        "processing_outcomes",
        _CREATE_SUCCESSOR_INDEX,
    ),
    ("table", "processing_checkpoint", "processing_checkpoint", _CREATE_CHECKPOINT),
    ("table", "processing_barrier", "processing_barrier", _CREATE_BARRIER),
)


class NormalizationStore:
    """Own a pinned state directory, lifetime lock and one synchronous connection."""

    def __init__(self, installation: Installation, *, producer: str) -> None:
        identifier(producer)
        self._producer = producer
        self._owner_thread = threading.get_ident()
        self._closed = False
        self._failed = False
        self._resources = ExitStack()
        try:
            state_fd = self._resources.enter_context(
                directory(installation.state_dir, installation.owner_uid, private=True)
            )
            lock_fd = self._open_file(state_fd, _LOCK_NAME, installation.owner_uid)
            self._resources.callback(os.close, lock_fd)
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise NormalizerOwned("Normalization state is already owned") from None
            database_fd = self._open_file(
                state_fd, _DATABASE_NAME, installation.owner_uid
            )
            self._resources.callback(os.close, database_fd)
            database = Path(f"/proc/self/fd/{state_fd}/{_DATABASE_NAME}")
            self._connection = sqlite3.connect(database, autocommit=True)
            self._resources.callback(self._connection.close)
            # Verify the fixed name still denotes the validated inode after open.
            for name, fd in ((_LOCK_NAME, lock_fd), (_DATABASE_NAME, database_fd)):
                pinned = os.fstat(fd)
                current = os.stat(name, dir_fd=state_fd, follow_symlinks=False)
                if (pinned.st_dev, pinned.st_ino) != (current.st_dev, current.st_ino):
                    raise NormalizationError("Unsafe normalization state file")
            self._initialize()
        except BaseException as error:
            self._closed = True
            self._resources.close()
            if isinstance(error, NormalizationError):
                raise
            if isinstance(error, Exception):
                raise NormalizationError(
                    "Unable to initialize normalization state"
                ) from None
            raise

    @staticmethod
    def _open_file(state_fd: int, name: str, owner_uid: int) -> int:
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
                raise NormalizationError("Unsafe normalization state file")
            return fd
        except BaseException:
            os.close(fd)
            raise

    def _initialize(self) -> None:
        connection = self._connection
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=FULL")
        connection.execute("PRAGMA foreign_keys=ON")
        if connection.execute("PRAGMA journal_mode").fetchone() != ("wal",):
            raise NormalizationError("SQLite WAL mode is unavailable")
        if connection.execute("PRAGMA synchronous").fetchone() != (2,):
            raise NormalizationError("SQLite FULL synchronization is unavailable")
        if connection.execute("PRAGMA foreign_keys").fetchone() != (1,):
            raise NormalizationError("SQLite foreign keys are unavailable")
        version = connection.execute("PRAGMA user_version").fetchone()
        if version == (0,):
            if (
                connection.execute("SELECT 1 FROM sqlite_schema LIMIT 1").fetchone()
                is not None
            ):
                raise NormalizationError("Invalid normalization schema")
            connection.execute("BEGIN IMMEDIATE")
            try:
                for _, _, _, sql in _SCHEMA:
                    connection.execute(sql)
                connection.execute(
                    "INSERT INTO normalization_metadata VALUES (1, ?)",
                    (self._producer,),
                )
                connection.execute("PRAGMA user_version=1")
                connection.execute("COMMIT")
            except BaseException:
                if connection.in_transaction:
                    connection.execute("ROLLBACK")
                raise
        elif version != (_SCHEMA_VERSION,):
            raise NormalizationError("Unsupported normalization schema")
        self._validate_schema()
        if connection.execute("PRAGMA quick_check(1)").fetchall() != [("ok",)]:
            raise NormalizationError("Normalization database integrity check failed")
        if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise NormalizationError("Normalization database foreign key check failed")
        self._validate_rows()

    def _validate_schema(self) -> None:
        rows = self._connection.execute(
            "SELECT type, name, tbl_name, sql FROM sqlite_schema "
            "WHERE name NOT GLOB 'sqlite_*' ORDER BY name"
        ).fetchall()
        actual = tuple((r[0], r[1], r[2], " ".join(r[3].split())) for r in rows)
        expected = tuple(
            sorted(
                (
                    (kind, name, table, " ".join(sql.split()))
                    for kind, name, table, sql in _SCHEMA
                ),
                key=lambda row: row[1],
            )
        )
        if actual != expected:
            raise NormalizationError("Invalid normalization schema")
        metadata = self._connection.execute(
            "SELECT singleton, producer FROM normalization_metadata"
        ).fetchall()
        if metadata != [(1, self._producer)]:
            raise NormalizationError("Normalization producer does not own this state")

    def _validate_rows(self) -> None:
        # Decode incrementally, retaining no database-sized list of owned values.
        for row in self._rows("SELECT * FROM candle_observations"):
            self._decode_observation(row)
        for row in self._rows("SELECT * FROM processing_outcomes"):
            self._decode_outcome(row)
        self.checkpoint()
        self.barrier()

    def _require_open(self) -> None:
        if self._failed:
            raise NormalizationError("Durable normalization store failed")
        if self._closed:
            raise NormalizationError("Normalization store is closed")
        self._require_thread()

    def _require_thread(self) -> None:
        if threading.get_ident() != self._owner_thread:
            raise NormalizationError("Normalization store requires its owning thread")

    def _row(self, sql: str, parameters: tuple[object, ...] = ()) -> sqlite3.Row | None:
        cursor = self._connection.cursor()
        cursor.row_factory = sqlite3.Row
        try:
            return cast(sqlite3.Row | None, cursor.execute(sql, parameters).fetchone())
        except sqlite3.Error:
            raise NormalizationError("Unable to read normalization state") from None
        finally:
            cursor.close()

    def _rows(
        self, sql: str, parameters: tuple[object, ...] = ()
    ) -> Iterator[sqlite3.Row]:
        cursor = self._connection.cursor()
        cursor.row_factory = sqlite3.Row
        try:
            yield from cursor.execute(sql, parameters)
        except sqlite3.Error:
            raise NormalizationError("Unable to read normalization state") from None
        finally:
            cursor.close()

    def checkpoint(self) -> IngestionId | None:
        self._require_open()
        row = self._row("SELECT * FROM processing_checkpoint")
        if row is None:
            return None
        try:
            if row["singleton"] != 1:
                raise ValueError
            return self._identity(row)
        except (TypeError, ValueError, KeyError, IndexError):
            raise NormalizationError("Invalid normalization checkpoint") from None

    def barrier(self) -> ProcessingBarrier | None:
        self._require_open()
        row = self._row("SELECT * FROM processing_barrier")
        return None if row is None else self._decode_barrier(row)

    def outcome(self, identity: IngestionId) -> ProcessingOutcome | None:
        self._require_open()
        row = self._row(
            "SELECT * FROM processing_outcomes WHERE producer=? AND epoch=? AND offset=?",
            (identity.producer, identity.epoch, identity.offset),
        )
        return None if row is None else self._decode_outcome(row)

    def observation(self, revision: str) -> CandleSemantics | None:
        self._require_open()
        _revision(revision)
        row = self._row(
            "SELECT * FROM candle_observations WHERE revision=?", (revision,)
        )
        return None if row is None else self._decode_observation(row)

    def block(
        self,
        record: RawRecord,
        *,
        expected_predecessor: IngestionId | None,
        reason: BarrierReason,
        schema: SchemaRef | None,
        instrument: InstrumentId | None = None,
    ) -> ProcessingBarrier:
        """Persist the next blocker without changing outcomes or progress."""
        self._require_open()
        connection = self._connection
        try:
            connection.execute("BEGIN IMMEDIATE")
            predecessor = self.checkpoint()
            if predecessor != expected_predecessor:
                raise NormalizationError(
                    "Normalization checkpoint does not match predecessor"
                )
            identity = record.identity
            if identity.producer != self._producer or identity.offset <= (
                0 if predecessor is None else predecessor.offset
            ):
                raise NormalizationError("Invalid normalization record order")
            if (
                not isinstance(reason, BarrierReason)
                or not isinstance(schema, SchemaRef)
                or (
                    reason is BarrierReason.UNSUPPORTED_SCHEMA
                    and instrument is not None
                )
                or (
                    reason is BarrierReason.METADATA_UNAVAILABLE
                    and (
                        not isinstance(instrument, InstrumentId)
                        or instrument != record.envelope.subject
                    )
                )
            ):
                raise NormalizationError("Invalid normalization barrier details")
            barrier = ProcessingBarrier(
                identity,
                predecessor,
                record.envelope.content_sha256,
                reason,
                schema,
                instrument,
            )
            existing = self.barrier()
            if existing is None:
                connection.execute(
                    "INSERT INTO processing_barrier VALUES "
                    "(1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        *_identity_values(identity),
                        *_identity_values(predecessor),
                        barrier.raw_sha256,
                        reason.value,
                        schema.name,
                        schema.version.major,
                        schema.version.minor,
                        instrument.venue if instrument is not None else None,
                        instrument.category if instrument is not None else None,
                        instrument.symbol if instrument is not None else None,
                    ),
                )
            elif existing != barrier:
                raise NormalizationError("Normalization record does not match barrier")
            if self.barrier() != barrier or self.checkpoint() != predecessor:
                raise NormalizationError("Normalization barrier readback failed")
            connection.execute("COMMIT")
            return barrier
        except BaseException as error:
            self._rollback()
            if isinstance(error, NormalizationError):
                raise
            if isinstance(error, Exception):
                raise NormalizationError(
                    "Unable to persist normalization barrier"
                ) from None
            raise

    def process(
        self,
        record: RawRecord,
        normalization: CandleNormalization | NormalizationRejection,
        *,
        expected_predecessor: IngestionId | None,
    ) -> ProcessingOutcome:
        """Commit one immutable outcome and its checkpoint as a single unit."""
        self._require_open()
        connection = self._connection
        try:
            connection.execute("BEGIN IMMEDIATE")
            predecessor = self.checkpoint()
            if predecessor != expected_predecessor:
                raise NormalizationError(
                    "Normalization checkpoint does not match predecessor"
                )
            identity = record.identity
            if identity.producer != self._producer or identity.offset <= (
                0 if predecessor is None else predecessor.offset
            ):
                raise NormalizationError("Invalid normalization record order")
            barrier = self.barrier()
            if barrier is not None and (
                barrier.blocker != identity
                or barrier.predecessor != predecessor
                or barrier.raw_sha256 != record.envelope.content_sha256
            ):
                raise NormalizationError("Normalization record does not match barrier")
            if isinstance(normalization, CandleNormalization):
                if (
                    normalization.candle.raw_record != identity
                    or normalization.candle.receipt != record.envelope.receipt
                ):
                    raise NormalizationError(
                        "Normalization result does not match raw record"
                    )
                kind = self._store_semantics(normalization.semantics)
            else:
                if (
                    normalization.raw_record != identity
                    or normalization.raw_sha256 != record.envelope.content_sha256
                ):
                    raise NormalizationError(
                        "Normalization result does not match raw record"
                    )
                kind = OutcomeKind.REJECTED
            outcome = self._processing_outcome(record, normalization, predecessor, kind)
            self._insert_outcome(outcome)
            identity_values = (identity.producer, identity.epoch, identity.offset)
            if predecessor is None:
                connection.execute(
                    "INSERT INTO processing_checkpoint VALUES (1, ?, ?, ?)",
                    identity_values,
                )
            else:
                updated = connection.execute(
                    "UPDATE processing_checkpoint SET producer=?, epoch=?, offset=? "
                    "WHERE singleton=1 AND producer=? AND epoch=? AND offset=?",
                    (
                        *identity_values,
                        predecessor.producer,
                        predecessor.epoch,
                        predecessor.offset,
                    ),
                )
                if updated.rowcount != 1:
                    raise NormalizationError("Normalization checkpoint did not advance")
            if barrier is not None:
                connection.execute(
                    "DELETE FROM processing_barrier WHERE singleton=1 "
                    "AND producer=? AND epoch=? AND offset=? AND raw_sha256=? "
                    "AND predecessor_producer IS ? AND predecessor_epoch IS ? "
                    "AND predecessor_offset IS ?",
                    (
                        *identity_values,
                        barrier.raw_sha256,
                        *_identity_values(predecessor),
                    ),
                )
            if (
                self.outcome(identity) != outcome
                or self.checkpoint() != identity
                or self.barrier() is not None
            ):
                raise NormalizationError("Normalization transaction readback failed")
            if (
                isinstance(normalization, CandleNormalization)
                and self.observation(normalization.candle.revision)
                != normalization.semantics
            ):
                raise NormalizationError(
                    "Stored semantic revision does not match computed values"
                )
            connection.execute("COMMIT")
            return outcome
        except BaseException as error:
            self._rollback()
            if isinstance(error, NormalizationError):
                raise
            if isinstance(error, Exception):
                raise NormalizationError(
                    "Unable to persist normalization outcome"
                ) from None
            raise

    def _rollback(self) -> None:
        try:
            if self._connection.in_transaction:
                self._connection.execute("ROLLBACK")
            if self._connection.in_transaction:
                raise NormalizationError("Durable normalization store failed")
        except BaseException:
            self._failed = True
            try:
                self._connection.close()
            except BaseException:
                pass
            raise NormalizationError("Durable normalization store failed") from None

    def _store_semantics(self, value: CandleSemantics) -> OutcomeKind:
        revision = value.revision()
        existing = self.observation(revision)
        if existing is not None:
            if existing != value:
                raise NormalizationError(
                    "Stored semantic revision does not match computed values"
                )
            return OutcomeKind.DUPLICATE
        key = value.key
        instrument = key.instrument
        previous = self._row(
            "SELECT MAX(finalized) AS finalized FROM candle_observations "
            "WHERE venue=? AND category=? AND symbol=? AND start_ns=? AND interval_ns=?",
            (
                instrument.venue,
                instrument.category,
                instrument.symbol,
                key.start_ns,
                key.interval_ns,
            ),
        )
        assert previous is not None
        if previous["finalized"] is None:
            kind = OutcomeKind.ACCEPTED
        elif previous["finalized"] == 1:
            kind = OutcomeKind.CONFLICT
        elif value.finalized:
            kind = OutcomeKind.FINALIZATION
        else:
            kind = OutcomeKind.OPEN_REVISION
        self._connection.execute(
            "INSERT INTO candle_observations VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                revision,
                instrument.venue,
                instrument.category,
                instrument.symbol,
                key.start_ns,
                key.interval_ns,
                value.open,
                value.high,
                value.low,
                value.close,
                value.volume,
                value.volume_unit,
                int(value.finalized),
                *_time_values(value.source_time),
                *_time_values(value.publication_time),
                json.dumps(
                    value.quality_flags, ensure_ascii=True, separators=(",", ":")
                ),
            ),
        )
        return kind

    @staticmethod
    def _processing_outcome(
        record: RawRecord,
        normalization: CandleNormalization | NormalizationRejection,
        predecessor: IngestionId | None,
        kind: OutcomeKind,
    ) -> ProcessingOutcome:
        revision: str | None
        code: RejectionCode | None
        field: RejectionField | None
        output_schema: SchemaRef | None
        normalized_at_ns: int | None
        if isinstance(normalization, CandleNormalization):
            revision = normalization.candle.revision
            code = None
            field = None
            output_schema = normalization.candle.schema
            normalizer_version = normalization.candle.normalizer_version
            normalized_at_ns = normalization.candle.normalized_at_ns
        else:
            revision = None
            code = normalization.code
            field = normalization.field
            output_schema = normalization.output_schema
            normalizer_version = normalization.normalizer_version
            normalized_at_ns = normalization.normalized_at_ns
        return ProcessingOutcome(
            identity=record.identity,
            predecessor=predecessor,
            raw_sha256=record.envelope.content_sha256,
            kind=kind,
            semantic_revision=revision,
            rejection_code=code,
            rejection_field=field,
            input_schema=normalization.input_schema,
            instrument_schema=normalization.instrument_schema,
            output_schema=output_schema,
            instrument_revision=normalization.instrument_revision,
            normalizer_version=normalizer_version,
            receipt=record.envelope.receipt,
            normalized_at_ns=normalized_at_ns,
        )

    def _insert_outcome(self, outcome: ProcessingOutcome) -> None:
        receipt = outcome.receipt
        quality = receipt.quality
        predecessor = _identity_values(outcome.predecessor)
        values: dict[str, object] = {
            "producer": outcome.identity.producer,
            "epoch": outcome.identity.epoch,
            "offset": outcome.identity.offset,
            "predecessor_producer": predecessor[0],
            "predecessor_epoch": predecessor[1],
            "predecessor_offset": predecessor[2],
            "raw_sha256": outcome.raw_sha256,
            "kind": outcome.kind.value,
            "semantic_revision": outcome.semantic_revision,
            "rejection_code": outcome.rejection_code,
            "rejection_field": outcome.rejection_field,
            "instrument_revision": outcome.instrument_revision,
            "normalizer_version": outcome.normalizer_version,
            "receipt_wall_time_ns": receipt.wall_time_ns,
            "receipt_monotonic_ns": receipt.monotonic_ns,
            "receipt_session_id": receipt.session_id,
            "quality_epoch": quality.epoch,
            "quality_status": quality.status,
            "quality_offset_ns": quality.offset_ns,
            "quality_uncertainty_ns": quality.uncertainty_ns,
            "quality_evidence_age_ns": quality.evidence_age_ns,
            "normalized_at_ns": outcome.normalized_at_ns,
        }
        for prefix, schema in (
            ("input_schema", outcome.input_schema),
            ("instrument_schema", outcome.instrument_schema),
            ("output_schema", outcome.output_schema),
        ):
            values[prefix + "_name"] = None if schema is None else schema.name
            values[prefix + "_major"] = None if schema is None else schema.version.major
            values[prefix + "_minor"] = None if schema is None else schema.version.minor
        # Column names are fixed above; all record-derived values are parameters.
        self._connection.execute(
            f"INSERT INTO processing_outcomes ({', '.join(values)}) "
            f"VALUES ({', '.join('?' for _ in values)})",
            tuple(values.values()),
        )

    def status(self) -> NormalizationStatus:
        self._require_open()
        return NormalizationStatus(
            self._producer,
            self.checkpoint(),
            self.barrier(),
            "wal",
            "FULL",
            sqlite3.sqlite_version,
        )

    def close(self) -> None:
        if self._closed:
            return
        self._require_thread()
        self._closed = True
        self._resources.close()

    def __enter__(self) -> "NormalizationStore":
        self._require_open()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def _identity(self, row: sqlite3.Row, prefix: str = "") -> IngestionId:
        result = IngestionId(
            _text(row[prefix + "producer"]),
            _text(row[prefix + "epoch"]),
            _integer(row[prefix + "offset"]),
        )
        if result.producer != self._producer or result.offset <= 0:
            raise ValueError
        return result

    def _predecessor(
        self, row: sqlite3.Row, current: IngestionId
    ) -> IngestionId | None:
        values = tuple(
            row["predecessor_" + name] for name in ("producer", "epoch", "offset")
        )
        if _absent(values):
            return None
        result = self._identity(row, "predecessor_")
        if result.offset >= current.offset:
            raise ValueError
        return result

    @staticmethod
    def _decode_observation(row: sqlite3.Row) -> CandleSemantics:
        try:
            # Structural validity is checked on every read. Hash/value binding
            # is checked against the incoming full semantics inside process().
            _revision(row["revision"])
            encoded_flags = _text(row["quality_flags"])
            flags = json.loads(encoded_flags)
            if type(flags) is not list or any(type(flag) is not str for flag in flags):
                raise ValueError
            if (
                json.dumps(flags, ensure_ascii=True, separators=(",", ":"))
                != encoded_flags
            ):
                raise ValueError
            finalized = _integer(row["finalized"])
            if finalized not in (0, 1) or _integer(row["start_ns"]) < 0:
                raise ValueError
            value = CandleSemantics(
                CandleKey(
                    InstrumentId(
                        _text(row["venue"]),
                        _text(row["category"]),
                        _text(row["symbol"]),
                    ),
                    _integer(row["start_ns"]),
                    _integer(row["interval_ns"]),
                ),
                _text(row["open"]),
                _text(row["high"]),
                _text(row["low"]),
                _text(row["close"]),
                _text(row["volume"]),
                _text(row["volume_unit"]),
                bool(finalized),
                _time(row, "source_time"),
                _time(row, "publication_time"),
                tuple(flags),
            )
            if (
                not Decimal(value.low)
                <= min(Decimal(value.open), Decimal(value.close))
                <= max(Decimal(value.open), Decimal(value.close))
                <= Decimal(value.high)
            ):
                raise ValueError
            return value
        except (TypeError, ValueError, KeyError, IndexError):
            raise NormalizationError("Invalid normalization observation") from None

    def _decode_outcome(self, row: sqlite3.Row) -> ProcessingOutcome:
        try:
            identity = self._identity(row)
            predecessor = self._predecessor(row, identity)
            raw_sha256 = _text(row["raw_sha256"])
            digest(raw_sha256)
            kind = OutcomeKind(_text(row["kind"]))
            semantic = (
                None
                if row["semantic_revision"] is None
                else _revision(row["semantic_revision"])
            )
            code = (
                None
                if row["rejection_code"] is None
                else RejectionCode(_text(row["rejection_code"]))
            )
            field = (
                None
                if row["rejection_field"] is None
                else RejectionField(_text(row["rejection_field"]))
            )
            input_schema = _schema(row, "input_schema")
            instrument_schema = _schema(row, "instrument_schema")
            output_schema = _schema(row, "output_schema")
            instrument_revision = _optional_text(row["instrument_revision"])
            normalizer_version = _text(row["normalizer_version"])
            identifier(normalizer_version)
            if instrument_revision is not None:
                identifier(instrument_revision)
            if (
                len(
                    {
                        instrument_schema is None,
                        instrument_revision is None,
                        output_schema is None,
                    }
                )
                != 1
            ):
                raise ValueError
            normalized_at = _optional_integer(row["normalized_at_ns"])
            if kind is OutcomeKind.REJECTED:
                if semantic is not None or code is None:
                    raise ValueError
            elif (
                semantic is None
                or code is not None
                or field is not None
                or any(
                    value is None
                    for value in (
                        input_schema,
                        instrument_schema,
                        output_schema,
                        instrument_revision,
                        normalized_at,
                    )
                )
            ):
                raise ValueError
            quality_status = _text(row["quality_status"])
            if quality_status not in ("unknown", "degraded", "healthy"):
                raise ValueError
            receipt = ClockSample(
                _integer(row["receipt_wall_time_ns"]),
                _integer(row["receipt_monotonic_ns"]),
                _text(row["receipt_session_id"]),
                TimeQuality(
                    _text(row["quality_epoch"]),
                    cast(Literal["unknown", "degraded", "healthy"], quality_status),
                    _optional_integer(row["quality_offset_ns"]),
                    _optional_integer(row["quality_uncertainty_ns"]),
                    _optional_integer(row["quality_evidence_age_ns"]),
                ),
            )
            return ProcessingOutcome(
                identity,
                predecessor,
                raw_sha256,
                kind,
                semantic,
                code,
                field,
                input_schema,
                instrument_schema,
                output_schema,
                instrument_revision,
                normalizer_version,
                receipt,
                normalized_at,
            )
        except (TypeError, ValueError, KeyError, IndexError):
            raise NormalizationError("Invalid normalization outcome") from None

    def _decode_barrier(self, row: sqlite3.Row) -> ProcessingBarrier:
        try:
            if row["singleton"] != 1:
                raise ValueError
            blocker = self._identity(row)
            predecessor = self._predecessor(row, blocker)
            raw_sha256 = _text(row["raw_sha256"])
            digest(raw_sha256)
            reason = BarrierReason(_text(row["reason"]))
            schema = _schema(row, "schema")
            values = tuple(
                row["instrument_" + key] for key in ("venue", "category", "symbol")
            )
            instrument = (
                None if _absent(values) else InstrumentId(*(_text(v) for v in values))
            )
            if schema is None or (reason is BarrierReason.UNSUPPORTED_SCHEMA) != (
                instrument is None
            ):
                raise ValueError
            return ProcessingBarrier(
                blocker, predecessor, raw_sha256, reason, schema, instrument
            )
        except (TypeError, ValueError, KeyError, IndexError):
            raise NormalizationError("Invalid normalization barrier") from None


def _identity_values(
    identity: IngestionId | None,
) -> tuple[str | None, str | None, int | None]:
    if identity is None:
        return (None, None, None)
    return (identity.producer, identity.epoch, identity.offset)


def _time_values(value: SourceTime | None) -> tuple[int | None, str | None]:
    return (None, None) if value is None else (value.value, value.unit.value)


def _text(value: object) -> str:
    if type(value) is not str:
        raise ValueError
    return value


def _optional_text(value: object) -> str | None:
    return None if value is None else _text(value)


def _integer(value: object) -> int:
    if type(value) is not int or not -(2**63) <= value <= 2**63 - 1:
        raise ValueError
    return value


def _optional_integer(value: object) -> int | None:
    return None if value is None else _integer(value)


def _absent(values: tuple[object, ...]) -> bool:
    if all(value is None for value in values):
        return True
    if any(value is None for value in values):
        raise ValueError
    return False


def _revision(value: object) -> str:
    text = _text(value)
    if not text.startswith("sha256:"):
        raise ValueError("Expected a semantic revision")
    digest(text[7:])
    return text


def _schema(row: sqlite3.Row, prefix: str) -> SchemaRef | None:
    values = tuple(row[prefix + "_" + key] for key in ("name", "major", "minor"))
    if _absent(values):
        return None
    return SchemaRef(
        _text(values[0]), Version(_integer(values[1]), _integer(values[2]))
    )


def _time(row: sqlite3.Row, prefix: str) -> SourceTime | None:
    values = (row[prefix + "_value"], row[prefix + "_unit"])
    if _absent(values):
        return None
    return SourceTime(_integer(values[0]), TimeUnit(_text(values[1])))
