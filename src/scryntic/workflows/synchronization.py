"""Protected enrollment, offline anchors, and retained authenticated F17 pulls."""

import asyncio
import threading
from collections.abc import Awaitable, Callable
from dataclasses import asdict
from typing import Protocol, cast

from scryntic.application.operations import (
    ApplicationError,
    ApplicationResult,
    ErrorCode,
    Request,
)
from scryntic.application.sync_operations import (
    SyncEnrollRequest,
    SyncPullRequest,
    SyncStatusRequest,
)
from scryntic.archive.canonical import JsonValue, canonical_json_bytes
from scryntic.clock.chrony import ChronyStatus
from scryntic.clock.host import SystemClockReader
from scryntic.clock.monitor import ClockMonitor
from scryntic.configuration.loader import load_configuration
from scryntic.configuration.paths import Installation
from scryntic.configuration.values import BoundaryError, Capability, Configuration
from scryntic.imports.protocol import ImportError as ArtifactImportError
from scryntic.sync.catalog import PullCatalog
from scryntic.sync.model import Enrollment, PullError, PullLimits
from scryntic.sync.pull import pull
from scryntic.sync.sftp import SFTPRemote
from scryntic.workflows.wire import result


class SyncContext(Protocol):
    @property
    def installation(self) -> Installation: ...

    async def run_io[T](self, function: Callable[[], T]) -> T: ...

    def read_config_json(self, leaf: str, maxbytes: int) -> object: ...


