"""Immutable raw archive port; format identifiers never authorize code loading."""

from dataclasses import dataclass
from typing import Protocol

from scryntic.domain.identity import SchemaRef
from scryntic.domain.raw import IngestionId, RawRecord
from scryntic.domain.validation import digest, identifier, integer


@dataclass(frozen=True, slots=True)
class ArchiveLimits:
    max_records: int
    max_encoded_bytes: int
    max_decoded_bytes: int

    def __post_init__(self) -> None:
        for value in (self.max_records, self.max_encoded_bytes, self.max_decoded_bytes):
            integer(value, 1)


@dataclass(frozen=True, slots=True)
class RawSegment:
    sha256: str
    format: SchemaRef
    codec: str
    encoded_bytes: int
    decoded_bytes: int
    record_count: int

    def __post_init__(self) -> None:
        digest(self.sha256)
        identifier(self.codec)
        for value in (self.encoded_bytes, self.decoded_bytes, self.record_count):
            integer(value, 1)

    def require_within(self, limits: ArchiveLimits) -> None:
        if (
            self.encoded_bytes > limits.max_encoded_bytes
            or self.decoded_bytes > limits.max_decoded_bytes
            or self.record_count > limits.max_records
        ):
            raise ValueError("Raw segment exceeds local decoding limits")


@dataclass(frozen=True, slots=True)
class RawRecordRef:
    segment_sha256: str
    record_index: int
    identity: IngestionId

    def __post_init__(self) -> None:
        digest(self.segment_sha256)
        integer(self.record_index, 0)


class RawArchive(Protocol):
    async def seal(
        self, records: tuple[RawRecord, ...], limits: ArchiveLimits
    ) -> RawSegment:
        """Return only after an immutable segment is durable; publication is separate."""
        ...

    async def read(self, reference: RawRecordRef, limits: ArchiveLimits) -> RawRecord:
        """Verify bytes/identity under local limits; preserve original envelope evidence."""
        ...
