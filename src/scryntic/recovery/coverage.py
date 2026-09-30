"""Bounded coverage/checkpoints on the ingestion writer's reserved metadata lane."""

import json
from dataclasses import asdict, dataclass, replace
from enum import StrEnum
from typing import Any, Literal, cast

from scryntic.domain.identity import SubjectId
from scryntic.domain.raw import RawEnvelope, RawRecord
from scryntic.domain.time import ClockSample, TimeQuality
from scryntic.domain.validation import digest, identifier, integer
from scryntic.ingestion.sqlite_spool import DurableIngestor, IngestionError

_SPAN_LIMIT = 64
_REPAIR_CANDLES = 256


class Family(StrEnum):
    CANDLE = "candle"
    TRADE = "trade"
    LIQUIDATION = "liquidation"
    BOOK = "book"
    PERIODIC = "periodic"
    NEWS = "news"
    ONCHAIN = "onchain"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class RecoveryStream:
    key: str
    source: str
    stream: str
    channel: str
    subject: SubjectId | None
    family: Family
    recovery: Literal["backfill", "resnapshot", "coverage-loss"]

    def __post_init__(self) -> None:
        for value in (self.key, self.source, self.stream, self.channel):
            identifier(value)
        if not isinstance(self.family, Family) or self.recovery not in (
            "backfill",
            "resnapshot",
            "coverage-loss",
        ):
            raise ValueError("Invalid family recovery policy")


@dataclass(frozen=True, slots=True)
class CandleEvidence:
    start_ns: int
    interval_ns: int
    finalized: bool
    market_sha256: str | None = None

    def __post_init__(self) -> None:
        integer(self.start_ns)
        integer(self.interval_ns, 1)
        if type(self.finalized) is not bool:
            raise TypeError("Expected candle finality")
        if self.market_sha256 is not None:
            digest(self.market_sha256)


@dataclass(frozen=True, slots=True)
class BookEvidence:
    sequence: int
    snapshot: bool

    def __post_init__(self) -> None:
        integer(self.sequence)
        if type(self.snapshot) is not bool:
            raise TypeError("Expected book snapshot flag")


@dataclass(frozen=True, slots=True)
class CoverageSpan:
    start_ns: int
    end_ns: int | None
    status: Literal["complete", "pending", "unknown", "unrecoverable"]
    reason: str
    detected: ClockSample
    checkpoint_offset: int
    first_sequence: int | None = None
    last_sequence: int | None = None
    cursor: bytes | None = None
    repaired: tuple[int, ...] = ()
    pages: int = 0
    attempts: int = 0

    def __post_init__(self) -> None:
        integer(self.start_ns)
        if self.end_ns is not None:
            integer(self.end_ns)
            if self.end_ns <= self.start_ns:
                raise ValueError("Empty coverage span")
        if self.status not in ("complete", "pending", "unknown", "unrecoverable"):
            raise ValueError("Invalid coverage status")
        identifier(self.reason)
        integer(self.checkpoint_offset)
        integer(self.pages)
        integer(self.attempts)
        for sequence in (self.first_sequence, self.last_sequence):
            if sequence is not None:
                integer(sequence)
        if self.cursor is not None and (
            type(self.cursor) is not bytes or len(self.cursor) > 4096
        ):
            raise ValueError("Invalid recovery cursor")
        if type(self.repaired) is not tuple or len(self.repaired) > _REPAIR_CANDLES:
            raise ValueError("Repair evidence exceeds limit")
        for start in self.repaired:
            integer(start)


@dataclass(frozen=True, slots=True)
class CoverageSnapshot:
    active: bool
    checkpoint_ns: int
    raw_offset: int = 0
    last_start_ns: int | None = None
    book_valid: bool = False
    last_sequence: int | None = None
    spans: tuple[CoverageSpan, ...] = ()
    unknown_since_ns: int | None = None
    unknown_detected: ClockSample | None = None
    final_candles: tuple[tuple[int, str], ...] = ()

    def __post_init__(self) -> None:
        if type(self.active) is not bool or type(self.book_valid) is not bool:
            raise TypeError("Invalid coverage state")
        integer(self.checkpoint_ns)
        integer(self.raw_offset)
        for value in (self.last_start_ns, self.last_sequence):
            if value is not None:
                integer(value)
        if self.unknown_since_ns is not None:
            integer(self.unknown_since_ns)
            if not isinstance(self.unknown_detected, ClockSample):
                raise ValueError("Unknown coverage needs detection evidence")
        if type(self.spans) is not tuple or len(self.spans) > _SPAN_LIMIT:
            raise ValueError("Coverage history quota reached")
        if (
            type(self.final_candles) is not tuple
            or len(self.final_candles) > _REPAIR_CANDLES
        ):
            raise ValueError("Final candle cache quota reached")
        for start, fingerprint in self.final_candles:
            integer(start)
            digest(fingerprint)


