"""Ordered pulls, staged metadata lookup and durable retryable barriers."""

import sqlite3
from collections.abc import Iterator, Mapping
from contextlib import closing
from dataclasses import replace
from pathlib import Path
from typing import Any, cast

import pytest

from scryntic.domain.identity import EntityId, InstrumentId, SchemaRef, Version
from scryntic.domain.market import Instrument
from scryntic.domain.raw import RawRecord
from scryntic.ingestion.sqlite_spool import DurableIngestor
from scryntic.normalization import runner
from scryntic.normalization.candle import (
    FAKE_CANDLE_SCHEMA,
    CandleNormalization,
    ParsedFakeCandle,
    RejectionCode,
    inspect_fake_candle,
)
from scryntic.normalization.runner import Blocked, NoWork, Processed, process_next
from scryntic.normalization.sqlite_store import (
    BarrierReason,
    NormalizationError,
    NormalizationStore,
    OutcomeKind,
)
from tests.normalization.helpers import (
    INSTRUMENT_ID,
    envelope,
    fake_candle_payload,
    installation,
    instrument,
    normalization,
    raw_record,
)

NORMALIZED_AT_NS = 1_700_000_002_000_000_000
UNKNOWN_SCHEMA = SchemaRef("future_candle", Version(2, 0))


class RecordingReader:
    def __init__(self, records: tuple[RawRecord, ...] = ()) -> None:
        self.records = records
        self.calls: list[tuple[int, int]] = []

    def records_after(self, offset: int, *, limit: int) -> tuple[RawRecord, ...]:
        self.calls.append((offset, limit))
        return tuple(
            record for record in self.records if record.identity.offset > offset
        )[:limit]


class RecordingInstruments(Mapping[InstrumentId, Instrument]):
    def __init__(self, values: dict[InstrumentId, Instrument] | None = None) -> None:
        self.entries = {} if values is None else values
        self.lookups: list[InstrumentId] = []

    def __getitem__(self, key: InstrumentId) -> Instrument:
        self.lookups.append(key)
        return self.entries[key]

    def __iter__(self) -> Iterator[InstrumentId]:
        return iter(self.entries)

    def __len__(self) -> int:
        return len(self.entries)


class FaultConnection(sqlite3.Connection):
    failure: str | None = None
    ignored: str | None = None

    def execute(self, sql: str, parameters: Any = (), /) -> sqlite3.Cursor:
        if self.failure is not None and sql.startswith(self.failure):
            self.failure = None
            raise sqlite3.OperationalError("private database detail")
        if self.ignored is not None and sql.startswith(self.ignored):
            self.ignored = None
            return super().execute("SELECT 1")
        return super().execute(sql, parameters)


def fault_connections(monkeypatch: pytest.MonkeyPatch) -> list[FaultConnection]:
    real_connect = sqlite3.connect
    connections: list[FaultConnection] = []

    def connect(database: Path, **kwargs: Any) -> sqlite3.Connection:
        connection = real_connect(database, factory=FaultConnection, **kwargs)
        connections.append(connection)
        return connection

    monkeypatch.setattr(sqlite3, "connect", connect)
    return connections


def counts(state_dir: Path) -> tuple[int, ...]:
    with closing(sqlite3.connect(state_dir / "normalization.sqlite3")) as connection:
        return tuple(
            connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            for table in (
                "candle_observations",
                "processing_outcomes",
                "processing_checkpoint",
                "processing_barrier",
            )
        )


def test_offset_gaps_follow_exact_existing_records_and_predecessors(
    tmp_path: Path,
) -> None:
    records = tuple(raw_record(offset=offset) for offset in (2, 5, 9))
    reader = RecordingReader(records)
    with NormalizationStore(installation(tmp_path), producer="collector-a") as store:
        predecessor = None
        for record in records:
            result = process_next(
                reader,
                store,
                {INSTRUMENT_ID: instrument()},
                normalized_at_ns=NORMALIZED_AT_NS,
            )
            assert isinstance(result, Processed)
            assert result.outcome.identity == record.identity
            assert result.outcome.predecessor == predecessor
            assert result.outcome.normalized_at_ns == NORMALIZED_AT_NS
            assert store.checkpoint() == record.identity
            predecessor = record.identity
        assert reader.calls == [(0, 1), (2, 1), (5, 1)]


