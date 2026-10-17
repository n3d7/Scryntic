"""Finite local collection and truthful offline configuration/status workflows."""

import asyncio
import json
import signal
import sys
import threading
from collections.abc import Callable
from importlib.util import find_spec
from typing import Protocol

from scryntic.application.local_operations import (
    BackfillRequest,
    CollectRequest,
    ConfigValidateRequest,
    SourceInspectRequest,
    SourcesListRequest,
    StatusRequest,
)
from scryntic.application.operations import (
    ApplicationError,
    ApplicationResult,
    ErrorCode,
    Request,
)
from scryntic.application.sources import SourceDescriptor
from scryntic.configuration.loader import load_configuration
from scryntic.configuration.paths import Installation
from scryntic.configuration.values import BoundaryError, Capability
from scryntic.domain.time import ClockSample
from scryntic.sources.bybit import BybitHistoricalSource
from scryntic.sources.bybit_live import BybitLiveSource
from scryntic.sources.fake import DeterministicCandleSource
from scryntic.workflows.wire import result


class LocalContext(Protocol):
    @property
    def installation(self) -> Installation: ...

    async def run_io[T](self, function: Callable[[], T]) -> T: ...


class _DescriptorClock:
    def sample(self) -> ClockSample:
        raise RuntimeError("Descriptor inspection cannot observe a clock")


def _descriptor(value: SourceDescriptor) -> list[dict[str, object]]:
    return [
        {
            "schema": {
                "name": item.schema.name,
                "major": item.schema.version.major,
                "minor": item.schema.version.minor,
            },
            "subject_kind": item.subject_kind,
            "category": item.category,
            "operations": sorted(operation.value for operation in item.operations),
            "sequencing": item.sequencing,
            "recovery": item.recovery,
            "history_start_ns": item.history_start_ns,
            "history_end_ns": item.history_end_ns,
        }
        for item in value.capabilities
    ]


async def _source(profile: str) -> dict[str, object]:
    if profile == "fixture":
        source = DeterministicCandleSource()
        try:
            return {
                "profile": profile,
                "source_id": source.descriptor.source_id,
                "network": False,
                "capabilities": _descriptor(source.descriptor),
                "max_page_records": source.descriptor.max_page_records,
            }
        finally:
            await source.close()
    clock = _DescriptorClock()
    history = BybitHistoricalSource(clock=clock)
    live = BybitLiveSource(clock=clock)
    try:
        return {
            "profile": profile,
            "source_id": history.descriptor.source_id,
            "network": True,
            "capabilities": _descriptor(history.descriptor)
            + _descriptor(live.descriptor),
            "max_page_records": history.descriptor.max_page_records,
        }
    finally:
        await live.close()
        await history.close()


class LocalWorkflows:
    def __init__(self, context: LocalContext) -> None:
        self._context = context
        self._cancel = threading.Event()
        self._pending: asyncio.Task[ApplicationResult] | None = None
        self._process: asyncio.subprocess.Process | None = None

    async def execute(self, request: Request) -> ApplicationResult:
        if self._pending is not None:
            raise ApplicationError(ErrorCode.BUSY)
        self._cancel.clear()
        task = asyncio.create_task(
            self._child(request)
            if isinstance(request, (CollectRequest, BackfillRequest))
            else self._context.run_io(lambda: self._execute(request))
        )
        self._pending = task
        try:
            return await _await_owned(task, self._cancel.set)
        finally:
            self._pending = None

    async def cancel(self) -> None:
        self._cancel.set()
        pending = self._pending
        if pending is not None:
            try:
                await _await_owned(pending, self._cancel.set)
            except ApplicationError:
                # Explicit cancellation is reported by the operation's waiter.
                pass

    async def aclose(self) -> None:
        await self.cancel()

    async def _child(
        self, request: CollectRequest | BackfillRequest
    ) -> ApplicationResult:
        from scryntic.workflows.collection_host import encode_request

        def approved() -> bytes:
            configuration = load_configuration(self._context.installation)
            configuration.required_reference(Capability.PUBLIC_COLLECTION)
            if find_spec("pyarrow") is None:
                raise ApplicationError(ErrorCode.UNAVAILABLE)
            return encode_request(self._context.installation, request)

        try:
            raw = await self._context.run_io(approved)
        except BoundaryError:
            raise ApplicationError(ErrorCode.CONFIGURATION) from None
        deadline = asyncio.get_running_loop().time() + _child_deadline_seconds(request)
        try:
            process = await _spawn_collection()
        except OSError:
            raise ApplicationError(ErrorCode.UNAVAILABLE) from None
        self._process = process
        owned = _ChildIO(process, raw)
        try:
            reply = await owned.run(self._cancel, deadline)
            return _decode_reply(reply, request)
        except (OSError, ValueError, TypeError):
            raise ApplicationError(ErrorCode.INCOMPLETE) from None
        finally:
            cleanup = asyncio.create_task(owned.aclose())
            try:
                await _await_owned(cleanup)
            finally:
                self._process = None

    def _execute(self, request: Request) -> ApplicationResult:
        try:
            configuration = load_configuration(self._context.installation)
            if isinstance(request, ConfigValidateRequest):
                return result(request.operation, configuration.diagnostics())
            if isinstance(request, StatusRequest):
                return result(
                    request.operation,
                    {
                        "observation": "offline",
                        "liveness": "unknown",
                        "readiness": "unknown",
                        "configuration": configuration.diagnostics(),
                        "live_observation_available": False,
                    },
                )
            if isinstance(request, SourceInspectRequest):
                return result(request.operation, asyncio.run(_source(request.profile)))
            if isinstance(request, SourcesListRequest):

                async def sources() -> list[dict[str, object]]:
                    return [await _source("fixture"), await _source("bybit")]

                return result(request.operation, {"sources": asyncio.run(sources())})
            raise ApplicationError(ErrorCode.UNSUPPORTED)
        except ApplicationError:
            raise
        except BoundaryError:
            raise ApplicationError(ErrorCode.CONFIGURATION) from None
        except OSError:
            raise ApplicationError(ErrorCode.UNAVAILABLE) from None
        except (ValueError, TypeError):
            raise ApplicationError(ErrorCode.INVALID) from None