def _clock(value: dict[str, Any]) -> ClockSample:
    fields = dict(value)
    fields["quality"] = TimeQuality(**fields["quality"])
    return ClockSample(**fields)


class CoverageLedger:
    """One supervised stream owner; CAS refuses stale concurrent recovery writers."""

    def __init__(
        self, spool: DurableIngestor, stream: RecoveryStream, *, baseline_ns: int
    ) -> None:
        integer(baseline_ns)
        self.spool = spool
        self.stream = stream
        self._stored = spool.recovery_get(stream.key)
        self._failed = False
        self.snapshot = CoverageSnapshot(False, baseline_ns)
        if self._stored is not None:
            try:
                document = json.loads(self._stored)
                if (
                    set(document) != {"version", "stream", "state"}
                    or document["version"] != 1
                    or document["stream"] != self._context()
                ):
                    raise ValueError()
                state = document["state"]
                spans = []
                for value in state["spans"]:
                    fields = dict(value)
                    fields["detected"] = _clock(fields["detected"])
                    fields["cursor"] = (
                        None
                        if fields["cursor"] is None
                        else bytes.fromhex(fields["cursor"])
                    )
                    fields["repaired"] = tuple(fields["repaired"])
                    spans.append(CoverageSpan(**fields))
                state["spans"] = tuple(spans)
                state["final_candles"] = tuple(
                    tuple(value) for value in state["final_candles"]
                )
                if state["unknown_detected"] is not None:
                    state["unknown_detected"] = _clock(state["unknown_detected"])
                self.snapshot = CoverageSnapshot(**state)
            except (TypeError, ValueError, KeyError, RecursionError):
                raise IngestionError("Invalid recovery metadata") from None

    def _context(self) -> dict[str, object]:
        # JSON roundtrip canonicalizes tuples in typed subject identities.
        return cast(dict[str, object], json.loads(json.dumps(asdict(self.stream))))

    def coverage(
        self, start_ns: int, end_ns: int
    ) -> Literal["complete", "pending", "unknown", "unrecoverable"]:
        integer(start_ns)
        integer(end_ns)
        if start_ns >= end_ns:
            raise ValueError("Empty coverage query")
        spans = [
            span
            for span in self.snapshot.spans
            if span.start_ns < end_ns
            and (span.end_ns is None or span.end_ns > start_ns)
        ]
        for status in ("unrecoverable", "unknown", "pending"):
            if any(span.status == status for span in spans):
                return status
        if (
            self.snapshot.unknown_since_ns is not None
            and self.snapshot.unknown_since_ns < end_ns
        ):
            return "unknown"
        covered_until = start_ns
        for span in sorted(spans, key=lambda value: value.start_ns):
            if span.start_ns > covered_until or span.end_ns is None:
                break
            covered_until = max(covered_until, span.end_ns)
        return "complete" if covered_until >= end_ns else "unknown"

    def _save(self, snapshot: CoverageSnapshot, *, restart: bool = False) -> None:
        if self._failed:
            raise IngestionError("Recovery ledger failed; restart required")
        state = asdict(snapshot)
        for span in state["spans"]:
            span["cursor"] = None if span["cursor"] is None else span["cursor"].hex()
        value = json.dumps(
            {"version": 1, "stream": self._context(), "state": state},
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        try:
            # Leave bounded space for restart uncertainty even at the loss quota.
            if len(value) > 61_440 and not restart:
                raise IngestionError("Recovery metadata headroom reached")
            self.spool.recovery_put(self.stream.key, value, expected=self._stored)
        except BaseException:
            self._failed = True
            self.snapshot = replace(self.snapshot, book_valid=False)
            # Never subsequently write a clean session after a lost marker.
            raise
        self._stored = value
        self.snapshot = snapshot

    def begin(self, sample: ClockSample) -> None:
        state = self.snapshot
        if self._stored is not None:
            # Dirty exit or clean downtime both lack source continuity evidence.
            reason = "restart-unverified" if state.active else "resume-unverified"
            span = CoverageSpan(
                state.checkpoint_ns, None, "unknown", reason, sample, state.raw_offset
            )
            if len(state.spans) < _SPAN_LIMIT - 1:
                state = replace(state, spans=state.spans + (span,))
            else:
                since = (
                    state.checkpoint_ns
                    if state.unknown_since_ns is None
                    else min(state.checkpoint_ns, state.unknown_since_ns)
                )
                state = replace(state, unknown_since_ns=since, unknown_detected=sample)
        self._save(
            replace(state, active=True, book_valid=False, last_sequence=None),
            restart=True,
        )

    def finish(self) -> None:
        self._save(replace(self.snapshot, active=False, book_valid=False))

    def loss(
        self,
        start_ns: int,
        end_ns: int | None,
        reason: str,
        sample: ClockSample,
        *,
        first_sequence: int | None = None,
        last_sequence: int | None = None,
    ) -> None:
        status: Literal["pending", "unknown", "unrecoverable"] = "unknown"
        if (
            self.stream.family is Family.CANDLE
            and self.stream.recovery == "backfill"
            and end_ns is not None
        ):
            status = "pending"
        elif self.stream.family in (Family.TRADE, Family.LIQUIDATION, Family.BOOK):
            status = "unrecoverable"
        span = CoverageSpan(
            start_ns,
            end_ns,
            status,
            reason,
            sample,
            self.snapshot.raw_offset,
            first_sequence,
            last_sequence,
        )
        if len(self.snapshot.spans) >= _SPAN_LIMIT - 1:
            raise IngestionError("Coverage history quota reached")
        # Invalidate in memory before attempting persistence; never publish a valid
        # book after a marker failure. The stored active session covers restart.
        self.snapshot = replace(self.snapshot, book_valid=False)
        self._save(replace(self.snapshot, spans=self.snapshot.spans + (span,)))

    def observed(self, record: RawRecord, candle: CandleEvidence | None = None) -> None:
        envelope = record.envelope
        if (envelope.source, envelope.stream, envelope.channel, envelope.subject) != (
            self.stream.source,
            self.stream.stream,
            self.stream.channel,
            self.stream.subject,
        ):
            raise ValueError("Recovery stream identity mismatch")
        self._observed_sequence(record)
        if candle is not None:
            self._observed_candle(record, candle)
        self._save(
            replace(
                self.snapshot,
                raw_offset=max(self.snapshot.raw_offset, record.identity.offset),
            )
        )

    def matches(self, envelope: RawEnvelope) -> bool:
        return (
            envelope.source,
            envelope.stream,
            envelope.channel,
            envelope.subject,
        ) == (
            self.stream.source,
            self.stream.stream,
            self.stream.channel,
            self.stream.subject,
        )

    def _observed_sequence(self, record: RawRecord) -> None:
        sequence = record.envelope.source_sequence
        if (
            self.stream.family not in (Family.TRADE, Family.LIQUIDATION)
            or sequence is None
        ):
            return
        state = self.snapshot
        previous = state.last_sequence
        if previous is not None and sequence > previous + 1:
            self.loss(
                state.checkpoint_ns,
                None,
                "trade-sequence-gap",
                record.envelope.receipt,
                first_sequence=previous + 1,
                last_sequence=sequence - 1,
            )
            state = self.snapshot
        self.snapshot = replace(state, last_sequence=max(sequence, previous or 0))

    def _close_uncertain(self, candle: CandleEvidence) -> None:
        spans = list(self.snapshot.spans)
        for index, span in enumerate(spans):
            if span.end_ns is None and candle.start_ns > span.start_ns:
                spans[index] = replace(
                    span,
                    end_ns=candle.start_ns,
                    status="pending"
                    if self.stream.recovery == "backfill"
                    else "unknown",
                )
        self.snapshot = replace(self.snapshot, spans=tuple(spans))

    def _candle_gap(
        self, last: int | None, candle: CandleEvidence, sample: ClockSample
    ) -> None:
        if last is None:
            return
        state = self.snapshot
        missing_start = (
            last if state.checkpoint_ns <= last else last + candle.interval_ns
        )
        covered = any(
            span.start_ns <= missing_start
            and span.end_ns is not None
            and span.end_ns >= candle.start_ns
            for span in state.spans
        )
        if candle.start_ns > missing_start and not covered:
            self.loss(missing_start, candle.start_ns, "candle-gap", sample)

    def _observed_candle(self, record: RawRecord, candle: CandleEvidence) -> None:
        last = self.snapshot.last_start_ns
        self._close_uncertain(candle)
        self._candle_gap(last, candle, record.envelope.receipt)
        state = replace(self.snapshot, last_start_ns=max(candle.start_ns, last or 0))
        if candle.finalized:
            boundary = candle.start_ns + candle.interval_ns
            unresolved = [
                span.start_ns for span in state.spans if span.status != "complete"
            ]
            if unresolved:
                boundary = min(boundary, min(unresolved))
            state = replace(state, checkpoint_ns=max(state.checkpoint_ns, boundary))
            if candle.market_sha256 is not None:
                known = dict(state.final_candles)
                existing = known.get(candle.start_ns)
                if existing is not None and existing != candle.market_sha256:
                    raise IngestionError("Conflicting finalized candle evidence")
                known[candle.start_ns] = candle.market_sha256
                state = replace(
                    state, final_candles=tuple(sorted(known.items())[-_REPAIR_CANDLES:])
                )
        self.snapshot = state

    def repair_page(
        self,
        index: int,
        starts: tuple[int, ...],
        cursor: bytes | None,
        *,
        interval_ns: int,
        exhausted: bool,
        raw_offset: int = 0,
        final_candles: tuple[tuple[int, str], ...] = (),
    ) -> None:
        span = self.snapshot.spans[index]
        assert span.end_ns is not None
        count = (span.end_ns - span.start_ns) // interval_ns
        if (
            count > _REPAIR_CANDLES
            or count * interval_ns != span.end_ns - span.start_ns
        ):
            raise ValueError("Repair range exceeds bounded candle grid")
        proven = tuple(
            sorted(
                set(span.repaired).union(
                    start for start in starts if span.start_ns <= start < span.end_ns
                )
            )
        )
        expected = tuple(range(span.start_ns, span.end_ns, interval_ns))
        complete = proven == expected
        status: Literal["complete", "unknown", "pending"] = "pending"
        if complete:
            status = "complete"
        elif exhausted:
            status = "unknown"
        updated = replace(
            span, repaired=proven, cursor=cursor, pages=span.pages + 1, status=status
        )
        spans = list(self.snapshot.spans)
        spans[index] = updated
        known = dict(self.snapshot.final_candles)
        known.update(final_candles)
        self._save(
            replace(
                self.snapshot,
                spans=tuple(spans),
                raw_offset=max(raw_offset, self.snapshot.raw_offset),
                final_candles=tuple(sorted(known.items())[-_REPAIR_CANDLES:]),
            )
        )

    def book_observed(self, record: RawRecord, evidence: BookEvidence) -> bool:
        self.observed(record)
        if evidence.snapshot:
            self.resnapshot(evidence.sequence)
            return True
        return self.book_delta(evidence.sequence, record.envelope.receipt)

    def resnapshot(self, sequence: int) -> None:
        if self.stream.family is not Family.BOOK:
            raise ValueError("Resnapshot requires book family")
        integer(sequence)
        self._save(replace(self.snapshot, book_valid=True, last_sequence=sequence))

    def exhaust(self, index: int, reason: str) -> None:
        spans = list(self.snapshot.spans)
        spans[index] = replace(
            spans[index], status="unknown", reason=reason, cursor=None
        )
        self._save(replace(self.snapshot, spans=tuple(spans)))

    def repair_attempt(self, index: int) -> None:
        spans = list(self.snapshot.spans)
        spans[index] = replace(spans[index], attempts=spans[index].attempts + 1)
        self._save(replace(self.snapshot, spans=tuple(spans)))

    def book_delta(self, sequence: int, sample: ClockSample) -> bool:
        integer(sequence)
        state = self.snapshot
        if not state.book_valid or state.last_sequence is None:
            return False
        if sequence != state.last_sequence + 1:
            self.loss(
                state.checkpoint_ns,
                None,
                "book-sequence-gap",
                sample,
                first_sequence=state.last_sequence + 1,
                last_sequence=sequence,
            )
            return False
        self._save(replace(state, last_sequence=sequence))
        return True
