"""Typed job workflows retain actual owners and durable immutable contexts."""

import asyncio
import json
import threading
from collections.abc import Callable
from pathlib import Path
from typing import TypeVar

import pytest

from scryntic.application.job_operations import SubmitJobRequest
from scryntic.application.operations import ApplicationError, ErrorCode, Operation
from scryntic.jobs.codec import encode_response
from scryntic.jobs.contracts import JobAttempt
from scryntic.jobs.fake import calculate
from scryntic.jobs.store import JobStore
from scryntic.model_worker.launcher import CPUWorker
from scryntic.replay.contracts import ReplayConfig
from scryntic.replay.service import PreparedReplay
from tests.jobs.helpers import NOW, job
from tests.normalization.helpers import installation

T = TypeVar("T")


def test_job_list_is_bounded_stable_and_expires_deadlines(tmp_path: Path) -> None:
    current = [NOW]
    with JobStore(installation(tmp_path), clock=lambda: current[0]) as store:
        store.submit(job("job-b"))
        store.submit(job("job-a"))
        assert [r.job.job_id for r in store.list(limit=1)] == ["job-a"]
        assert [r.job.job_id for r in store.list(limit=1, offset=1)] == ["job-b"]
        current[0] += 11_000_000_000
        assert {r.state.value for r in store.list()} == {"deadline_expired"}
        with pytest.raises(ValueError):
            store.list(limit=1025)


def test_registry_rejects_unsafe_paths_and_nonpositive_horizon() -> None:
    from scryntic.application.job_operations import WORKFLOWS

    submit = next(w for w in WORKFLOWS if w.operation is Operation.JOBS_SUBMIT)
    parameters = {
        "dataset": "a" * 64,
        "job_id": "case-a",
        "start_ns": "0",
        "end_ns": "12",
    }
    request = submit.request(parameters)
    assert request.operation is Operation.JOBS_SUBMIT
    for changed in (
        {"horizon": "0"},
        {"model_config": "../private.json"},
        {"provider": "module:loader"},
    ):
        with pytest.raises(ApplicationError) as failure:
            submit.request(parameters | changed)
        assert failure.value.code is ErrorCode.INVALID


class Context:
    code_revision = "test-code"
    dependency_lock_sha256 = "d" * 64

    def __init__(self, root: Path) -> None:
        self.installation = installation(root)

    async def run_io(self, operation: Callable[[], T]) -> T:
        return await asyncio.to_thread(operation)

    def read_config_json(self, leaf: str, maxbytes: int) -> object:
        return json.loads((self.installation.config_dir / leaf).read_bytes())

    def export_bundle(self, leaf: str, files: dict[str, bytes]) -> dict[str, object]:
        raise AssertionError("Jobs must not export bundles")


def prepared(context: Context, checksum: str, config: ReplayConfig) -> PreparedReplay:
    from scryntic.dataset.recipes import CoverageClaim
    from scryntic.replay.reader import CandleReplayReader
    from scryntic.replay.service import PreparedReplay
    from tests.dataset.test_recipes import _clock
    from tests.replay.helpers import START, STEP, snapshot, sources

    inputs = sources()
    manifest, rows = snapshot(inputs)
    semantics = inputs[0].value.semantics
    assert semantics is not None
    instrument = semantics.key.instrument
    claim = CoverageClaim(
        instrument, STEP, START, START + 12 * STEP, "complete", _clock(START)
    )
    manifest["coverage_claims"] = [claim.projection()]
    return PreparedReplay(
        CandleReplayReader(manifest, rows, config), {"manifest_sha256": checksum}, {}
    )


def submit_request(
    job_id: str = "case-a", *, provider: str = "fake-persistence"
) -> SubmitJobRequest:
    from scryntic.application.job_operations import SubmitJobRequest
    from tests.replay.helpers import START, STEP

    return SubmitJobRequest(
        "a" * 64, job_id, START, START + 12 * STEP, provider=provider
    )


@pytest.fixture
def isolated_fixture(monkeypatch: pytest.MonkeyPatch) -> None:
    import scryntic.workflows.jobs as workflows

    monkeypatch.setattr(workflows, "prepare_replay", prepared)

    async def execute(self: CPUWorker, attempt: JobAttempt) -> bytes:
        result = calculate(attempt, attempt.job.review, "persistence")
        return encode_response(attempt, result)

    monkeypatch.setattr(CPUWorker, "execute", execute)


def test_submit_restart_run_and_verified_result(
    tmp_path: Path, isolated_fixture: None
) -> None:
    from scryntic.application.job_operations import (
        JobResultRequest,
        JobRunRequest,
        JobStatusRequest,
    )
    from scryntic.workflows.jobs import JobWorkflows

    context = Context(tmp_path)

    async def exercise() -> None:
        first = JobWorkflows(context)
        record = await first.execute(submit_request())
        assert json.loads(record.payload)["job"]["state"] == "queued"
        await first.aclose()
        restarted = JobWorkflows(context)
        try:
            result = await restarted.execute(JobRunRequest("case-a"))
            assert json.loads(result.payload)["job"]["state"] == "succeeded"
            status = await restarted.execute(JobStatusRequest("case-a"))
            assert "token" not in json.loads(status.payload)["job"]
            accepted = await restarted.execute(JobResultRequest("case-a"))
            assert (
                json.loads(accepted.payload)["artifact"]["provider_id"]
                == "fake-persistence"
            )
        finally:
            await restarted.aclose()

    asyncio.run(exercise())


