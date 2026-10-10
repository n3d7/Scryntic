"""Application acceptance and durable cancellation/publication crash windows."""

import asyncio
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

import pytest

from scryntic.application.providers import ForecastRequest
from scryntic.configuration.paths import Installation
from scryntic.forecast.artifact import ImmutableForecastStore
from scryntic.jobs.contracts import ForecastInputs, JobAttempt
from scryntic.jobs.fake import JobProvider
from scryntic.jobs.service import JobService
from scryntic.jobs.store import JobStore
from tests.jobs.helpers import NOW, job, policy, review
from tests.normalization.helpers import installation


class FixedInputs:
    def read(self, request: ForecastRequest) -> ForecastInputs:
        assert request.dataset == job().forecast.dataset
        return job().inputs


def services(
    tmp_path: Path,
    *,
    provider: JobProvider | None = None,
    clock: Callable[[], int] = lambda: NOW,
    fault: Callable[[str], None] | None = None,
) -> tuple[Installation, JobStore, ImmutableForecastStore, JobService]:
    from scryntic.jobs.fake import LocalFakeProvider
    from scryntic.jobs.service import JobService
    from scryntic.jobs.store import JobStore

    target = installation(tmp_path)
    implementation = LocalFakeProvider(review()) if provider is None else provider
    store = JobStore(target, clock=clock)
    artifacts = ImmutableForecastStore(target)
    service = JobService(
        store,
        (implementation,),
        policy(implementation.review),
        artifacts,
        FixedInputs(),
        fault=fault,
    )
    return target, store, artifacts, service


def test_success_is_idempotent_and_binds_job_admission_and_dataset(
    tmp_path: Path,
) -> None:
    _, store, artifacts, service = services(tmp_path)

    async def exercise() -> None:
        submitted = service.submit_job(job())
        assert submitted.state.value == "queued"
        first = await service.run("job-a")
        second = await service.run("job-a")
        assert first == second
        assert first.state.value == "succeeded"
        ref = service.result("job-a")
        payload = artifacts.read(ref)
        assert payload["job"]["request_sha256"] == job().sha256
        assert payload["job"]["policy_sha256"] == job().policy.sha256
        assert ref.dataset == job().forecast.dataset
        assert payload["result"]["points"][0]["value"] == "2.0"
        await service.aclose()

    try:
        asyncio.run(exercise())
    finally:
        store.close()


def test_artifact_records_the_admitted_nonzero_seed(tmp_path: Path) -> None:
    _, store, artifacts, service = services(tmp_path)
    value = replace(job(), seed=73)

    async def exercise() -> None:
        service.submit_job(value)
        await service.run(value.job_id)
        payload = artifacts.read(service.result(value.job_id))
        assert payload["seed"] == 73
        assert payload["determinism"]["seed"] == 73
        await service.aclose()

    try:
        asyncio.run(exercise())
    finally:
        store.close()


def test_submission_retry_preserves_identity_when_clock_advances(
    tmp_path: Path,
) -> None:
    current = [NOW]
    _, store, _, service = services(tmp_path, clock=lambda: current[0])
    value = job()
    try:
        first = service.submit(
            value.forecast,
            value.review.descriptor.provider_id,
            intended_use=value.intended_use,
            deadline_ns=value.deadline_ns,
        )
        current[0] += 1
        assert (
            service.submit(
                value.forecast,
                value.review.descriptor.provider_id,
                intended_use=value.intended_use,
                deadline_ns=value.deadline_ns,
            )
            == first
        )
        assert store.get(value.job_id).job.submitted_ns == NOW
    finally:
        store.close()


