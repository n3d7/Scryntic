"""Deterministic logical selection from validated publication inputs."""

from dataclasses import replace

import pytest

from scryntic.archive.canonical import PublicationInput
from scryntic.configuration.clock import ClockLimits
from scryntic.dataset import selection
from scryntic.dataset.selection import SelectionError, SourceInput, select_candles
from scryntic.domain.time import ClockSample, TimeQuality
from scryntic.normalization.candle import RejectionCode, RejectionField
from scryntic.normalization.sqlite_store import OutcomeKind
from tests.normalization.helpers import fake_candle_payload, raw_record
from tests.publication.helpers import FixedNormalizationReader


def _source(
    offset: int,
    *,
    close: str = "100.75",
    finalized: bool = False,
    kind: OutcomeKind = OutcomeKind.ACCEPTED,
    start_ns: int = 1_700_000_000_000_000_000,
) -> SourceInput:
    raw = raw_record(
        offset=offset,
        payload=fake_candle_payload(
            close=close, finalized=finalized, start_ns=start_ns
        ),
    )
    reader = FixedNormalizationReader((raw,))
    outcome = reader.outcome(raw.identity)
    assert outcome is not None
    assert outcome.semantic_revision is not None
    semantics = reader.observation(outcome.semantic_revision)
    assert semantics is not None
    if kind is OutcomeKind.REJECTED:
        outcome = replace(
            outcome,
            kind=kind,
            semantic_revision=None,
            rejection_code=RejectionCode.INVALID_SCHEMA,
            rejection_field=RejectionField.SCHEMA,
        )
        semantics = None
    else:
        outcome = replace(outcome, kind=kind)
    return SourceInput(
        manifest_sha256="a" * 64,
        raw_object_sha256="b" * 64,
        normalized_object_sha256="c" * 64,
        ordinal=offset - 1,
        value=PublicationInput(raw, outcome, semantics),
    )


def test_duplicate_revision_and_finalization_select_one_row_with_all_evidence() -> None:
    accepted = _source(1)
    duplicate = _source(2, kind=OutcomeKind.DUPLICATE)
    revision = _source(3, close="100.20", kind=OutcomeKind.OPEN_REVISION)
    final = _source(4, close="100.30", finalized=True, kind=OutcomeKind.FINALIZATION)

    rows = select_candles((final, revision, duplicate, accepted))

    assert len(rows) == 1
    assert rows[0].selected == final
    assert tuple(item.value.raw.identity.offset for item in rows[0].evidence) == (
        1,
        2,
        3,
        4,
    )


def test_rows_are_ordered_by_time_then_instrument_key() -> None:
    later = _source(1, start_ns=1_700_000_060_000_000_000)
    earlier = _source(2)

    rows = select_candles((later, earlier))
    semantics = [row.selected.value.semantics for row in rows]
    assert all(value is not None for value in semantics)

    assert [value.key.start_ns for value in semantics if value is not None] == [
        1_700_000_000_000_000_000,
        1_700_000_060_000_000_000,
    ]


@pytest.mark.parametrize("kind", [OutcomeKind.CONFLICT, OutcomeKind.REJECTED])
def test_conflict_or_rejection_fails_entire_selection(kind: OutcomeKind) -> None:
    accepted = _source(1)
    bad = _source(2, close="100.40", finalized=True, kind=kind)

    with pytest.raises(SelectionError):
        select_candles((accepted, bad))


@pytest.mark.parametrize("kind", [OutcomeKind.CONFLICT, OutcomeKind.REJECTED])
def test_strict_timing_cannot_hide_known_conflict_behind_unknown_receipt(
    kind: OutcomeKind,
) -> None:
    accepted = _source(1)
    bad = _source(2, close="100.40", finalized=True, kind=kind)
    cutoff = ClockSample(
        1_700_000_100_000_000_000, 1, "cutoff", TimeQuality("epoch", "healthy", 0, 5, 0)
    )
    prepared_clock_limits = ClockLimits()
    with pytest.raises(SelectionError):
        selection.strict_eligible((accepted, bad), cutoff, prepared_clock_limits)


def test_duplicate_requires_a_prior_identical_semantic_revision() -> None:
    prepared_source = (_source(1, kind=OutcomeKind.DUPLICATE),)
    with pytest.raises(SelectionError):
        select_candles(prepared_source)
