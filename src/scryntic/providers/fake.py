"""Two deterministic local forecasts over validated F08 candle snapshots."""

from __future__ import annotations

from decimal import Decimal

from scryntic.application.providers import (
    ForecastPoint,
    ForecastRequest,
    ForecastResult,
    ModelIdentity,
    ProviderDescriptor,
)
from scryntic.dataset.snapshot import DATASET_SCHEMA, DatasetReader


def _descriptor(provider_id: str, revision: str) -> ProviderDescriptor:
    return ProviderDescriptor(
        provider_id=provider_id,
        model=ModelIdentity(
            origin="trusted-in-process-f09-fake",
            publisher="scryntic",
            revision=revision,
            license_id="internal-test-only",
            usage_policy_id="f09-fake-only",
            loading_requirements=(),
        ),
        capabilities=frozenset({"forecast"}),
        input_schemas=(DATASET_SCHEMA,),
        max_rows=1_024,
        max_horizon=24,
        execution="local",
    )


class _CandleFakeProvider:
    def __init__(self, reader: DatasetReader, descriptor: ProviderDescriptor) -> None:
        self._reader = reader
        self.descriptor = descriptor

    def _input(self, request: ForecastRequest) -> tuple[int, Decimal, Decimal]:
        self.descriptor.require(request)
        if request.target != "close" or request.covariates:
            raise ValueError("Fake providers support only univariate close forecasts")
        table = self._reader.read_table(request.dataset)
        if table.num_rows != request.dataset.row_count or table.num_rows < 2:
            raise ValueError("Fake provider requires at least two dataset rows")
        rows = table.select(["start_ns", "interval_ns", "close"]).to_pylist()
        if any(row["interval_ns"] != request.frequency_ns for row in rows):
            raise ValueError("Dataset frequency does not match request")
        if any(
            rows[index]["start_ns"] >= rows[index + 1]["start_ns"]
            for index in range(len(rows) - 1)
        ):
            raise ValueError("Dataset rows must be ordered by start time")
        previous, latest = rows[-2:]
        return latest["start_ns"], previous["close"], latest["close"]

    def _result(
        self, request: ForecastRequest, last_start: int, values: tuple[Decimal, ...]
    ) -> ForecastResult:
        return ForecastResult(
            request.request_id,
            request.dataset,
            self.descriptor.provider_id,
            self.descriptor.model.revision,
            tuple(
                ForecastPoint(
                    last_start + request.frequency_ns * (index + 1), float(value)
                )
                for index, value in enumerate(values)
            ),
        )


class PersistenceFakeProvider(_CandleFakeProvider):
    """Repeat the last close at every requested horizon point."""

    def __init__(self, reader: DatasetReader) -> None:
        super().__init__(reader, _descriptor("f09-persistence", "f09-persistence-v1"))

    async def forecast(self, request: ForecastRequest) -> ForecastResult:
        last_start, _, latest = self._input(request)
        return self._result(request, last_start, (latest,) * request.horizon)


class TrendFakeProvider(_CandleFakeProvider):
    """Extend the difference between the last two closes."""

    def __init__(self, reader: DatasetReader) -> None:
        super().__init__(reader, _descriptor("f09-trend", "f09-trend-v1"))

    async def forecast(self, request: ForecastRequest) -> ForecastResult:
        last_start, previous, latest = self._input(request)
        step = latest - previous
        return self._result(
            request,
            last_start,
            tuple(latest + step * (index + 1) for index in range(request.horizon)),
        )