@pytest.mark.parametrize("have_checkpoint", [False, True])
def test_no_work_preserves_progress(tmp_path: Path, have_checkpoint: bool) -> None:
    target = installation(tmp_path)
    reader = RecordingReader()
    with NormalizationStore(target, producer="collector-a") as store:
        first = raw_record(offset=2)
        if have_checkpoint:
            store.process(first, normalization(first), expected_predecessor=None)
        checkpoint = store.checkpoint()
        before = counts(target.state_dir)
        result = process_next(reader, store, {}, normalized_at_ns=NORMALIZED_AT_NS)
        assert result == NoWork(checkpoint)
        assert reader.calls == [(2 if have_checkpoint else 0, 1)]
        assert counts(target.state_dir) == before


@pytest.mark.parametrize("invalid", ["multiple", "producer", "equal", "lower", "error"])
def test_reader_failure_never_mutates_progress(tmp_path: Path, invalid: str) -> None:
    first = raw_record(offset=5)
    later = raw_record(offset=9)

    class BadReader(RecordingReader):
        def records_after(self, offset: int, *, limit: int) -> tuple[RawRecord, ...]:
            self.calls.append((offset, limit))
            if invalid == "error":
                raise RuntimeError("private reader detail")
            if invalid == "multiple":
                return later, raw_record(offset=10)
            if invalid == "producer":
                return (
                    replace(later, identity=replace(later.identity, producer="other")),
                )
            return (raw_record(offset=5 if invalid == "equal" else 2),)

    reader = BadReader()
    target = installation(tmp_path)
    with NormalizationStore(target, producer="collector-a") as store:
        store.process(first, normalization(first), expected_predecessor=None)
        before = counts(target.state_dir)
        with pytest.raises(NormalizationError) as error:
            process_next(reader, store, {}, normalized_at_ns=NORMALIZED_AT_NS)
        assert "private" not in str(error.value)
        assert reader.calls == [(5, 1)]
        assert store.checkpoint() == first.identity
        assert counts(target.state_dir) == before


@pytest.mark.parametrize("invalid", ["json", "decimal", "subject"])
def test_malformed_evidence_is_rejected_before_metadata(
    tmp_path: Path, invalid: str
) -> None:
    record = raw_record(
        payload=b"invalid"
        if invalid == "json"
        else fake_candle_payload(close="oops" if invalid == "decimal" else "100.75"),
        subject=EntityId("fake", "news", "story")
        if invalid == "subject"
        else INSTRUMENT_ID,
    )
    metadata = RecordingInstruments()
    with NormalizationStore(installation(tmp_path), producer="collector-a") as store:
        result = process_next(
            RecordingReader((record,)),
            store,
            metadata,
            normalized_at_ns=NORMALIZED_AT_NS,
        )
        assert isinstance(result, Processed)
        assert result.outcome.kind is OutcomeKind.REJECTED
        assert (
            result.outcome.rejection_code
            is {
                "json": RejectionCode.INVALID_JSON,
                "decimal": RejectionCode.INVALID_DECIMAL,
                "subject": RejectionCode.INVALID_SUBJECT,
            }[invalid]
        )
        assert store.checkpoint() == record.identity
        assert store.barrier() is None
        assert metadata.lookups == []


