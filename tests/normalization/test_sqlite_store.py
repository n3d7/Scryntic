"""Real SQLite ownership, durable schema and strict read boundary tests."""

import os
import sqlite3
import stat
import threading
from dataclasses import FrozenInstanceError, replace
from hashlib import sha256
from pathlib import Path
from typing import Any, cast

import pytest

from scryntic.domain.identity import SchemaRef, Version
from scryntic.domain.raw import IngestionId
from scryntic.domain.time import ClockSample, TimeQuality
from scryntic.normalization.candle import CandleNormalization, NormalizationRejection
from scryntic.normalization.sqlite_store import (
    BarrierReason,
    NormalizationError,
    NormalizationStore,
    NormalizerOwned,
    OutcomeKind,
)
from tests.normalization.helpers import (
    fake_candle_payload,
    installation,
    normalization,
    raw_record,
)


class TransactionConnection(sqlite3.Connection):
    """Real SQLite with boundary-only failure injection and SQL observation."""

    failure: str | None = None
    rollback_failure: str | None = None
    ignored_statement: str | None = None

    def execute(self, sql: str, parameters: Any = (), /) -> sqlite3.Cursor:
        if self.ignored_statement is not None and sql.startswith(
            self.ignored_statement
        ):
            self.ignored_statement = None
            return super().execute("SELECT 1")
        if self.failure is not None and sql.startswith(self.failure):
            self.failure = None
            raise sqlite3.OperationalError("injected private detail")
        if sql == "ROLLBACK":
            if self.rollback_failure == "raise":
                raise sqlite3.OperationalError("private rollback detail")
            if self.rollback_failure == "residual":
                return super().execute("SELECT 1")
        return super().execute(sql, parameters)

    def commit(self) -> None:
        pytest.fail("Production must use explicit SQL COMMIT")

    def rollback(self) -> None:
        pytest.fail("Production must use explicit SQL ROLLBACK")


def observe_connection(monkeypatch: pytest.MonkeyPatch) -> list[TransactionConnection]:
    real_connect = sqlite3.connect
    connections: list[TransactionConnection] = []

    def connect(database: Path, **kwargs: Any) -> sqlite3.Connection:
        assert kwargs["autocommit"] is True
        connection = real_connect(database, factory=TransactionConnection, **kwargs)
        connections.append(connection)
        return connection

    monkeypatch.setattr(sqlite3, "connect", connect)
    return connections


