"""Real SQLite ownership, durable schema and strict read boundary tests."""

import os
import sqlite3
import stat
import threading
from dataclasses import FrozenInstanceError
from hashlib import sha256
from pathlib import Path
from typing import Any, cast

import pytest

from scryntic.domain.raw import IngestionId
from scryntic.normalization.sqlite_store import (
    BarrierReason,
    NormalizationError,
    NormalizationStore,
    NormalizerOwned,
    OutcomeKind,
)
from tests.normalization.helpers import installation


def test_competing_owner_is_rejected_before_second_sqlite_open(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = installation(tmp_path)
    real_connect = sqlite3.connect
    opened: list[Path] = []

    def connect(database: Path, **kwargs: Any) -> sqlite3.Connection:
        assert kwargs["autocommit"] is True
        opened.append(database)
        return cast(sqlite3.Connection, real_connect(database, **kwargs))

    monkeypatch.setattr(sqlite3, "connect", connect)
    with NormalizationStore(target, producer="collector-a"):
        with pytest.raises(NormalizerOwned, match="already owned"):
            NormalizationStore(target, producer="collector-a")
    assert len(opened) == 1


def test_owner_only_creation_lock_lifetime_and_idempotent_close(tmp_path: Path) -> None:
    target = installation(tmp_path)
    first = NormalizationStore(target, producer="collector-a")
    for name in ("normalization.lock", "normalization.sqlite3"):
        info = (target.state_dir / name).stat()
        assert stat.S_IMODE(info.st_mode) == 0o600
        assert info.st_uid == os.geteuid()
        assert info.st_nlink == 1
    with pytest.raises(NormalizerOwned):
        NormalizationStore(target, producer="collector-a")
    first.close()
    first.close()
    with NormalizationStore(target, producer="collector-a") as replacement:
        assert replacement.checkpoint() is None
    assert not (target.state_dir / "ingestion.sqlite3").exists()


@pytest.mark.parametrize("name", ["normalization.lock", "normalization.sqlite3"])
@pytest.mark.parametrize(
    "unsafe", ["symlink", "hardlink", "mode", "owner", "directory"]
)
def test_rejects_unsafe_fixed_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str, unsafe: str
) -> None:
    target = installation(tmp_path)
    path = target.state_dir / name
    if unsafe == "directory":
        path.mkdir(mode=0o700)
    elif unsafe == "symlink":
        path.symlink_to(tmp_path / "missing")
    elif unsafe == "hardlink":
        original = tmp_path / "original"
        original.touch(mode=0o600)
        path.hardlink_to(original)
    else:
        path.touch(mode=0o600)
        if unsafe == "mode":
            path.chmod(0o640)
        else:
            # Wrong UID cannot be created without privilege: alter only fstat's
            # ownership evidence for this exact inode, preserving real I/O.
            real_fstat = os.fstat
            inode = path.stat().st_ino

            def fstat(fd: int) -> os.stat_result:
                info = real_fstat(fd)
                if info.st_ino == inode:
                    values = list(info)
                    values[4] = info.st_uid + 1
                    return os.stat_result(values)
                return info

            monkeypatch.setattr(os, "fstat", fstat)
    with pytest.raises(NormalizationError):
        NormalizationStore(target, producer="collector-a")


def test_empty_reads_status_and_closed_boundary(tmp_path: Path) -> None:
    target = installation(tmp_path)
    store = NormalizationStore(target, producer="collector-a")
    status = store.status()
    assert status.producer == "collector-a"
    assert status.checkpoint is None
    assert status.barrier is None
    assert status.journal_mode == "wal"
    assert status.synchronous == "FULL"
    assert status.sqlite_version == sqlite3.sqlite_version
    assert store.barrier() is None
    assert store.outcome(IngestionId("collector-a", "epoch-a", 1)) is None
    assert store.observation("sha256:" + "0" * 64) is None
    with pytest.raises(FrozenInstanceError):
        status.producer = "other"  # type: ignore[misc]
    store.close()
    for read in (
        store.checkpoint,
        store.barrier,
        store.status,
        lambda: store.outcome(IngestionId("collector-a", "epoch-a", 1)),
        lambda: store.observation("sha256:" + "0" * 64),
        store.__enter__,
    ):
        with pytest.raises(NormalizationError, match="Normalization store is closed"):
            read()