@pytest.mark.parametrize("unsupported", [False, True])
def test_missing_prerequisite_blocks_without_progress(
    tmp_path: Path, unsupported: bool
) -> None:
    record = raw_record(
        payload=fake_candle_payload(
            schema=("future_candle", 2, 0) if unsupported else ("fake_candle", 1, 0)
        )
    )
    metadata = RecordingInstruments()
    target = installation(tmp_path)
    reader = RecordingReader((record, raw_record(offset=2)))
    with NormalizationStore(target, producer="collector-a") as store:
        result = process_next(
            reader, store, metadata, normalized_at_ns=NORMALIZED_AT_NS
        )
        assert isinstance(result, Blocked)
        assert result.barrier == store.barrier()
        assert result.barrier.blocker == record.identity
        assert result.barrier.predecessor is None
        assert result.barrier.raw_sha256 == record.envelope.content_sha256
        assert result.barrier.reason is (
            BarrierReason.UNSUPPORTED_SCHEMA
            if unsupported
            else BarrierReason.METADATA_UNAVAILABLE
        )
        assert result.barrier.schema == (
            UNKNOWN_SCHEMA if unsupported else FAKE_CANDLE_SCHEMA
        )
        assert result.barrier.instrument == (None if unsupported else INSTRUMENT_ID)
        assert store.checkpoint() is None
        assert store.outcome(record.identity) is None
        assert counts(target.state_dir) == (0, 0, 0, 1)
        assert metadata.lookups == ([] if unsupported else [INSTRUMENT_ID])
        assert reader.calls == [(0, 1)]


def test_valid_supported_candle_looks_up_metadata_once(tmp_path: Path) -> None:
    record = raw_record()
    metadata = RecordingInstruments({INSTRUMENT_ID: instrument()})
    with NormalizationStore(installation(tmp_path), producer="collector-a") as store:
        result = process_next(
            RecordingReader((record,)),
            store,
            metadata,
            normalized_at_ns=NORMALIZED_AT_NS,
        )
        assert isinstance(result, Processed)
        assert result.outcome.kind is OutcomeKind.ACCEPTED
        assert metadata.lookups == [INSTRUMENT_ID]


def test_mismatched_metadata_identity_is_fatal_without_progress(tmp_path: Path) -> None:
    metadata = RecordingInstruments(
        {
            INSTRUMENT_ID: instrument(
                identity=InstrumentId("fake-venue", "spot", "ETH-USDT")
            )
        }
    )
    target = installation(tmp_path)
    with NormalizationStore(target, producer="collector-a") as store:
        with pytest.raises(NormalizationError):
            process_next(
                RecordingReader((raw_record(),)),
                store,
                metadata,
                normalized_at_ns=NORMALIZED_AT_NS,
            )
        assert metadata.lookups == [INSTRUMENT_ID]
        assert counts(target.state_dir) == (0, 0, 0, 0)


@pytest.mark.parametrize("timestamp", [True, 1.5, 2**63, -(2**63) - 1])
@pytest.mark.parametrize(
    "payload",
    [
        b"invalid",
        fake_candle_payload(),
        fake_candle_payload(schema=("future_candle", 2, 0)),
    ],
)
def test_invalid_normalization_time_cannot_write_any_result(
    tmp_path: Path, timestamp: object, payload: bytes
) -> None:
    target = installation(tmp_path)
    with NormalizationStore(target, producer="collector-a") as store:
        with pytest.raises((TypeError, ValueError, NormalizationError)):
            process_next(
                RecordingReader((raw_record(payload=payload),)),
                store,
                {},
                normalized_at_ns=cast(int, timestamp),
            )
        assert counts(target.state_dir) == (0, 0, 0, 0)


