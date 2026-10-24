"""One durable execution lifecycle for application-owned fixed workloads."""

from __future__ import annotations

import asyncio
import sqlite3
from abc import ABC, abstractmethod
from typing import Protocol

from scryntic.jobs.contracts import TERMINAL, JobState
from scryntic.jobs.durable import (
    DurableArtifact,
    DurableAttempt,
    DurableRecord,
    DurableRequest,
)
from scryntic.jobs.store import DurableJobStore, JobError
from scryntic.research.ledger import ResearchLedger


class Executor[Request: DurableRequest](Protocol):
    async def execute(self, attempt: DurableAttempt[Request]) -> bytes: ...


class DurableRunner[Request: DurableRequest, Artifact: DurableArtifact](ABC):
    def __init__(
        self, store: DurableJobStore[Request, Artifact], *, max_active: int = 2
    ) -> None:
        if type(max_active) is not int or not 1 <= max_active <= 8:
            raise JobError("Active job quota outside bounds")
        self._store = store
        self._max_active = max_active
        self._closing = False
        self._active: dict[str, asyncio.Task[bytes]] = {}

    @abstractmethod
    def _executor(self, job: Request) -> Executor[Request]: ...

    @abstractmethod
    def _accept(
        self, active: DurableRecord[Request, Artifact], data: bytes
    ) -> None: ...

    def _audit(self, job: Request, phase: str, attempt: int) -> None:
        if ResearchLedger.active(self._store.installation):
            with ResearchLedger(self._store.installation) as ledger:
                ledger.trial(job.job_id, job.sha256, phase, attempt)

    def cancel(self, job_id: str) -> DurableRecord[Request, Artifact]:
        try:
            before = self._store.get(job_id)
            try:
                self._audit(before.job, "intervention", before.attempt)
            finally:
                record = self._store.cancel(job_id)
        finally:
            task = self._active.get(job_id)
            if task is not None:
                task.cancel()
        return record

    def _cancelled(self, active: DurableRecord[Request, Artifact]) -> None:
        try:
            self._audit(active.job, "intervention", active.attempt)
        finally:
            if self._closing:
                self._store.finish(active, JobState.INTERRUPTED, reason="shutdown")
            else:
                self._store.cancel(active.job.job_id)

    async def run(
        self, job_id: str, *, resume: bool = False
    ) -> DurableRecord[Request, Artifact]:
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
            self._audit(active.job, "started", active.attempt)
            provider = self._executor(active.job)
            await self._execute(active, provider)
        except asyncio.CancelledError:
            self._cancelled(active)
            if self._owner_cancelled():
                raise
        except Exception:
            self._store.finish(active, JobState.FAILED, reason="provider_failed")
        record = self._store.get(job_id)
        self._audit(record.job, record.state.value, record.attempt)
        return record

    @staticmethod
    def _owner_cancelled() -> bool:
        owner = asyncio.current_task()
        return owner is not None and bool(owner.cancelling())

    async def _execute(
        self, active: DurableRecord[Request, Artifact], provider: Executor[Request]
    ) -> None:
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

    async def aclose(self) -> None:
        self._closing = True
        tasks = tuple(self._active.items())
        failure: Exception | None = None
        try:
            for job_id, _ in tasks:
                try:
                    active = self._store.get(job_id)
                    self._store.finish(active, JobState.INTERRUPTED, reason="shutdown")
                except (OSError, ValueError, sqlite3.Error) as error:
                    if failure is None:
                        failure = error
        finally:
            for _, task in tasks:
                task.cancel()
            if tasks:
                await asyncio.gather(
                    *(task for _, task in tasks), return_exceptions=True
                )
        if failure is not None:
            raise failure