def test_methods_require_constructing_thread(tmp_path: Path) -> None:
    errors: list[Exception] = []
    with NormalizationStore(installation(tmp_path), producer="collector-a") as store:

        def wrong_thread() -> None:
            for call in (store.status, store.close):
                try:
                    call()
                except Exception as error:
                    errors.append(error)

        thread = threading.Thread(target=wrong_thread)
        thread.start()
        thread.join(timeout=3)
        assert len(errors) == 2
        assert all(isinstance(error, NormalizationError) for error in errors)
        assert store.status().producer == "collector-a"


def test_producer_binding_and_constructor_failure_release(tmp_path: Path) -> None:
    target = installation(tmp_path)
    with NormalizationStore(target, producer="collector-a"):
        pass
    with pytest.raises(NormalizationError, match="producer"):
        NormalizationStore(target, producer="collector-b")
    with NormalizationStore(target, producer="collector-a"):
        pass


@pytest.mark.parametrize(
    "sql",
    [
        "PRAGMA user_version=2",
        "CREATE TABLE unexpected (value TEXT) STRICT",
        "CREATE INDEX unexpected ON normalization_metadata(producer)",
        "CREATE VIEW unexpected AS SELECT producer FROM normalization_metadata",
        "CREATE TRIGGER unexpected AFTER UPDATE ON normalization_metadata BEGIN SELECT 1; END",
        "ALTER TABLE normalization_metadata ADD COLUMN unexpected TEXT",
    ],
)
def test_rejects_unsupported_version_and_unexpected_schema(
    tmp_path: Path, sql: str
) -> None:
    target = installation(tmp_path)
    with NormalizationStore(target, producer="collector-a"):
        pass
    with sqlite3.connect(
        target.state_dir / "normalization.sqlite3", autocommit=True
    ) as db:
        db.execute(sql)
    with pytest.raises(NormalizationError, match="schema"):
        NormalizationStore(target, producer="collector-a")


def test_foreign_key_check_detects_dangling_checkpoint_after_quick_check(
    tmp_path: Path,
) -> None:
    target = installation(tmp_path)
    with NormalizationStore(target, producer="collector-a"):
        pass
    with sqlite3.connect(
        target.state_dir / "normalization.sqlite3", autocommit=True
    ) as db:
        db.execute("PRAGMA foreign_keys=OFF")
        db.execute(
            "INSERT INTO processing_checkpoint VALUES (1, 'collector-a', 'epoch-a', 5)"
        )
        assert db.execute("PRAGMA quick_check(1)").fetchall() == [("ok",)]
    with pytest.raises(NormalizationError, match="foreign key"):
        NormalizationStore(target, producer="collector-a")


@pytest.mark.parametrize(
    ("statement", "replacement", "message"),
    [
        ("PRAGMA journal_mode", "SELECT 'delete'", "WAL"),
        ("PRAGMA synchronous", "SELECT 1", "FULL"),
        ("PRAGMA foreign_keys", "SELECT 0", "foreign keys"),
        ("PRAGMA quick_check(1)", "SELECT 'corrupt secret row'", "integrity"),
    ],
)
def test_reads_back_pragmas_and_checks_integrity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    statement: str,
    replacement: str,
    message: str,
) -> None:
    target = installation(tmp_path)
    real_connect = sqlite3.connect

    class FaultyConnection(sqlite3.Connection):
        def execute(self, sql: str, parameters: Any = (), /) -> sqlite3.Cursor:
            return super().execute(replacement if sql == statement else sql, parameters)

    def connect(database: Path, **kwargs: Any) -> sqlite3.Connection:
        return cast(
            sqlite3.Connection,
            real_connect(database, factory=FaultyConnection, **kwargs),
        )

    with monkeypatch.context() as patch:
        patch.setattr(sqlite3, "connect", connect)
        with pytest.raises(NormalizationError, match=message) as error:
            NormalizationStore(target, producer="collector-a")
        assert "secret" not in str(error.value)
    with NormalizationStore(target, producer="collector-a"):
        pass


