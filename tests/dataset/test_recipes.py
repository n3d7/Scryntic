"""Explicit selection policies and gap-aware deterministic recipes."""

from dataclasses import replace
from decimal import Decimal

import pytest

from scryntic.configuration.clock import ClockLimits
from scryntic.dataset.recipes import (
    CoverageClaim,
    RecipePolicy,
    apply_policy,
    derive_rows,
)
from scryntic.dataset.selection import SelectionError, SourceInput, select_candles
from scryntic.domain.time import ClockSample, TimeQuality
from scryntic.normalization.sqlite_store import OutcomeKind
from tests.dataset.test_selection import _source


def _clock(wall: int) -> ClockSample:
    return ClockSample(wall, 0, "session", TimeQuality("epoch", "healthy", 0, 0, 0))


def _received(source: SourceInput, wall: int) -> SourceInput:
    raw = replace(
        source.value.raw,
        envelope=replace(
            source.value.raw.envelope,
            receipt=_clock(wall),
            payload_limit=len(source.value.raw.envelope.payload),
        ),
    )
    return replace(
        source,
        value=replace(
            source.value,
            raw=raw,
            outcome=replace(source.value.outcome, receipt=raw.envelope.receipt),
        ),
    )


def test_strict_cutoff_excludes_later_repair_and_revision() -> None:
    first = _received(_source(1), 10)
    final = _received(
        _source(2, close="100.20", finalized=True, kind=OutcomeKind.FINALIZATION), 30
    )
    semantics = first.value.semantics
    assert semantics is not None
    key = semantics.key
    repair = CoverageClaim(
        key.instrument,
        key.interval_ns,
        key.start_ns,
        key.start_ns + key.interval_ns,
        "complete",
        _clock(30),
    )
    selected, records = apply_policy(
        (first, final),
        RecipePolicy(mode="as-observed"),
        _clock(20),
        ClockLimits(),
        (repair,),
    )
    assert selected[0].selected == first
    assert selected[0].evidence == (first,)
    assert (
        next(record for record in records if record.get("reason") == "coverage")[
            "coverage_status"
        ]
        == "unknown"
    )


def test_strict_duplicate_without_original_fails_closed() -> None:
    first = _received(_source(1), 30)
    duplicate = _received(_source(2, kind=OutcomeKind.DUPLICATE), 10)
    policy, cutoff, limits = RecipePolicy(mode="as-observed"), _clock(20), ClockLimits()
    with pytest.raises(SelectionError):
        apply_policy((first, duplicate), policy, cutoff, limits)


def test_historical_unhealthy_receipts_are_recorded_or_rejected() -> None:
    source = _source(1)
    selected, records = apply_policy((source,), RecipePolicy(), None, ClockLimits())
    assert selected
    assert any(
        record["reason"] == "time-quality" and not record["excluded"]
        for record in records
    )
    policy, limits = RecipePolicy(time_quality="require-healthy"), ClockLimits()
    with pytest.raises(SelectionError, match="healthy"):
        apply_policy((source,), policy, None, limits)


@pytest.mark.parametrize("steps", [True, -1, 129, 1.5])
def test_policy_rejects_invalid_steps(steps: int) -> None:
    with pytest.raises((TypeError, ValueError)):
        RecipePolicy(lag_steps=steps)


def test_first_revision_retains_finalization_evidence() -> None:
    first = _source(1)
    last = _source(2, close="100.20", finalized=True, kind=OutcomeKind.FINALIZATION)
    selected, _ = apply_policy(
        (first, last), RecipePolicy(revision="first"), None, ClockLimits()
    )
    assert selected[0].selected == first
    assert selected[0].evidence == (first, last)


def test_exclusions_cannot_hide_conflicts() -> None:
    conflict = _source(1, kind=OutcomeKind.CONFLICT)
    policy, limits = RecipePolicy(finalized_only=True), ClockLimits()
    with pytest.raises(SelectionError):
        apply_policy((conflict,), policy, None, limits)


def test_missing_coverage_is_unknown_and_require_rejects() -> None:
    source = _source(1)
    policy, limits = RecipePolicy(coverage="require-complete"), ClockLimits()
    with pytest.raises(SelectionError, match="coverage"):
        apply_policy((source,), policy, None, limits)
    selected, reasons = apply_policy(
        (source,), RecipePolicy(coverage="exclude"), None, ClockLimits()
    )
    assert not selected
    assert (
        next(reason for reason in reasons if reason["reason"] == "coverage")[
            "coverage_status"
        ]
        == "unknown"
    )


def test_partial_complete_claim_and_overlapping_pending_are_conservative() -> None:
    source = _source(1)
    semantics = source.value.semantics
    assert semantics is not None
    key = semantics.key
    complete = CoverageClaim(
        key.instrument,
        key.interval_ns,
        key.start_ns,
        key.start_ns + key.interval_ns,
        "complete",
        _clock(0),
    )
    pending = replace(complete, end_ns=key.start_ns + 1, status="pending")
    selected, _ = apply_policy(
        (source,), RecipePolicy(coverage="exclude"), None, ClockLimits(), (complete,)
    )
    assert selected
    selected, _ = apply_policy(
        (source,),
        RecipePolicy(coverage="exclude"),
        None,
        ClockLimits(),
        (complete, pending),
    )
    assert not selected
    selected, _ = apply_policy(
        (source,),
        RecipePolicy(coverage="exclude"),
        None,
        ClockLimits(),
        (replace(complete, end_ns=key.start_ns + 1),),
    )
    assert not selected


def test_derivations_require_continuity_and_final_labels_without_mutation() -> None:
    first = _source(1, finalized=True)
    semantics = first.value.semantics
    assert semantics is not None
    start, interval = semantics.key.start_ns, semantics.key.interval_ns
    second = _source(2, finalized=True, close="100.20", start_ns=start + interval)
    fourth = _source(3, finalized=True, start_ns=start + 3 * interval)
    selected = select_candles((first, second, fourth))
    rows = [
        {"close": Decimal(item.selected.value.semantics.close)}
        for item in selected
        if item.selected.value.semantics is not None
    ]
    derived, definitions = derive_rows(
        selected, rows, RecipePolicy(lag_steps=1, label_horizon_steps=1)
    )
    assert derived[0]["label_close"] == Decimal("100.20")
    assert derived[1]["lag_close"] == Decimal("100.75")
    assert derived[1]["label_close"] is None
    assert derived[2]["lag_close"] is None
    assert all(set(row) == {"close"} for row in rows)
    assert definitions[0]["label_source_fingerprints"]


def test_label_intermediate_open_candle_blocks_future_target() -> None:
    first = _source(1, finalized=True)
    semantics = first.value.semantics
    assert semantics is not None
    key = semantics.key
    second = _source(2, start_ns=key.start_ns + key.interval_ns)
    third = _source(3, finalized=True, start_ns=key.start_ns + 2 * key.interval_ns)
    selected = select_candles((first, second, third))
    derived, definitions = derive_rows(
        selected, [{}, {}, {}], RecipePolicy(label_horizon_steps=2)
    )
    assert derived[0]["label_close"] is None
    assert definitions[0]["label_missing_reason"] == "not-finalized"
