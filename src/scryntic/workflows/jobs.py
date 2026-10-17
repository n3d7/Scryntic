"""Durable application job composition; model code runs only in fixed workers."""

from __future__ import annotations

import asyncio
import inspect
import json
import os
import stat
import threading
from collections.abc import Awaitable, Callable
from concurrent.futures import Future
from dataclasses import asdict
from hashlib import sha256
from pathlib import Path
from typing import Any, cast, overload

from scryntic.application.job_operations import (
    AnalyzeRequest,
    JobCancelRequest,
    JobResultRequest,
    JobResumeRequest,
    JobRunRequest,
    JobsListRequest,
    JobStatusRequest,
    ProvidersListRequest,
    SubmitJobRequest,
)
from scryntic.application.operations import (
    ApplicationError,
    ApplicationResult,
    ErrorCode,
    Request,
)
from scryntic.application.providers import ForecastRequest
from scryntic.archive.canonical import JsonValue, canonical_json_bytes
from scryntic.configuration.paths import Installation, directory
from scryntic.dataset.schemas import EVOLVED_DATASET_SCHEMA
from scryntic.domain.dataset import DatasetRef
from scryntic.forecast.artifact import ImmutableForecastStore
from scryntic.imports.protocol import ImportError as ArtifactImportError
from scryntic.jobs.contracts import JobRecord, JobRequest, JobState
from scryntic.jobs.service import JobService
from scryntic.jobs.store import JobError, JobStore
from scryntic.models.definitions import DEFINITIONS, definition, policy
from scryntic.models.preprocessing import ReplayJobInputs, ReplayWindows
from scryntic.models.provider import select_provider
from scryntic.models.selection import ModelSelection
from scryntic.models.window import ForecastWindow
from scryntic.replay.contracts import ReplayClock, ReplayConfig
from scryntic.workflows.data import DataContext, prepare_replay
from scryntic.workflows.wire import result

MAX_PLAN_BYTES = 65536


def _status(record: JobRecord) -> dict[str, object]:
    return {
        "job_id": record.job.job_id,
        "state": record.state.value,
        "attempt": record.attempt,
        "provider_id": record.job.review.descriptor.provider_id,
        "dataset": record.job.forecast.dataset.manifest_sha256,
        "deadline_ns": record.job.deadline_ns,
        "updated_ns": record.updated_ns,
        "reason": record.reason,
        "artifact": None if record.artifact is None else asdict(record.artifact),
    }


class _Plans:
    """Pinned private no-replace context objects; orphaned objects never dispatch."""

    def __init__(self, installation: Installation) -> None:
        self.installation = installation

    @staticmethod
    def _name(job_id: str) -> str:
        return sha256(job_id.encode()).hexdigest() + ".json"

    def read(self, job_id: str) -> dict[str, Any] | None:
        root = self.installation.state_dir / "job-contexts"
        try:
            with directory(root, self.installation.owner_uid, private=True) as root_fd:
                try:
                    fd = os.open(
                        self._name(job_id),
                        os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC,
                        dir_fd=root_fd,
                    )
                except FileNotFoundError:
                    return None
                try:
                    info = os.fstat(fd)
                    if (
                        not stat.S_ISREG(info.st_mode)
                        or info.st_uid != self.installation.owner_uid
                        or info.st_mode & 0o277
                    ):
                        raise ValueError("Unsafe job context")
                    raw = os.read(fd, MAX_PLAN_BYTES + 1)
                    if not 0 < len(raw) <= MAX_PLAN_BYTES:
                        raise ValueError("Unbounded job context")
                    value = json.loads(raw)
                    if type(value) is not dict or canonical_json_bytes(value) != raw:
                        raise ValueError("Noncanonical job context")
                    return value
                finally:
                    os.close(fd)
        except FileNotFoundError:
            return None

    def write(self, job_id: str, value: dict[str, Any]) -> None:
        raw = canonical_json_bytes(value)
        if not 0 < len(raw) <= MAX_PLAN_BYTES:
            raise ValueError("Unbounded job context")
        installation = self.installation
        with directory(
            installation.state_dir, installation.owner_uid, private=True
        ) as state_fd:
            try:
                os.mkdir("job-contexts", 0o700, dir_fd=state_fd)
                os.fsync(state_fd)
            except FileExistsError:
                pass
            with directory(
                installation.state_dir / "job-contexts",
                installation.owner_uid,
                private=True,
            ) as root_fd:
                if len(os.listdir(root_fd)) >= 1024 and self.read(job_id) is None:
                    raise ValueError("Job context quota reached")
                name = self._name(job_id)
                temporary = ".pending-" + os.urandom(16).hex()
                fd = os.open(
                    temporary,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                    0o600,
                    dir_fd=root_fd,
                )
                try:
                    with os.fdopen(fd, "wb") as output:
                        output.write(raw)
                        output.flush()
                        os.fchmod(output.fileno(), 0o400)
                        os.fsync(output.fileno())
                    try:
                        os.link(
                            temporary,
                            name,
                            src_dir_fd=root_fd,
                            dst_dir_fd=root_fd,
                            follow_symlinks=False,
                        )
                    except FileExistsError:
                        if self.read(job_id) != value:
                            raise ValueError("Job context identity conflict") from None
                    os.fsync(root_fd)
                finally:
                    os.unlink(temporary, dir_fd=root_fd)
                    os.fsync(root_fd)


