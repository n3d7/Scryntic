"""Application-owned forecast validation before immutable artifact acceptance."""

from dataclasses import dataclass
from typing import Protocol

from scryntic.application.providers import (
    ForecastPoint,
    ForecastProvider,
    ForecastRequest,
    ForecastResult,
    ProviderDescriptor,
)
from scryntic.domain.dataset import DatasetRef
from scryntic.domain.validation import digest, identifier, integer


@dataclass(frozen=True, slots=True)
class VerifiedDataset:
    """Dataset facts read and checked against an exact DatasetRef."""

    reference: DatasetRef
    last_start_ns: int
    interval_ns: int

    def __post_init__(self) -> None:
        if not isinstance(self.reference, DatasetRef) or self.reference.row_count < 1:
            raise ValueError("Analysis requires a nonempty dataset reference")
        integer(self.last_start_ns)
        integer(self.interval_ns, 1)


@dataclass(frozen=True, slots=True)
class ForecastArtifactRef:
    sha256: str
    dataset: DatasetRef
    provider_id: str
    model_revision: str

    def __post_init__(self) -> None:
        digest(self.sha256)
        if not isinstance(self.dataset, DatasetRef):
            raise TypeError("Expected dataset reference")
        identifier(self.provider_id)
        identifier(self.model_revision)


@dataclass(frozen=True, slots=True)
class ValidatedForecast:
    request: ForecastRequest
    dataset: VerifiedDataset
    descriptor: ProviderDescriptor
    result: ForecastResult

    def validate(self) -> None:
        if self.descriptor.execution != "local":
            raise ValueError("F09 accepts only trusted local fake providers")
        self.validate_result()

    def validate_result(self) -> None:
        """Shared application-owned checks; callers separately authorize execution."""
        if self.request.target != "close" or self.request.covariates:
            raise ValueError("F09 supports only the close target without covariates")
        if self.dataset.reference != self.request.dataset:
            raise ValueError("Forecast request does not bind the verified dataset")
        if self.dataset.interval_ns != self.request.frequency_ns:
            raise ValueError("Forecast frequency disagrees with dataset interval")
        self.descriptor.require(self.request)
        if type(self.result) is not ForecastResult:
            raise TypeError("Expected a forecast result value")
        if (
            type(self.result.points) is not tuple
            or len(self.result.points) != self.request.horizon
            or len(self.result.points) > self.descriptor.max_horizon
        ):
            raise ValueError("Provider point count exceeds the request or capability")
        for point in self.result.points:
            if type(point) is not ForecastPoint:
                raise TypeError("Expected forecast point values")
            ForecastPoint(point.timestamp_ns, point.value)
        self.result.validate_for(self.request, self.descriptor)
        expected_start = self.dataset.last_start_ns + self.request.frequency_ns
        if self.result.points[0].timestamp_ns != expected_start:
            raise ValueError("Forecast origin disagrees with selected dataset")


class ForecastArtifactWriter(Protocol):
    def write(self, value: ValidatedForecast) -> ForecastArtifactRef: ...


class ForecastAnalysis:
    """Invoke a provider through the port, then accept only a validated result."""

    def __init__(
        self, provider: ForecastProvider, writer: ForecastArtifactWriter
    ) -> None:
        self._provider = provider
        self._writer = writer

    async def run(
        self, request: ForecastRequest, dataset: VerifiedDataset
    ) -> ForecastArtifactRef:
        descriptor = self._provider.descriptor
        if not isinstance(descriptor, ProviderDescriptor):
            raise TypeError("Expected provider descriptor")
        if request.dataset != dataset.reference:
            raise ValueError("Forecast request does not bind the verified dataset")
        descriptor.require(request)
        raw_result = await self._provider.forecast(request)
        if type(raw_result) is not ForecastResult:
            raise TypeError("Expected a forecast result value")
        if (
            type(raw_result.points) is not tuple
            or len(raw_result.points) != request.horizon
            or len(raw_result.points) > descriptor.max_horizon
        ):
            raise ValueError("Provider point count exceeds the request or capability")
        if any(type(point) is not ForecastPoint for point in raw_result.points):
            raise TypeError("Expected forecast point values")
        result = ForecastResult(
            raw_result.request_id,
            raw_result.dataset,
            raw_result.provider_id,
            raw_result.model_revision,
            tuple(
                ForecastPoint(point.timestamp_ns, point.value)
                for point in raw_result.points
            ),
        )
        accepted = ValidatedForecast(request, dataset, descriptor, result)
        accepted.validate()
        return self._writer.write(accepted)
