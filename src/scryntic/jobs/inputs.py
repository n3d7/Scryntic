"""Read approved F18 primitives through its existing restricted decoder boundary."""

from decimal import Decimal
from typing import Protocol

from scryntic.application.analysis import VerifiedDataset
from scryntic.application.providers import ForecastRequest
from scryntic.dataset.service import DatasetService
from scryntic.jobs.contracts import ForecastInputs


class InputReader(Protocol):
    def read(self, request: ForecastRequest) -> ForecastInputs: ...


class F18Inputs:
    def __init__(self, datasets: DatasetService) -> None:
        self._datasets = datasets

    def read(self, request: ForecastRequest) -> ForecastInputs:
        if (
            not 2 <= request.dataset.row_count <= 1024
            or request.target != "close"
            or request.covariates
        ):
            raise ValueError("F20 requires bounded candle close inputs")
        self._datasets.pins(request.dataset)
        rows = self._datasets.inspect(
            request.dataset, offset=request.dataset.row_count - 2, limit=2
        )
        first, last = rows
        if (
            any(
                not row["finalized"] or row["interval_ns"] != request.frequency_ns
                for row in rows
            )
            or first["start_ns"] + request.frequency_ns != last["start_ns"]
            or any(first[key] != last[key] for key in ("venue", "category", "symbol"))
        ):
            raise ValueError("F20 fake inputs require contiguous finalized candles")
        values = tuple(float(Decimal(row["close"])) for row in rows)
        inputs = ForecastInputs(
            VerifiedDataset(request.dataset, last["start_ns"], request.frequency_ns),
            values,
        )
        inputs.validate(request)
        return inputs