class _Worker:
    """One cancellation signal, delivered to the real owner instead of its awaiter."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._task: asyncio.Task[dict[str, object]] | None = None
        self._cancelled = False

    async def run(
        self, operation: Callable[[], Awaitable[dict[str, object]]]
    ) -> dict[str, object]:
        with self._lock:
            self._loop = asyncio.get_running_loop()
            self._task = cast(asyncio.Task[dict[str, object]], asyncio.current_task())
            cancelled = self._cancelled
        try:
            if cancelled:
                raise asyncio.CancelledError
            return await operation()
        finally:
            with self._lock:
                self._loop = None
                self._task = None

    def cancel(self) -> None:
        with self._lock:
            if self._cancelled:
                return
            self._cancelled = True
            if self._loop is not None and self._task is not None:
                self._loop.call_soon_threadsafe(self._task.cancel)


class SynchronizationWorkflows:
    def __init__(self, context: SyncContext) -> None:
        self.context = context
        self._worker: _Worker | None = None
        self._active: asyncio.Task[dict[str, object]] | None = None
        self._closing = False
        self._clock: ClockMonitor | None = None
        self._clock_configuration: Configuration | None = None

    def _configuration(self) -> Configuration:
        configuration = load_configuration(self.context.installation)
        if Capability.SYNCHRONIZATION not in configuration.enabled:
            raise ApplicationError(ErrorCode.UNSUPPORTED)
        return configuration

    def _descriptor(
        self, request: SyncEnrollRequest
    ) -> tuple[Enrollment, bytes | None]:
        value = self.context.read_config_json(request.descriptor, 65536)
        try:
            if (
                type(value) is not dict
                or set(value) != {"version", "endpoint", "trust"}
                or type(value["version"]) is not int
                or value["version"] != 1
            ):
                raise ValueError("Unknown enrollment descriptor")
            endpoint, trust = value["endpoint"], value["trust"]
            if type(endpoint) is not dict or set(endpoint) != {
                "name",
                "host",
                "port",
                "username",
                "remote_root",
                "host_key",
                "producer",
                "epoch",
            }:
                raise ValueError("Unknown endpoint fields")
            enrolled = Enrollment(**endpoint)
            if type(trust) is not dict:
                raise ValueError("Missing explicit trust")
            if trust == {"mode": "genesis"}:
                return enrolled, None
            if (
                set(trust) != {"mode", "manifest"}
                or trust["mode"] != "checkpoint"
                or type(trust["manifest"]) is not dict
            ):
                raise ValueError("Unknown trust policy")
            return enrolled, canonical_json_bytes(cast(JsonValue, trust["manifest"]))
        except (TypeError, ValueError, KeyError):
            raise ApplicationError(ErrorCode.CONFIGURATION) from None

    async def _execute(self, request: Request) -> dict[str, object]:
        configuration = self._configuration()
        if type(request) is SyncEnrollRequest:
            endpoint, checkpoint = self._descriptor(request)
            with PullCatalog(self.context.installation) as catalog:
                catalog.enroll(endpoint, checkpoint)
                return {
                    "name": endpoint.name,
                    "anchor": asdict(catalog.anchor(endpoint.name)),
                    "bootstrap": "genesis" if checkpoint is None else "checkpoint",
                }
        if type(request) is SyncStatusRequest:
            with PullCatalog(self.context.installation) as catalog:
                endpoint = catalog.enrollment(request.name)
                return {
                    "name": endpoint.name,
                    "producer": endpoint.producer,
                    "epoch": endpoint.epoch,
                    "anchor": asdict(catalog.anchor(request.name)),
                    "epochs": list(catalog.epochs(request.name)),
                    "freshness": "unknown",
                    "observation": "offline",
                }
        if type(request) is not SyncPullRequest:
            raise ApplicationError(ErrorCode.UNSUPPORTED)
        limits = PullLimits(
            wall_seconds=request.wall_seconds,
            max_new_manifests=request.max_new_manifests,
        )
        with PullCatalog(self.context.installation, pull_limits=limits) as catalog:
            endpoint = catalog.enrollment(request.name)
            if self._clock is None or self._clock_configuration != configuration:
                self._clock = ClockMonitor(
                    SystemClockReader(), ChronyStatus(), configuration.clock
                )
                self._clock_configuration = configuration
            async with asyncio.timeout(limits.wall_seconds):
                receipt = self._clock.sample()
                async with SFTPRemote(
                    endpoint, self.context.installation, configuration, limits
                ) as remote:
                    accepted = await pull(
                        catalog, request.name, remote, receipt, limits
                    )
            return {
                "name": request.name,
                "anchor": asdict(accepted.anchor),
                "imported": [asdict(reference) for reference in accepted.imported],
                "transferred_bytes": accepted.transferred_bytes,
                "transferred_objects": accepted.transferred_objects,
                "complete": accepted.complete,
                "freshness": accepted.freshness,
                "observation": "authenticated-pull",
                "receipt": asdict(receipt),
            }

    async def execute(self, request: Request) -> ApplicationResult:
        if type(request) not in (SyncStatusRequest, SyncEnrollRequest, SyncPullRequest):
            raise ApplicationError(ErrorCode.UNSUPPORTED)
        if self._closing:
            raise ApplicationError(ErrorCode.UNAVAILABLE)
        if self._active is not None and not self._active.done():
            raise ApplicationError(ErrorCode.BUSY)
        worker = _Worker()
        self._worker = worker
        self._active = asyncio.create_task(
            self.context.run_io(
                lambda: asyncio.run(worker.run(lambda: self._guarded(request)))
            )
        )
        self._active.add_done_callback(
            lambda task: None if task.cancelled() else task.exception()
        )
        active = self._active
        try:
            fields = await asyncio.shield(active)
            return result(
                request.operation, fields, complete=fields.get("complete", True) is True
            )
        except ApplicationError:
            raise
        except BoundaryError:
            raise ApplicationError(ErrorCode.CONFIGURATION) from None
        except ArtifactImportError:
            raise ApplicationError(ErrorCode.UNAVAILABLE) from None
        except PullError:
            raise ApplicationError(ErrorCode.FAILED) from None
        except (OSError, ValueError, TypeError):
            raise ApplicationError(ErrorCode.UNAVAILABLE) from None
        finally:
            if active.done() and self._active is active:
                self._active = None

    async def _guarded(self, request: Request) -> dict[str, object]:
        try:
            return await self._execute(request)
        except ApplicationError:
            raise
        except BoundaryError:
            raise ApplicationError(ErrorCode.CONFIGURATION) from None
        except ArtifactImportError:
            raise ApplicationError(ErrorCode.UNAVAILABLE) from None
        except PullError:
            raise ApplicationError(ErrorCode.FAILED) from None
        except (OSError, ValueError, TypeError):
            raise ApplicationError(ErrorCode.UNAVAILABLE) from None

    async def cancel(self) -> None:
        if self._worker is not None:
            self._worker.cancel()
        if self._active is not None:
            await _join_transport(self._active)

    async def aclose(self) -> None:
        self._closing = True
        await self.cancel()


async def _join_transport(active: asyncio.Task[dict[str, object]]) -> None:
    """Join the actual transport owner, preserving caller cancellation."""
    cancellation = None
    while not active.done():
        try:
            await asyncio.shield(active)
        except asyncio.CancelledError as interrupted:
            caller = asyncio.current_task()
            if caller is not None and caller.cancelling():
                cancellation = interrupted
            if active.cancelled():
                break
    if not active.cancelled():
        active.result()
    if cancellation is not None:
        raise cancellation
