"""Single-owner F07 publication state and atomic manifest registration."""

import fcntl
import os
import sqlite3
import stat
import threading
from collections.abc import Callable, Iterator
from contextlib import ExitStack
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from types import TracebackType
from typing import cast

from scryntic.application.archive import ArchiveLimits
from scryntic.archive.canonical import (
    INPUT_FINGERPRINT_ALGORITHM,
    ORDERED_INPUT_ALGORITHM,
    ordered_digest_from_pairs,
)
from scryntic.archive.model import Partition
from scryntic.configuration.paths import Installation, directory
from scryntic.domain.raw import IngestionId
from scryntic.domain.validation import digest, identifier, integer
from scryntic.publication.manifest import (
    GENESIS_MANIFEST_HASH,
    ManifestDocument,
    ManifestRef,
    parse_manifest,
)

_DATABASE_NAME = "publication.sqlite3"
_LOCK_NAME = "publication.lock"
_SCHEMA_VERSION = 1


class PublicationError(RuntimeError):
    """Fixed-message publication state failure."""


class PublisherOwned(PublicationError):
    """Another cooperative owner already holds the publication lock."""


class PendingState(StrEnum):
    RESERVED = "reserved"
    PREPARED = "prepared"


@dataclass(frozen=True, slots=True)
class PublicationLimits:
    max_batch_records: int
    raw: ArchiveLimits
    normalized: ArchiveLimits
    max_manifest_bytes: int
    max_manifests_per_read: int

    def __post_init__(self) -> None:
        for value in (
            self.max_batch_records,
            self.max_manifest_bytes,
            self.max_manifests_per_read,
        ):
            integer(value, 1)
        if not isinstance(self.raw, ArchiveLimits) or not isinstance(
            self.normalized, ArchiveLimits
        ):
            raise TypeError("Expected archive limits")


@dataclass(frozen=True, slots=True)
class PublicationReservation:
    checkpoint_before: IngestionId | None
    checkpoint_after: IngestionId
    epoch: str
    sequence: int
    previous_manifest_hash: str
    partition: Partition
    input_fingerprint_algorithm: str
    inputs: tuple[tuple[IngestionId, str], ...]
    ordered_input_algorithm: str
    ordered_input_digest: str

    def __post_init__(self) -> None:
        identifier(self.epoch)
        integer(self.sequence, 1)
        digest(self.previous_manifest_hash)
        digest(self.ordered_input_digest)
        if self.input_fingerprint_algorithm != INPUT_FINGERPRINT_ALGORITHM:
            raise ValueError("Unsupported input fingerprint algorithm")
        if self.ordered_input_algorithm != ORDERED_INPUT_ALGORITHM:
            raise ValueError("Unsupported ordered input algorithm")
        if not isinstance(self.checkpoint_after, IngestionId) or not isinstance(
            self.partition, Partition
        ):
            raise TypeError("Invalid publication reservation")
        if self.checkpoint_before is not None and not isinstance(
            self.checkpoint_before, IngestionId
        ):
            raise TypeError("Invalid publication predecessor")
        if type(self.inputs) is not tuple or not self.inputs:
            raise ValueError("Publication reservation requires inputs")
        producer = self.checkpoint_after.producer
        previous_offset = 0
        for identity, fingerprint in self.inputs:
            if (
                not isinstance(identity, IngestionId)
                or identity.producer != producer
                or identity.epoch != self.epoch
                or identity.offset <= previous_offset
            ):
                raise ValueError("Invalid publication input order")
            digest(fingerprint)
            previous_offset = identity.offset
        if self.inputs[-1][0] != self.checkpoint_after:
            raise ValueError("Reservation checkpoint does not match inputs")
        if self.checkpoint_before is not None and (
            self.checkpoint_before.producer != producer
            or self.checkpoint_before.offset >= self.inputs[0][0].offset
        ):
            raise ValueError("Invalid publication checkpoint order")
        if ordered_digest_from_pairs(self.inputs) != self.ordered_input_digest:
            raise ValueError("Ordered publication digest mismatch")
        if self.sequence == 1:
            if self.previous_manifest_hash != GENESIS_MANIFEST_HASH:
                raise ValueError("Invalid publication genesis")
        elif self.previous_manifest_hash == GENESIS_MANIFEST_HASH:
            raise ValueError("Invalid publication predecessor")


