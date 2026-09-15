"""Immutable logical dataset references; a resolver owns manifests and files."""

from dataclasses import dataclass

from scryntic.domain.identity import SchemaRef
from scryntic.domain.validation import digest, integer


@dataclass(frozen=True, slots=True)
class DatasetRef:
    manifest_sha256: str
    schema: SchemaRef
    row_count: int

    def __post_init__(self) -> None:
        digest(self.manifest_sha256)
        integer(self.row_count, 0)