@pytest.mark.parametrize("reject", [False, True])
def test_process_transaction_exposes_complete_outcome_and_checkpoint_together(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, reject: bool
) -> None:
    target = installation(tmp_path)
    real_connect = sqlite3.connect
    connections = observe_connection(monkeypatch)
    record = raw_record(
        offset=3, payload=b"not json" if reject else fake_candle_payload()
    )
    result = normalization(record)
    with NormalizationStore(target, producer="collector-a") as store:
        (db,) = connections
        statements: list[tuple[str, bool]] = []
        snapshots: list[tuple[int, int, int]] = []
        with real_connect(
            target.state_dir / "normalization.sqlite3", autocommit=True
        ) as reader:

            def trace(sql: str) -> None:
                statements.append((sql, db.in_transaction))
                snapshots.append(
                    tuple(
                        reader.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
                        for table in (
                            "candle_observations",
                            "processing_outcomes",
                            "processing_checkpoint",
                        )
                    )
                )

            db.set_trace_callback(trace)
            outcome = store.process(record, result, expected_predecessor=None)
            db.set_trace_callback(None)
            assert statements[0] == ("BEGIN IMMEDIATE", False)
            assert statements[-1] == ("COMMIT", True)
            assert all(active for _, active in statements[1:])
            assert not db.in_transaction
            assert all(snapshot == (0, 0, 0) for snapshot in snapshots)
            sql = [statement for statement, _ in statements]
            outcome_insert = next(
                i
                for i, statement in enumerate(sql)
                if statement.startswith("INSERT INTO processing_outcomes")
            )
            checkpoint_write = next(
                i
                for i, statement in enumerate(sql)
                if statement.startswith("INSERT INTO processing_checkpoint")
            )
            assert outcome_insert < checkpoint_write < len(sql) - 2
            assert any(
                statement.startswith("SELECT * FROM processing_outcomes")
                for statement in sql[checkpoint_write + 1 : -1]
            )
            assert any(
                statement.startswith("SELECT * FROM processing_checkpoint")
                for statement in sql[checkpoint_write + 1 : -1]
            )
            assert reader.execute(
                "SELECT count(*) FROM processing_outcomes"
            ).fetchone() == (1,)
            assert reader.execute(
                "SELECT producer, epoch, offset FROM processing_checkpoint"
            ).fetchone() == ("collector-a", "epoch-a", 3)
            assert outcome == store.outcome(record.identity)
            assert outcome.predecessor is None
            assert outcome.identity == record.identity
            assert outcome.raw_sha256 == record.envelope.content_sha256
            assert outcome.receipt == record.envelope.receipt
            assert store.checkpoint() == record.identity
            if isinstance(result, NormalizationRejection):
                assert outcome.kind is OutcomeKind.REJECTED
                assert outcome.semantic_revision is None
                assert outcome.rejection_code == "invalid_json"
                assert outcome.input_schema is None
                assert reader.execute(
                    "SELECT count(*) FROM candle_observations"
                ).fetchone() == (0,)
            else:
                assert outcome.kind is OutcomeKind.ACCEPTED
                assert store.observation(result.candle.revision) == result.semantics
                semantic_insert = next(
                    i
                    for i, statement in enumerate(sql)
                    if statement.startswith("INSERT INTO candle_observations")
                )
                assert semantic_insert < outcome_insert
                assert outcome.input_schema == result.input_schema
                assert outcome.instrument_schema == result.instrument_schema
                assert outcome.output_schema == result.candle.schema
                assert outcome.instrument_revision == result.instrument_revision
                assert outcome.normalizer_version == result.candle.normalizer_version
                assert outcome.normalized_at_ns == result.candle.normalized_at_ns


@pytest.mark.parametrize(
    "invalid", ["stale", "epoch", "producer", "same", "lower", "zero"]
)
def test_process_requires_exact_predecessor_and_increasing_producer_offsets(
    tmp_path: Path, invalid: str
) -> None:
    first = raw_record(offset=3)
    later = raw_record(offset=8, epoch="epoch-b")
    with NormalizationStore(installation(tmp_path), producer="collector-a") as store:
        store.process(first, normalization(first), expected_predecessor=None)
        expected: IngestionId | None = first.identity
        if invalid == "stale":
            expected = None
        elif invalid == "epoch":
            expected = replace(first.identity, epoch="other-epoch")
        elif invalid == "producer":
            later = replace(later, identity=replace(later.identity, producer="other"))
        else:
            later = replace(
                later,
                identity=replace(
                    later.identity, offset={"same": 3, "lower": 2, "zero": 0}[invalid]
                ),
            )
        with pytest.raises(NormalizationError):
            store.process(later, normalization(later), expected_predecessor=expected)
        assert store.checkpoint() == first.identity
        assert store.outcome(later.identity) is None


def test_process_offset_gaps_and_epoch_changes_link_exact_history(
    tmp_path: Path,
) -> None:
    target = installation(tmp_path)
    records = [
        raw_record(offset=3),
        raw_record(offset=8, epoch="epoch-b"),
        raw_record(offset=20, epoch="epoch-c", payload=b"invalid"),
    ]
    with NormalizationStore(target, producer="collector-a") as store:
        predecessor = None
        for record in records:
            outcome = store.process(
                record, normalization(record), expected_predecessor=predecessor
            )
            assert outcome.predecessor == predecessor
            predecessor = record.identity
    with NormalizationStore(target, producer="collector-a") as store:
        assert store.checkpoint() == records[-1].identity
        for record in records:
            assert store.outcome(record.identity) is not None


