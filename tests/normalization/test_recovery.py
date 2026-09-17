"""Startup rejects inconsistent progress; process death preserves atomic results."""

import sqlite3
import subprocess
import sys
from contextlib import closing
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from scryntic.domain.raw import IngestionId
from scryntic.normalization.candle import FAKE_CANDLE_SCHEMA, CandleNormalization
from scryntic.normalization.runner import Blocked, Processed, process_next
from scryntic.normalization.sqlite_store import (
    BarrierReason,
    NormalizationError,
    NormalizationStore,
    OutcomeKind,
)
from tests.normalization.helpers import (
    INSTRUMENT_ID,
    NORMALIZED_AT_NS,
    FixedRawReader,
    installation,
    instrument,
    normalization,
    raw_record,
    recovery_records,
)


@pytest.mark.parametrize(
    ("corruption", "statements", "message"),
    [
        (
            "outcomes_without_checkpoint",
            ("DELETE FROM processing_checkpoint",),
            "Invalid normalization processing chain",
        ),
        (
            "checkpoint_without_outcome",
            ("DELETE FROM processing_outcomes",),
            "Normalization database foreign key check failed",
        ),
        (
            "two_roots",
            (
                "UPDATE processing_outcomes SET predecessor_producer=NULL, "
                "predecessor_epoch=NULL, predecessor_offset=NULL WHERE offset=9",
            ),
            "Invalid normalization processing chain",
        ),
        (
            "missing_predecessor",
            (
                "UPDATE processing_outcomes SET predecessor_epoch='missing' "
                "WHERE offset=9",
            ),
            "Normalization database foreign key check failed",
        ),
        (
            "two_successors",
            (
                "DROP INDEX one_successor_per_outcome",
                "CREATE INDEX one_successor_per_outcome ON processing_outcomes "
                "(predecessor_producer, predecessor_epoch, predecessor_offset) "
                "WHERE predecessor_producer IS NOT NULL",
                "UPDATE processing_outcomes SET predecessor_epoch='epoch-a', "
                "predecessor_offset=2 WHERE offset=9",
                # Restore the exact declared schema while retaining the corrupt
                # duplicate index entries, which normal SQLite writes forbid.
                "PRAGMA writable_schema=ON",
                "UPDATE sqlite_schema SET sql=replace(sql, 'CREATE INDEX', "
                "'CREATE UNIQUE INDEX') WHERE name='one_successor_per_outcome'",
                "PRAGMA writable_schema=OFF",
            ),
            "Invalid normalization processing chain",
        ),
        (
            "nonincreasing_predecessor",
            (
                "UPDATE processing_outcomes SET predecessor_producer='collector-a', "
                "predecessor_epoch='epoch-c', predecessor_offset=9 WHERE offset=2",
            ),
            "Normalization database integrity check failed",
        ),
        (
            "checkpoint_not_tail",
            ("UPDATE processing_checkpoint SET epoch='epoch-b', offset=5",),
            "Invalid normalization processing chain",
        ),
        (
            "unreachable_outcome",
            (
                "UPDATE processing_outcomes SET predecessor_producer=NULL, "
                "predecessor_epoch=NULL, predecessor_offset=NULL WHERE offset=5",
            ),
            "Invalid normalization processing chain",
        ),
        (
            "barrier_predecessor_mismatch",
            (
                "UPDATE processing_barrier SET predecessor_epoch='epoch-b', "
                "predecessor_offset=5",
            ),
            "Invalid normalization barrier",
        ),
        (
            "barrier_null_predecessor_with_checkpoint",
            (
                "UPDATE processing_barrier SET predecessor_producer=NULL, "
                "predecessor_epoch=NULL, predecessor_offset=NULL",
            ),
            "Invalid normalization barrier",
        ),
        (
            "blocker_not_greater_than_predecessor",
            ("UPDATE processing_barrier SET offset=9",),
            "Normalization database integrity check failed",
        ),
    ],
    ids=lambda value: value if isinstance(value, str) and " " not in value else None,
)
def test_startup_rejects_corrupt_chain_without_repair(
    tmp_path: Path, corruption: str, statements: tuple[str, ...], message: str
) -> None:
    target = installation(tmp_path)
    first = raw_record(offset=2)
    rejected = raw_record(offset=5, epoch="epoch-b", payload=b"not json")
    last = raw_record(offset=9, epoch="epoch-c")
    blocker = raw_record(offset=12)
    with NormalizationStore(target, producer="collector-a") as store:
        predecessor = None
        for record in (first, rejected, last):
            store.process(
                record, normalization(record), expected_predecessor=predecessor
            )
            predecessor = record.identity
        if corruption.startswith(("barrier_", "blocker_")):
            store.block(
                blocker,
                expected_predecessor=last.identity,
                reason=BarrierReason.METADATA_UNAVAILABLE,
                schema=FAKE_CANDLE_SCHEMA,
                instrument=INSTRUMENT_ID,
            )
    database = target.state_dir / "normalization.sqlite3"
    with closing(sqlite3.connect(database, autocommit=True)) as db:
        db.execute("PRAGMA foreign_keys=OFF")
        db.execute("PRAGMA ignore_check_constraints=ON")
        for statement in statements:
            db.execute(statement)
        before = tuple(db.iterdump())

    # A second attempt also fails: startup neither repairs nor fabricates progress.
    for _ in range(2):
        with pytest.raises(NormalizationError, match=f"^{message}$"):
            with NormalizationStore(target, producer="collector-a"):
                pytest.fail(f"Accepted corruption: {corruption}")
    with closing(sqlite3.connect(database, autocommit=True)) as db:
        assert tuple(db.iterdump()) == before