class _Owner:
    """Retain a real owner loop through cancelled presentation awaiters."""

    def __init__(self, installation: Installation) -> None:
        self._installation = installation
        self._ready: Future[None] = Future()
        self._done: Future[None] = Future()
        self.loop: asyncio.AbstractEventLoop | None = None
        self.store: JobStore | None = None
        self.services: dict[str, JobService] = {}
        self.active: set[asyncio.Task[Any]] = set()
        self.closing = False
        self.thread = threading.Thread(
            target=self._run, name="application-jobs", daemon=False
        )
        self.thread.start()

    def _run(self) -> None:
        failure: BaseException | None = None
        try:
            self.loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self.loop)
            self.store = JobStore(self._installation)
            self._ready.set_result(None)
            self.loop.run_forever()
        except (
            Exception,
            asyncio.CancelledError,
            KeyboardInterrupt,
            SystemExit,
        ) as error:
            failure = error
            if not self._ready.done():
                self._ready.set_exception(error)
        finally:
            if self.store is not None:
                try:
                    self.store.close()
                except (
                    Exception,
                    asyncio.CancelledError,
                    KeyboardInterrupt,
                    SystemExit,
                ) as error:
                    failure = error
            if self.loop is not None:
                try:
                    self.loop.run_until_complete(self.loop.shutdown_asyncgens())
                    self.loop.run_until_complete(self.loop.shutdown_default_executor())
                except (
                    Exception,
                    asyncio.CancelledError,
                    KeyboardInterrupt,
                    SystemExit,
                ) as error:
                    failure = error
                try:
                    self.loop.close()
                except (
                    Exception,
                    asyncio.CancelledError,
                    KeyboardInterrupt,
                    SystemExit,
                ) as error:
                    failure = error
            if failure is None:
                self._done.set_result(None)
            else:
                self._done.set_exception(failure)

    @property
    def journal(self) -> JobStore:
        if self.store is None:
            raise ApplicationError(ErrorCode.UNAVAILABLE)
        return self.store

    @overload
    async def call[T](
        self, operation: Callable[[], Awaitable[T]], *, control: bool = False
    ) -> T: ...

    @overload
    async def call[T](
        self, operation: Callable[[], T], *, control: bool = False
    ) -> T: ...

    async def call[T](
        self, operation: Callable[[], T | Awaitable[T]], *, control: bool = False
    ) -> T:
        await asyncio.shield(asyncio.wrap_future(self._ready))
        if self.closing and not control:
            raise ApplicationError(ErrorCode.UNAVAILABLE)

        async def owned() -> T:
            if self.closing and not control:
                raise ApplicationError(ErrorCode.UNAVAILABLE)
            task = asyncio.current_task()
            assert task is not None
            if not control:
                if len(self.active) >= 8:
                    raise ApplicationError(ErrorCode.BUSY)
                self.active.add(task)
            try:
                result = operation()
                return await result if inspect.isawaitable(result) else result
            finally:
                self.active.discard(task)

        assert self.loop is not None
        retained = asyncio.wrap_future(
            asyncio.run_coroutine_threadsafe(owned(), self.loop)
        )
        retained.add_done_callback(lambda f: None if f.cancelled() else f.exception())
        return await asyncio.shield(retained)

    async def stop(self) -> None:
        self.closing = True

        async def settle() -> None:
            for service in tuple(self.services.values()):
                await service.aclose()
            if self.active:
                await asyncio.gather(*tuple(self.active), return_exceptions=True)

        if self._ready.done() and self._ready.exception() is not None:
            return
        await self.call(settle, control=True)

    async def close(self) -> None:
        cancellation: asyncio.CancelledError | None = None
        try:
            await self.stop()
        finally:
            initialization_failed = (
                self._ready.done() and self._ready.exception() is not None
            )
            if (
                not initialization_failed
                and self.loop is not None
                and self.thread.is_alive()
            ):
                self.loop.call_soon_threadsafe(self.loop.stop)
            joining = asyncio.create_task(asyncio.to_thread(self.thread.join))
            while not joining.done():
                try:
                    await asyncio.shield(joining)
                except asyncio.CancelledError as error:
                    cancellation = error
            joining.result()
            failure = self._done.exception()
            if failure is not None and failure is not self._ready.exception():
                raise failure
        if cancellation is not None:
            raise cancellation


