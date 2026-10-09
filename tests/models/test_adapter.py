import importlib
import os
import sys
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from scryntic.models.adapters import TimesFMAdapter, bounded_points
from scryntic.models.window import ForecastWindow
from tests.jobs.helpers import job


def test_vendor_adapter_refuses_coordinator_execution() -> None:
    request = job()
    window = ForecastWindow(request.forecast.dataset, (0, 1), (1.0, 2.0), 1)
    adapter = TimesFMAdapter()
    with pytest.raises(ValueError, match="worker"):
        adapter.infer(window, 2, 0)
    assert "torch" not in sys.modules


def test_output_conversion_is_bounded_and_primitive() -> None:
    assert bounded_points([[1.0, 2.0]], 2) == (1.0, 2.0)


@pytest.mark.parametrize(
    "values",
    [
        [[1.0]],
        [[1.0, float("inf")]],
        [[True, 2.0]],
        [[1.0, "2"]],
        [[1.0, 2.0], [3.0, 4.0]],
        [],
        "vendor-object",
    ],
)
def test_output_rejects_wrong_shapes_nonfinite_and_foreign_values(
    values: object,
) -> None:
    with pytest.raises(ValueError):
        bounded_points(values, 2)


@pytest.mark.parametrize("defect", [None, "cuda", "device", "shape", "nonfinite"])
def test_adapter_uses_only_fixed_offline_loader_and_deterministic_cpu_settings(
    monkeypatch: pytest.MonkeyPatch, defect: str | None
) -> None:
    # Control-flow unit test. Actual native/runtime evidence belongs to test_host.
    monkeypatch.setattr(os, "getpid", lambda: 1)
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    torch = MagicMock()
    torch.version.cuda = "unexpected" if defect == "cuda" else None
    torch.cuda.is_available.return_value = False
    torch.inference_mode.return_value = nullcontext()
    np = MagicMock()
    np.isfinite.return_value.all.return_value = defect != "nonfinite"
    points = MagicMock()
    points.shape = (2, 2) if defect == "shape" else (1, 2)
    points.tolist.return_value = [[1.0, 2.0]]
    model = MagicMock()
    model.model.device.type = "cuda" if defect == "device" else "cpu"
    model.forecast.return_value = (points, None)
    model_module = SimpleNamespace(TimesFM_2p5_200M_torch=MagicMock(return_value=model))
    config = SimpleNamespace(ForecastConfig=MagicMock())
    modules = {
        "numpy": np,
        "torch": torch,
        "timesfm.configs": config,
        "timesfm.timesfm_2p5.timesfm_2p5_torch": model_module,
    }
    monkeypatch.setattr(importlib, "import_module", lambda name: modules[name])
    request = job()
    window = ForecastWindow(request.forecast.dataset, (0, 1), (1.0, 2.0), 1)
    adapter = TimesFMAdapter()
    if defect is not None:
        with pytest.raises(ValueError):
            adapter.infer(window, 2, 7)
        return
    assert TimesFMAdapter().infer(window, 2, 7) == (1.0, 2.0)
    model.load_checkpoint.assert_called_once_with(
        "/model/model.safetensors", torch_compile=False
    )
    model.from_pretrained.assert_not_called()
    torch.manual_seed.assert_called_once_with(7)
    torch.set_num_threads.assert_called_once_with(1)
    torch.set_num_interop_threads.assert_called_once_with(1)
    torch.use_deterministic_algorithms.assert_called_once_with(True)
    assert config.ForecastConfig.call_args.kwargs["max_context"] == 512
    assert (
        config.ForecastConfig.call_args.kwargs["use_continuous_quantile_head"] is False
    )
