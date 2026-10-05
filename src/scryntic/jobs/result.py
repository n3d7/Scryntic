"""An admitted F20 result retains the existing application artifact contract."""

from dataclasses import dataclass

from scryntic.application.analysis import ValidatedForecast
from scryntic.jobs.contracts import JobRequest


@dataclass(frozen=True, slots=True)
class AdmittedForecast(ValidatedForecast):
    job: JobRequest

    def validate(self) -> None:
        self.job.__post_init__()
        if (
            self.request != self.job.forecast
            or self.dataset != self.job.inputs.verified
            or self.descriptor != self.job.review.descriptor
        ):
            raise ValueError("Result does not match admitted job")
        self.validate_result()
