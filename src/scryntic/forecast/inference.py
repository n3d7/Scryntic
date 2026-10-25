"""Fixed O(1) zero-return inference on the shared durable job lifecycle.

This trusted arithmetic adapter loads no model or framework and receives no
credentials, archive handles or outcome capability. Real sources fail admission.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from decimal import Decimal
from hashlib import sha256
from typing import Any

from scryntic.archive.canonical import canonical_json_bytes
from scryntic.configuration.paths import Installation
from scryntic.domain.validation import digest, integer
from scryntic.forecast.ledger import ForecastLedger
from scryntic.forecast.ledger_contracts import (
    RUNTIME_SHA256,
    ForecastContent,
    code_identity,
    decoded,
    identity,
)
from scryntic.jobs.contracts import JobState
from scryntic.jobs.durable import DurableAttempt, DurableRecord
from scryntic.jobs.runner import DurableRunner, Executor
from scryntic.jobs.store import DurableJobStore, JobError


def implementation_sha256() -> str:
    return code_identity()


@dataclass(frozen=True, slots=True)
class InferenceJob:
    content: ForecastContent
    submitted_ns: int
    code_sha256: str

    def __post_init__(self) -> None:
        self.content.__post_init__()
        integer(self.submitted_ns, 0)
        if (
            self.code_sha256 != implementation_sha256()
            or self.content.slot.stream.code_sha256 != self.code_sha256
            or self.content.slot.stream.runtime_sha256 != RUNTIME_SHA256
            or self.content.point != Decimal(0)
            or self.content.reason is not None
            or not self.content.slot.origin_ns
            <= self.submitted_ns
            <= self.content.slot.deadline_ns
        ):
            raise ValueError("Unregistered PI04 inference job")

    @property
    def job_id(self) -> str:
        return "pi04-" + self.content.slot.sha256

    @property
    def deadline_ns(self) -> int:
        # Existing jobs use an exclusive boundary; publication uses inclusive t+60s.
        return self.content.slot.deadline_ns + 1

    def encode(self) -> bytes:
        self.__post_init__()
        return canonical_json_bytes(
            {
                "content": self.content.body(),
                "submitted_ns": self.submitted_ns,
                "code_sha256": self.code_sha256,
            }
        )

    @property
    def sha256(self) -> str:
        return sha256(self.encode()).hexdigest()

    @classmethod
    def decode(cls, data: bytes) -> InferenceJob:
        value = decoded(data)
        if set(value) != {"content", "submitted_ns", "code_sha256"}:
            raise ValueError("Unknown PI04 job fields")
        result = cls(
            ForecastContent.from_body(value["content"]),
            value["submitted_ns"],
            value["code_sha256"],
        )
        if result.encode() != data:
            raise ValueError("Invalid PI04 job encoding")
        return result


@dataclass(frozen=True, slots=True)
class PublicationRef:
    sha256: str

    def __post_init__(self) -> None:
        digest(self.sha256)


class InferenceStore(DurableJobStore[InferenceJob, PublicationRef]):
    _lock_name = "pi04-inference.lock"
    _database_name = "pi04-inference.sqlite3"

    def __init__(
        self,
        installation: Installation,
        ledger: ForecastLedger,
        *,
        clock: Callable[[], int] = time.time_ns,
    ) -> None:
        self.ledger = ledger
        super().__init__(installation, clock=clock)

    def _encode_job(self, job: InferenceJob) -> bytes:
        return job.encode()

    def _decode_job(self, data: bytes) -> InferenceJob:
        return InferenceJob.decode(data)

    def _artifact(
        self, job: InferenceJob, state: JobState, value: object
    ) -> PublicationRef | None:
        if state is not JobState.SUCCEEDED:
            if value is not None:
                raise JobError("PI04 non-success has a publication reference")
            return None
        if value != job.content.sha256:
            raise JobError("PI04 publication reference mismatch")
        self.ledger.publication(job.content.slot.sha256)
        return PublicationRef(value)

    def _validate_artifact(
        self,
        active: DurableRecord[InferenceJob, PublicationRef],
        artifact: PublicationRef,
    ) -> None:
        if (
            artifact.sha256 != active.job.content.sha256
            or self.ledger.publication(active.job.content.slot.sha256)["content_sha256"]
            != artifact.sha256
        ):
            raise JobError("PI04 result identity mismatch")

    def _audit_states(self) -> None:
        # Deliberately replaces the shared store's PI01 fitting audit hook.
        for row in self._connection.execute(
            "SELECT * FROM jobs ORDER BY request_id"
        ).fetchall():
            record = self._record(row)
            body = {
                "job_sha256": record.job.sha256,
                "state": record.state.value,
                "attempt": record.attempt,
                "token": record.token,
            }
            subject = identity(body)
            if not any(
                e["subject"] == subject for e in self.ledger.events("inference")
            ):
                self.ledger._append("inference", subject, body)


class ZeroExecutor:
    async def execute(self, attempt: DurableAttempt[InferenceJob]) -> bytes:
        attempt.__post_init__()
        attempt.job.__post_init__()
        await asyncio.sleep(0)
        return canonical_json_bytes(
            {
                "job_sha256": attempt.job.sha256,
                "number": attempt.number,
                "token": attempt.token,
                "point": "0",
            }
        )


class InferenceService(DurableRunner[InferenceJob, PublicationRef]):
    def __init__(self, store: InferenceStore, ledger: ForecastLedger) -> None:
        super().__init__(store, max_active=1)
        self.ledger, self.executor = ledger, ZeroExecutor()

    def _audit(self, job: InferenceJob, phase: str, attempt: int) -> None:
        # Never call ResearchLedger.trial for inference.
        pass

    def _executor(self, job: InferenceJob) -> Executor[InferenceJob]:
        job.__post_init__()
        return self.executor

    def schedule(
        self, contents: tuple[ForecastContent, ...]
    ) -> tuple[DurableRecord[InferenceJob, PublicationRef], ...]:
        if type(contents) is not tuple or len(contents) > 64 or self._closing:
            raise ValueError("PI04 dispatch bound or closing service")
        result = []
        now = self._store.now()
        for value in contents:
            value.__post_init__()
            value = replace(value, point=Decimal(0), reason=None)
            slot = value.slot
            existing = self._store.find("pi04-" + slot.sha256)
            if existing is not None:
                if existing.job.content != value:
                    raise JobError("PI04 scheduled input conflict")
                result.append(existing)
            elif now > slot.deadline_ns:
                body: dict[str, Any] = {
                    "state": "missed",
                    "reason": "deadline",
                    "as_of_ns": now,
                    "slot": value.body()["slot"],
                }
                if not any(
                    e["subject"] == slot.sha256 for e in self.ledger.events("slot")
                ):
                    self.ledger._append("slot", slot.sha256, body)
            elif now >= slot.origin_ns:
                result.append(
                    self._store.submit(
                        InferenceJob(value, now, implementation_sha256())
                    )
                )
        return tuple(result)

    async def run(
        self, job_id: str, *, resume: bool = False
    ) -> DurableRecord[InferenceJob, PublicationRef]:
        record = self._store.get(job_id)
        # A publication committed before a crash is authoritative, even if the job
        # acknowledgement was lost. Reconciliation never re-executes or backdates.
        if any(
            e["subject"] == record.job.content.slot.sha256
            for e in self.ledger.events("publication")
        ):
            return record
        return await super().run(job_id, resume=resume)

    async def infer_due(
        self, contents: tuple[ForecastContent, ...]
    ) -> tuple[DurableRecord[InferenceJob, PublicationRef], ...]:
        records = self.schedule(contents)
        result = []
        for record in records:
            if record.state in (JobState.QUEUED, JobState.INTERRUPTED):
                record = await self.run(
                    record.job.job_id, resume=record.state is JobState.INTERRUPTED
                )
            result.append(record)
        return tuple(result)

    def _accept(
        self, active: DurableRecord[InferenceJob, PublicationRef], data: bytes
    ) -> None:
        current = self._store.get(active.job.job_id)
        if (
            current.state is not JobState.RUNNING
            or current.execution() != active.execution()
        ):
            return
        expected = {
            "job_sha256": active.job.sha256,
            "number": active.attempt,
            "token": active.token,
            "point": "0",
        }
        if decoded(data) != expected:
            raise JobError("PI04 fixed result/fence mismatch")
        assert active.token is not None
        receipt = self.ledger.publish(active.job.content, attempt_id=active.token)
        if receipt["disposition"] != "accepted":
            raise JobError("PI04 publication conflict")
        self._store.finish(
            active,
            JobState.SUCCEEDED,
            artifact=PublicationRef(active.job.content.sha256),
        )