def test_restart_context_change_prevents_dispatch(
    tmp_path: Path, isolated_fixture: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    import scryntic.workflows.jobs as workflows
    from scryntic.application.job_operations import JobRunRequest
    from scryntic.workflows.jobs import JobWorkflows

    context = Context(tmp_path)

    async def exercise() -> None:
        first = JobWorkflows(context)
        await first.execute(submit_request())
        await first.aclose()
        original = prepared

        def changed(
            context: Context, checksum: str, config: ReplayConfig
        ) -> PreparedReplay:
            value = original(context, checksum, config)
            value.reader.rows[-1]["close"] = "999"
            return value

        monkeypatch.setattr(workflows, "prepare_replay", changed)
        restarted = JobWorkflows(context)
        request = JobRunRequest("case-a")
        try:
            with pytest.raises(ApplicationError):
                await restarted.execute(request)
        finally:
            await restarted.aclose()

    asyncio.run(exercise())


def test_missing_model_provisioning_does_not_create_job(
    tmp_path: Path, isolated_fixture: None
) -> None:
    from scryntic.application.job_operations import JobsListRequest
    from scryntic.workflows.jobs import JobWorkflows

    async def exercise() -> None:
        services = JobWorkflows(Context(tmp_path))
        request = submit_request(provider="timesfm-2.5")
        try:
            with pytest.raises(ApplicationError) as failure:
                await services.execute(request)
            assert failure.value.code is ErrorCode.CONFIGURATION
            result = await services.execute(JobsListRequest())
            assert json.loads(result.payload)["jobs"] == []
        finally:
            await services.aclose()

    asyncio.run(exercise())


def test_changed_plan_request_cannot_replace_durable_job_binding(
    tmp_path: Path, isolated_fixture: None
) -> None:
    from scryntic.application.job_operations import JobRunRequest
    from scryntic.workflows.jobs import JobWorkflows

    context = Context(tmp_path)

    async def exercise() -> None:
        first = JobWorkflows(context)
        await first.execute(submit_request())
        await first.aclose()
        path = next((context.installation.state_dir / "job-contexts").glob("*.json"))
        value = json.loads(path.read_bytes())
        value["request"]["seed"] = 1
        path.chmod(0o600)
        path.write_text(json.dumps(value, sort_keys=True, separators=(",", ":")))
        path.chmod(0o400)
        restarted = JobWorkflows(context)
        request = JobRunRequest("case-a")
        try:
            with pytest.raises(ApplicationError):
                await restarted.execute(request)
        finally:
            await restarted.aclose()

    asyncio.run(exercise())


@pytest.mark.parametrize("explicit", [False, True])
def test_shutdown_and_explicit_cancel_settle_actual_worker(
    tmp_path: Path,
    isolated_fixture: None,
    monkeypatch: pytest.MonkeyPatch,
    explicit: bool,
) -> None:
    from scryntic.application.job_operations import (
        JobCancelRequest,
        JobResumeRequest,
        JobRunRequest,
        JobStatusRequest,
    )
    from scryntic.workflows.jobs import JobWorkflows

    entered, settled = threading.Event(), threading.Event()

    async def waiting(self: CPUWorker, attempt: JobAttempt) -> bytes:
        entered.set()
        try:
            await asyncio.Event().wait()
            raise AssertionError("Blocked worker unexpectedly returned")
        finally:
            settled.set()

    monkeypatch.setattr(CPUWorker, "execute", waiting)
    context = Context(tmp_path)

    async def exercise() -> None:
        services = JobWorkflows(context)
        await services.execute(submit_request())
        operation = asyncio.create_task(services.execute(JobRunRequest("case-a")))
        assert await asyncio.to_thread(entered.wait, 2)
        if explicit:
            await services.execute(JobCancelRequest("case-a"))
        else:
            operation.cancel()
            with pytest.raises(asyncio.CancelledError):
                await operation
            await services.cancel()
        await services.aclose()
        assert settled.is_set()
        if not operation.cancelled():
            await operation
        reopened = JobWorkflows(context)
        try:
            result = await reopened.execute(JobStatusRequest("case-a"))
            assert json.loads(result.payload)["job"]["state"] == (
                "cancelled" if explicit else "interrupted"
            )
            if explicit:
                request = JobResumeRequest("case-a")
                with pytest.raises(ApplicationError):
                    await reopened.execute(request)
            else:

                async def primitive(self: CPUWorker, attempt: JobAttempt) -> bytes:
                    return encode_response(
                        attempt, calculate(attempt, attempt.job.review)
                    )

                monkeypatch.setattr(CPUWorker, "execute", primitive)
                resumed = await reopened.execute(JobResumeRequest("case-a"))
                assert json.loads(resumed.payload)["job"]["state"] == "succeeded"
                assert json.loads(resumed.payload)["job"]["attempt"] == 2
        finally:
            await reopened.aclose()

    asyncio.run(exercise())


def test_second_workflow_owner_reports_busy_and_closes_cleanly(
    tmp_path: Path, isolated_fixture: None
) -> None:
    from scryntic.application.job_operations import JobsListRequest
    from scryntic.workflows.jobs import JobWorkflows

    async def exercise() -> None:
        first, second = JobWorkflows(Context(tmp_path)), JobWorkflows(Context(tmp_path))
        await first.execute(JobsListRequest())
        request = JobsListRequest()
        try:
            with pytest.raises(ApplicationError) as failure:
                await second.execute(request)
            assert failure.value.code is ErrorCode.BUSY
            await second.aclose()
        finally:
            await first.aclose()

    asyncio.run(exercise())


@pytest.mark.parametrize(
    "error_type", [RuntimeError, asyncio.CancelledError, KeyboardInterrupt, SystemExit]
)
def test_owner_initialization_failure_reaches_caller_and_closes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, error_type: type[BaseException]
) -> None:
    import scryntic.workflows.jobs as workflows
    from scryntic.application.job_operations import JobsListRequest
    from scryntic.configuration.paths import Installation
    from scryntic.workflows.jobs import JobWorkflows

    def unavailable(installation: Installation) -> JobStore:
        raise error_type("Owner initialization unavailable")

    monkeypatch.setattr(workflows, "JobStore", unavailable)

    async def exercise() -> None:
        services = JobWorkflows(Context(tmp_path))
        request = JobsListRequest()
        try:
            with pytest.raises(error_type):
                await services.execute(request)
        finally:
            await services.aclose()

    asyncio.run(exercise())