def test_schema_is_strict_versioned_and_foreign_keys_are_enabled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_connect = sqlite3.connect
    connections: list[sqlite3.Connection] = []

    def connect(database: Path, **kwargs: Any) -> sqlite3.Connection:
        connection = cast(sqlite3.Connection, real_connect(database, **kwargs))
        connections.append(connection)
        return connection

    monkeypatch.setattr(sqlite3, "connect", connect)
    with NormalizationStore(installation(tmp_path), producer="collector-a"):
        (db,) = connections
        assert db.execute("PRAGMA foreign_keys").fetchone() == (1,)
        assert db.execute("PRAGMA user_version").fetchone() == (1,)
        assert not db.in_transaction
        tables = db.execute("PRAGMA table_list").fetchall()
        owned = {row[1]: row[5] for row in tables if not row[1].startswith("sqlite_")}
        assert owned == {
            "normalization_metadata": 1,
            "candle_observations": 1,
            "processing_outcomes": 1,
            "processing_checkpoint": 1,
            "processing_barrier": 1,
        }
        with pytest.raises(sqlite3.IntegrityError):
            db.execute(
                "INSERT INTO processing_checkpoint VALUES (1, 'collector-a', 'epoch-a', 1)"
            )


_SEMANTIC_BYTES = (
    b'["scryntic-candle-semantic-v1",["fake-venue","spot","BTC-USDT"],10,60,'
    b'"1","2","1","2","3","BTC",false,[7,"ms"],[8,"ns"],["test"]]'
)
_REVISION = "sha256:" + sha256(_SEMANTIC_BYTES).hexdigest()


def seed_state(path: Path) -> None:
    """Insert independent explicit wire/storage fixtures, without store writers."""
    with sqlite3.connect(path, autocommit=True) as db:
        db.execute("PRAGMA foreign_keys=ON")
        db.execute(
            "INSERT INTO candle_observations VALUES "
            "(?, 'fake-venue', 'spot', 'BTC-USDT', 10, 60, '1', '2', '1', '2', '3', "
            "'BTC', 0, 7, 'ms', 8, 'ns', '[\"test\"]')",
            (_REVISION,),
        )
        common = {
            "producer": "collector-a",
            "epoch": "epoch-a",
            "offset": 2,
            "raw_sha256": "a" * 64,
            "kind": "rejected",
            "rejection_code": "invalid_json",
            "normalizer_version": "f06.fake_candle.v1",
            "receipt_wall_time_ns": 100,
            "receipt_monotonic_ns": 101,
            "receipt_session_id": "session-a",
            "quality_epoch": "clock-a",
            "quality_status": "healthy",
            "quality_offset_ns": -2,
            "quality_uncertainty_ns": 3,
            "quality_evidence_age_ns": 4,
        }
        insert_values(db, "processing_outcomes", common)
        insert_values(
            db,
            "processing_outcomes",
            {
                **common,
                "epoch": "epoch-b",
                "offset": 5,
                "predecessor_producer": "collector-a",
                "predecessor_epoch": "epoch-a",
                "predecessor_offset": 2,
                "kind": "accepted",
                "rejection_code": None,
                "semantic_revision": _REVISION,
                "input_schema_name": "fake_candle",
                "input_schema_major": 1,
                "input_schema_minor": 0,
                "instrument_schema_name": "instrument",
                "instrument_schema_major": 1,
                "instrument_schema_minor": 0,
                "output_schema_name": "candle",
                "output_schema_major": 1,
                "output_schema_minor": 0,
                "instrument_revision": "instrument-r1",
                "normalized_at_ns": 102,
            },
        )
        db.execute(
            "INSERT INTO processing_checkpoint VALUES (1, 'collector-a', 'epoch-b', 5)"
        )
        db.execute(
            "INSERT INTO processing_barrier VALUES "
            "(1, 'collector-a', 'epoch-c', 9, 'collector-a', 'epoch-b', 5, ?, "
            "'unsupported_schema', 'fake_candle', 2, 0, NULL, NULL, NULL)",
            ("b" * 64,),
        )


def insert_values(
    db: sqlite3.Connection, table: str, values: dict[str, object]
) -> None:
    # All table/column names are test-owned literals; data remains parameterized.
    db.execute(
        f"INSERT INTO {table} ({', '.join(values)}) VALUES ({', '.join('?' for _ in values)})",
        tuple(values.values()),
    )


