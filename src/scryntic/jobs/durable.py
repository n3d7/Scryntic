"""Shared typed durable identities; forecast and research codecs stay separate."""

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from scryntic.domain.validation import identifier, integer

if TYPE_CHECKING:
    from scryntic.jobs.contracts import JobState


class DurableRequest(Protocol):
    @property
    def job_id(self) -> str: ...
    @property
    def sha256(self) -> str: ...
    @property
    def submitted_ns(self) -> int: ...
    @property
    def deadline_ns(self) -> int: ...


class DurableArtifact(Protocol):
    @property
    def sha256(self) -> str: ...


@dataclass(frozen=True, slots=True)
class DurableAttempt[Request: DurableRequest]:
    job: Request
    number: int
    token: str

    def __post_init__(self) -> None:
        integer(self.number, 1)
        identifier(self.token)
        if self.number > 3 or len(self.token) != 32:
            raise ValueError("Invalid job attempt fence")


@dataclass(frozen=True, slots=True)
class DurableRecord[Request: DurableRequest, ArtifactT: DurableArtifact]:
    job: Request
    state: "JobState"
    attempt: int
    token: str | None
    artifact: ArtifactT | None
    reason: str | None
    updated_ns: int

    def execution(self) -> DurableAttempt[Request]:
        from scryntic.jobs.contracts import JobState

        if self.state is not JobState.RUNNING or self.token is None:
            raise ValueError("Job has no active attempt")
        return DurableAttempt(self.job, self.attempt, self.token)