@pytest.mark.parametrize("unsupported", [False, True])
def test_restart_retries_only_same_blocker_then_processes_after_prerequisite_arrives(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, unsupported: bool
) -> None:
    first = raw_record(offset=2)
    blocker = raw_record(
        offset=5,
        payload=fake_candle_payload(
            schema=("future_candle", 2, 0) if unsupported else ("fake_candle", 1, 0)
        ),
    )
    reader = RecordingReader((first, blocker, raw_record(offset=9)))
    metadata = RecordingInstruments()
    target = installation(tmp_path)
    with NormalizationStore(target, producer="collector-a") as store:
        store.process(first, normalization(first), expected_predecessor=None)
        blocked = process_next(
            reader, store, metadata, normalized_at_ns=NORMALIZED_AT_NS
        )
        assert isinstance(blocked, Blocked)
    with NormalizationStore(target, producer="collector-a") as store:
        retried = process_next(
            reader, store, metadata, normalized_at_ns=NORMALIZED_AT_NS
        )
        assert retried == blocked
        assert store.checkpoint() == first.identity
        assert counts(target.state_dir) == (1, 1, 1, 1)
        metadata.entries[INSTRUMENT_ID] = instrument()
        if unsupported:
            # Simulate newly supported inspection and pure normalization together.
            parsed = inspect_fake_candle(raw_record())
            assert isinstance(parsed, ParsedFakeCandle)
            upgraded = replace(parsed, schema=UNKNOWN_SCHEMA)
            result = normalization(replace(blocker, envelope=envelope()))
            assert isinstance(result, CandleNormalization)
            result = replace(result, input_schema=UNKNOWN_SCHEMA)
            monkeypatch.setattr(runner, "inspect_fake_candle", lambda record: upgraded)
            monkeypatch.setattr(
                runner, "normalize_parsed_candle", lambda *args, **kwargs: result
            )
        processed = process_next(
            reader, store, metadata, normalized_at_ns=NORMALIZED_AT_NS
        )
        assert isinstance(processed, Processed)
        assert processed.outcome.identity == blocker.identity
        assert processed.outcome.predecessor == first.identity
        assert store.checkpoint() == blocker.identity
        assert store.barrier() is None
        assert counts(target.state_dir) == (1, 2, 1, 0)
        assert reader.calls == [(2, 1), (2, 1), (2, 1)]


@pytest.mark.parametrize("mismatch", ["empty", "offset", "epoch", "hash"])
def test_existing_barrier_must_match_next_raw_identity_and_hash(
    tmp_path: Path, mismatch: str
) -> None:
    record = raw_record(offset=5)
    target = installation(tmp_path)
    with NormalizationStore(target, producer="collector-a") as store:
        blocked = process_next(
            RecordingReader((record,)), store, {}, normalized_at_ns=NORMALIZED_AT_NS
        )
        assert isinstance(blocked, Blocked)
    candidate = {
        "empty": (),
        "offset": (raw_record(offset=9),),
        "epoch": (raw_record(offset=5, epoch="other"),),
        "hash": (raw_record(offset=5, payload=fake_candle_payload(close="100.5")),),
    }[mismatch]
    reader = RecordingReader(candidate)
    metadata = RecordingInstruments({INSTRUMENT_ID: instrument()})
    with NormalizationStore(target, producer="collector-a") as store:
        with pytest.raises(NormalizationError):
            process_next(reader, store, metadata, normalized_at_ns=NORMALIZED_AT_NS)
        assert store.barrier() == blocked.barrier
        assert store.checkpoint() is None
        assert counts(target.state_dir) == (0, 0, 0, 1)
        assert metadata.lookups == []
        assert reader.calls == [(0, 1)]


@pytest.mark.parametrize("failure", ["INSERT INTO processing_barrier", "COMMIT"])
def test_barrier_insert_or_commit_failure_rolls_back_and_allows_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    target = installation(tmp_path)
    connections = fault_connections(monkeypatch)
    reader = RecordingReader((raw_record(),))
    with NormalizationStore(target, producer="collector-a") as store:
        db = connections[0]
        db.failure = failure
        with pytest.raises(NormalizationError) as error:
            process_next(reader, store, {}, normalized_at_ns=NORMALIZED_AT_NS)
        assert "private" not in str(error.value)
        assert not db.in_transaction
        assert counts(target.state_dir) == (0, 0, 0, 0)
        assert isinstance(
            process_next(reader, store, {}, normalized_at_ns=NORMALIZED_AT_NS), Blocked
        )