def test_decodes_populated_state_and_returns_owned_frozen_values(
    tmp_path: Path,
) -> None:
    target = installation(tmp_path)
    with NormalizationStore(target, producer="collector-a"):
        pass
    seed_state(target.state_dir / "normalization.sqlite3")
    with NormalizationStore(target, producer="collector-a") as store:
        assert store.checkpoint() == IngestionId("collector-a", "epoch-b", 5)
        observation = store.observation(_REVISION)
        assert observation is not None
        assert observation.canonical_bytes() == _SEMANTIC_BYTES
        rejected = store.outcome(IngestionId("collector-a", "epoch-a", 2))
        assert rejected is not None
        assert rejected.kind is OutcomeKind.REJECTED
        assert rejected.predecessor is None
        assert rejected.rejection_code == "invalid_json"
        assert rejected.input_schema is None
        accepted = store.outcome(IngestionId("collector-a", "epoch-b", 5))
        assert accepted is not None
        assert accepted.kind is OutcomeKind.ACCEPTED
        assert accepted.predecessor == rejected.identity
        assert accepted.semantic_revision == _REVISION
        assert accepted.raw_sha256 == "a" * 64
        assert accepted.normalized_at_ns == 102
        assert accepted.instrument_revision == "instrument-r1"
        assert accepted.receipt.wall_time_ns == 100
        assert accepted.receipt.monotonic_ns == 101
        assert accepted.receipt.session_id == "session-a"
        assert accepted.receipt.quality.epoch == "clock-a"
        assert accepted.receipt.quality.status == "healthy"
        assert accepted.receipt.quality.offset_ns == -2
        assert accepted.receipt.quality.uncertainty_ns == 3
        assert accepted.receipt.quality.evidence_age_ns == 4
        barrier = store.barrier()
        assert barrier is not None
        assert barrier.blocker == IngestionId("collector-a", "epoch-c", 9)
        assert barrier.predecessor == accepted.identity
        assert barrier.reason is BarrierReason.UNSUPPORTED_SCHEMA
        assert barrier.raw_sha256 == "b" * 64
        assert barrier.schema is not None and barrier.schema.version.major == 2
        assert barrier.instrument is None
        assert store.status().barrier == barrier
    assert observation.canonical_bytes() == _SEMANTIC_BYTES
    with pytest.raises(FrozenInstanceError):
        accepted.kind = OutcomeKind.DUPLICATE  # type: ignore[misc]


@pytest.mark.parametrize(
    "sql",
    [
        "UPDATE candle_observations SET open='1.00'",
        "UPDATE candle_observations SET high='3'",
        "UPDATE candle_observations SET quality_flags='[ \"test\" ]'",
        'UPDATE candle_observations SET quality_flags=\'["test","test"]\'',
        "UPDATE candle_observations SET quality_flags='{}'",
        "UPDATE processing_outcomes SET raw_sha256='secret invalid hash' WHERE offset=2",
        "UPDATE processing_outcomes SET receipt_session_id='secret invalid session' WHERE offset=2",
        "UPDATE processing_outcomes SET normalizer_version='secret invalid version' WHERE offset=2",
        "UPDATE processing_outcomes SET instrument_revision='secret invalid revision' WHERE offset=5",
        "UPDATE processing_outcomes SET input_schema_name='secret invalid schema' WHERE offset=5",
        "UPDATE processing_barrier SET raw_sha256='secret invalid hash'",
        "UPDATE processing_barrier SET schema_name='secret invalid schema'",
    ],
)
def test_startup_rejects_invalid_stored_values_without_echoing_rows(
    tmp_path: Path, sql: str
) -> None:
    target = installation(tmp_path)
    with NormalizationStore(target, producer="collector-a"):
        pass
    path = target.state_dir / "normalization.sqlite3"
    seed_state(path)
    with sqlite3.connect(path, autocommit=True) as db:
        db.execute(sql)
        assert db.execute("PRAGMA quick_check(1)").fetchall() == [("ok",)]
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []
    with pytest.raises(NormalizationError) as error:
        NormalizationStore(target, producer="collector-a")
    assert "secret" not in str(error.value)


