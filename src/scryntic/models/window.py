"""Model-independent, bounded history; no labels or vendor objects."""

from dataclasses import dataclass
from math import isfinite
from typing import Any

from scryntic.archive.canonical import JsonObject
from scryntic.domain.dataset import DatasetRef
from scryntic.jobs.contracts import JobRequest

MAX_CONTEXT = 512


@dataclass(frozen=True, slots=True)
class ForecastWindow:
    dataset: DatasetRef
    starts: tuple[int, ...]
    closes: tuple[float, ...]
    frequency_ns: int

    def validate(self, job: JobRequest) -> None:
        if (
            self.dataset != job.forecast.dataset
            or type(self.starts) is not tuple
            or type(self.closes) is not tuple
            or not 2 <= len(self.starts) == len(self.closes) <= MAX_CONTEXT
            or len(self.starts) > self.dataset.row_count
            or self.frequency_ns != job.forecast.frequency_ns
            or any(type(t) is not int or not -(2**63) <= t < 2**63 for t in self.starts)
            or any(
                b - a != self.frequency_ns
                for a, b in zip(self.starts, self.starts[1:], strict=False)
            )
            or any(
                type(v) is not float or not isfinite(v) or abs(v) > 1e20
                for v in self.closes
            )
            or self.starts[-1] != job.inputs.verified.last_start_ns
            or self.closes[-2:] != job.inputs.closes
        ):
            raise ValueError(
                "Forecast history does not match bounded verified job inputs"
            )

    def projection(self) -> JsonObject:
        return {"starts": list(self.starts), "closes": [repr(v) for v in self.closes]}

    @classmethod
    def from_projection(cls, value: Any, job: JobRequest) -> "ForecastWindow":
        if type(value) is not dict or set(value) != {"starts", "closes"}:
            raise ValueError("Invalid model history fields")
        starts, closes = value["starts"], value["closes"]
        if (
            type(starts) is not list
            or type(closes) is not list
            or len(starts) > MAX_CONTEXT
            or len(closes) > MAX_CONTEXT
        ):
            raise ValueError("Unbounded model history")
        if any(type(v) is not str or len(v) > 64 for v in closes):
            raise ValueError("Invalid model history values")
        floats = tuple(float(v) for v in closes)
        if [repr(v) for v in floats] != closes:
            raise ValueError("Noncanonical model history")
        result = cls(
            job.forecast.dataset, tuple(starts), floats, job.forecast.frequency_ns
        )
        result.validate(job)
        return result
