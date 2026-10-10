"""Execution-owned lifecycle: deadline expiry does not terminate an operation."""

import asyncio
import math
import time
from dataclasses import dataclass, replace
from typing import Protocol

from scryntic.daemon.health import (
    Fault,
    HealthCache,
    HealthSnapshot,
    ShutdownPhase,
    State,
)
from scryntic.daemon.logging import Event, StructuredLogger


class Services(Protocol):
    async def start(self) -> None: ...

    def stop_intake(self) -> None: ...

    async def drain(self) -> None: ...

    async def close(self) -> None: ...

    def health(self) -> HealthSnapshot: ...


@dataclass(frozen=True, slots=True)
class LifecycleLimits:
    startup_s: float = 30.0
    shutdown_s: float = 15.0
    poll_s: float = 0.1

    def __post_init__(self) -> None:
        for value in (self.startup_s, self.shutdown_s, self.poll_s):
            if (
                isinstance(value, bool)
                or not math.isfinite(value)
                or not 0 < value <= 300
            ):
                raise ValueError("Invalid lifecycle deadline")
        if self.poll_s > min(self.startup_s, self.shutdown_s):
            object.__setattr__(self, "poll_s", min(self.startup_s, self.shutdown_s))


@dataclass(frozen=True, slots=True)
class ShutdownOutcome:
    exit_code: int
    complete: bool
    unfinished_operations: int


class Daemon:
    """Single-use owner; outstanding phase tasks survive cancelled awaiters.

    An incomplete outcome requires the foreground host to terminate its process.
    Resources are deliberately left owned until their operations actually finish.
    """

    def __init__(
        self,
        services: Services,
        *,
        limits: LifecycleLimits | None = None,
        cache: HealthCache | None = None,
        logger: StructuredLogger | None = None,
    ) -> None:
        self.services = services
        self.limits = limits or LifecycleLimits()
        self.cache = cache or HealthCache()
        self.logger = logger or StructuredLogger()
        self._stop = asyncio.Event()
        self._force = asyncio.Event()
        self._state = State.STARTING
        self._services_state = State.STARTING
        self._logged_faults: tuple[Fault, ...] = ()
        self._failed = False
        self._intake_stopped = False
        self._deadline: float | None = None
        self._operations: list[asyncio.Task[None]] = []
        self._driver: asyncio.Task[ShutdownOutcome] | None = None
        self._shutdown_phase = ShutdownPhase.NONE
        self.outcome: ShutdownOutcome | None = None

    @property
    def operations(self) -> tuple[asyncio.Task[None], ...]:
        return tuple(self._operations)

    @property
    def unfinished_operations(self) -> int:
        return sum(not task.done() for task in self._operations)

    def request_stop(self) -> None:
        """Signal-safe event-loop callback: close admission exactly once."""
        if self._stop.is_set():
            self._force.set()
            return
        self._deadline = time.monotonic() + self.limits.shutdown_s
        self._stop.set()
        self._state = State.STOPPING
        self._intake_stopped = True
        self._shutdown_phase = ShutdownPhase.INTAKE
        try:
            self.services.stop_intake()
        except Exception:
            self._failed = True
            self.logger.emit(Event.PIPELINE_FAULT, fault=Fault.SHUTDOWN)
        self.logger.emit(Event.STATE, state=State.STOPPING)
        self._refresh()

    def _refresh(self) -> None:
        try:
            snapshot = self.services.health()
        except Exception:
            snapshot = HealthSnapshot(state=State.FAILED, faults=(Fault.STORAGE,))
        if snapshot.state is State.FAILED:
            self._failed = True
        self._services_state = snapshot.state
        now = time.monotonic_ns()
        if self._state in (State.HEALTHY, State.DEGRADED):
            self._state = snapshot.state
        self.cache.publish(
            replace(
                snapshot,
                state=self._state,
                sampled_at_ns=now,
                heartbeat_ns=now,
                ready=self._state in (State.HEALTHY, State.DEGRADED),
                shutdown_intake_stopped=self._intake_stopped,
                shutdown_pending=max(
                    snapshot.shutdown_pending, self.unfinished_operations
                ),
                unfinished_operations=max(
                    snapshot.unfinished_operations, self.unfinished_operations
                ),
                shutdown_phase=self._shutdown_phase,
                shutdown_deadline_ns=(
                    int(self._deadline * 1_000_000_000)
                    if self._deadline is not None
                    else None
                ),
            )
        )
        observed = self.cache.snapshot(now)
        for fault in observed.faults:
            if fault not in self._logged_faults:
                self.logger.emit(
                    Event.SOURCE_FAULT
                    if fault is Fault.SOURCE
                    else Event.PIPELINE_FAULT,
                    fault=fault,
                )
        self._logged_faults = observed.faults

    def health(self) -> HealthSnapshot:
        return self.cache.snapshot()

    async def _phase(
        self, operation: asyncio.Task[None], deadline: float
    ) -> bool | None:
        self._operations.append(operation)
        while not operation.done():
            if self._force.is_set():
                return None
            if self._deadline is not None:
                deadline = min(deadline, self._deadline)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            await asyncio.wait((operation,), timeout=min(self.limits.poll_s, remaining))
            self._refresh()
        try:
            operation.result()
        except (Exception, asyncio.CancelledError):
            self._failed = True
            self.logger.emit(Event.PIPELINE_FAULT, fault=Fault.SHUTDOWN)
            return False
        return True

    def _finish(self, complete: bool) -> ShutdownOutcome:
        if not complete or self._failed:
            self._state = State.FAILED
        else:
            self._state = State.STOPPING
        self._shutdown_phase = (
            ShutdownPhase.COMPLETE if complete else ShutdownPhase.INCOMPLETE
        )
        self._refresh()
        self.logger.emit(
            Event.SHUTDOWN, state=self._state, count=self.unfinished_operations
        )
        self.outcome = ShutdownOutcome(
            int(self._failed or not complete), complete, self.unfinished_operations
        )
        return self.outcome

    async def _drive(self) -> ShutdownOutcome:
        self.logger.emit(Event.STATE, state=State.STARTING)
        self._refresh()
        started = await self._phase(
            asyncio.create_task(self.services.start()),
            time.monotonic() + self.limits.startup_s,
        )
        if started is None:
            self.request_stop()
            return self._finish(False)
        if started:
            if not self._stop.is_set():
                self._state = self._services_state
                self._refresh()
                self.logger.emit(Event.STATE, state=self._state)
            while not self._stop.is_set() and not self._failed:
                try:
                    async with asyncio.timeout(self.limits.poll_s):
                        await self._stop.wait()
                except TimeoutError:
                    pass
                self._refresh()
        if not self._stop.is_set():
            self.request_stop()
        assert self._deadline is not None
        self._shutdown_phase = ShutdownPhase.DRAIN
        drained = await self._phase(
            asyncio.create_task(self.services.drain()), self._deadline
        )
        if drained is None:
            return self._finish(False)
        if self._force.is_set():
            return self._finish(False)
        self._shutdown_phase = ShutdownPhase.CLOSE
        closed = await self._phase(
            asyncio.create_task(self.services.close()), self._deadline
        )
        return self._finish(drained is True and closed is True)

    async def run(self) -> ShutdownOutcome:
        if self._driver is not None:
            raise RuntimeError("Daemon already started")
        self._driver = asyncio.create_task(self._drive())
        try:
            return await asyncio.shield(self._driver)
        except asyncio.CancelledError:
            self.request_stop()
            while not self._driver.done():
                try:
                    await asyncio.shield(self._driver)
                except asyncio.CancelledError:
                    self.request_stop()
            self._driver.result()
            raise
