"""F19 finalized-candle features cannot silently consume open lag candles."""

from scryntic.replay.contracts import ReplayClock
from scryntic.replay.reader import CandleReplayReader
from tests.dataset.test_recipes import _received
from tests.dataset.test_selection import _source
from tests.replay.helpers import START, STEP, snapshot, sources
from tests.replay.test_reader import config


def test_nonfinal_lag_is_explicitly_excluded() -> None:
    inputs = sources()
    open_lag = _received(
        _source(1, finalized=False, close="100", start_ns=START), START + STEP
    )
    reader = CandleReplayReader(
        *snapshot((open_lag, *inputs[1:]), lag=1), config(mode="as-observed")
    )
    frame, reasons = reader.feature_result(1, ReplayClock(START + 2 * STEP).sample)
    assert frame is None
    assert reasons == ("lag-not-finalized",)
