"""Admission and validation own result acceptance; provider tasks own no state."""

import asyncio
from collections.abc import Callable

from scryntic.application.analysis import ForecastArtifactRef
from scryntic.application.providers import ForecastRequest
from scryntic.forecast.artifact import ImmutableForecastStore
from scryntic.jobs.admission import AdmissionPolicy
from scryntic.jobs.codec import decode_response
from scryntic.jobs.contracts import TERMINAL, JobRecord, JobRequest, JobState
from scryntic.jobs.fake import JobProvider
from scryntic.jobs.inputs import InputReader
from scryntic.jobs.result import AdmittedForecast
from scryntic.jobs.store import JobError, JobStore


class JobService:
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
        self._store = store
        self._policy = policy
        self._artifacts = artifacts
        self._inputs = inputs
        self._max_active = max_active
        self._fault = fault
        self._closing = False
        self._active: dict[str, asyncio.Task[bytes]] = {}

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
        if self._inputs.read(job.forecast) != job.inputs:
            raise JobError("Verified dataset inputs changed")
        return provider

    def submit_job(self, job: JobRequest) -> JobRecord:
        self._validate(job)
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

    def cancel(self, job_id: str) -> JobRecord:
        record = self._store.cancel(job_id)
        task = self._active.get(job_id)
        if task is not None:
            task.cancel()
        return record

    def _point(self, phase: str) -> None:
        if self._fault is not None:
            self._fault(phase)

    def _accept(self, active: JobRecord, data: bytes) -> None:
        attempt = active.execution()
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

    def _cancelled(self, active: JobRecord) -> None:
        if self._closing:
            self._store.finish(active, JobState.INTERRUPTED, reason="shutdown")
        else:
            self._store.cancel(active.job.job_id)

    async def run(self, job_id: str, *, resume: bool = False) -> JobRecord:
        if self._closing:
            raise JobError("Provider job service is closing")
        existing = self._store.get(job_id)
        if existing.state in TERMINAL and not resume:
            return existing
        if len(self._active) >= self._max_active or job_id in self._active:
            raise JobError("Provider execution already active or quota reached")
        active = self._store.claim(job_id, resume=resume)
        if active.state is not JobState.RUNNING:
            return active
        try:
            provider = self._validate(active.job)
            await self._execute(active, provider)
        except asyncio.CancelledError:
            self._cancelled(active)
            if self._owner_cancelled():
                raise
        except Exception:
            self._store.finish(active, JobState.FAILED, reason="provider_failed")
        return self._store.get(job_id)

    @staticmethod
    def _owner_cancelled() -> bool:
        owner = asyncio.current_task()
        return owner is not None and bool(owner.cancelling())

    async def _execute(self, active: JobRecord, provider: JobProvider) -> None:
        task = asyncio.create_task(provider.execute(active.execution()))
        job_id = active.job.job_id
        self._active[job_id] = task
        remaining = max(0, active.job.deadline_ns - self._store.now()) / 1_000_000_000
        timeout = asyncio.timeout(remaining)
        try:
            async with timeout:
                data = await task
            if self._owner_cancelled():
                raise asyncio.CancelledError
            if timeout.expired():
                self._store.finish(active, JobState.DEADLINE_EXPIRED, reason="deadline")
            else:
                self._accept(active, data)
        except TimeoutError:
            state = JobState.DEADLINE_EXPIRED if timeout.expired() else JobState.FAILED
            reason = "deadline" if timeout.expired() else "provider_timeout"
            self._store.finish(active, state, reason=reason)
        finally:
            self._active.pop(job_id, None)
            if not task.done():
                task.cancel()

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

    async def aclose(self) -> None:
        self._closing = True
        tasks = tuple(self._active.items())
        for job_id, task in tasks:
            active = self._store.get(job_id)
            self._store.finish(active, JobState.INTERRUPTED, reason="shutdown")
            task.cancel()
        if tasks:
            await asyncio.gather(*(task for _, task in tasks), return_exceptions=True)