def test_startup_accepts_gaps_epoch_changes_rejections_and_barrier(
    tmp_path: Path,
) -> None:
    target = installation(tmp_path)
    records = (
        raw_record(offset=2),
        raw_record(offset=5, epoch="epoch-b", payload=b"not json"),
        raw_record(offset=9, epoch="epoch-c"),
    )
    with NormalizationStore(target, producer="collector-a") as store:
        predecessor = None
        outcomes = []
        for record in records:
            outcomes.append(
                store.process(
                    record, normalization(record), expected_predecessor=predecessor
                )
            )
            predecessor = record.identity
        barrier = store.block(
            raw_record(offset=12),
            expected_predecessor=predecessor,
            reason=BarrierReason.METADATA_UNAVAILABLE,
            schema=FAKE_CANDLE_SCHEMA,
            instrument=INSTRUMENT_ID,
        )
    with NormalizationStore(target, producer="collector-a") as store:
        assert store.checkpoint() == IngestionId("collector-a", "epoch-c", 9)
        assert [store.outcome(record.identity) for record in records] == outcomes
        assert [outcome.kind for outcome in outcomes] == [
            OutcomeKind.ACCEPTED,
            OutcomeKind.REJECTED,
            OutcomeKind.DUPLICATE,
        ]
        assert [outcome.predecessor for outcome in outcomes] == [
            None,
            IngestionId("collector-a", "epoch-a", 2),
            IngestionId("collector-a", "epoch-b", 5),
        ]
        assert store.barrier() == barrier


def test_observation_rows_do_not_fabricate_processing_progress(tmp_path: Path) -> None:
    target = installation(tmp_path)
    record = raw_record(offset=2)
    result = normalization(record)
    assert isinstance(result, CandleNormalization)
    with NormalizationStore(target, producer="collector-a") as store:
        store.process(record, result, expected_predecessor=None)
    with closing(
        sqlite3.connect(target.state_dir / "normalization.sqlite3", autocommit=True)
    ) as db:
        db.execute("PRAGMA foreign_keys=OFF")
        db.execute("DELETE FROM processing_checkpoint")
        db.execute("DELETE FROM processing_outcomes")
    with NormalizationStore(target, producer="collector-a") as store:
        assert store.checkpoint() is None
        assert store.outcome(record.identity) is None
        assert store.observation(result.candle.revision) == result.semantics
        outcome = store.process(record, result, expected_predecessor=None)
        assert outcome.kind is OutcomeKind.DUPLICATE
        assert outcome.semantic_revision == result.candle.revision
        assert _counts(target.state_dir) == (1, 1, 1, 0)


def _counts(state_dir: Path) -> tuple[int, ...]:
    with closing(sqlite3.connect(state_dir / "normalization.sqlite3")) as db:
        return tuple(
            db.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            for table in (
                "candle_observations",
                "processing_outcomes",
                "processing_checkpoint",
                "processing_barrier",
            )
        )


def _crash(root: Path, stage: str) -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "tests.normalization.crash_normalizer",
            str(root),
            stage,
        ],
        cwd=Path(__file__).resolve().parents[2],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert completed.returncode == 86, (
        completed.returncode,
        completed.stdout,
        completed.stderr,
    )
    assert completed.stdout == ""
    assert completed.stderr == ""


