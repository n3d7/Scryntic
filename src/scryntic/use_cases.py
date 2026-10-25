"""Reusable application facade; presentation never owns a subsystem resource."""

import asyncio
from typing import Protocol

from scryntic.application.operations import (
    ApplicationError,
    ApplicationResult,
    ErrorCode,
    Operation,
    Request,
    Workflow,
)
from scryntic.configuration.loader import load_configuration
from scryntic.configuration.paths import Installation
from scryntic.configuration.values import BoundaryError, Capability
from scryntic.workflows.context import WorkflowContext


class Handler(Protocol):
    async def execute(self, request: Request) -> ApplicationResult: ...


def available_workflows() -> tuple[Workflow, ...]:
    from scryntic.application.data_operations import WORKFLOWS as data
    from scryntic.application.forecast_operations import WORKFLOWS as forecasts
    from scryntic.application.job_operations import WORKFLOWS as jobs
    from scryntic.application.local_operations import WORKFLOWS as local
    from scryntic.application.sync_operations import WORKFLOWS as sync

    return local + sync + data + jobs + forecasts


class ApplicationServices:
    def __init__(
        self,
        installation: Installation | None = None,
        *,
        context: WorkflowContext | None = None,
    ) -> None:
        self.context = context or WorkflowContext(installation)
        self._handlers: dict[str, Handler] = {}
        self._active: asyncio.Task[ApplicationResult] | None = None
        self._current: Handler | None = None
        self._cancel_requested = False
        self._settlement: asyncio.Task[None] | None = None
        self._close_task: asyncio.Task[None] | None = None
        self._closed = False

    def _handler(self, request: Request) -> Handler:
        group = _operation_group(request.operation)
        if (
            group in ("data", "jobs", "forecasts")
            and request.operation is not Operation.PROVIDERS_LIST
        ):
            configuration = load_configuration(self.context.installation)
            if Capability.LOCAL_ANALYSIS not in configuration.enabled:
                raise ApplicationError(ErrorCode.UNSUPPORTED)
        if group not in self._handlers:
            self._handlers[group] = self._create_handler(group)
        return self._handlers[group]

    def _create_handler(self, group: str) -> Handler:
        if group == "forecasts":
            from scryntic.workflows.forecasts import ForecastWorkflows

            return ForecastWorkflows(self.context)
        if group == "data":
            from scryntic.workflows.data import DataWorkflows

            return DataWorkflows(self.context)
        if group == "jobs":
            from scryntic.workflows.jobs import JobWorkflows

            return JobWorkflows(self.context)
        if group == "sync":
            from scryntic.workflows.synchronization import SynchronizationWorkflows

            return SynchronizationWorkflows(self.context)
        from scryntic.workflows.local import LocalWorkflows

        return LocalWorkflows(self.context)

    async def _execute(self, request: Request) -> ApplicationResult:
        try:
            self._current = self._handler(request)
            value = await self._current.execute(request)
            if self._cancel_requested:
                raise ApplicationError(ErrorCode.CANCELLED)
            return value
        except ApplicationError:
            raise
        except BoundaryError:
            raise ApplicationError(ErrorCode.CONFIGURATION) from None
        except ImportError:
            raise ApplicationError(ErrorCode.UNAVAILABLE) from None
        except Exception:
            raise ApplicationError(ErrorCode.FAILED) from None
        finally:
            self._current = None

    async def execute(self, request: Request) -> ApplicationResult:
        if (
            self._closed
            or (self._active is not None and not self._active.done())
            or (self._settlement is not None and not self._settlement.done())
        ):
            raise ApplicationError(ErrorCode.BUSY)
        if not isinstance(request.operation, Operation):
            raise ApplicationError(ErrorCode.INVALID)
        self._cancel_requested = False
        self._settlement = None
        self._active = asyncio.create_task(self._execute(request))
        self._active.add_done_callback(_observe)
        try:
            return await asyncio.shield(self._active)
        except asyncio.CancelledError:
            await self.cancel()
            raise

    async def _settle(self) -> None:
        self._cancel_requested = True
        handler = self._current
        cancel = getattr(handler, "cancel", None)
        if cancel is not None:
            await cancel()
        if self._active is not None:
            await asyncio.wait((self._active,))
        if handler is self._handlers.get("jobs"):
            close = getattr(handler, "aclose", None)
            if close is not None:
                await close()
            self._handlers.pop("jobs", None)

    async def cancel(self) -> None:
        if self._active is None or self._active.done():
            return
        if self._settlement is None:
            self._settlement = asyncio.create_task(self._settle())
        await _join(self._settlement)

    async def _close(self) -> None:
        self._closed = True
        await self.cancel()
        for handler in self._handlers.values():
            close = getattr(handler, "aclose", None)
            if close is not None:
                await close()
        await self.context.aclose()

    async def aclose(self) -> None:
        if self._close_task is None:
            self._close_task = asyncio.create_task(self._close())
        await _join(self._close_task)


async def _join(task: asyncio.Task[None]) -> None:
    cancellation = None
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError as interrupted:
            if task.cancelled():
                raise
            cancellation = interrupted
    task.result()
    if cancellation is not None:
        raise cancellation


def _operation_group(operation: Operation) -> str:
    if operation.value.startswith("forecasts."):
        return "forecasts"
    if operation in (
        Operation.DATA_INSPECT,
        Operation.DATASET_BUILD,
        Operation.DATASET_INSPECT,
        Operation.DATASET_EXPORT,
        Operation.REPLAY,
    ):
        return "data"
    if operation.value.startswith("jobs.") or operation in (
        Operation.ANALYZE,
        Operation.PROVIDERS_LIST,
    ):
        return "jobs"
    if operation.value.startswith("sync."):
        return "sync"
    return "local"


def _observe(task: asyncio.Task[ApplicationResult]) -> None:
    if not task.cancelled():
        task.exception()
