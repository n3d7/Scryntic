"""Focused inference adapters. Constructors run only inside the fixed worker."""

import importlib
import os
from math import isfinite
from typing import Any, Protocol

from scryntic.models.window import ForecastWindow


class ForecastAdapter(Protocol):
    def infer(
        self, window: ForecastWindow, horizon: int, seed: int
    ) -> tuple[float, ...]: ...


class TimesFMAdapter:
    def infer(
        self, window: ForecastWindow, horizon: int, seed: int
    ) -> tuple[float, ...]:
        if os.getpid() != 1 or os.environ.get("HF_HUB_OFFLINE") != "1":
            raise ValueError("Vendor inference requires the qualified worker")
        # These literal dependencies belong to the separate reviewed runtime;
        # they are intentionally absent from the coordinator/collector profile.
        np = importlib.import_module("numpy")
        torch = importlib.import_module("torch")
        config_module = importlib.import_module("timesfm.configs")
        model_module = importlib.import_module("timesfm.timesfm_2p5.timesfm_2p5_torch")

        if torch.version.cuda is not None or torch.cuda.is_available():
            raise ValueError("CPU runtime unexpectedly exposes CUDA")
        torch.set_num_threads(1)
        torch.set_num_interop_threads(1)
        torch.manual_seed(seed)
        torch.use_deterministic_algorithms(True)
        torch.set_float32_matmul_precision("highest")
        model = model_module.TimesFM_2p5_200M_torch(torch_compile=False)
        # Deliberately bypass from_pretrained and its Hub/config lookup.
        model.load_checkpoint("/model/model.safetensors", torch_compile=False)
        if model.model.device.type != "cpu":
            raise ValueError("Unexpected inference device")
        model.compile(
            config_module.ForecastConfig(
                max_context=512,
                max_horizon=128,
                normalize_inputs=True,
                per_core_batch_size=1,
                use_continuous_quantile_head=False,
                force_flip_invariance=False,
                infer_is_positive=True,
                fix_quantile_crossing=False,
            )
        )
        with torch.inference_mode():
            points, _ = model.forecast(
                horizon=horizon, inputs=[np.asarray(window.closes, dtype=np.float32)]
            )
        if points.shape != (1, horizon) or not np.isfinite(points).all():
            raise ValueError("Model returned invalid forecast dimensions or values")
        return bounded_points(points.tolist(), horizon)


def bounded_points(value: Any, horizon: int) -> tuple[float, ...]:
    if (
        type(horizon) is not int
        or not 1 <= horizon <= 24
        or type(value) is not list
        or len(value) != 1
    ):
        raise ValueError("Invalid bounded point forecast")
    values = value[0]
    if (
        type(values) is not list
        or len(values) != horizon
        or any(type(v) is not float or not isfinite(v) for v in values)
    ):
        raise ValueError("Invalid bounded point forecast")
    return tuple(values)