OUTCOME_STAGES = (
    "before_begin",
    "after_observation_insert",
    "after_outcome_insert",
    "after_checkpoint_write",
    "after_barrier_delete",
    "before_outcome_commit",
    "after_outcome_commit",
)


@pytest.mark.parametrize(
    ("pending", "stage"),
    [
        (pending, stage)
        for pending in range(3)
        for stage in OUTCOME_STAGES
        if pending == 0
        or stage not in ("after_observation_insert", "after_barrier_delete")
    ],
)
def test_process_kill_recovers_atomic_outcome_and_replays_without_semantic_duplication(
    tmp_path: Path, pending: int, stage: str
) -> None:
    target = installation(tmp_path)
    records = recovery_records()
    reader = FixedRawReader(records)
    metadata = {INSTRUMENT_ID: instrument()}
    with NormalizationStore(target, producer="collector-a") as store:
        for _ in range(pending):
            assert isinstance(
                process_next(
                    reader, store, metadata, normalized_at_ns=NORMALIZED_AT_NS
                ),
                Processed,
            )
        checkpoint = store.checkpoint()
        # The first accepted transaction must clear an already durable barrier.
        barrier = None
        if pending == 0:
            blocked = process_next(reader, store, {}, normalized_at_ns=NORMALIZED_AT_NS)
            assert isinstance(blocked, Blocked)
            barrier = blocked.barrier
    before = _counts(target.state_dir)
    _crash(tmp_path, stage)
    committed = stage == "after_outcome_commit"
    with NormalizationStore(target, producer="collector-a") as store:
        assert store.checkpoint() == (
            records[pending].identity if committed else checkpoint
        )
        outcome = store.outcome(records[pending].identity)
        if committed:
            assert outcome is not None
            assert outcome.predecessor == checkpoint
            assert (
                outcome.kind
                is (OutcomeKind.ACCEPTED, OutcomeKind.REJECTED, OutcomeKind.DUPLICATE)[
                    pending
                ]
            )
            assert store.barrier() is None
            assert _counts(target.state_dir) == (1, pending + 1, 1, 0)
        else:
            assert outcome is None
            assert store.barrier() == barrier
            assert _counts(target.state_dir) == before

        # The first pull after restart retries only the uncommitted record, or
        # immediately advances beyond the durable commit; then finish the replay.
        start = pending + int(committed)
        for index in range(start, 3):
            processed = process_next(
                reader, store, metadata, normalized_at_ns=NORMALIZED_AT_NS
            )
            assert isinstance(processed, Processed)
            assert processed.outcome.identity == records[index].identity
            assert (
                processed.outcome.kind
                is (OutcomeKind.ACCEPTED, OutcomeKind.REJECTED, OutcomeKind.DUPLICATE)[
                    index
                ]
            )
            assert (
                processed.outcome.raw_sha256 == records[index].envelope.content_sha256
            )
            assert processed.outcome.receipt == records[index].envelope.receipt
        blocked = process_next(
            reader, store, metadata, normalized_at_ns=NORMALIZED_AT_NS
        )
        assert isinstance(blocked, Blocked)
        assert blocked.barrier.blocker == records[3].identity
        assert blocked.barrier.reason is BarrierReason.UNSUPPORTED_SCHEMA
        first = store.outcome(records[0].identity)
        duplicate = store.outcome(records[2].identity)
        assert first is not None and duplicate is not None
        assert first.semantic_revision == duplicate.semantic_revision
        assert first.identity != duplicate.identity
        assert store.checkpoint() == records[2].identity
        assert _counts(target.state_dir) == (1, 3, 1, 1)


