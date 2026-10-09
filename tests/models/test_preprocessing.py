from dataclasses import replace

import pytest

from scryntic.dataset.schemas import EVOLVED_DATASET_SCHEMA
from scryntic.domain.dataset import DatasetRef
from scryntic.models.preprocessing import ReplayWindows
from scryntic.replay.contracts import ReplayClock
from scryntic.replay.reader import CandleReplayReader
from tests.replay.helpers import START, STEP, snapshot, sources
from tests.replay.test_reader import config


def test_context_uses_only_observable_history_and_never_target_values() -> None:
    manifest, rows = snapshot(sources(12))
    reader = CandleReplayReader(manifest, rows, config())
    windows = ReplayWindows(
        reader, DatasetRef("a" * 64, EVOLVED_DATASET_SCHEMA, len(rows))
    )
    values = windows.at(4, ReplayClock(START + 5 * STEP).sample)
    assert values.starts[-1] == START + 4 * STEP
    assert len(values.closes) == 5
    assert values.closes[-1] == 100.04
    assert 100.05 not in values.closes
    assert all(t < START + 5 * STEP for t in values.starts)


def test_future_receipt_and_noncontiguous_inputs_do_not_reach_model() -> None:
    inputs = sources(12)
    manifest, rows = snapshot(inputs)
    reader = CandleReplayReader(manifest, rows, replace(config(), mode="as-observed"))
    windows = ReplayWindows(
        reader, DatasetRef("a" * 64, EVOLVED_DATASET_SCHEMA, len(rows))
    )
    clock = ReplayClock(START + 4 * STEP).sample
    with pytest.raises(ValueError):
        windows.at(4, clock)
    manifest, rows = snapshot(inputs[:3] + inputs[4:])
    reader = CandleReplayReader(manifest, rows, config())
    windows = ReplayWindows(
        reader, DatasetRef("a" * 64, EVOLVED_DATASET_SCHEMA, len(rows))
    )
    clock = ReplayClock(START + 5 * STEP).sample
    with pytest.raises(ValueError):
        windows.at(3, clock)