@pytest.mark.parametrize("cancel_close", [False, True])
def test_failed_owner_close_joins_after_loop_has_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cancel_close: bool
) -> None:
    import scryntic.workflows.jobs as workflows
    from scryntic.application.job_operations import JobsListRequest
    from scryntic.configuration.paths import Installation

    loop_closed, release_owner, joining = (
        threading.Event(),
        threading.Event(),
        threading.Event(),
    )
    new_event_loop = asyncio.new_event_loop

    def delayed_owner_loop() -> asyncio.AbstractEventLoop:
        loop = new_event_loop()
        close = loop.close

        def delayed_close() -> None:
            close()
            loop_closed.set()
            release_owner.wait(5)

        monkeypatch.setattr(loop, "close", delayed_close)
        return loop

    def unavailable(installation: Installation) -> JobStore:
        raise asyncio.CancelledError("Owner initialization unavailable")

    monkeypatch.setattr(asyncio, "new_event_loop", delayed_owner_loop)
    monkeypatch.setattr(workflows, "JobStore", unavailable)

    async def exercise() -> None:
        services = workflows.JobWorkflows(Context(tmp_path))
        request = JobsListRequest()
        with pytest.raises(asyncio.CancelledError):
            await services.execute(request)
        owner = services._owner
        assert owner is not None
        join = owner.thread.join

        def observed_join() -> None:
            joining.set()
            join()

        monkeypatch.setattr(owner.thread, "join", observed_join)
        closing = None
        try:
            assert await asyncio.to_thread(loop_closed.wait, 2)
            assert owner.thread.is_alive()
            closing = asyncio.create_task(services.aclose())
            if not await asyncio.to_thread(joining.wait, 2):
                await closing
                pytest.fail("Close did not join the actual owner thread")
            if cancel_close:
                closing.cancel()
                await asyncio.sleep(0)
                closing.cancel()
                await asyncio.sleep(0)
            assert not closing.done()
            assert owner.thread.is_alive()
            release_owner.set()
            if cancel_close:
                with pytest.raises(asyncio.CancelledError):
                    await closing
            else:
                await closing
            assert not owner.thread.is_alive()
            assert owner._done.done()
        finally:
            release_owner.set()
            if closing is not None:
                await asyncio.gather(closing, return_exceptions=True)
            await asyncio.to_thread(join)

    asyncio.run(exercise())


@pytest.mark.parametrize(
    "error_type", [RuntimeError, asyncio.CancelledError, KeyboardInterrupt, SystemExit]
)
def test_owner_cleanup_failure_reaches_close_caller(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, error_type: type[BaseException]
) -> None:
    from scryntic.application.job_operations import JobsListRequest
    from scryntic.workflows.jobs import JobWorkflows

    original_close = JobStore.close

    def failing_close(store: JobStore) -> None:
        original_close(store)
        raise error_type("Owner cleanup unavailable")

    monkeypatch.setattr(JobStore, "close", failing_close)

    async def exercise() -> None:
        services = JobWorkflows(Context(tmp_path))
        await services.execute(JobsListRequest())
        with pytest.raises(error_type):
            await services.aclose()

    asyncio.run(exercise())