def test_process_retains_every_classification_and_semantic_revision(
    tmp_path: Path,
) -> None:
    target = installation(tmp_path)
    vectors = [
        ("100.1", False, OutcomeKind.ACCEPTED),
        ("100.1", False, OutcomeKind.DUPLICATE),
        ("100.2", False, OutcomeKind.OPEN_REVISION),
        ("100.3", True, OutcomeKind.FINALIZATION),
        ("100.4", True, OutcomeKind.CONFLICT),
        ("100.5", False, OutcomeKind.CONFLICT),
        ("100.4", True, OutcomeKind.DUPLICATE),
    ]
    with NormalizationStore(target, producer="collector-a") as store:
        predecessor = None
        prior_outcomes = []
        prior_observations = {}
        for offset, (close, finalized, expected) in enumerate(vectors, start=1):
            record = raw_record(
                offset=offset,
                payload=fake_candle_payload(close=close, finalized=finalized),
            )
            result = normalization(record)
            assert isinstance(result, CandleNormalization)
            outcome = store.process(record, result, expected_predecessor=predecessor)
            assert outcome.kind is expected
            assert outcome.semantic_revision == result.candle.revision
            assert outcome.predecessor == predecessor
            prior_outcomes.append(outcome)
            prior_observations[result.candle.revision] = result.semantics
            for previous in prior_outcomes:
                assert store.outcome(previous.identity) == previous
            for revision, semantics in prior_observations.items():
                assert store.observation(revision) == semantics
            predecessor = record.identity
    with sqlite3.connect(
        target.state_dir / "normalization.sqlite3", autocommit=True
    ) as db:
        assert db.execute("SELECT count(*) FROM processing_outcomes").fetchone() == (7,)
        assert db.execute("SELECT count(*) FROM candle_observations").fetchone() == (5,)


def test_process_accepts_first_final_and_classifies_each_logical_key_independently(
    tmp_path: Path,
) -> None:
    with NormalizationStore(installation(tmp_path), producer="collector-a") as store:
        first = raw_record(payload=fake_candle_payload(finalized=True))
        second = raw_record(
            offset=2, payload=fake_candle_payload(start_ns=1_700_000_060_000_000_000)
        )
        assert (
            store.process(first, normalization(first), expected_predecessor=None).kind
            is OutcomeKind.ACCEPTED
        )
        assert (
            store.process(
                second, normalization(second), expected_predecessor=first.identity
            ).kind
            is OutcomeKind.ACCEPTED
        )


def test_process_provenance_only_changes_are_auditable_duplicates(
    tmp_path: Path,
) -> None:
    first = raw_record()
    later = raw_record(
        offset=10,
        epoch="epoch-b",
        payload=fake_candle_payload(close="100.7500"),
        receipt=ClockSample(
            99, 55, "session-b", TimeQuality("clock-b", "healthy", -2, 3, 4)
        ),
    )
    first_result = normalization(first)
    result = normalization(later)
    assert isinstance(first_result, CandleNormalization)
    assert isinstance(result, CandleNormalization)
    result = replace(
        result,
        input_schema=SchemaRef("fake_candle", Version(1, 1)),
        instrument_schema=SchemaRef("instrument", Version(1, 2)),
        instrument_revision="instrument-r2",
        candle=replace(
            result.candle, normalizer_version="f06.fake_candle.v2", normalized_at_ns=999
        ),
    )
    with NormalizationStore(installation(tmp_path), producer="collector-a") as store:
        original = store.process(first, first_result, expected_predecessor=None)
        duplicate = store.process(later, result, expected_predecessor=first.identity)
        assert duplicate.kind is OutcomeKind.DUPLICATE
        assert duplicate.semantic_revision == original.semantic_revision
        assert duplicate.raw_sha256 != original.raw_sha256
        assert duplicate.receipt == later.envelope.receipt
        assert duplicate.input_schema == SchemaRef("fake_candle", Version(1, 1))
        assert duplicate.instrument_schema == SchemaRef("instrument", Version(1, 2))
        assert duplicate.output_schema == SchemaRef("candle", Version(1, 0))
        assert duplicate.instrument_revision == "instrument-r2"
        assert duplicate.normalizer_version == "f06.fake_candle.v2"
        assert duplicate.normalized_at_ns == 999
        assert store.outcome(first.identity) == original