@pytest.mark.parametrize("ignore", [False, True])
def test_barrier_delete_failure_rolls_back_entire_process_and_preserves_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, ignore: bool
) -> None:
    target = installation(tmp_path)
    connections = fault_connections(monkeypatch)
    record = raw_record()
    reader = RecordingReader((record,))
    with NormalizationStore(target, producer="collector-a") as store:
        blocked = process_next(reader, store, {}, normalized_at_ns=NORMALIZED_AT_NS)
        assert isinstance(blocked, Blocked)
        db = connections[0]
        if ignore:
            db.ignored = "DELETE FROM processing_barrier"
        else:
            db.failure = "DELETE FROM processing_barrier"
        with pytest.raises(NormalizationError):
            process_next(
                reader,
                store,
                {INSTRUMENT_ID: instrument()},
                normalized_at_ns=NORMALIZED_AT_NS,
            )
        assert not db.in_transaction
        assert store.barrier() == blocked.barrier
        assert counts(target.state_dir) == (0, 0, 0, 1)
        assert isinstance(
            process_next(
                reader,
                store,
                {INSTRUMENT_ID: instrument()},
                normalized_at_ns=NORMALIZED_AT_NS,
            ),
            Processed,
        )
        assert counts(target.state_dir) == (1, 1, 1, 0)


def test_barrier_transaction_is_invisible_until_commit_and_idempotent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = installation(tmp_path)
    connections = fault_connections(monkeypatch)
    record = raw_record(offset=2)
    with NormalizationStore(target, producer="collector-a") as store:
        db = connections[0]
        statements: list[tuple[str, bool]] = []
        snapshots: list[tuple[int, ...]] = []

        def trace(sql: str) -> None:
            statements.append((sql, db.in_transaction))
            snapshots.append(counts(target.state_dir))

        db.set_trace_callback(trace)
        barrier = store.block(
            record,
            expected_predecessor=None,
            reason=BarrierReason.METADATA_UNAVAILABLE,
            schema=FAKE_CANDLE_SCHEMA,
            instrument=INSTRUMENT_ID,
        )
        db.set_trace_callback(None)
        assert statements[0] == ("BEGIN IMMEDIATE", False)
        assert statements[-1] == ("COMMIT", True)
        assert all(active for _, active in statements[1:])
        assert all(snapshot == (0, 0, 0, 0) for snapshot in snapshots)
        assert counts(target.state_dir) == (0, 0, 0, 1)
        assert (
            store.block(
                record,
                expected_predecessor=None,
                reason=BarrierReason.METADATA_UNAVAILABLE,
                schema=FAKE_CANDLE_SCHEMA,
                instrument=INSTRUMENT_ID,
            )
            == barrier
        )
        assert counts(target.state_dir) == (0, 0, 0, 1)


@pytest.mark.parametrize("invalid", ["stale", "producer", "equal", "lower"])
def test_block_checks_predecessor_producer_and_order(
    tmp_path: Path, invalid: str
) -> None:
    target = installation(tmp_path)
    first = raw_record(offset=5)
    record = raw_record(offset=9)
    expected = first.identity
    if invalid == "producer":
        record = replace(record, identity=replace(record.identity, producer="other"))
    elif invalid in ("equal", "lower"):
        record = raw_record(offset=5 if invalid == "equal" else 2)
    elif invalid == "stale":
        expected = replace(first.identity, epoch="other")
    with NormalizationStore(target, producer="collector-a") as store:
        store.process(first, normalization(first), expected_predecessor=None)
        with pytest.raises(NormalizationError):
            store.block(
                record,
                expected_predecessor=expected,
                reason=BarrierReason.METADATA_UNAVAILABLE,
                schema=FAKE_CANDLE_SCHEMA,
                instrument=INSTRUMENT_ID,
            )
        assert store.checkpoint() == first.identity
        assert counts(target.state_dir) == (1, 1, 1, 0)


