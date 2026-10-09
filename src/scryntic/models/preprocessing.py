"""Forecast history from F19's hindsight-free reader, without imputation."""

from collections.abc import Mapping
from decimal import Decimal
from types import MappingProxyType

from scryntic.application.analysis import VerifiedDataset
from scryntic.application.providers import ForecastRequest
from scryntic.domain.dataset import DatasetRef
from scryntic.domain.time import ClockSample
from scryntic.jobs.contracts import ForecastInputs, JobRequest
from scryntic.models.window import MAX_CONTEXT, ForecastWindow
from scryntic.replay.reader import CandleReplayReader


class ReplayWindows:
    def __init__(self, reader: CandleReplayReader, reference: DatasetRef) -> None:
        if reference.row_count != len(reader.rows):
            raise ValueError(
                "Forecast reference differs from validated replay snapshot"
            )
        self._reader = reader
        self._reference = reference
        self._indexes = {
            (reader.series(i), row["start_ns"]): i for i, row in enumerate(reader.rows)
        }

    def at(self, index: int, decision: ClockSample) -> ForecastWindow:
        reader = self._reader
        if type(index) is not int or not 0 <= index < len(reader.rows):
            raise ValueError("Unknown forecast origin")
        row = reader.rows[index]
        series = reader.series(index)
        starts: list[int] = []
        values: list[float] = []
        for step in range(MAX_CONTEXT):
            start = row["start_ns"] - step * row["interval_ns"]
            candidate = self._indexes.get((series, start))
            if candidate is None:
                break
            frame, _ = reader.feature_result(candidate, decision)
            if frame is None:
                break
            # Explicit float32-compatible bounded conversion. Labels/derived
            # targets never enter this window; normalization is window-local.
            value = frame.values[0]
            if abs(value) > Decimal("1e20"):
                raise ValueError("Forecast close exceeds numerical bounds")
            starts.append(start)
            values.append(float(value))
        if len(starts) < 2:
            raise ValueError("Insufficient contiguous observable forecast history")
        return ForecastWindow(
            self._reference,
            tuple(reversed(starts)),
            tuple(reversed(values)),
            row["interval_ns"],
        )


class ReplayJobInputs:
    """Frozen application-owned windows serve both F20 verification and adapters."""

    def __init__(self, windows: Mapping[str, ForecastWindow]) -> None:
        if not 1 <= len(windows) <= 1024:
            raise ValueError("Unbounded replay forecast plan")
        self._windows = MappingProxyType(dict(windows))

    def read(self, request: ForecastRequest) -> ForecastInputs:
        window = self._windows.get(request.request_id)
        if (
            window is None
            or request.dataset != window.dataset
            or request.frequency_ns != window.frequency_ns
        ):
            raise ValueError("Unknown or changed replay forecast request")
        result = ForecastInputs(
            VerifiedDataset(window.dataset, window.starts[-1], window.frequency_ns),
            window.closes[-2:],
        )
        result.validate(request)
        return result

    def window(self, job: JobRequest) -> ForecastWindow:
        if self.read(job.forecast) != job.inputs:
            raise ValueError("Replay inputs changed after admission")
        result = self._windows[job.job_id]
        result.validate(job)
        return result
