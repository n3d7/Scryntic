"""Optional source capabilities; no source is required to imitate an exchange."""

from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import InitVar, dataclass, field
from enum import StrEnum
from typing import Literal, Protocol, runtime_checkable

from scryntic.domain.identity import (
    CONTRACT_VERSION,
    InstrumentId,
    SchemaRef,
    SubjectId,
    Version,
)
from scryntic.domain.raw import RawEnvelope
from scryntic.domain.time import ClockSample
from scryntic.domain.validation import identifier, immutable_tuple, integer

BYBIT_CANDLE_SCHEMA = SchemaRef("bybit_candle", Version(1, 0))


class SourceOperation(StrEnum):
    STREAM = "stream"
    HISTORY = "history"
    DISCOVER = "discover"


@dataclass(frozen=True, slots=True)
class SourceCapability:
    schema: SchemaRef
    subject_kind: str
    category: str | None
    operations: frozenset[SourceOperation]
    sequencing: Literal["none", "source-sequence"]
    recovery: Literal["backfill", "resnapshot", "coverage-loss"]
    history_start_ns: int | None = None
    history_end_ns: int | None = None

    def __post_init__(self) -> None:
        identifier(self.subject_kind)
        if self.category is not None:
            identifier(self.category)
        if self.subject_kind != "instrument" and self.category is not None:
            raise ValueError("Only instruments carry market categories")
        if type(self.operations) is not frozenset or any(
            not isinstance(value, SourceOperation) for value in self.operations
        ):
            raise TypeError("Expected immutable source operations")
        if self.sequencing not in ("none", "source-sequence"):
            raise ValueError("Unsupported sequencing policy")
        if self.recovery not in ("backfill", "resnapshot", "coverage-loss"):
            raise ValueError("Unsupported recovery policy")
        for value in (self.history_start_ns, self.history_end_ns):
            if value is not None:
                integer(value)
        if (
            self.history_start_ns is not None
            and self.history_end_ns is not None
            and self.history_start_ns >= self.history_end_ns
        ):
            raise ValueError("Empty historical range")


@dataclass(frozen=True, slots=True)
class SourceDescriptor:
    source_id: str
    adapter_version: str
    capabilities: tuple[SourceCapability, ...]
    max_payload_bytes: int
    max_page_records: int
    contract_version: Version = CONTRACT_VERSION

    def __post_init__(self) -> None:
        identifier(self.source_id)
        identifier(self.adapter_version)
        immutable_tuple(self.capabilities, 256)
        integer(self.max_payload_bytes, 1)
        integer(self.max_page_records, 1)
        SchemaRef("source-api", CONTRACT_VERSION).require_readable(
            SchemaRef("source-api", self.contract_version)
        )

    def require(
        self, schema: SchemaRef, operation: SourceOperation, subject: SubjectId | None
    ) -> SourceCapability:
        if isinstance(subject, InstrumentId):
            kind = "instrument"
        elif subject:
            kind = subject.kind
        else:
            kind = "none"
        category = subject.category if isinstance(subject, InstrumentId) else None
        for capability in self.capabilities:
            if (
                operation not in capability.operations
                or capability.subject_kind != kind
                or capability.category != category
            ):
                continue
            try:
                capability.schema.require_readable(schema)
            except ValueError:
                continue
            return capability
        raise ValueError("Unsupported source capability")


@dataclass(frozen=True, slots=True)
class StreamRequest:
    schema: SchemaRef
    subject: SubjectId | None


@dataclass(frozen=True, slots=True)
class HistoryRequest:
    stream: StreamRequest
    start_ns: int
    end_ns: int
    page_size: int
    cursor: bytes | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        integer(self.start_ns)
        integer(self.end_ns)
        integer(self.page_size, 1)
        if self.start_ns >= self.end_ns:
            raise ValueError("Empty historical range")
        _cursor(self.cursor)


def _cursor(value: bytes | None) -> None:
    if value is not None and (type(value) is not bytes or len(value) > 4096):
        raise ValueError("Expected bounded opaque cursor bytes")


@dataclass(frozen=True, slots=True)
class RawPage:
    envelopes: tuple[RawEnvelope, ...]
    next_cursor: bytes | None = field(repr=False)
    record_limit: InitVar[int]

    def __post_init__(self, record_limit: int) -> None:
        integer(record_limit, 1)
        immutable_tuple(self.envelopes, record_limit)
        _cursor(self.next_cursor)


class SourceAdapter(Protocol):
    @property
    def descriptor(self) -> SourceDescriptor: ...

    async def close(self) -> None: ...


class StreamingSource(SourceAdapter, Protocol):
    def stream(self, request: StreamRequest) -> AsyncIterator[RawEnvelope]: ...


class HistoricalSource(SourceAdapter, Protocol):
    async def fetch(self, request: HistoryRequest) -> RawPage: ...


@dataclass(frozen=True, slots=True)
class SourceLoss:
    reason: Literal["disconnect", "overflow", "malformed", "stall"]
    detected: ClockSample

    def __post_init__(self) -> None:
        if self.reason not in ("disconnect", "overflow", "malformed", "stall"):
            raise ValueError("Invalid source loss reason")


class SourceBufferLease(Protocol):
    def release(self) -> None: ...


@runtime_checkable
class LossReportingSource(StreamingSource, Protocol):
    """Optional notification before retry; failure to record loss stops intake."""

    def set_loss_handler(
        self, handler: Callable[[SourceLoss], Awaitable[None]]
    ) -> None: ...

    def set_start_gate(self, gate: Callable[[], bool]) -> None: ...

    def set_buffer_admission(
        self, acquire: Callable[[int], SourceBufferLease | None]
    ) -> None: ...


class SourceTransientError(RuntimeError):
    """Safe retryable public failure; configuration/schema errors are separate."""


class SourceConfigurationError(ValueError):
    """Retry needs local configuration correction, not a reconnect loop."""


@runtime_checkable
class ResnapshotSource(StreamingSource, Protocol):
    """Optional family action; the adapter must document its continuity proof."""

    async def request_snapshot(self) -> None: ...