@pytest.mark.parametrize(
    "change",
    [
        "venue='other'",
        "category='future'",
        "symbol='ETH-USDT'",
        "start_ns=start_ns+1",
        "interval_ns=interval_ns+1",
        "open='100.2'",
        "high='102'",
        "low='99'",
        "close='100.8'",
        "volume='13'",
        "volume_unit='USDT'",
        "finalized=1",
        "source_time_value=source_time_value+1",
        "source_time_unit='us'",
        "publication_time_value=9",
        "publication_time_unit='ms'",
        "quality_flags='[\"changed\"]'",
    ],
)
def test_process_rejects_stored_semantic_revision_value_mismatch_after_reopen(
    tmp_path: Path, change: str
) -> None:
    target = installation(tmp_path)
    payload = fake_candle_payload(publication_time={"value": 8, "unit": "ns"})
    first = raw_record(payload=payload)
    later = raw_record(offset=3, payload=payload)
    with NormalizationStore(target, producer="collector-a") as store:
        store.process(first, normalization(first), expected_predecessor=None)
    with sqlite3.connect(
        target.state_dir / "normalization.sqlite3", autocommit=True
    ) as db:
        db.execute(f"UPDATE candle_observations SET {change}")
    with NormalizationStore(target, producer="collector-a") as store:
        with pytest.raises(
            NormalizationError,
            match="^Stored semantic revision does not match computed values$",
        ):
            store.process(
                later, normalization(later), expected_predecessor=first.identity
            )
        assert store.checkpoint() == first.identity
        assert store.outcome(later.identity) is None


@pytest.mark.parametrize(
    "failure",
    [
        "BEGIN IMMEDIATE",
        "INSERT INTO candle_observations",
        "INSERT INTO processing_outcomes",
        "INSERT INTO processing_checkpoint",
        "UPDATE processing_checkpoint",
        "COMMIT",
    ],
)
def test_process_write_failure_rolls_back_and_permits_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    target = installation(tmp_path)
    real_connect = sqlite3.connect
    connections = observe_connection(monkeypatch)
    record = raw_record(offset=5)
    result = normalization(record)
    assert isinstance(result, CandleNormalization)
    with NormalizationStore(target, producer="collector-a") as store:
        (db,) = connections
        predecessor = None
        if failure == "UPDATE processing_checkpoint":
            first = raw_record(payload=b"invalid")
            store.process(first, normalization(first), expected_predecessor=None)
            predecessor = first.identity
        statements: list[str] = []
        db.set_trace_callback(statements.append)
        db.failure = failure
        with pytest.raises(NormalizationError) as error:
            store.process(record, result, expected_predecessor=predecessor)
        assert "private" not in str(error.value)
        assert not db.in_transaction
        assert ("ROLLBACK" in statements) is (failure != "BEGIN IMMEDIATE")
        assert store.checkpoint() == predecessor
        assert store.outcome(record.identity) is None
        assert store.observation(result.candle.revision) is None
        with real_connect(
            target.state_dir / "normalization.sqlite3", autocommit=True
        ) as reader:
            assert reader.execute(
                "SELECT count(*) FROM processing_outcomes"
            ).fetchone() == (int(predecessor is not None),)
        assert (
            store.process(record, result, expected_predecessor=predecessor).kind
            is OutcomeKind.ACCEPTED
        )
        assert store.checkpoint() == record.identity


