"""Admission and validation own result acceptance; provider tasks own no state."""

from collections.abc import Callable
from typing import cast

from scryntic.application.analysis import ForecastArtifactRef
from scryntic.application.providers import ForecastRequest
from scryntic.forecast.artifact import ImmutableForecastStore
from scryntic.jobs.admission import AdmissionPolicy
from scryntic.jobs.codec import decode_response
from scryntic.jobs.contracts import JobAttempt, JobRecord, JobRequest, JobState
from scryntic.jobs.durable import DurableAttempt, DurableRecord
from scryntic.jobs.fake import JobProvider
from scryntic.jobs.inputs import InputReader
from scryntic.jobs.result import AdmittedForecast
from scryntic.jobs.runner import DurableRunner, Executor
from scryntic.jobs.store import JobError, JobStore
from scryntic.research.ledger import guard_dataset


class ForecastExecutor:
    def __init__(self, provider: JobProvider) -> None:
        self.provider = provider

    async def execute(self, attempt: DurableAttempt[JobRequest]) -> bytes:
        return await self.provider.execute(
            JobAttempt(attempt.job, attempt.number, attempt.token)
        )


class JobService(DurableRunner[JobRequest, ForecastArtifactRef]):
    _store: JobStore

    def _executor(self, job: JobRequest) -> Executor[JobRequest]:
        return ForecastExecutor(self._validate(job))

    async def run(self, job_id: str, *, resume: bool = False) -> JobRecord:
        return cast(JobRecord, await super().run(job_id, resume=resume))

    def cancel(self, job_id: str) -> JobRecord:
        return cast(JobRecord, super().cancel(job_id))

    def __init__(
        self,
        store: JobStore,
        providers: tuple[JobProvider, ...],
        policy: AdmissionPolicy,
        artifacts: ImmutableForecastStore,
        inputs: InputReader,
        *,
        max_active: int = 2,
        fault: Callable[[str], None] | None = None,
    ) -> None:
        if type(max_active) is not int or not 1 <= max_active <= 8:
            raise JobError("Active job quota outside bounds")
        self._providers = {
            item.review.descriptor.provider_id: item for item in providers
        }
        if len(self._providers) != len(providers) or len(providers) > 64:
            raise JobError("Duplicate/unbounded provider registrations")
        super().__init__(store, max_active=max_active)
        self._policy = policy
        self._artifacts = artifacts
        self._inputs = inputs
        self._fault = fault

    def _validate(self, job: JobRequest) -> JobProvider:
        if self._closing:
            raise JobError("Provider job service is closing")
        provider = self._providers.get(job.review.descriptor.provider_id)
        if (
            provider is None
            or provider.review != job.review
            or self._policy != job.policy
        ):
            raise JobError("Job admission no longer matches operator policy")
        job.review.admit(self._policy, job.forecast, job.intended_use)
        verified = job.inputs.verified
        guard_dataset(
            self._store.installation,
            job.forecast.dataset.manifest_sha256,
            {
                "time_range_ns": [
                    verified.last_start_ns,
                    verified.last_start_ns
                    + job.forecast.frequency_ns * job.forecast.horizon,
                ]
            },
            "inspect",
        )
        if self._inputs.read(job.forecast) != job.inputs:
            raise JobError("Verified dataset inputs changed")
        return provider

    def submit_job(self, job: JobRequest) -> JobRecord:
        self._validate(job)
        self._audit(job, "registered", 0)
        return self._store.submit(job)

    def submit(
        self,
        request: ForecastRequest,
        provider_id: str,
        *,
        intended_use: str,
        deadline_ns: int,
        seed: int = 0,
    ) -> JobRecord:
        provider = self._providers.get(provider_id)
        if provider is None:
            raise JobError("Unknown registered provider")
        existing = self._store.find(request.request_id)
        job = JobRequest(
            forecast=request,
            review=provider.review,
            policy=self._policy,
            inputs=self._inputs.read(request),
            intended_use=intended_use,
            submitted_ns=self._store.now()
            if existing is None
            else existing.job.submitted_ns,
            deadline_ns=deadline_ns,
            seed=seed,
        )
        return self.submit_job(job)

    def _point(self, phase: str) -> None:
        if self._fault is not None:
            self._fault(phase)

    def _accept(
        self, active: DurableRecord[JobRequest, ForecastArtifactRef], data: bytes
    ) -> None:
        raw_attempt = active.execution()
        attempt = JobAttempt(raw_attempt.job, raw_attempt.number, raw_attempt.token)
        result = decode_response(data, attempt)
        job = active.job
        accepted = AdmittedForecast(
            job.forecast, job.inputs.verified, job.review.descriptor, result, job
        )
        accepted.validate()
        if self._store.get(job.job_id).state is not JobState.RUNNING:
            return
        reference = self._artifacts.write(accepted)
        self._point("after_artifact")
        if self._store.finish(active, JobState.SUCCEEDED, artifact=reference):
            self._point("after_success")

    def result(self, job_id: str) -> ForecastArtifactRef:
        record = self._store.get(job_id)
        if record.state is not JobState.SUCCEEDED or record.artifact is None:
            raise JobError("Provider job has no accepted result")
        payload = self._artifacts.read(record.artifact)
        if payload.get("job") != {
            "request_sha256": record.job.sha256,
            "review_sha256": record.job.review.sha256,
            "policy_sha256": record.job.policy.sha256,
            "intended_use": record.job.intended_use,
        }:
            raise JobError("Accepted artifact job binding mismatch")
        return record.artifact
