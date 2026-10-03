"""Policy guards and conservative filtering preserve explicit missing evidence."""

from dataclasses import replace
from typing import Any, Literal

import pytest

from scryntic.configuration.clock import ClockLimits
from scryntic.dataset.recipes import (
    CoverageClaim,
    RecipePolicy,
    apply_policy,
    derive_rows,
)
from scryntic.dataset.selection import SelectionError, select_candles
from tests.dataset.test_recipes import _clock
from tests.dataset.test_selection import _source


@pytest.mark.parametrize("field", ["mode", "revision", "coverage", "time_quality"])
def test_unsupported_policy_is_explicit(field: str) -> None:
    values: dict[str, Any] = {field: "unsupported"}
    with pytest.raises(ValueError, match="Unsupported recipe policy"):
        RecipePolicy(**values)


def test_finality_policy_rejects_implicit_boolean() -> None:
    with pytest.raises(TypeError, match="boolean"):
        RecipePolicy(finalized_only=1)  # type: ignore[arg-type]


def claim() -> CoverageClaim:
    semantics = _source(1).value.semantics
    assert semantics is not None
    key = semantics.key
    return CoverageClaim(
        key.instrument,
        key.interval_ns,
        key.start_ns,
        key.start_ns + key.interval_ns,
        "complete",
        _clock(0),
    )


@pytest.mark.parametrize(
    ("field", "value", "exception"),
    [
        ("instrument", "BTCUSDT", TypeError),
        ("end_ns", 0, ValueError),
        ("status", "repaired", ValueError),
        ("detected", None, TypeError),
        ("interval_ns", 0, ValueError),
    ],
)
def test_coverage_claim_requires_typed_nonempty_evidence(
    field: str, value: object, exception: type[Exception]
) -> None:
    original = claim()
    changes: dict[str, Any] = {field: value}
    with pytest.raises(exception):
        replace(original, **changes)


def test_strict_cutoff_cannot_be_missing_or_unhealthy() -> None:
    source = _source(1)
    policy, limits = RecipePolicy(mode="as-observed"), ClockLimits()
    with pytest.raises(SelectionError, match="healthy cutoff"):
        apply_policy((source,), policy, None, limits)
    with pytest.raises(SelectionError, match="healthy cutoff"):
        apply_policy((source,), policy, source.value.raw.envelope.receipt, limits)
    historical, cutoff = RecipePolicy(), _clock(1)
    with pytest.raises(SelectionError, match="Historical"):
        apply_policy((source,), historical, cutoff, limits)


def test_time_and_finality_exclusions_record_exact_source() -> None:
    source = _source(1)
    for policy, reason in (
        (RecipePolicy(time_quality="exclude"), "time-quality"),
        (RecipePolicy(finalized_only=True), "not-finalized"),
    ):
        selected, records = apply_policy((source,), policy, None, ClockLimits())
        assert not selected
        assert len(records) == 1
        assert records[0]["reason"] == reason
        assert records[0]["offset"] == source.value.raw.identity.offset
        assert len(records[0]["input_fingerprint"]) == 64


def test_separate_complete_ranges_do_not_bridge_a_gap() -> None:
    source, complete = _source(1), claim()
    midpoint = complete.start_ns + complete.interval_ns // 2
    captures = (
        replace(complete, end_ns=midpoint),
        replace(complete, start_ns=midpoint + 1),
    )
    selected, records = apply_policy(
        (source,), RecipePolicy(coverage="exclude"), None, ClockLimits(), captures
    )
    assert not selected
    assert records[-1]["coverage_status"] == "unknown"


@pytest.mark.parametrize("status", ["unknown", "unrecoverable"])
def test_unresolved_coverage_is_not_hidden_by_complete_capture(
    status: Literal["unknown", "unrecoverable"],
) -> None:
    source, complete = _source(1), claim()
    unresolved = replace(complete, status=status)
    _, records = apply_policy(
        (source,), RecipePolicy(), None, ClockLimits(), (complete, unresolved)
    )
    assert records[-1]["coverage_status"] == status


def test_recipe_evidence_and_row_cardinality_must_agree() -> None:
    selected = select_candles((_source(1),))
    policy = RecipePolicy(lag_steps=1)
    with pytest.raises(SelectionError, match="differ in length"):
        derive_rows(selected, [], policy)
    with pytest.raises(SelectionError, match="duplicate recipe candle"):
        derive_rows((selected[0], selected[0]), [{}, {}], policy)


def test_disabled_derivation_preserves_rows_without_inventing_provenance() -> None:
    selected = select_candles((_source(1),))
    rows = [{"close": "100.75"}]
    derived, definitions = derive_rows(selected, rows, RecipePolicy())
    assert derived == rows
    assert derived is not rows
    assert derived[0] is not rows[0]
    assert definitions == []