@pytest.mark.parametrize("rollback_failure", ["raise", "residual"])
def test_process_failed_rollback_closes_connection_and_permanently_fails_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, rollback_failure: str
) -> None:
    target = installation(tmp_path)
    connections = observe_connection(monkeypatch)
    record = raw_record()
    result = normalization(record)
    assert isinstance(result, CandleNormalization)
    store = NormalizationStore(target, producer="collector-a")
    (db,) = connections
    db.failure = "INSERT INTO processing_outcomes"
    db.rollback_failure = rollback_failure
    with pytest.raises(
        NormalizationError, match="^Durable normalization store failed$"
    ):
        store.process(record, result, expected_predecessor=None)
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        db.execute("SELECT 1")
    for call in (
        store.checkpoint,
        store.barrier,
        store.status,
        store.__enter__,
        lambda: store.outcome(record.identity),
        lambda: store.observation(result.candle.revision),
        lambda: store.process(record, result, expected_predecessor=None),
    ):
        with pytest.raises(
            NormalizationError, match="^Durable normalization store failed$"
        ):
            call()
    store.close()
    with NormalizationStore(target, producer="collector-a") as replacement:
        assert replacement.checkpoint() is None
        assert replacement.outcome(record.identity) is None


def test_process_engine_rollback_does_not_issue_redundant_rollback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    connections = observe_connection(monkeypatch)
    record = raw_record()
    result = normalization(record)
    assert isinstance(result, CandleNormalization)
    with NormalizationStore(installation(tmp_path), producer="collector-a") as store:
        (db,) = connections
        db.execute(
            "CREATE TEMP TRIGGER fail_outcome BEFORE INSERT ON processing_outcomes "
            "BEGIN SELECT RAISE(ROLLBACK, 'private engine detail'); END"
        )
        statements: list[str] = []
        db.set_trace_callback(statements.append)
        with pytest.raises(NormalizationError) as error:
            store.process(record, result, expected_predecessor=None)
        assert "private" not in str(error.value)
        assert not db.in_transaction
        assert "ROLLBACK" not in statements
        assert store.checkpoint() is None
        assert store.outcome(record.identity) is None
        assert store.observation(result.candle.revision) is None
        db.execute("DROP TRIGGER fail_outcome")
        assert (
            store.process(record, result, expected_predecessor=None).kind
            is OutcomeKind.ACCEPTED
        )


@pytest.mark.parametrize(
    "ignored_statement",
    ["INSERT INTO processing_checkpoint", "UPDATE processing_checkpoint"],
)
def test_process_verifies_checkpoint_write_before_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, ignored_statement: str
) -> None:
    connections = observe_connection(monkeypatch)
    record = raw_record(offset=5)
    result = normalization(record)
    assert isinstance(result, CandleNormalization)
    with NormalizationStore(installation(tmp_path), producer="collector-a") as store:
        (db,) = connections
        predecessor = None
        if ignored_statement.startswith("UPDATE"):
            first = raw_record(payload=b"invalid")
            store.process(first, normalization(first), expected_predecessor=None)
            predecessor = first.identity
        db.ignored_statement = ignored_statement
        with pytest.raises(NormalizationError):
            store.process(record, result, expected_predecessor=predecessor)
        assert store.checkpoint() == predecessor
        assert store.outcome(record.identity) is None
        assert store.observation(result.candle.revision) is None


@pytest.mark.parametrize("mismatch", [None, "identity", "hash", "predecessor"])
def test_process_deletes_only_the_matching_barrier_atomically(
    tmp_path: Path, mismatch: str | None
) -> None:
    target = installation(tmp_path)
    first = raw_record()
    record = raw_record(offset=5)
    with NormalizationStore(target, producer="collector-a") as store:
        store.process(first, normalization(first), expected_predecessor=None)
        with sqlite3.connect(
            target.state_dir / "normalization.sqlite3", autocommit=True
        ) as db:
            db.execute(
                "INSERT INTO processing_barrier VALUES "
                "(1, 'collector-a', 'epoch-a', ?, ?, ?, ?, ?, "
                "'metadata_unavailable', 'fake_candle', 1, 0, 'fake-venue', 'spot', 'BTC-USDT')",
                (
                    7 if mismatch == "identity" else 5,
                    None if mismatch == "predecessor" else "collector-a",
                    None if mismatch == "predecessor" else "epoch-a",
                    None if mismatch == "predecessor" else 1,
                    "a" * 64 if mismatch == "hash" else record.envelope.content_sha256,
                ),
            )
        barrier = store.barrier()
        assert barrier is not None
        if mismatch is None:
            outcome = store.process(
                record, normalization(record), expected_predecessor=first.identity
            )
            assert outcome.kind is OutcomeKind.DUPLICATE
            assert store.barrier() is None
            assert store.checkpoint() == record.identity
        else:
            with pytest.raises(NormalizationError, match="barrier"):
                store.process(
                    record, normalization(record), expected_predecessor=first.identity
                )
            assert store.barrier() == barrier
            assert store.checkpoint() == first.identity
            assert store.outcome(record.identity) is None