@pytest.mark.parametrize(
    "defect", ["provider_timeout", "suppressed_caller_cancel", "suppressed_deadline"]
)
def test_timeout_origin_and_suppressed_cancellation_cannot_be_misreported(
    tmp_path: Path, defect: str
) -> None:
    from scryntic.jobs.fake import LocalFakeProvider

    entered = asyncio.Event()

    class Provider(LocalFakeProvider):
        async def execute(self, attempt: JobAttempt) -> bytes:
            entered.set()
            if defect == "provider_timeout":
                raise TimeoutError("provider timeout before job deadline")
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                return await super().execute(attempt)
            raise AssertionError("Waiting fixture unexpectedly completed")

    _, store, _, service = services(tmp_path, provider=Provider(review()))
    value = job()
    if defect == "suppressed_deadline":
        value = replace(value, deadline_ns=NOW + 50_000_000)

    async def exercise() -> None:
        service.submit_job(value)
        operation = asyncio.create_task(service.run(value.job_id))
        await asyncio.wait_for(entered.wait(), 1)
        if defect == "suppressed_caller_cancel":
            operation.cancel()
            with pytest.raises(asyncio.CancelledError):
                await operation
            assert store.get(value.job_id).state.value == "cancelled"
        else:
            completed = await asyncio.wait_for(operation, 1)
            expected = "failed" if defect == "provider_timeout" else "deadline_expired"
            assert completed.state.value == expected
            assert completed.artifact is None
            if defect == "provider_timeout":
                assert completed.reason == "provider_timeout"
        await service.aclose()

    try:
        asyncio.run(exercise())
    finally:
        store.close()


@pytest.mark.parametrize("mode", ["caller", "explicit", "deadline", "shutdown"])
def test_cancel_deadline_and_shutdown_are_durable_and_late_output_is_rejected(
    tmp_path: Path, mode: str
) -> None:
    from scryntic.jobs.fake import LocalFakeProvider
    from scryntic.jobs.store import JobStore

    entered = asyncio.Event()
    release = asyncio.Event()

    class WaitingProvider(LocalFakeProvider):
        async def execute(self, attempt: JobAttempt) -> bytes:
            entered.set()
            await release.wait()
            return await super().execute(attempt)

    current = [NOW]
    target, store, _, service = services(
        tmp_path, provider=WaitingProvider(review()), clock=lambda: current[0]
    )
    value = job()
    expected = {
        "caller": "cancelled",
        "explicit": "cancelled",
        "deadline": "deadline_expired",
        "shutdown": "interrupted",
    }[mode]

    async def exercise() -> None:
        service.submit_job(value)
        operation = asyncio.create_task(service.run(value.job_id))
        await asyncio.wait_for(entered.wait(), 1)
        if mode == "caller":
            operation.cancel()
            with pytest.raises(asyncio.CancelledError):
                await operation
        elif mode == "explicit":
            assert service.cancel(value.job_id).state.value == expected
            assert (await operation).state.value == expected
        elif mode == "deadline":
            current[0] = value.deadline_ns
            assert store.get(value.job_id).state.value == expected
            release.set()
            assert (await operation).state.value == expected
        else:
            await service.aclose()
            assert (await operation).state.value == expected
        assert store.get(value.job_id).state.value == expected
        with pytest.raises(ValueError):
            service.result(value.job_id)
        await service.aclose()

    try:
        asyncio.run(exercise())
    finally:
        store.close()
    with JobStore(target, clock=lambda: current[0] + 1) as reopened:
        assert reopened.get(value.job_id).state.value == expected
        assert reopened.get(value.job_id).artifact is None


