"""Versioned provider jobs bind admission and exact application-owned inputs."""

from dataclasses import dataclass
from enum import StrEnum
from math import isfinite

from scryntic.application.analysis import ForecastArtifactRef, VerifiedDataset
from scryntic.application.providers import ForecastRequest
from scryntic.domain.identity import SchemaRef, Version
from scryntic.domain.validation import identifier, integer
from scryntic.jobs.admission import AdmissionPolicy, ModelReview, fingerprint

JOB_SCHEMA = SchemaRef("scryntic.provider-job", Version(1, 0))
RESPONSE_SCHEMA = SchemaRef("scryntic.provider-response", Version(1, 0))
MAX_JOB_BYTES = 65_536
MAX_RESPONSE_BYTES = 65_536
MAX_DURATION_NS = 300_000_000_000
MAX_ATTEMPTS = 3


class JobState(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    INTERRUPTED = "interrupted"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    DEADLINE_EXPIRED = "deadline_expired"


TERMINAL = frozenset(
    {JobState.SUCCEEDED, JobState.FAILED, JobState.CANCELLED, JobState.DEADLINE_EXPIRED}
)


@dataclass(frozen=True, slots=True)
class ForecastInputs:
    verified: VerifiedDataset
    closes: tuple[float, ...]

    def validate(self, request: ForecastRequest) -> None:
        if (
            self.verified.reference != request.dataset
            or self.verified.interval_ns != request.frequency_ns
            or request.dataset.row_count < 2
            or type(self.closes) is not tuple
            or len(self.closes) != 2
            or any(
                type(value) is not float or not isfinite(value) for value in self.closes
            )
            or not -(2**63)
            <= self.verified.last_start_ns
            <= 2**63 - 1 - request.horizon * request.frequency_ns
        ):
            raise ValueError("Invalid or mismatched verified job inputs")


@dataclass(frozen=True, slots=True, kw_only=True)
class JobRequest:
    forecast: ForecastRequest
    review: ModelReview
    policy: AdmissionPolicy
    inputs: ForecastInputs
    intended_use: str
    submitted_ns: int
    deadline_ns: int
    seed: int = 0
    schema: SchemaRef = JOB_SCHEMA

    def __post_init__(self) -> None:
        if self.schema != JOB_SCHEMA:
            raise ValueError("Unsupported job contract version")
        identifier(self.intended_use)
        integer(self.submitted_ns, 0)
        integer(self.deadline_ns, 1)
        integer(self.seed, 0)
        if (
            not 0 < self.deadline_ns - self.submitted_ns <= MAX_DURATION_NS
            or self.deadline_ns >= 2**63
            or self.seed >= 2**32
        ):
            raise ValueError("Job deadline/seed outside bounds")
        if (
            self.forecast.target != "close"
            or self.forecast.covariates
            or self.forecast.horizon > 24
        ):
            raise ValueError("F20 fake jobs require bounded close forecasts")
        self.review.admit(self.policy, self.forecast, self.intended_use)
        self.inputs.validate(self.forecast)

    @property
    def job_id(self) -> str:
        return self.forecast.request_id

    @property
    def sha256(self) -> str:
        return fingerprint(self)


@dataclass(frozen=True, slots=True)
class JobAttempt:
    job: JobRequest
    number: int
    token: str

    def __post_init__(self) -> None:
        integer(self.number, 1)
        identifier(self.token)
        if self.number > MAX_ATTEMPTS or len(self.token) != 32:
            raise ValueError("Invalid job attempt fence")


@dataclass(frozen=True, slots=True)
class JobRecord:
    job: JobRequest
    state: JobState
    attempt: int
    token: str | None
    artifact: ForecastArtifactRef | None
    reason: str | None
    updated_ns: int

    def execution(self) -> JobAttempt:
        if self.state is not JobState.RUNNING or self.token is None:
            raise ValueError("Job has no active attempt")
        return JobAttempt(self.job, self.attempt, self.token)