async def _await_owned[T](
    task: asyncio.Task[T], on_cancel: Callable[[], None] | None = None
) -> T:
    cancellation: asyncio.CancelledError | None = None
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError as error:
            cancellation = error
            if on_cancel is not None:
                on_cancel()
        except Exception:
            break
    if cancellation is not None:
        if not task.cancelled():
            task.exception()
        raise cancellation
    return task.result()


class _ChildIO:
    def __init__(self, process: asyncio.subprocess.Process, raw: bytes) -> None:
        self.process = process
        assert process.stdin is not None
        assert process.stdout is not None
        assert process.stderr is not None
        self.overflow = asyncio.Event()
        self.writer = asyncio.create_task(_write_request(process.stdin, raw))
        self.stdout = asyncio.create_task(
            _read_bounded(process.stdout, 1024 * 1024, self.overflow)
        )
        self.stderr = asyncio.create_task(
            _read_bounded(process.stderr, 65536, self.overflow)
        )
        self.waited = asyncio.create_task(process.wait())
        self.readers = (self.writer, self.stdout, self.stderr)

    def _failed(self) -> bool:
        return self.overflow.is_set() or any(
            task.done() and not task.cancelled() and task.exception() is not None
            for task in self.readers
        )

    async def run(self, cancel: threading.Event, deadline: float) -> bytes:
        terminated = False
        forced = False
        loop = asyncio.get_running_loop()
        while not all(task.done() for task in (*self.readers, self.waited)):
            if cancel.is_set() and not terminated and self.process.returncode is None:
                self.process.send_signal(signal.SIGTERM)
                terminated = True
                deadline = min(deadline, loop.time() + 20)
            if self._failed() or loop.time() >= deadline:
                forced = True
                if self.process.returncode is None:
                    self.process.kill()
                break
            await asyncio.sleep(0.05)
        code = await self.waited
        if cancel.is_set():
            raise ApplicationError(ErrorCode.CANCELLED)
        if code == ApplicationError(ErrorCode.UNAVAILABLE).exit_code:
            raise ApplicationError(ErrorCode.UNAVAILABLE)
        if forced or code != 0:
            raise ApplicationError(ErrorCode.INCOMPLETE)
        return self.stdout.result()

    async def aclose(self) -> None:
        if self.process.returncode is None:
            self.process.kill()
        await self.waited
        for task in self.readers:
            if not task.done():
                task.cancel()
        await asyncio.gather(*self.readers, return_exceptions=True)


async def _write_request(stream: asyncio.StreamWriter, raw: bytes) -> None:
    try:
        stream.write(raw)
        await stream.drain()
    except (BrokenPipeError, ConnectionResetError):
        pass
    finally:
        stream.close()


def _decode_reply(
    raw: bytes, request: CollectRequest | BackfillRequest
) -> ApplicationResult:
    fields = json.loads(raw)
    if (
        type(fields) is not dict
        or set(fields) != {"version", "ok", "operation", "complete", "data"}
        or type(fields["version"]) is not int
        or fields["version"] != 1
        or fields["ok"] is not True
        or fields["operation"] != request.operation.value
        or type(fields["complete"]) is not bool
        or type(fields["data"]) is not dict
    ):
        raise ApplicationError(ErrorCode.INCOMPLETE)
    return result(request.operation, fields["data"], complete=fields["complete"])


async def _spawn_collection() -> asyncio.subprocess.Process:
    return await asyncio.create_subprocess_exec(
        sys.executable,
        "-I",
        "-m",
        "scryntic.workflows.collection_host",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"},
    )


def _child_deadline_seconds(request: CollectRequest | BackfillRequest) -> float:
    return 30.0 + request.seconds + 15.0 + 5.0


async def _read_bounded(
    stream: asyncio.StreamReader, maximum: int, overflow: asyncio.Event
) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while block := await stream.read(8192):
        total += len(block)
        if total > maximum:
            # Continue consuming without retaining excess output. A paused pipe
            # can otherwise prevent Process.wait() from completing after kill.
            overflow.set()
        else:
            chunks.append(block)
    if total > maximum:
        raise ApplicationError(ErrorCode.INCOMPLETE)
    return b"".join(chunks)