@pytest.mark.parametrize(
    "difference", ["identity", "hash", "reason", "schema", "instrument"]
)
def test_different_barrier_cannot_replace_existing_evidence(
    tmp_path: Path, difference: str
) -> None:
    target = installation(tmp_path)
    record = raw_record(offset=2)
    with NormalizationStore(target, producer="collector-a") as store:
        barrier = store.block(
            record,
            expected_predecessor=None,
            reason=BarrierReason.METADATA_UNAVAILABLE,
            schema=FAKE_CANDLE_SCHEMA,
            instrument=INSTRUMENT_ID,
        )
        changed_record = (
            raw_record(offset=3)
            if difference == "identity"
            else raw_record(offset=2, payload=b"different")
            if difference == "hash"
            else record
        )
        changed_instrument = InstrumentId("fake-venue", "spot", "ETH-USDT")
        with pytest.raises(NormalizationError):
            store.block(
                changed_record,
                expected_predecessor=None,
                reason=BarrierReason.UNSUPPORTED_SCHEMA
                if difference == "reason"
                else BarrierReason.METADATA_UNAVAILABLE,
                schema=UNKNOWN_SCHEMA if difference == "schema" else FAKE_CANDLE_SCHEMA,
                instrument=None
                if difference == "reason"
                else changed_instrument
                if difference == "instrument"
                else INSTRUMENT_ID,
            )
        assert store.barrier() == barrier
        assert counts(target.state_dir) == (0, 0, 0, 1)


@pytest.mark.parametrize(
    "reason,schema,subject",
    [
        (BarrierReason.UNSUPPORTED_SCHEMA, None, None),
        (BarrierReason.UNSUPPORTED_SCHEMA, UNKNOWN_SCHEMA, INSTRUMENT_ID),
        (BarrierReason.METADATA_UNAVAILABLE, None, INSTRUMENT_ID),
        (BarrierReason.METADATA_UNAVAILABLE, FAKE_CANDLE_SCHEMA, None),
    ],
)
def test_incomplete_barrier_details_cannot_be_persisted(
    tmp_path: Path,
    reason: BarrierReason,
    schema: SchemaRef | None,
    subject: InstrumentId | None,
) -> None:
    target = installation(tmp_path)
    with NormalizationStore(target, producer="collector-a") as store:
        with pytest.raises(NormalizationError):
            store.block(
                raw_record(),
                expected_predecessor=None,
                reason=reason,
                schema=schema,
                instrument=subject,
            )
        assert counts(target.state_dir) == (0, 0, 0, 0)


def test_barrier_readback_detects_missing_insert(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = installation(tmp_path)
    connections = fault_connections(monkeypatch)
    with NormalizationStore(target, producer="collector-a") as store:
        connections[0].ignored = "INSERT INTO processing_barrier"
        with pytest.raises(NormalizationError):
            store.block(
                raw_record(),
                expected_predecessor=None,
                reason=BarrierReason.METADATA_UNAVAILABLE,
                schema=FAKE_CANDLE_SCHEMA,
                instrument=INSTRUMENT_ID,
            )
        assert counts(target.state_dir) == (0, 0, 0, 0)


def test_real_f05_reader_preserves_duplicate_delivery_provenance_in_separate_store(
    tmp_path: Path,
) -> None:
    target = installation(tmp_path)
    with DurableIngestor(
        target,
        producer="collector-a",
        epoch="epoch-a",
        capacity=2,
        max_payload_bytes=8192,
    ) as ingestor:
        first = ingestor.accept(envelope())
        duplicate = ingestor.accept(envelope())
        with NormalizationStore(target, producer="collector-a") as store:
            results = [
                process_next(
                    ingestor,
                    store,
                    {INSTRUMENT_ID: instrument()},
                    normalized_at_ns=NORMALIZED_AT_NS,
                )
                for _ in range(2)
            ]
            assert all(isinstance(result, Processed) for result in results)
            accepted = store.outcome(first.identity)
            repeated = store.outcome(duplicate.identity)
            assert accepted is not None and repeated is not None
            assert accepted.kind is OutcomeKind.ACCEPTED
            assert repeated.kind is OutcomeKind.DUPLICATE
            assert accepted.semantic_revision == repeated.semantic_revision
            assert repeated.predecessor == first.identity
            assert store.checkpoint() == duplicate.identity
            assert process_next(
                ingestor, store, {}, normalized_at_ns=NORMALIZED_AT_NS
            ) == NoWork(duplicate.identity)
            assert (target.state_dir / "normalization.sqlite3").is_file()
            assert counts(target.state_dir) == (1, 2, 1, 0)
        assert ingestor.records_after(0, limit=2) == (first, duplicate)
