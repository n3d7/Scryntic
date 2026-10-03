"""Future receipts, uncertainty and derived values cannot enter replay features."""

from dataclasses import replace
from typing import Any

import pytest

from scryntic.replay.contracts import ReplayClock, ReplayConfig
from scryntic.replay.reader import CandleReplayReader
from tests.dataset.test_recipes import _received
from tests.replay.helpers import START, STEP, snapshot, sources


def config(**kwargs: Any) -> ReplayConfig:
    return ReplayConfig(
        START,
        START + 5 * STEP,
        START + 9 * STEP,
        START + 13 * STEP,
        coverage="include",
        time_quality="include",
        **kwargs,
    )


def test_reader_exposes_features_without_future_label() -> None:
    manifest, rows = snapshot()
    reader = CandleReplayReader(manifest, rows, config())
    clock = ReplayClock(START + STEP).sample
    frame = reader.features(0, clock)
    assert frame is not None
    assert str(frame.values[0]) == "100"
    assert not hasattr(frame, "label_close")
    assert reader.target(0, clock) is None
    assert reader.target(0, ReplayClock(START + 2 * STEP).sample) is not None


def test_late_backfill_and_future_revision_are_unavailable() -> None:
    inputs = sources()
    late = _received(inputs[0], START + 10 * STEP)
    manifest, rows = snapshot((late, *inputs[1:]))
    strict = CandleReplayReader(manifest, rows, config(mode="as-observed"))
    historical = CandleReplayReader(manifest, rows, config())
    clock = ReplayClock(START + STEP).sample
    assert strict.features(0, clock) is None
    assert historical.features(0, clock) is not None


def test_lag_cannot_import_late_previous_candle() -> None:
    inputs = sources()
    manifest, rows = snapshot(
        (_received(inputs[0], START + 10 * STEP), *inputs[1:]), lag=1
    )
    reader = CandleReplayReader(manifest, rows, config(mode="as-observed"))
    assert reader.features(1, ReplayClock(START + 2 * STEP).sample) is None


def test_latest_observation_must_precede_earliest_decision() -> None:
    inputs = sources()
    late = replace(
        inputs[0].value.raw.envelope.receipt,
        quality=replace(inputs[0].value.raw.envelope.receipt.quality, uncertainty_ns=2),
    )
    raw = replace(
        inputs[0].value.raw,
        envelope=replace(
            inputs[0].value.raw.envelope,
            receipt=late,
            payload_limit=len(inputs[0].value.raw.envelope.payload),
        ),
    )
    value = replace(
        inputs[0].value, raw=raw, outcome=replace(inputs[0].value.outcome, receipt=late)
    )
    manifest, rows = snapshot((replace(inputs[0], value=value), *inputs[1:]))
    reader = CandleReplayReader(manifest, rows, config(mode="as-observed"))
    assert (
        reader.features(0, ReplayClock(START + STEP + 3, uncertainty_ns=2).sample)
        is None
    )
    assert (
        reader.features(0, ReplayClock(START + STEP + 4, uncertainty_ns=2).sample)
        is not None
    )


def test_forged_future_lag_lineage_and_values_fail() -> None:
    configuration = config()
    manifest, rows = snapshot(lag=1)
    manifest["derived_lineage"][1]["lag_source_fingerprints"] = [
        manifest["rows"][2]["selected"]["input_fingerprint"]
    ]
    with pytest.raises(ValueError, match="lineage"):
        CandleReplayReader(manifest, rows, configuration)
    manifest, rows = snapshot()
    rows[0]["label_close"] = "999"
    with pytest.raises(ValueError, match="derived"):
        CandleReplayReader(manifest, rows, configuration)