@pytest.mark.parametrize(
    "sql",
    [
        "UPDATE candle_observations SET finalized=2",
        "UPDATE candle_observations SET source_time_value=NULL",
        "UPDATE candle_observations SET publication_time_unit=NULL",
        "UPDATE processing_outcomes SET predecessor_epoch=NULL WHERE offset=5",
        "UPDATE processing_outcomes SET input_schema_minor=NULL WHERE offset=5",
        "UPDATE processing_outcomes SET instrument_schema_minor=NULL WHERE offset=5",
        "UPDATE processing_outcomes SET output_schema_minor=NULL WHERE offset=5",
        "UPDATE processing_outcomes SET semantic_revision=NULL WHERE offset=5",
        "UPDATE processing_outcomes SET rejection_field='arbitrary' WHERE offset=2",
        "UPDATE processing_outcomes SET rejection_code=NULL WHERE offset=2",
        "UPDATE processing_outcomes SET kind='arbitrary' WHERE offset=5",
        "UPDATE processing_outcomes SET quality_status='arbitrary' WHERE offset=5",
        "UPDATE processing_outcomes SET quality_offset_ns=NULL WHERE offset=5",
        "UPDATE processing_outcomes SET predecessor_offset=5 WHERE offset=5",
        "UPDATE processing_outcomes SET normalized_at_ns=NULL WHERE offset=5",
        "UPDATE processing_outcomes SET instrument_revision=NULL WHERE offset=5",
        "UPDATE processing_barrier SET predecessor_epoch=NULL",
        "UPDATE processing_barrier SET schema_name=NULL, schema_major=NULL, schema_minor=NULL",
        "UPDATE processing_barrier SET reason='metadata_unavailable'",
        "UPDATE processing_barrier SET instrument_venue='fake-venue'",
        "UPDATE processing_barrier SET predecessor_offset=9",
    ],
)
def test_database_constraints_reject_incomplete_and_invalid_composites(
    tmp_path: Path, sql: str
) -> None:
    target = installation(tmp_path)
    with NormalizationStore(target, producer="collector-a"):
        pass
    path = target.state_dir / "normalization.sqlite3"
    seed_state(path)
    with sqlite3.connect(path, autocommit=True) as db:
        db.execute("PRAGMA foreign_keys=ON")
        with pytest.raises(sqlite3.IntegrityError):
            db.execute(sql)


@pytest.mark.parametrize("read", ["checkpoint", "outcome", "barrier"])
def test_read_decoder_rejects_zero_committed_offsets(tmp_path: Path, read: str) -> None:
    target = installation(tmp_path)
    with NormalizationStore(target, producer="collector-a"):
        pass
    path = target.state_dir / "normalization.sqlite3"
    seed_state(path)
    with NormalizationStore(target, producer="collector-a") as store:
        # Bypass checks only on a direct adversarial connection to exercise the
        # read decoder independently of startup integrity checks.
        with sqlite3.connect(path, autocommit=True) as db:
            db.execute("PRAGMA ignore_check_constraints=ON")
            table = {
                "checkpoint": "processing_checkpoint",
                "outcome": "processing_outcomes",
                "barrier": "processing_barrier",
            }[read]
            clause = " WHERE offset=2" if read == "outcome" else ""
            db.execute(f"UPDATE {table} SET offset=0{clause}")
        with pytest.raises(NormalizationError):
            if read == "outcome":
                store.outcome(IngestionId("collector-a", "epoch-a", 0))
            elif read == "checkpoint":
                store.checkpoint()
            else:
                store.barrier()


