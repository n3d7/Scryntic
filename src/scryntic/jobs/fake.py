"""Fixed application-owned F20 fixtures; never load weights or downloaded code."""

from hashlib import sha256
from typing import Literal, Protocol

from scryntic.application.providers import ForecastPoint, ForecastResult
from scryntic.jobs.admission import ModelReview
from scryntic.jobs.codec import encode_response
from scryntic.jobs.contracts import JobAttempt


class JobProvider(Protocol):
    @property
    def review(self) -> ModelReview: ...

    async def execute(self, attempt: JobAttempt) -> bytes: ...


def require_fixture(
    review: ModelReview, algorithm: Literal["persistence", "trend"]
) -> None:
    model = review.descriptor.model
    revision = "f20-fixed-" + algorithm + "-v1"
    if (
        model.origin != "scryntic:builtin-f20-fake"
        or model.publisher != "scryntic"
        or model.revision != revision
        or model.artifact_sha256 != (sha256(revision.encode("ascii")).hexdigest(),)
    ):
        raise ValueError("Only the pinned F20 fixture is executable")


def calculate(
    attempt: JobAttempt,
    review: ModelReview,
    algorithm: Literal["persistence", "trend"] = "persistence",
) -> ForecastResult:
    require_fixture(review, algorithm)
    if attempt.job.review != review:
        raise ValueError("Provider review mismatch")
    job = attempt.job
    job.__post_init__()
    request = job.forecast
    previous, last = job.inputs.closes
    slope = 0.0 if algorithm == "persistence" else last - previous
    points = tuple(
        ForecastPoint(
            job.inputs.verified.last_start_ns + (index + 1) * request.frequency_ns,
            last + (index + 1) * slope,
        )
        for index in range(request.horizon)
    )
    result = ForecastResult(
        request.request_id,
        request.dataset,
        review.descriptor.provider_id,
        review.descriptor.model.revision or "",
        points,
    )
    result.validate_for(request, review.descriptor)
    return result


class LocalFakeProvider:
    def __init__(
        self,
        review: ModelReview,
        *,
        algorithm: Literal["persistence", "trend"] = "persistence",
    ) -> None:
        if review.descriptor.execution != "local":
            raise ValueError("Local fake requires local execution")
        require_fixture(review, algorithm)
        self.review = review
        self._algorithm = algorithm

    async def execute(self, attempt: JobAttempt) -> bytes:
        return encode_response(
            attempt, calculate(attempt, self.review, self._algorithm)
        )
