"""Small interface-neutral application requests/results for the early slice."""

from dataclasses import dataclass

from scryntic.application.sources import StreamRequest
from scryntic.domain.dataset import DatasetRef
from scryntic.domain.identity import SchemaRef
from scryntic.domain.raw import IngestionId
from scryntic.domain.validation import digest, identifier, immutable_tuple


@dataclass(frozen=True, slots=True)
class CollectRequest:
    source_id: str
    stream: StreamRequest

    def __post_init__(self) -> None:
        identifier(self.source_id)


@dataclass(frozen=True, slots=True)
class CollectResult:
    source_id: str
    accepted_through: IngestionId | None

    def __post_init__(self) -> None:
        identifier(self.source_id)


@dataclass(frozen=True, slots=True)
class BuildDatasetRequest:
    input_manifests: tuple[str, ...]
    recipe: SchemaRef

    def __post_init__(self) -> None:
        immutable_tuple(self.input_manifests, 4096)
        if not self.input_manifests:
            raise ValueError("Dataset selection needs input manifests")
        for value in self.input_manifests:
            digest(value)


@dataclass(frozen=True, slots=True)
class BuildDatasetResult:
    dataset: DatasetRef
