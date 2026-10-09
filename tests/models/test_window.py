from dataclasses import replace

import pytest

from scryntic.models.window import ForecastWindow
from tests.jobs.helpers import job


def test_window_binds_history_to_f20_verified_inputs() -> None:
    attempt = job()
    window = ForecastWindow(
        attempt.forecast.dataset,
        (
            attempt.inputs.verified.last_start_ns - attempt.forecast.frequency_ns,
            attempt.inputs.verified.last_start_ns,
        ),
        (1.0, 2.0),
        attempt.forecast.frequency_ns,
    )
    window.validate(attempt)
    changed_close = replace(window, closes=(1.0, 9.0))
    changed_origin = replace(
        window, starts=tuple(t + window.frequency_ns for t in window.starts)
    )
    with pytest.raises(ValueError):
        changed_close.validate(attempt)
    with pytest.raises(ValueError):
        changed_origin.validate(attempt)
    assert ForecastWindow.from_projection(window.projection(), attempt) == window


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"starts": [], "closes": []},
        {"starts": [0, 1], "closes": ["1.00", "2.0"]},
        {"starts": [0, 1], "closes": [True, "2.0"]},
        {"starts": [0, 1], "closes": ["nan", "2.0"]},
        {"starts": [0, 1], "closes": ["1.0", "2.0"], "label": "3.0"},
    ],
)
def test_worker_rejects_invalid_or_target_bearing_history(
    payload: dict[str, object],
) -> None:
    request = job()
    with pytest.raises(ValueError):
        ForecastWindow.from_projection(payload, request)


@pytest.mark.parametrize(
    "values", [(float("nan"), 2.0), (True, 2.0), (1.0,), (1e40, 2.0)]
)
def test_window_rejects_unbounded_or_non_numeric_inputs(
    values: tuple[float, ...],
) -> None:
    request = job()
    window = ForecastWindow(request.forecast.dataset, (0, 1), values, 1)
    with pytest.raises(ValueError):
        window.validate(request)
