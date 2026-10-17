"""Closed job requests and interface-neutral forms."""

import re
from dataclasses import dataclass

from scryntic.application.operations import Field, Operation, Workflow
from scryntic.domain.validation import digest, identifier, integer

PROVIDERS = ("fake-persistence", "fake-trend", "timesfm-2.5")


JOB_IDENTIFIER_LABEL = "Job identifier"


@dataclass(frozen=True, slots=True)
class ProvidersListRequest:
    @property
    def operation(self) -> Operation:
        return Operation.PROVIDERS_LIST


@dataclass(frozen=True, slots=True)
class SubmitJobRequest:
    dataset: str
    job_id: str
    start_ns: int
    end_ns: int
    provider: str = "fake-persistence"
    horizon: int = 1
    seed: int = 0
    timeout_seconds: int = 240
    origin_index: int = -1
    decision_ns: int = -1
    model_config: str = ""

    @property
    def operation(self) -> Operation:
        return Operation.JOBS_SUBMIT

    def __post_init__(self) -> None:
        digest(self.dataset)
        identifier(self.job_id)
        integer(self.start_ns, 0)
        integer(self.end_ns, 1)
        integer(self.horizon, 1)
        integer(self.seed, 0)
        integer(self.timeout_seconds, 1)
        integer(self.origin_index, -1)
        integer(self.decision_ns, -1)
        if (
            self.provider not in PROVIDERS
            or self.end_ns - self.start_ns < 3
            or self.end_ns >= 2**63
            or self.horizon > 24
            or self.seed >= 2**32
            or self.timeout_seconds > 300
            or self.origin_index > 1023
            or self.decision_ns >= 2**63
        ):
            raise ValueError("Unsupported job request")
        if not isinstance(self.model_config, str) or (
            self.model_config
            and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", self.model_config)
        ):
            raise ValueError("Invalid model configuration name")


@dataclass(frozen=True, slots=True)
class AnalyzeRequest(SubmitJobRequest):
    @property
    def operation(self) -> Operation:
        return Operation.ANALYZE


@dataclass(frozen=True, slots=True)
class JobStatusRequest:
    job_id: str

    @property
    def operation(self) -> Operation:
        return Operation.JOBS_STATUS

    def __post_init__(self) -> None:
        identifier(self.job_id)


@dataclass(frozen=True, slots=True)
class JobRunRequest(JobStatusRequest):
    @property
    def operation(self) -> Operation:
        return Operation.JOBS_RUN


@dataclass(frozen=True, slots=True)
class JobResumeRequest(JobStatusRequest):
    @property
    def operation(self) -> Operation:
        return Operation.JOBS_RESUME


@dataclass(frozen=True, slots=True)
class JobCancelRequest(JobStatusRequest):
    @property
    def operation(self) -> Operation:
        return Operation.JOBS_CANCEL


@dataclass(frozen=True, slots=True)
class JobResultRequest(JobStatusRequest):
    @property
    def operation(self) -> Operation:
        return Operation.JOBS_RESULT


@dataclass(frozen=True, slots=True)
class JobsListRequest:
    limit: int = 100
    offset: int = 0

    @property
    def operation(self) -> Operation:
        return Operation.JOBS_LIST

    def __post_init__(self) -> None:
        integer(self.limit, 1)
        integer(self.offset, 0)
        if self.limit > 100 or self.offset > 1024:
            raise ValueError("Job list exceeds bounds")


_SUBMIT_FIELDS = (
    Field("dataset", "Dataset SHA-256", required=True),
    Field("job_id", JOB_IDENTIFIER_LABEL, required=True),
    Field("start_ns", "Replay range start (UTC ns)", required=True),
    Field("end_ns", "Replay range end (UTC ns)", required=True),
    Field("provider", "Provider", "fake-persistence", choices=PROVIDERS),
    Field("horizon", "Forecast steps", "1"),
    Field("seed", "Deterministic seed", "0"),
    Field("timeout_seconds", "Job deadline duration (seconds)", "240"),
    Field(
        "origin_index",
        "Origin row index",
        "-1",
        help="-1 selects the immutable snapshot's final row.",
    ),
    Field(
        "decision_ns",
        "Historical decision (UTC ns)",
        "-1",
        help="-1 uses the explicit replay range end.",
    ),
    Field(
        "model_config",
        "Protected model descriptor name",
        "",
        help="A JSON file in the installation configuration directory.",
    ),
)


def _submit(values: dict[str, str], kind: type[SubmitJobRequest]) -> SubmitJobRequest:
    return kind(
        dataset=values["dataset"],
        job_id=values["job_id"],
        start_ns=int(values["start_ns"]),
        end_ns=int(values["end_ns"]),
        provider=values["provider"],
        horizon=int(values["horizon"]),
        seed=int(values["seed"]),
        timeout_seconds=int(values["timeout_seconds"]),
        origin_index=int(values["origin_index"]),
        decision_ns=int(values["decision_ns"]),
        model_config=values["model_config"],
    )


WORKFLOWS = (
    Workflow(
        Operation.PROVIDERS_LIST,
        "Inspect registered providers",
        "Analysis",
        (),
        lambda _: ProvidersListRequest(),
    ),
    Workflow(
        Operation.ANALYZE,
        "Analyze an immutable dataset",
        "Analysis",
        _SUBMIT_FIELDS,
        lambda v: _submit(v, AnalyzeRequest),
    ),
    Workflow(
        Operation.JOBS_SUBMIT,
        "Submit a durable forecast job",
        "Jobs",
        _SUBMIT_FIELDS,
        lambda v: _submit(v, SubmitJobRequest),
    ),
    Workflow(
        Operation.JOBS_LIST,
        "List durable jobs",
        "Jobs",
        (Field("limit", "Maximum jobs", "100"), Field("offset", "Offset", "0")),
        lambda v: JobsListRequest(int(v["limit"]), int(v["offset"])),
    ),
    Workflow(
        Operation.JOBS_STATUS,
        "Inspect job status",
        "Jobs",
        (Field("job_id", JOB_IDENTIFIER_LABEL, required=True),),
        lambda v: JobStatusRequest(v["job_id"]),
    ),
    Workflow(
        Operation.JOBS_RUN,
        "Run a queued job",
        "Jobs",
        (Field("job_id", JOB_IDENTIFIER_LABEL, required=True),),
        lambda v: JobRunRequest(v["job_id"]),
    ),
    Workflow(
        Operation.JOBS_RESUME,
        "Resume an interrupted job",
        "Jobs",
        (Field("job_id", JOB_IDENTIFIER_LABEL, required=True),),
        lambda v: JobResumeRequest(v["job_id"]),
    ),
    Workflow(
        Operation.JOBS_CANCEL,
        "Cancel a durable job",
        "Jobs",
        (Field("job_id", JOB_IDENTIFIER_LABEL, required=True),),
        lambda v: JobCancelRequest(v["job_id"]),
        confirmation="Cancel this job permanently?",
    ),
    Workflow(
        Operation.JOBS_RESULT,
        "Inspect an accepted result",
        "Jobs",
        (Field("job_id", JOB_IDENTIFIER_LABEL, required=True),),
        lambda v: JobResultRequest(v["job_id"]),
    ),
)