@dataclass(frozen=True, slots=True)
class PendingPublication:
    state: PendingState
    reservation: PublicationReservation
    manifest_bytes: bytes | None
    manifest_ref: ManifestRef | None


@dataclass(frozen=True, slots=True)
class CatalogEntry:
    ref: ManifestRef
    checkpoint_before: IngestionId | None
    checkpoint_after: IngestionId
    previous_manifest_hash: str
    ordered_input_digest: str
    manifest_bytes: bytes


@dataclass(frozen=True, slots=True)
class PublicationStatus:
    producer: str
    checkpoint: IngestionId | None
    journal_mode: str
    synchronous: str
    sqlite_version: str


_CREATE_METADATA = """
CREATE TABLE publication_metadata (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    producer TEXT NOT NULL
) STRICT
""".strip()

_CREATE_CATALOG = """
CREATE TABLE publication_catalog (
    manifest_hash TEXT PRIMARY KEY NOT NULL,
    producer TEXT NOT NULL,
    epoch TEXT NOT NULL,
    sequence INTEGER NOT NULL CHECK (sequence > 0),
    previous_manifest_hash TEXT NOT NULL,
    before_producer TEXT,
    before_epoch TEXT,
    before_offset INTEGER CHECK (before_offset > 0),
    after_producer TEXT NOT NULL,
    after_epoch TEXT NOT NULL,
    after_offset INTEGER NOT NULL CHECK (after_offset > 0),
    ordered_input_digest TEXT NOT NULL,
    raw_object_sha256 TEXT NOT NULL,
    normalized_object_sha256 TEXT NOT NULL,
    manifest_bytes BLOB NOT NULL,
    UNIQUE (producer, epoch, sequence),
    UNIQUE (producer, after_offset),
    CHECK ((before_producer IS NULL) = (before_epoch IS NULL)
        AND (before_producer IS NULL) = (before_offset IS NULL)),
    CHECK (before_offset < after_offset),
    CHECK (producer = after_producer)
) STRICT
""".strip()

_CREATE_CHECKPOINT = """
CREATE TABLE publication_checkpoint (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    producer TEXT NOT NULL,
    epoch TEXT NOT NULL,
    offset INTEGER NOT NULL CHECK (offset > 0),
    manifest_hash TEXT NOT NULL REFERENCES publication_catalog(manifest_hash)
) STRICT
""".strip()

_CREATE_PENDING = """
CREATE TABLE pending_publication (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    state TEXT NOT NULL CHECK (state IN ('reserved', 'prepared')),
    before_producer TEXT,
    before_epoch TEXT,
    before_offset INTEGER CHECK (before_offset > 0),
    after_producer TEXT NOT NULL,
    after_epoch TEXT NOT NULL,
    after_offset INTEGER NOT NULL CHECK (after_offset > 0),
    epoch TEXT NOT NULL,
    sequence INTEGER NOT NULL CHECK (sequence > 0),
    previous_manifest_hash TEXT NOT NULL,
    partition_source TEXT NOT NULL,
    partition_event_family TEXT NOT NULL CHECK (partition_event_family = 'candle'),
    partition_utc_date TEXT NOT NULL,
    input_fingerprint_algorithm TEXT NOT NULL,
    ordered_input_algorithm TEXT NOT NULL,
    ordered_input_digest TEXT NOT NULL,
    manifest_bytes BLOB,
    manifest_hash TEXT,
    CHECK ((before_producer IS NULL) = (before_epoch IS NULL)
        AND (before_producer IS NULL) = (before_offset IS NULL)),
    CHECK (before_offset < after_offset),
    CHECK ((state = 'reserved' AND manifest_bytes IS NULL AND manifest_hash IS NULL)
        OR (state = 'prepared' AND manifest_bytes IS NOT NULL AND manifest_hash IS NOT NULL))
) STRICT
""".strip()