def test_process_rejection_keeps_recoverable_schema_and_field_provenance(
    tmp_path: Path,
) -> None:
    record = raw_record(payload=fake_candle_payload(close="invalid"))
    result = normalization(record)
    assert isinstance(result, NormalizationRejection)
    result = replace(
        result,
        instrument_schema=SchemaRef("instrument", Version(1, 0)),
        instrument_revision="metadata-r2",
        output_schema=SchemaRef("candle", Version(1, 0)),
        normalized_at_ns=999,
    )
    with NormalizationStore(installation(tmp_path), producer="collector-a") as store:
        outcome = store.process(record, result, expected_predecessor=None)
        assert outcome.kind is OutcomeKind.REJECTED
        assert outcome.rejection_code == "invalid_decimal"
        assert outcome.rejection_field == "close"
        assert outcome.input_schema == SchemaRef("fake_candle", Version(1, 0))
        assert outcome.instrument_schema == SchemaRef("instrument", Version(1, 0))
        assert outcome.instrument_revision == "metadata-r2"
        assert outcome.output_schema == SchemaRef("candle", Version(1, 0))
        assert outcome.normalized_at_ns == 999


@pytest.mark.parametrize(
    "mismatch", ["accepted_identity", "receipt", "rejected_identity", "raw_hash"]
)
def test_process_rejects_mismatched_raw_provenance(
    tmp_path: Path, mismatch: str
) -> None:
    record = raw_record(
        payload=b"invalid"
        if mismatch in ("rejected_identity", "raw_hash")
        else fake_candle_payload()
    )
    result = normalization(record)
    if isinstance(result, CandleNormalization):
        candle = (
            replace(
                result.candle, raw_record=IngestionId("collector-a", "epoch-other", 1)
            )
            if mismatch == "accepted_identity"
            else replace(
                result.candle, receipt=replace(result.candle.receipt, monotonic_ns=99)
            )
        )
        result = replace(result, candle=candle)
    else:
        result = (
            replace(result, raw_record=IngestionId("collector-a", "epoch-other", 1))
            if mismatch == "rejected_identity"
            else replace(result, raw_sha256="a" * 64)
        )
    with NormalizationStore(installation(tmp_path), producer="collector-a") as store:
        with pytest.raises(NormalizationError, match="raw record"):
            store.process(record, result, expected_predecessor=None)
        assert store.checkpoint() is None
        assert store.outcome(record.identity) is None


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


def test_outcome_wraps_sqlite_fetch_errors_without_echoing_stored_values(
    tmp_path: Path,
) -> None:
    target = installation(tmp_path)
    with NormalizationStore(target, producer="collector-a"):
        pass
    path = target.state_dir / "normalization.sqlite3"
    seed_state(path)
    with NormalizationStore(target, producer="collector-a") as store:
        with sqlite3.connect(path, autocommit=True) as db:
            db.execute(
                "UPDATE processing_outcomes "
                "SET raw_sha256=CAST(X'7365637265742dff' AS TEXT) WHERE offset=2"
            )
            assert db.execute("PRAGMA quick_check(1)").fetchall() == [("ok",)]
            assert db.execute("PRAGMA foreign_key_check").fetchall() == []
        with pytest.raises(
            NormalizationError, match="^Unable to read normalization state$"
        ) as error:
            store.outcome(IngestionId("collector-a", "epoch-a", 2))
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
