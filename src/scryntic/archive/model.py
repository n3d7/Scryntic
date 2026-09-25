"""Owned descriptors for F07 immutable archive objects and partition views."""

import re
from dataclasses import dataclass
from enum import StrEnum

from scryntic.domain.identity import SchemaRef
from scryntic.domain.validation import digest, identifier, integer


class ArchiveRole(StrEnum):
    RAW = "raw"
    NORMALIZED = "normalized"


@dataclass(frozen=True, slots=True)
class ArchiveObject:
    role: ArchiveRole
    sha256: str
    format: SchemaRef
    codec: str
    encoded_bytes: int
    decoded_bytes: int
    record_count: int

    def __post_init__(self) -> None:
        if not isinstance(self.role, ArchiveRole):
            raise TypeError("Expected an archive role")
        digest(self.sha256)
        identifier(self.codec)
        for value in (self.encoded_bytes, self.decoded_bytes, self.record_count):
            integer(value, 1)


@dataclass(frozen=True, slots=True)
class Partition:
    source: str
    event_family: str
    utc_date: str

    def __post_init__(self) -> None:
        identifier(self.source)
        if self.event_family != "candle":
            raise ValueError("F07 supports only the candle event family")
        if re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", self.utc_date) is None:
            raise ValueError("Expected a canonical UTC date")