def test_constructor_rolls_back_partial_schema_and_releases_owner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = installation(tmp_path)
    real_connect = sqlite3.connect
    statements: list[str] = []

    class FailingSchema(sqlite3.Connection):
        def execute(self, sql: str, parameters: Any = (), /) -> sqlite3.Cursor:
            statements.append(sql)
            if sql.startswith("CREATE TABLE processing_outcomes"):
                raise sqlite3.OperationalError("injected private detail")
            return super().execute(sql, parameters)

        def commit(self) -> None:
            pytest.fail("Production must use explicit SQL COMMIT")

        def rollback(self) -> None:
            pytest.fail("Production must use explicit SQL ROLLBACK")

    def connect(database: Path, **kwargs: Any) -> sqlite3.Connection:
        return cast(
            sqlite3.Connection, real_connect(database, factory=FailingSchema, **kwargs)
        )

    with monkeypatch.context() as patch:
        patch.setattr(sqlite3, "connect", connect)
        with pytest.raises(NormalizationError) as error:
            NormalizationStore(target, producer="collector-a")
        assert "private" not in str(error.value)
    assert "BEGIN IMMEDIATE" in statements
    assert statements[-1] == "ROLLBACK"
    with sqlite3.connect(
        target.state_dir / "normalization.sqlite3", autocommit=True
    ) as db:
        assert db.execute("PRAGMA user_version").fetchone() == (0,)
        assert db.execute("SELECT name FROM sqlite_schema").fetchall() == []
    with NormalizationStore(target, producer="collector-a"):
        pass


def test_connection_closes_while_ownership_is_still_held(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = installation(tmp_path)
    real_connect = sqlite3.connect
    closed: list[bool] = []

    class CheckedClose(sqlite3.Connection):
        def close(self) -> None:
            with pytest.raises(NormalizerOwned):
                NormalizationStore(target, producer="collector-a")
            super().close()
            closed.append(True)

    def connect(database: Path, **kwargs: Any) -> sqlite3.Connection:
        return cast(
            sqlite3.Connection, real_connect(database, factory=CheckedClose, **kwargs)
        )

    with monkeypatch.context() as patch:
        patch.setattr(sqlite3, "connect", connect)
        with NormalizationStore(target, producer="collector-a"):
            pass
    assert closed == [True]
    with NormalizationStore(target, producer="collector-a"):
        pass


@pytest.mark.parametrize("conflict", ["producer_offset", "second_successor"])
def test_database_rejects_duplicate_offset_and_branching_successors(
    tmp_path: Path, conflict: str
) -> None:
    target = installation(tmp_path)
    with NormalizationStore(target, producer="collector-a"):
        pass
    path = target.state_dir / "normalization.sqlite3"
    seed_state(path)
    with sqlite3.connect(path, autocommit=True) as db:
        db.row_factory = sqlite3.Row
        values = dict(
            db.execute("SELECT * FROM processing_outcomes WHERE offset=5").fetchone()
        )
        values["epoch"] = "epoch-new"
        if conflict == "second_successor":
            values["offset"] = 9
        with pytest.raises(sqlite3.IntegrityError):
            insert_values(db, "processing_outcomes", values)


def test_metadata_barrier_retains_complete_instrument_identity(tmp_path: Path) -> None:
    target = installation(tmp_path)
    with NormalizationStore(target, producer="collector-a"):
        pass
    path = target.state_dir / "normalization.sqlite3"
    seed_state(path)
    with sqlite3.connect(path, autocommit=True) as db:
        db.execute(
            "UPDATE processing_barrier SET reason='metadata_unavailable', schema_major=1, "
            "instrument_venue='fake-venue', instrument_category='spot', instrument_symbol='BTC-USDT'"
        )
    with NormalizationStore(target, producer="collector-a") as store:
        barrier = store.barrier()
        assert barrier is not None
        assert barrier.reason is BarrierReason.METADATA_UNAVAILABLE
        assert barrier.instrument is not None
        assert (
            barrier.instrument.venue,
            barrier.instrument.category,
            barrier.instrument.symbol,
        ) == ("fake-venue", "spot", "BTC-USDT")


def test_unversioned_existing_database_is_rejected_without_schema_repair(
    tmp_path: Path,
) -> None:
    target = installation(tmp_path)
    path = target.state_dir / "normalization.sqlite3"
    path.touch(mode=0o600)
    with sqlite3.connect(path, autocommit=True) as db:
        db.execute("CREATE TABLE existing (value TEXT) STRICT")
    with pytest.raises(NormalizationError, match="schema"):
        NormalizationStore(target, producer="collector-a")
    with sqlite3.connect(path, autocommit=True) as db:
        assert db.execute("PRAGMA user_version").fetchone() == (0,)
        assert db.execute("SELECT name FROM sqlite_schema").fetchall() == [
            ("existing",)
        ]
