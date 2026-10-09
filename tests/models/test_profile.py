import resource

from scryntic.model_worker.controls import expected_limits
from scryntic.model_worker.profile import FORECAST_CPU, SYNTHETIC_CPU


def test_real_profile_is_separate_and_keeps_synthetic_bounds() -> None:
    assert expected_limits()["NPROC"] == [0, 0]
    assert expected_limits()["AS"] == [256 * 1024**2] * 2
    assert expected_limits(FORECAST_CPU)["AS"] == [8 * 1024**3] * 2
    assert expected_limits(FORECAST_CPU)["NPROC"] == [8, 8]
    assert SYNTHETIC_CPU.allow_threads is False
    assert FORECAST_CPU.allow_threads is True
    assert "/model" in FORECAST_CPU.readonly
    assert "/runtime" in FORECAST_CPU.readonly
    assert resource.RLIMIT_NPROC >= 0