class JobWorkflows:
    def __init__(self, context: DataContext) -> None:
        self.context = context
        self._owner: _Owner | None = None
        self._plans = _Plans(context.installation)
        self._closing = False

    def _get_owner(self) -> _Owner:
        if self._closing:
            raise ApplicationError(ErrorCode.UNAVAILABLE)
        if self._owner is None:
            self._owner = _Owner(self.context.installation)
        return self._owner

    def _selection(self, provider: str, leaf: str) -> ModelSelection:
        if not leaf:
            if definition(provider).adapter is not None:
                raise ApplicationError(ErrorCode.CONFIGURATION)
            return ModelSelection(provider)
        value = self.context.read_config_json(leaf, 4096)
        if (
            type(value) is not dict
            or not {"selected"}
            <= set(value)
            <= {"selected", "artifacts", "runtime", "runtime_sha256"}
            or any(type(v) is not str for v in value.values())
        ):
            raise ApplicationError(ErrorCode.CONFIGURATION)
        if value["selected"] != provider:
            raise ApplicationError(ErrorCode.CONFIGURATION)
        checked = cast(dict[str, str], value)
        return ModelSelection(
            provider,
            Path(checked["artifacts"]) if "artifacts" in checked else None,
            Path(checked["runtime"]) if "runtime" in checked else None,
            checked.get("runtime_sha256"),
        )

    def _prepare(
        self, request: SubmitJobRequest, existing: dict[str, Any] | None
    ) -> tuple[dict[str, Any], ForecastWindow, Any]:
        selection = self._selection(request.provider, request.model_config)
        third = (request.end_ns - request.start_ns) // 3
        config = ReplayConfig(
            request.start_ns,
            request.start_ns + third,
            request.start_ns + 2 * third,
            request.end_ns,
        )
        prepared = prepare_replay(self.context, request.dataset, config)
        reader = prepared.reader
        reference = DatasetRef(
            request.dataset, EVOLVED_DATASET_SCHEMA, len(reader.rows)
        )
        if not 2 <= reference.row_count <= 1024:
            raise ValueError("Model dataset outside bounds")
        index = (
            reference.row_count - 1
            if request.origin_index == -1
            else request.origin_index
        )
        if not 0 <= index < reference.row_count:
            raise ValueError("Invalid model origin")
        decision = request.end_ns if request.decision_ns == -1 else request.decision_ns
        row = reader.rows[index]
        if (
            not request.start_ns
            <= row["start_ns"]
            < row["start_ns"] + row["interval_ns"]
            <= decision
            <= request.end_ns
        ):
            raise ValueError("Origin outside requested history")
        window = ReplayWindows(reader, reference).at(
            index, ReplayClock(decision).sample
        )
        selected = tuple(
            i for i, start in enumerate(window.starts) if start >= request.start_ns
        )
        window = ForecastWindow(
            reference,
            tuple(window.starts[i] for i in selected),
            tuple(window.closes[i] for i in selected),
            window.frequency_ns,
        )
        if len(window.starts) < 2:
            raise ValueError("Insufficient approved model history")
        projection = {
            "version": 1,
            "request": asdict(request),
            "replay": config.projection(),
            "origin_index": index,
            "decision_ns": decision,
            "selection": {
                "selected": selection.selected,
                "artifacts": None
                if selection.artifacts is None
                else str(selection.artifacts),
                "runtime": None
                if selection.runtime is None
                else str(selection.runtime),
                "runtime_sha256": selection.runtime_sha256,
            },
            "dataset": asdict(reference),
            "context_sha256": sha256(
                canonical_json_bytes(window.projection())
            ).hexdigest(),
        }
        if existing is not None:
            expected = {
                k: v
                for k, v in existing.items()
                if k not in {"submitted_ns", "deadline_ns"}
            }
            if expected != json.loads(
                canonical_json_bytes(cast(JsonValue, projection))
            ):
                raise ValueError("Immutable job context changed")
        inputs = ReplayJobInputs({request.job_id: window})
        provider = select_provider(selection, inputs)
        return projection, window, provider

    async def _submit(self, request: SubmitJobRequest) -> JobRecord:
        owner = self._get_owner()
        existing = await owner.call(lambda: self._plans.read(request.job_id))
        projection, window, provider = await self.context.run_io(
            lambda: self._prepare(request, existing)
        )

        def submit() -> JobRecord:
            assert owner.store is not None
            submitted = (
                owner.store.now() if existing is None else existing["submitted_ns"]
            )
            projection.update(
                submitted_ns=submitted,
                deadline_ns=submitted + request.timeout_seconds * 10**9,
            )
            inputs = ReplayJobInputs({request.job_id: window})
            service = JobService(
                owner.store,
                (provider,),
                policy((provider.review,)),
                ImmutableForecastStore(self.context.installation),
                inputs,
                max_active=1,
            )
            forecast = ForecastRequest(
                request_id=request.job_id,
                dataset=window.dataset,
                target="close",
                frequency_ns=window.frequency_ns,
                horizon=request.horizon,
            )
            # Validate admission before making a durable context; immutable plan is
            # installed before the journal so a crash cannot leave a runnable job
            # without its context proof.
            job = JobRequest(
                forecast=forecast,
                review=provider.review,
                policy=policy((provider.review,)),
                inputs=inputs.read(forecast),
                intended_use="forecast-research",
                submitted_ns=submitted,
                deadline_ns=projection["deadline_ns"],
                seed=request.seed,
            )
            self._plans.write(request.job_id, projection)
            record = service.submit_job(job)
            owner.services.setdefault(request.job_id, service)
            return record

        return await owner.call(submit)

    async def _run(self, request: JobRunRequest | JobResumeRequest) -> JobRecord:
        owner = self._get_owner()
        assert owner is not None
        record = await owner.call(lambda: owner.journal.get(request.job_id))
        if record.state is JobState.SUCCEEDED and type(request) is JobRunRequest:
            return record
        plan = await owner.call(lambda: self._plans.read(request.job_id))
        if plan is None:
            raise ApplicationError(ErrorCode.UNAVAILABLE)
        if (
            set(plan)
            != {
                "version",
                "request",
                "replay",
                "origin_index",
                "decision_ns",
                "selection",
                "dataset",
                "context_sha256",
                "submitted_ns",
                "deadline_ns",
            }
            or plan["version"] != 1
        ):
            raise ApplicationError(ErrorCode.UNAVAILABLE)
        submission = SubmitJobRequest(**plan["request"])
        if (
            submission.job_id != request.job_id
            or plan["submitted_ns"] != record.job.submitted_ns
            or plan["deadline_ns"] != record.job.deadline_ns
        ):
            raise ApplicationError(ErrorCode.UNAVAILABLE)
        _, window, provider = await self.context.run_io(
            lambda: self._prepare(submission, plan)
        )
        forecast = ForecastRequest(
            request_id=submission.job_id,
            dataset=window.dataset,
            target="close",
            frequency_ns=window.frequency_ns,
            horizon=submission.horizon,
        )
        reconstructed = JobRequest(
            forecast=forecast,
            review=provider.review,
            policy=policy((provider.review,)),
            inputs=ReplayJobInputs({submission.job_id: window}).read(forecast),
            intended_use="forecast-research",
            submitted_ns=plan["submitted_ns"],
            deadline_ns=plan["deadline_ns"],
            seed=submission.seed,
        )
        if reconstructed != record.job:
            raise ApplicationError(ErrorCode.UNAVAILABLE)

        async def execute() -> JobRecord:
            assert owner.store is not None
            service = owner.services.get(request.job_id)
            if service is None:
                service = JobService(
                    owner.store,
                    (provider,),
                    policy((provider.review,)),
                    ImmutableForecastStore(self.context.installation),
                    ReplayJobInputs({request.job_id: window}),
                    max_active=1,
                )
                owner.services[request.job_id] = service
            return await service.run(
                request.job_id, resume=type(request) is JobResumeRequest
            )

        return await owner.call(execute)

    @staticmethod
    def _providers() -> list[dict[str, object]]:
        return [
            {
                "name": item.name,
                "origin": item.origin,
                "publisher": item.publisher,
                "revision": item.revision,
                "capabilities": ["forecast"],
                "execution": "isolated-local",
                "max_rows": 1024,
                "max_horizon": 24,
                "requires_artifacts": item.adapter is not None,
            }
            for item in DEFINITIONS
        ]

    async def _submit_result(self, request: SubmitJobRequest) -> ApplicationResult:
        record = await self._submit(request)
        complete = True
        if type(request) is AnalyzeRequest:
            record = await self._run(JobRunRequest(request.job_id))
            complete = record.state is JobState.SUCCEEDED
        return result(request.operation, {"job": _status(record)}, complete=complete)

    async def _run_result(
        self, request: JobRunRequest | JobResumeRequest
    ) -> ApplicationResult:
        record = await self._run(request)
        return result(
            request.operation,
            {"job": _status(record)},
            complete=record.state is JobState.SUCCEEDED,
        )

    async def _list_result(self, request: JobsListRequest) -> ApplicationResult:
        owner = self._get_owner()
        records = await owner.call(
            lambda: owner.journal.list(limit=request.limit, offset=request.offset)
        )
        return result(
            request.operation,
            {
                "jobs": [_status(record) for record in records],
                "limit": request.limit,
                "offset": request.offset,
            },
        )

    def _verified_result(self, record: JobRecord) -> dict[str, object]:
        if record.state is not JobState.SUCCEEDED or record.artifact is None:
            raise ApplicationError(ErrorCode.INCOMPLETE)
        value = ImmutableForecastStore(self.context.installation).read(record.artifact)
        if value.get("job") != {
            "request_sha256": record.job.sha256,
            "review_sha256": record.job.review.sha256,
            "policy_sha256": record.job.policy.sha256,
            "intended_use": record.job.intended_use,
        }:
            raise ApplicationError(ErrorCode.UNAVAILABLE)
        return {"artifact": asdict(record.artifact), "result": value["result"]}

    def _control_fields(
        self, owner: _Owner, request: JobStatusRequest
    ) -> dict[str, object]:
        assert owner.store is not None
        if type(request) is JobCancelRequest:
            service = owner.services.get(request.job_id)
            record = (
                service.cancel(request.job_id)
                if service is not None
                else owner.store.cancel(request.job_id)
            )
        else:
            record = owner.store.get(request.job_id)
        if type(request) is JobResultRequest:
            return self._verified_result(record)
        return {"job": _status(record)}

    async def _control_result(self, request: JobStatusRequest) -> ApplicationResult:
        owner = self._get_owner()
        fields = await owner.call(lambda: self._control_fields(owner, request))
        return result(request.operation, fields)

    async def _dispatch(self, request: Request) -> ApplicationResult:
        if type(request) is ProvidersListRequest:
            return result(request.operation, {"providers": self._providers()})
        if type(request) in (SubmitJobRequest, AnalyzeRequest):
            return await self._submit_result(cast(SubmitJobRequest, request))
        if type(request) in (JobRunRequest, JobResumeRequest):
            return await self._run_result(
                cast(JobRunRequest | JobResumeRequest, request)
            )
        if type(request) is JobsListRequest:
            return await self._list_result(request)
        if type(request) in (JobStatusRequest, JobCancelRequest, JobResultRequest):
            return await self._control_result(cast(JobStatusRequest, request))
        raise ApplicationError(ErrorCode.UNSUPPORTED)

    async def execute(self, request: Request) -> ApplicationResult:
        try:
            return await self._dispatch(request)
        except ApplicationError:
            raise
        except JobError as error:
            code = (
                ErrorCode.BUSY
                if str(error) == "Provider job state already owned"
                else ErrorCode.FAILED
            )
            raise ApplicationError(code) from None
        except (ArtifactImportError, OSError, ValueError, TypeError, KeyError):
            raise ApplicationError(ErrorCode.UNAVAILABLE) from None

    async def cancel(self) -> None:
        self._closing = True
        if self._owner is not None:
            await self._owner.stop()

    async def aclose(self) -> None:
        self._closing = True
        if self._owner is not None:
            owner, self._owner = self._owner, None
            await owner.close()
