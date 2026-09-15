"""Opaque bounded input, separate from the identity assigned by durable ingestion."""

from dataclasses import InitVar, dataclass, field
from hashlib import sha256

from scryntic.domain.identity import SubjectId
from scryntic.domain.time import ClockSample, SourceTime
from scryntic.domain.validation import identifier, integer


@dataclass(frozen=True, slots=True)
class IngestionId:
    producer: str
    epoch: str
    offset: int

    def __post_init__(self) -> None:
        identifier(self.producer)
        identifier(self.epoch)
        integer(self.offset, 0)


@dataclass(frozen=True, slots=True, kw_only=True)
class RawEnvelope:
    source: str
    stream: str
    channel: str
    adapter_version: str
    receipt: ClockSample
    payload: bytes = field(repr=False)
    payload_limit: InitVar[int]
    subject: SubjectId | None = None
    source_time: SourceTime | None = None
    source_event_id: str | None = None
    source_sequence: int | None = None
    content_sha256: str = field(init=False)

    def __post_init__(self, payload_limit: int) -> None:
        integer(payload_limit, 1)
        if type(self.payload) is not bytes:
            raise TypeError("Raw payload must be immutable bytes")
        if len(self.payload) > payload_limit:
            raise ValueError("Raw payload exceeds the local payload limit")
        for value in (self.source, self.stream, self.channel, self.adapter_version):
            identifier(value)
        if self.source_event_id is not None:
            identifier(self.source_event_id)
        if self.source_sequence is not None:
            integer(self.source_sequence, 0)
        object.__setattr__(self, "content_sha256", sha256(self.payload).hexdigest())


@dataclass(frozen=True, slots=True)
class RawRecord:
    identity: IngestionId
    envelope: RawEnvelope
