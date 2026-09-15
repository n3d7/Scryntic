"""Forecast-only provider contract; descriptors are claims, never admission authority."""

from dataclasses import dataclass
from itertools import pairwise
from math import isfinite
from typing import Literal, Protocol

from scryntic.domain.dataset import DatasetRef
from scryntic.domain.identity import CONTRACT_VERSION, SchemaRef, Version
from scryntic.domain.validation import digest, identifier, immutable_tuple, integer


@dataclass(frozen=True, slots=True, kw_only=True)
class ModelIdentity:
    origin: str
    publisher: str
    revision: str
    license_id: str | None
    usage_policy_id: str | None
    loading_requirements: tuple[str, ...]
    artifact_sha256: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        # Origin is descriptive, never a fetch URL or import spec to execute.
        if (
            not isinstance(self.origin, str)
            or not 1 <= len(self.origin) <= 1024
            or not self.origin.isprintable()
        ):
            raise ValueError("Expected bounded model origin")
        for value in (
            self.publisher,
            self.revision,
            self.license_id,
            self.usage_policy_id,
        ):
            if value is not None:
                identifier(value)
        immutable_tuple(self.loading_requirements, 64)
        for value in self.loading_requirements:
            identifier(value)
        immutable_tuple(self.artifact_sha256, 256)
        for value in self.artifact_sha256:
            digest(value)


@dataclass(frozen=True, slots=True, kw_only=True)
class ProviderDescriptor:
    provider_id: str
    model: ModelIdentity
    capabilities: frozenset[str]
    input_schemas: tuple[SchemaRef, ...]
    max_rows: int
    max_horizon: int
    execution: Literal["local", "remote"]
    contract_version: Version = CONTRACT_VERSION

    def __post_init__(self) -> None:
        identifier(self.provider_id)
        integer(self.max_rows, 1)
        integer(self.max_horizon, 1)
        if type(self.capabilities) is not frozenset or len(self.capabilities) > 64:
            raise TypeError("Expected bounded immutable provider capabilities")
        for value in self.capabilities:
            identifier(value)
        immutable_tuple(self.input_schemas, 64)
        if self.execution not in ("local", "remote"):
            raise ValueError("Unknown provider execution location")
        SchemaRef("provider-api", CONTRACT_VERSION).require_readable(
            SchemaRef("provider-api", self.contract_version)
        )

    def require(self, request: "ForecastRequest") -> None:
        schema_supported = False
        for schema in self.input_schemas:
            try:
                schema.require_readable(request.dataset.schema)
            except ValueError:
                continue
            schema_supported = True
        if (
            "forecast" not in self.capabilities
            or request.horizon > self.max_horizon
            or request.dataset.row_count > self.max_rows
            or not schema_supported
            or (request.covariates and "forecast-covariates" not in self.capabilities)
            or (self.execution == "remote" and not request.allow_remote)
        ):
            raise ValueError("Unsupported provider request")


@dataclass(frozen=True, slots=True, kw_only=True)
class ForecastRequest:
    request_id: str
    dataset: DatasetRef
    target: str
    frequency_ns: int
    horizon: int
    covariates: tuple[str, ...] = ()
    allow_remote: bool = False

    def __post_init__(self) -> None:
        identifier(self.request_id)
        identifier(self.target)
        integer(self.frequency_ns, 1)
        integer(self.horizon, 1)
        immutable_tuple(self.covariates, 256)
        for value in self.covariates:
            identifier(value)
        if type(self.allow_remote) is not bool:
            raise TypeError("Expected explicit remote disclosure intent")


@dataclass(frozen=True, slots=True)
class ForecastPoint:
    timestamp_ns: int
    value: float

    def __post_init__(self) -> None:
        integer(self.timestamp_ns)
        if type(self.value) is not float or not isfinite(self.value):
            raise ValueError("Expected a finite post-transformation forecast value")


@dataclass(frozen=True, slots=True)
class ForecastResult:
    request_id: str
    dataset: DatasetRef
    provider_id: str
    model_revision: str
    points: tuple[ForecastPoint, ...]

    def __post_init__(self) -> None:
        for value in (self.request_id, self.provider_id, self.model_revision):
            identifier(value)
        if type(self.points) is not tuple:
            raise TypeError("Expected immutable forecast points")
        if any(a.timestamp_ns >= b.timestamp_ns for a, b in pairwise(self.points)):
            raise ValueError("Forecast timestamps must strictly increase")

    def validate_for(
        self, request: ForecastRequest, provider: ProviderDescriptor
    ) -> None:
        provider.require(request)
        if (
            self.request_id != request.request_id
            or self.dataset != request.dataset
            or self.provider_id != provider.provider_id
            or self.model_revision != provider.model.revision
            or len(self.points) != request.horizon
        ):
            raise ValueError("Forecast result does not match its bounded request")
        if any(
            b.timestamp_ns - a.timestamp_ns != request.frequency_ns
            for a, b in pairwise(self.points)
        ):
            raise ValueError("Forecast frequency does not match request")


class ForecastProvider(Protocol):
    @property
    def descriptor(self) -> ProviderDescriptor: ...

    async def forecast(self, request: ForecastRequest) -> ForecastResult: ...