@pytest.mark.parametrize("phase", ["after_artifact", "after_success"])
def test_restart_converges_across_publication_without_duplicate_success(
    tmp_path: Path, phase: str
) -> None:
    from scryntic.jobs.fake import LocalFakeProvider
    from scryntic.jobs.service import JobService
    from scryntic.jobs.store import JobStore

    def crash(point: str) -> None:
        if point == phase:
            raise SystemExit("simulated crash")

    saved, store, artifacts, service = services(tmp_path, fault=crash)
    service.submit_job(job())
    operation = service.run("job-a")
    with pytest.raises(SystemExit):
        asyncio.run(operation)
    store.close()
    with JobStore(saved, clock=lambda: NOW + 1) as reopened:
        restarted = JobService(
            reopened,
            (LocalFakeProvider(review()),),
            policy(review()),
            artifacts,
            FixedInputs(),
        )
        before = reopened.get("job-a")
        if phase == "after_artifact":
            assert before.state.value == "interrupted"
            recovered = asyncio.run(restarted.run("job-a", resume=True))
            assert recovered.attempt == 2
        else:
            assert before.state.value == "succeeded"
            recovered = asyncio.run(restarted.run("job-a"))
            assert recovered.attempt == 1
        assert recovered.state.value == "succeeded"
        assert restarted.result("job-a").dataset == job().forecast.dataset
        assert len(list((saved.state_dir / "forecasts/objects").rglob("*.json"))) == 1
        asyncio.run(restarted.aclose())


@pytest.mark.parametrize(
    "defect", ["bytes", "origin", "inputs", "policy", "correlation", "timestamp"]
)
def test_application_rejects_changed_admission_inputs_and_provider_results(
    tmp_path: Path, defect: str
) -> None:
    from scryntic.jobs.codec import encode_response
    from scryntic.jobs.fake import LocalFakeProvider, calculate

    class HostileProvider(LocalFakeProvider):
        async def execute(self, attempt: JobAttempt) -> bytes:
            if defect == "bytes":
                return b"{}"
            result = calculate(attempt, self.review, "persistence")
            if defect == "correlation":
                result = replace(result, request_id="other-job")
            elif defect == "timestamp":
                result = replace(
                    result,
                    points=tuple(
                        replace(point, timestamp_ns=point.timestamp_ns + 1)
                        for point in result.points
                    ),
                )
            return encode_response(attempt, result)

    target, store, artifacts, service = services(
        tmp_path, provider=HostileProvider(review())
    )
    value = job()
    if defect == "origin":
        descriptor = replace(
            value.review.descriptor,
            model=replace(value.review.descriptor.model, origin="other-origin"),
        )
        changed = replace(value.review, descriptor=descriptor)
        value = replace(value, review=changed, policy=policy(changed))
    elif defect == "policy":
        value = replace(value, policy=replace(value.policy, remote_enabled=True))
    elif defect == "inputs":
        value = replace(value, inputs=replace(value.inputs, closes=(3.0, 4.0)))
    try:
        # A previously admitted journal is rechecked against current operator state at dispatch.
        store.submit(value)
        result = asyncio.run(service.run(value.job_id))
        assert result.state.value == "failed"
        assert result.artifact is None
        with pytest.raises(ValueError):
            service.result(value.job_id)
        assert not (target.state_dir / "forecasts").exists()
    finally:
        asyncio.run(service.aclose())
        store.close()


def test_active_quota_duplicate_dispatch_and_shutdown_are_bounded(
    tmp_path: Path,
) -> None:
    from scryntic.jobs.fake import LocalFakeProvider

    entered = asyncio.Event()

    class Waiting(LocalFakeProvider):
        async def execute(self, attempt: JobAttempt) -> bytes:
            entered.set()
            await asyncio.Event().wait()
            raise AssertionError("Waiting fixture unexpectedly completed")

    _, store, artifacts, _ = services(tmp_path)
    service = JobService(
        store,
        (Waiting(review()),),
        policy(review()),
        artifacts,
        FixedInputs(),
        max_active=1,
    )

    async def exercise() -> None:
        service.submit_job(job())
        service.submit_job(job("job-b"))
        owner = asyncio.create_task(service.run("job-a"))
        await asyncio.wait_for(entered.wait(), 1)
        for name in ("job-a", "job-b"):
            with pytest.raises(ValueError, match="quota"):
                await service.run(name)
        await service.aclose()
        assert (await owner).state.value == "interrupted"
        assert store.get("job-b").state.value == "queued"
        with pytest.raises(ValueError, match="closing"):
            await service.run("job-b")

    try:
        asyncio.run(exercise())
    finally:
        store.close()