@pytest.mark.parametrize("unsupported", [False, True])
@pytest.mark.parametrize(
    "stage", ["after_barrier_insert", "before_barrier_commit", "after_barrier_commit"]
)
def test_barrier_process_kill_rediscovers_or_preserves_next_blocker(
    tmp_path: Path, unsupported: bool, stage: str
) -> None:
    target = installation(tmp_path)
    records = recovery_records()
    reader = FixedRawReader(records)
    metadata = {INSTRUMENT_ID: instrument()}
    pending = 3 if unsupported else 0
    with NormalizationStore(target, producer="collector-a") as store:
        for _ in range(pending):
            assert isinstance(
                process_next(
                    reader, store, metadata, normalized_at_ns=NORMALIZED_AT_NS
                ),
                Processed,
            )
        checkpoint = store.checkpoint()
    before = _counts(target.state_dir)
    _crash(tmp_path, stage)
    with NormalizationStore(target, producer="collector-a") as store:
        committed_barrier = store.barrier()
        assert (committed_barrier is not None) is (stage == "after_barrier_commit")
        assert store.checkpoint() == checkpoint
        assert store.outcome(records[pending].identity) is None
        assert _counts(target.state_dir) == (
            *before[:3],
            int(stage == "after_barrier_commit"),
        )
        blocked = process_next(reader, store, {}, normalized_at_ns=NORMALIZED_AT_NS)
        assert isinstance(blocked, Blocked)
        assert blocked.barrier.blocker == records[pending].identity
        assert blocked.barrier.predecessor == checkpoint
        assert blocked.barrier.raw_sha256 == records[pending].envelope.content_sha256
        assert blocked.barrier.reason is (
            BarrierReason.UNSUPPORTED_SCHEMA
            if unsupported
            else BarrierReason.METADATA_UNAVAILABLE
        )
        if committed_barrier is not None:
            assert blocked.barrier == committed_barrier
        assert (
            process_next(reader, store, {}, normalized_at_ns=NORMALIZED_AT_NS)
            == blocked
        )
        if not unsupported:
            processed = process_next(
                reader, store, metadata, normalized_at_ns=NORMALIZED_AT_NS
            )
            assert isinstance(processed, Processed)
            assert processed.outcome.identity == records[0].identity
            assert store.barrier() is None


class ReplayConnection(sqlite3.Connection):
    fail_commit = False

    def execute(self, sql: str, parameters: Any = (), /) -> sqlite3.Cursor:
        if self.fail_commit and sql == "COMMIT":
            self.fail_commit = False
            raise sqlite3.OperationalError("injected private commit detail")
        return super().execute(sql, parameters)


@pytest.mark.parametrize("failed_attempt", [False, True])
def test_equal_inputs_replay_identical_candles_semantics_and_outcome_provenance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failed_attempt: bool
) -> None:
    real_connect = sqlite3.connect
    connections: list[ReplayConnection] = []

    def connect(database: Path, **kwargs: Any) -> sqlite3.Connection:
        connection = real_connect(database, factory=ReplayConnection, **kwargs)
        connections.append(connection)
        return connection

    monkeypatch.setattr(sqlite3, "connect", connect)
    replays = []
    for attempt in range(2):
        target = installation(tmp_path / str(attempt))
        replay = []
        with NormalizationStore(target, producer="collector-a") as store:
            connection = connections[-1]
            predecessor = None
            for index, record in enumerate(recovery_records()[:3]):
                result = normalization(record)
                if failed_attempt and attempt == 1 and index == 0:
                    connection.fail_commit = True
                    with pytest.raises(
                        NormalizationError,
                        match="^Unable to persist normalization outcome$",
                    ):
                        store.process(record, result, expected_predecessor=None)
                    assert store.checkpoint() is None
                    assert store.outcome(record.identity) is None
                    assert _counts(target.state_dir) == (0, 0, 0, 0)
                outcome = store.process(
                    record, result, expected_predecessor=predecessor
                )
                assert store.outcome(record.identity) == outcome
                semantics = (
                    store.observation(result.candle.revision)
                    if isinstance(result, CandleNormalization)
                    else None
                )
                replay.append((result, outcome, semantics))
                predecessor = record.identity
            assert _counts(target.state_dir) == (1, 3, 1, 0)
        replays.append(replay)
    assert replays[0] == replays[1]
    assert [entry[1].kind for entry in replays[1]] == [
        OutcomeKind.ACCEPTED,
        OutcomeKind.REJECTED,
        OutcomeKind.DUPLICATE,
    ]


def test_normalization_time_changes_only_replay_provenance(tmp_path: Path) -> None:
    record = raw_record(offset=2)
    first = normalization(record)
    later = normalization(record, normalized_at_ns=NORMALIZED_AT_NS + 1)
    assert isinstance(first, CandleNormalization) and isinstance(
        later, CandleNormalization
    )
    outcomes = []
    for name, result in (("first", first), ("later", later)):
        with NormalizationStore(
            installation(tmp_path / name), producer="collector-a"
        ) as store:
            outcomes.append(store.process(record, result, expected_predecessor=None))
            assert store.observation(result.candle.revision) == result.semantics
    assert first.semantics == later.semantics
    assert first.candle.revision == later.candle.revision
    assert first.candle != later.candle
    assert replace(first.candle, normalized_at_ns=NORMALIZED_AT_NS + 1) == later.candle
    assert outcomes[0] != outcomes[1]
    assert replace(outcomes[0], normalized_at_ns=NORMALIZED_AT_NS + 1) == outcomes[1]
