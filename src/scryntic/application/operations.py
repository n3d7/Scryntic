"""Interface-neutral command contracts shared by CLI and terminal UI."""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

MAX_RESULT_BYTES = 1024 * 1024


class Operation(StrEnum):
    CONFIG_VALIDATE = "config.validate"
    SOURCES_LIST = "sources.list"
    SOURCES_INSPECT = "sources.inspect"
    COLLECT = "collect"
    BACKFILL = "backfill"
    STATUS = "status"
    SYNC_STATUS = "sync.status"
    SYNC_ENROLL = "sync.enroll"
    SYNC_PULL = "sync.pull"
    DATA_INSPECT = "data.inspect"
    DATASET_BUILD = "dataset.build"
    DATASET_INSPECT = "dataset.inspect"
    DATASET_EXPORT = "dataset.export"
    REPLAY = "replay"
    PROVIDERS_LIST = "providers.list"
    ANALYZE = "analyze"
    JOBS_SUBMIT = "jobs.submit"
    JOBS_LIST = "jobs.list"
    JOBS_STATUS = "jobs.status"
    JOBS_RUN = "jobs.run"
    JOBS_RESUME = "jobs.resume"
    JOBS_CANCEL = "jobs.cancel"
    JOBS_RESULT = "jobs.result"
    FORECASTS_STATUS = "forecasts.status"
    FORECASTS_INSPECT = "forecasts.inspect"
    FORECASTS_COMPARE = "forecasts.compare"
    FORECASTS_RUN = "forecasts.run"
    FORECASTS_MATURE = "forecasts.mature"


class ErrorCode(StrEnum):
    INVALID = "invalid_request"
    CONFIGURATION = "configuration_invalid"
    UNSUPPORTED = "unsupported_capability"
    UNAVAILABLE = "service_unavailable"
    BUSY = "resource_busy"
    FAILED = "operation_failed"
    INCOMPLETE = "operation_incomplete"
    CANCELLED = "cancelled"


_MESSAGES = {
    ErrorCode.INVALID: "Invalid or unsupported request fields.",
    ErrorCode.CONFIGURATION: "Configuration or credential policy requires correction.",
    ErrorCode.UNSUPPORTED: "The selected capability is not supported.",
    ErrorCode.UNAVAILABLE: "Required service or immutable artifact is unavailable.",
    ErrorCode.BUSY: "The resource is owned by another operation.",
    ErrorCode.FAILED: "The operation failed; durable checkpoints are preserved.",
    ErrorCode.INCOMPLETE: "The operation is incomplete; inspect its durable state.",
    ErrorCode.CANCELLED: "The operation was cancelled after owned work settled.",
}


class ApplicationError(RuntimeError):
    """Only fixed diagnostics cross an interface boundary."""

    def __init__(self, code: ErrorCode) -> None:
        if type(code) is not ErrorCode:
            raise TypeError("Expected application error code")
        self.code = code
        super().__init__(_MESSAGES[code])

    @property
    def exit_code(self) -> int:
        if self.code is ErrorCode.INVALID:
            return 2
        if self.code is ErrorCode.CONFIGURATION:
            return 3
        if self.code is ErrorCode.UNSUPPORTED:
            return 4
        if self.code in (ErrorCode.UNAVAILABLE, ErrorCode.BUSY):
            return 5
        if self.code is ErrorCode.INCOMPLETE:
            return 7
        if self.code is ErrorCode.CANCELLED:
            return 130
        return 6


class Request(Protocol):
    @property
    def operation(self) -> Operation: ...


@dataclass(frozen=True, slots=True)
class ApplicationResult:
    operation: Operation
    payload: bytes
    complete: bool = True

    def __post_init__(self) -> None:
        if type(self.operation) is not Operation or type(self.complete) is not bool:
            raise TypeError("Expected typed application result")
        if type(self.payload) is not bytes or len(self.payload) > MAX_RESULT_BYTES:
            raise ValueError("Application result exceeds its bounded range")


class Services(Protocol):
    async def execute(self, request: Request) -> ApplicationResult: ...

    async def cancel(self) -> None: ...

    async def aclose(self) -> None: ...


@dataclass(frozen=True, slots=True)
class Field:
    name: str
    label: str
    default: str = ""
    required: bool = False
    choices: tuple[str, ...] = ()
    help: str = ""


class Workflow:
    __slots__ = ("operation", "title", "group", "fields", "build", "confirmation")

    def __init__(
        self,
        operation: Operation,
        title: str,
        group: str,
        fields: tuple[Field, ...],
        build: Callable[[dict[str, str]], Request],
        confirmation: str | None = None,
    ) -> None:
        self.operation = operation
        self.title = title
        self.group = group
        self.fields = fields
        self.build = build
        self.confirmation = confirmation

    def request(self, parameters: Mapping[str, str]) -> Request:
        allowed = {field.name for field in self.fields}
        if set(parameters) - allowed:
            raise ApplicationError(ErrorCode.INVALID)
        values: dict[str, str] = {}
        for field in self.fields:
            value = parameters.get(field.name, field.default)
            if not isinstance(value, str) or len(value.encode()) > 65536:
                raise ApplicationError(ErrorCode.INVALID)
            if field.required and not value:
                raise ApplicationError(ErrorCode.INVALID)
            if field.choices and value not in field.choices:
                raise ApplicationError(ErrorCode.INVALID)
            values[field.name] = value
        try:
            result = self.build(values)
        except (TypeError, ValueError, KeyError, OverflowError):
            raise ApplicationError(ErrorCode.INVALID) from None
        if result.operation is not self.operation:
            raise ApplicationError(ErrorCode.INVALID)
        return result