_CREATE_PENDING_INPUTS = """
CREATE TABLE pending_inputs (
    singleton INTEGER NOT NULL REFERENCES pending_publication(singleton) ON DELETE CASCADE,
    ordinal INTEGER NOT NULL CHECK (ordinal >= 0),
    producer TEXT NOT NULL,
    epoch TEXT NOT NULL,
    offset INTEGER NOT NULL CHECK (offset > 0),
    input_fingerprint TEXT NOT NULL,
    PRIMARY KEY (singleton, ordinal),
    UNIQUE (producer, offset)
) STRICT
""".strip()

_SCHEMA = (
    ("table", "publication_metadata", "publication_metadata", _CREATE_METADATA),
    ("table", "publication_catalog", "publication_catalog", _CREATE_CATALOG),
    (
        "table",
        "publication_checkpoint",
        "publication_checkpoint",
        _CREATE_CHECKPOINT,
    ),
    ("table", "pending_publication", "pending_publication", _CREATE_PENDING),
    ("table", "pending_inputs", "pending_inputs", _CREATE_PENDING_INPUTS),
)


def _identity_values(value: IngestionId | None) -> tuple[object, object, object]:
    if value is None:
        return None, None, None
    return value.producer, value.epoch, value.offset


class PublicationStore:
    """Own the F07 database, lifetime lock, and one explicit transaction owner."""

    def __init__(
        self,
        installation: Installation,
        *,
        producer: str,
        fault: Callable[[str], None] | None = None,
    ) -> None:
        identifier(producer)
        self._producer = producer
        self._fault_callback = fault
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
                raise PublisherOwned("Publication state is already owned") from None
            database_fd = self._open_file(
                state_fd, _DATABASE_NAME, installation.owner_uid
            )
            self._resources.callback(os.close, database_fd)
            database = Path(f"/proc/self/fd/{state_fd}/{_DATABASE_NAME}")
            self._connection = sqlite3.connect(database, autocommit=True)
            self._resources.callback(self._connection.close)
            for name, fd in ((_LOCK_NAME, lock_fd), (_DATABASE_NAME, database_fd)):
                pinned = os.fstat(fd)
                current = os.stat(name, dir_fd=state_fd, follow_symlinks=False)
                if (pinned.st_dev, pinned.st_ino) != (current.st_dev, current.st_ino):
                    raise PublicationError("Unsafe publication state file")
            self._initialize()
        except BaseException as error:
            self._closed = True
            self._resources.close()
            if isinstance(error, PublicationError):
                raise
            if isinstance(error, Exception):
                raise PublicationError(
                    "Unable to initialize publication state"
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
                raise PublicationError("Unsafe publication state file")
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
            raise PublicationError("SQLite WAL mode is unavailable")
        if connection.execute("PRAGMA synchronous").fetchone() != (2,):
            raise PublicationError("SQLite FULL synchronization is unavailable")
        if connection.execute("PRAGMA foreign_keys").fetchone() != (1,):
            raise PublicationError("SQLite foreign keys are unavailable")
        version = connection.execute("PRAGMA user_version").fetchone()
        if version == (0,):
            if (
                connection.execute("SELECT 1 FROM sqlite_schema LIMIT 1").fetchone()
                is not None
            ):
                raise PublicationError("Invalid publication schema")
            connection.execute("BEGIN IMMEDIATE")
            try:
                for _, _, _, sql in _SCHEMA:
                    connection.execute(sql)
                connection.execute(
                    "INSERT INTO publication_metadata VALUES (1, ?)",
                    (self._producer,),
                )
                connection.execute("PRAGMA user_version=1")
                connection.execute("COMMIT")
            except BaseException:
                if connection.in_transaction:
                    connection.execute("ROLLBACK")
                raise
        elif version != (_SCHEMA_VERSION,):
            raise PublicationError("Unsupported publication schema")
        self._validate_schema()
        if connection.execute("PRAGMA quick_check(1)").fetchall() != [("ok",)]:
            raise PublicationError("Publication database integrity check failed")
        if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise PublicationError("Publication database foreign key check failed")
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
            raise PublicationError("Invalid publication schema")
        metadata = self._connection.execute(
            "SELECT singleton, producer FROM publication_metadata"
        ).fetchall()
        if metadata != [(1, self._producer)]:
            raise PublicationError("Publication producer does not own this state")

    def _validate_rows(self) -> None:
        previous = 0
        previous_checkpoint: IngestionId | None = None
        epoch_heads: dict[str, tuple[int, str]] = {}
        for entry in self.catalog_page(None, 2**31 - 1):
            if entry.checkpoint_after.offset <= previous:
                raise PublicationError("Invalid publication catalog order")
            if entry.checkpoint_before != previous_checkpoint:
                raise PublicationError("Invalid publication catalog chain")
            expected_sequence, expected_hash = epoch_heads.get(
                entry.ref.epoch, (0, GENESIS_MANIFEST_HASH)
            )
            if (
                entry.ref.sequence != expected_sequence + 1
                or entry.previous_manifest_hash != expected_hash
            ):
                raise PublicationError("Invalid publication epoch chain")
            epoch_heads[entry.ref.epoch] = (
                entry.ref.sequence,
                entry.ref.manifest_hash,
            )
            previous = entry.checkpoint_after.offset
            previous_checkpoint = entry.checkpoint_after
        checkpoint = self._checkpoint_entry()
        if checkpoint is not None:
            latest = self._row(
                "SELECT manifest_hash FROM publication_catalog "
                "ORDER BY after_offset DESC LIMIT 1"
            )
            if (
                latest is None
                or latest["manifest_hash"] != checkpoint.ref.manifest_hash
            ):
                raise PublicationError("Invalid publication checkpoint")
        pending = self.pending()
        if pending is not None and pending.reservation.checkpoint_before != (
            None if checkpoint is None else checkpoint.checkpoint_after
        ):
            raise PublicationError("Invalid pending publication predecessor")
        if pending is not None:
            expected_sequence, expected_hash = self.next_epoch_link(
                pending.reservation.epoch
            )
            if (
                pending.reservation.sequence,
                pending.reservation.previous_manifest_hash,
            ) != (expected_sequence, expected_hash):
                raise PublicationError("Invalid pending publication epoch chain")

    def _fault(self, stage: str) -> None:
        if self._fault_callback is not None:
            self._fault_callback(stage)

    def _require_open(self) -> None:
        if self._failed:
            raise PublicationError("Durable publication store failed")
        if self._closed:
            raise PublicationError("Publication store is closed")
        if threading.get_ident() != self._owner_thread:
            raise PublicationError("Publication store requires its owning thread")

    def _row(self, sql: str, parameters: tuple[object, ...] = ()) -> sqlite3.Row | None:
        cursor = self._connection.cursor()
        cursor.row_factory = sqlite3.Row
        try:
            return cast(sqlite3.Row | None, cursor.execute(sql, parameters).fetchone())
        except sqlite3.Error:
            raise PublicationError("Unable to read publication state") from None
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
            raise PublicationError("Unable to read publication state") from None
        finally:
            cursor.close()

    @staticmethod
    def _identity(row: sqlite3.Row, prefix: str) -> IngestionId | None:
        values = (
            row[f"{prefix}_producer"],
            row[f"{prefix}_epoch"],
            row[f"{prefix}_offset"],
        )
        if values == (None, None, None):
            return None
        if any(value is None for value in values):
            raise PublicationError("Invalid publication identity")
        return IngestionId(
            cast(str, values[0]), cast(str, values[1]), cast(int, values[2])
        )

    def status(self) -> PublicationStatus:
        self._require_open()
        checkpoint = self._checkpoint_entry()
        return PublicationStatus(
            self._producer,
            None if checkpoint is None else checkpoint.checkpoint_after,
            cast(str, self._connection.execute("PRAGMA journal_mode").fetchone()[0]),
            "FULL",
            sqlite3.sqlite_version,
        )

    def _decode_pending(self, row: sqlite3.Row) -> PendingPublication:
        inputs = tuple(
            (
                IngestionId(item["producer"], item["epoch"], item["offset"]),
                item["input_fingerprint"],
            )
            for item in self._rows(
                "SELECT * FROM pending_inputs WHERE singleton=1 ORDER BY ordinal"
            )
        )
        reservation = PublicationReservation(
            self._identity(row, "before"),
            cast(IngestionId, self._identity(row, "after")),
            row["epoch"],
            row["sequence"],
            row["previous_manifest_hash"],
            Partition(
                row["partition_source"],
                row["partition_event_family"],
                row["partition_utc_date"],
            ),
            row["input_fingerprint_algorithm"],
            inputs,
            row["ordered_input_algorithm"],
            row["ordered_input_digest"],
        )
        state = PendingState(row["state"])
        manifest_bytes = row["manifest_bytes"]
        manifest_hash = row["manifest_hash"]
        ref = (
            None
            if manifest_hash is None
            else ManifestRef(
                self._producer,
                reservation.epoch,
                reservation.sequence,
                manifest_hash,
            )
        )
        pending = PendingPublication(state, reservation, manifest_bytes, ref)
        if (state is PendingState.RESERVED) != (manifest_bytes is None and ref is None):
            raise PublicationError("Invalid pending publication")
        if manifest_bytes is not None:
            self._validate_prepared(reservation, manifest_bytes)
        return pending

    def pending(self) -> PendingPublication | None:
        self._require_open()
        row = self._row("SELECT * FROM pending_publication WHERE singleton=1")
        return None if row is None else self._decode_pending(row)

    def reserve(self, value: PublicationReservation) -> PendingPublication:
        self._require_open()
        if not isinstance(value, PublicationReservation):
            raise TypeError("Expected a publication reservation")
        connection = self._connection
        try:
            connection.execute("BEGIN IMMEDIATE")
            existing = self.pending()
            if existing is not None:
                if existing.reservation == value:
                    connection.execute("COMMIT")
                    return existing
                raise PublicationError("A different publication is pending")
            checkpoint = self._checkpoint_entry()
            expected = None if checkpoint is None else checkpoint.checkpoint_after
            if value.checkpoint_before != expected:
                raise PublicationError("Publication checkpoint changed")
            if value.checkpoint_after.producer != self._producer:
                raise PublicationError("Publication producer mismatch")
            next_sequence, previous_hash = self.next_epoch_link(value.epoch)
            if (value.sequence, value.previous_manifest_hash) != (
                next_sequence,
                previous_hash,
            ):
                raise PublicationError("Publication epoch chain changed")
            connection.execute(
                "INSERT INTO pending_publication VALUES "
                "(1, 'reserved', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL)",
                (
                    *_identity_values(value.checkpoint_before),
                    *_identity_values(value.checkpoint_after),
                    value.epoch,
                    value.sequence,
                    value.previous_manifest_hash,
                    value.partition.source,
                    value.partition.event_family,
                    value.partition.utc_date,
                    value.input_fingerprint_algorithm,
                    value.ordered_input_algorithm,
                    value.ordered_input_digest,
                ),
            )
            for ordinal, (identity, fingerprint) in enumerate(value.inputs):
                connection.execute(
                    "INSERT INTO pending_inputs VALUES (1, ?, ?, ?, ?, ?)",
                    (
                        ordinal,
                        identity.producer,
                        identity.epoch,
                        identity.offset,
                        fingerprint,
                    ),
                )
            self._fault("before_reserve_commit")
            stored = self.pending()
            if stored is None or stored.reservation != value:
                raise PublicationError("Publication reservation readback failed")
            connection.execute("COMMIT")
            return stored
        except BaseException as error:
            self._rollback()
            if isinstance(error, PublicationError):
                raise
            if isinstance(error, Exception):
                raise PublicationError("Unable to reserve publication") from None
            raise

    @staticmethod
    def _validate_prepared(
        reservation: PublicationReservation, manifest_bytes: bytes
    ) -> ManifestDocument:
        document = parse_manifest(manifest_bytes, len(manifest_bytes))
        body = document.body
        if (
            body.producer != reservation.checkpoint_after.producer
            or body.epoch != reservation.epoch
            or body.sequence != reservation.sequence
            or body.previous_manifest_hash != reservation.previous_manifest_hash
            or body.checkpoint_before != reservation.checkpoint_before
            or body.checkpoint_after != reservation.checkpoint_after
            or body.first_ingestion != reservation.inputs[0][0]
            or body.last_ingestion != reservation.inputs[-1][0]
            or body.record_count != len(reservation.inputs)
            or body.ordered_input_algorithm != reservation.ordered_input_algorithm
            or body.ordered_input_digest != reservation.ordered_input_digest
            or body.partition != reservation.partition
        ):
            raise PublicationError("Prepared manifest does not match reservation")
        return document

    def prepare(
        self, pending: PendingPublication, manifest_bytes: bytes
    ) -> PendingPublication:
        self._require_open()
        if (
            not isinstance(pending, PendingPublication)
            or type(manifest_bytes) is not bytes
        ):
            raise TypeError("Expected pending publication and exact bytes")
        document = self._validate_prepared(pending.reservation, manifest_bytes)
        connection = self._connection
        try:
            connection.execute("BEGIN IMMEDIATE")
            current = self.pending()
            if current != pending:
                raise PublicationError("Pending publication changed")
            if current.state is PendingState.PREPARED:
                if current.manifest_bytes != manifest_bytes:
                    raise PublicationError("Prepared manifest changed")
                connection.execute("COMMIT")
                return current
            connection.execute(
                "UPDATE pending_publication SET state='prepared', manifest_bytes=?, "
                "manifest_hash=? WHERE singleton=1 AND state='reserved'",
                (manifest_bytes, document.manifest_hash),
            )
            self._fault("before_prepare_commit")
            prepared = self.pending()
            if prepared is None or prepared.manifest_bytes != manifest_bytes:
                raise PublicationError("Prepared manifest readback failed")
            connection.execute("COMMIT")
            return prepared
        except BaseException as error:
            self._rollback()
            if isinstance(error, PublicationError):
                raise
            if isinstance(error, Exception):
                raise PublicationError("Unable to prepare publication") from None
            raise

    def commit_prepared(self, pending: PendingPublication) -> CatalogEntry:
        self._require_open()
        if (
            not isinstance(pending, PendingPublication)
            or pending.state is not PendingState.PREPARED
            or pending.manifest_bytes is None
        ):
            raise PublicationError("Publication is not prepared")
        document = self._validate_prepared(pending.reservation, pending.manifest_bytes)
        body = document.body
        connection = self._connection
        try:
            connection.execute("BEGIN IMMEDIATE")
            if self.pending() != pending:
                raise PublicationError("Pending publication changed")
            connection.execute(
                "INSERT INTO publication_catalog VALUES "
                "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    document.manifest_hash,
                    body.producer,
                    body.epoch,
                    body.sequence,
                    body.previous_manifest_hash,
                    *_identity_values(body.checkpoint_before),
                    *_identity_values(body.checkpoint_after),
                    body.ordered_input_digest,
                    body.objects[0].sha256,
                    body.objects[1].sha256,
                    pending.manifest_bytes,
                ),
            )
            self._fault("after_catalog_insert")
            checkpoint = self._checkpoint_entry()
            if checkpoint is None:
                connection.execute(
                    "INSERT INTO publication_checkpoint VALUES (1, ?, ?, ?, ?)",
                    (*_identity_values(body.checkpoint_after), document.manifest_hash),
                )
            else:
                before = body.checkpoint_before
                if before != checkpoint.checkpoint_after:
                    raise PublicationError("Publication checkpoint changed")
                updated = connection.execute(
                    "UPDATE publication_checkpoint SET producer=?, epoch=?, offset=?, "
                    "manifest_hash=? WHERE singleton=1 AND manifest_hash=?",
                    (
                        *_identity_values(body.checkpoint_after),
                        document.manifest_hash,
                        checkpoint.ref.manifest_hash,
                    ),
                )
                if updated.rowcount != 1:
                    raise PublicationError("Publication checkpoint did not advance")
            self._fault("after_checkpoint_update")
            connection.execute("DELETE FROM pending_publication WHERE singleton=1")
            self._fault("before_catalog_commit")
            entry = self.catalog_by_hash(document.manifest_hash)
            if entry is None or self.pending() is not None:
                raise PublicationError("Publication transaction readback failed")
            new_checkpoint = self._checkpoint_entry()
            if new_checkpoint != entry:
                raise PublicationError("Publication checkpoint readback failed")
            connection.execute("COMMIT")
            return entry
        except BaseException as error:
            self._rollback()
            if isinstance(error, PublicationError):
                raise
            if isinstance(error, Exception):
                raise PublicationError("Unable to commit publication") from None
            raise

    def _decode_catalog(self, row: sqlite3.Row) -> CatalogEntry:
        manifest_bytes = bytes(row["manifest_bytes"])
        document = parse_manifest(manifest_bytes, len(manifest_bytes))
        entry = CatalogEntry(
            document.ref,
            self._identity(row, "before"),
            cast(IngestionId, self._identity(row, "after")),
            row["previous_manifest_hash"],
            row["ordered_input_digest"],
            manifest_bytes,
        )
        body = document.body
        if (
            entry.ref.manifest_hash != row["manifest_hash"]
            or entry.ref.producer != row["producer"]
            or entry.ref.epoch != row["epoch"]
            or entry.ref.sequence != row["sequence"]
            or entry.checkpoint_before != body.checkpoint_before
            or entry.checkpoint_after != body.checkpoint_after
            or entry.previous_manifest_hash != body.previous_manifest_hash
            or entry.ordered_input_digest != body.ordered_input_digest
            or row["raw_object_sha256"] != body.objects[0].sha256
            or row["normalized_object_sha256"] != body.objects[1].sha256
        ):
            raise PublicationError("Invalid publication catalog entry")
        return entry

    def catalog_by_hash(self, manifest_hash: str) -> CatalogEntry | None:
        self._require_open()
        digest(manifest_hash)
        row = self._row(
            "SELECT * FROM publication_catalog WHERE manifest_hash=?",
            (manifest_hash,),
        )
        return None if row is None else self._decode_catalog(row)

    def catalog_by_epoch_sequence(
        self, epoch: str, sequence: int
    ) -> CatalogEntry | None:
        self._require_open()
        identifier(epoch)
        integer(sequence, 1)
        row = self._row(
            "SELECT * FROM publication_catalog WHERE producer=? AND epoch=? AND sequence=?",
            (self._producer, epoch, sequence),
        )
        return None if row is None else self._decode_catalog(row)

    def catalog_by_checkpoint(self, identity: IngestionId) -> CatalogEntry | None:
        self._require_open()
        row = self._row(
            "SELECT * FROM publication_catalog WHERE producer=? AND after_offset=?",
            (identity.producer, identity.offset),
        )
        if row is None:
            return None
        entry = self._decode_catalog(row)
        return entry if entry.checkpoint_after == identity else None

    def catalog_page(
        self, after: IngestionId | None, limit: int
    ) -> tuple[CatalogEntry, ...]:
        self._require_open()
        integer(limit, 1)
        offset = 0 if after is None else after.offset
        if after is not None and after.producer != self._producer:
            raise PublicationError("Publication producer mismatch")
        return tuple(
            self._decode_catalog(row)
            for row in self._rows(
                "SELECT * FROM publication_catalog WHERE producer=? AND after_offset>? "
                "ORDER BY after_offset LIMIT ?",
                (self._producer, offset, limit),
            )
        )

    def _checkpoint_entry(self) -> CatalogEntry | None:
        row = self._row("SELECT * FROM publication_checkpoint WHERE singleton=1")
        if row is None:
            return None
        entry = self.catalog_by_hash(row["manifest_hash"])
        if entry is None or entry.checkpoint_after != IngestionId(
            row["producer"], row["epoch"], row["offset"]
        ):
            raise PublicationError("Invalid publication checkpoint")
        return entry

    def next_epoch_link(self, epoch: str) -> tuple[int, str]:
        self._require_open()
        identifier(epoch)
        row = self._row(
            "SELECT sequence, manifest_hash FROM publication_catalog "
            "WHERE producer=? AND epoch=? ORDER BY sequence DESC LIMIT 1",
            (self._producer, epoch),
        )
        if row is None:
            return 1, GENESIS_MANIFEST_HASH
        return row["sequence"] + 1, row["manifest_hash"]

    def _rollback(self) -> None:
        try:
            if self._connection.in_transaction:
                self._connection.execute("ROLLBACK")
            if self._connection.in_transaction:
                raise PublicationError("Durable publication store failed")
        except BaseException:
            self._failed = True
            try:
                self._connection.close()
            except BaseException:
                pass
            raise PublicationError("Durable publication store failed") from None

    def close(self) -> None:
        if self._closed:
            return
        if threading.get_ident() != self._owner_thread:
            raise PublicationError("Publication store requires its owning thread")
        self._closed = True
        self._resources.close()

    def __enter__(self) -> "PublicationStore":
        self._require_open()
        return self

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()
