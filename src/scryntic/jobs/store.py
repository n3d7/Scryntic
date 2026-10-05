"""Single-owner private WAL/FULL job journal; acceptance is a fenced transaction."""

import fcntl
import os
import sqlite3
import threading
import time
import uuid
from collections.abc import Callable, Iterator
from contextlib import ExitStack, contextmanager
from types import TracebackType

from scryntic.application.analysis import ForecastArtifactRef
from scryntic.configuration.paths import Installation, directory
from scryntic.domain.validation import integer
from scryntic.jobs.codec import decode_job, encode_job
from scryntic.jobs.contracts import (
    MAX_ATTEMPTS,
    TERMINAL,
    JobRecord,
    JobRequest,
    JobState,
)
from scryntic.sqlite_state import connect_pinned, open_private_file, schema_matches

_LOCK = "provider-jobs.lock"
_DATABASE = "provider-jobs.sqlite3"
_SELECT_JOB = "SELECT * FROM jobs WHERE request_id=?"
_CREATE = """CREATE TABLE jobs (
request_id TEXT PRIMARY KEY NOT NULL,
request BLOB NOT NULL,
request_sha256 TEXT NOT NULL,
state TEXT NOT NULL CHECK(state IN ('queued','running','interrupted','succeeded','failed','cancelled','deadline_expired')),
attempt INTEGER NOT NULL CHECK(attempt BETWEEN 0 AND 3),
token TEXT,
result_sha256 TEXT,
reason TEXT,
updated_ns INTEGER NOT NULL,
CHECK((state='succeeded') = (result_sha256 IS NOT NULL)),
CHECK((attempt=0) = (token IS NULL))
)"""
_SCHEMA = (("table", "jobs", "jobs", _CREATE),)


class JobError(ValueError):
    """Fixed bounded job diagnostics never contain provider bodies or credentials."""


class JobStore:
    def __init__(
        self,
        installation: Installation,
        *,
        clock: Callable[[], int] = time.time_ns,
        max_jobs: int = 1024,
    ) -> None:
        integer(max_jobs, 1)
        if max_jobs > 1024:
            raise JobError("Job quota outside bounds")
        self._owner = threading.get_ident()
        self._clock = clock
        self._max_jobs = max_jobs
        self._closed = False
        self._failed = False
        self._resources = ExitStack()
        try:
            state_fd = self._resources.enter_context(
                directory(installation.state_dir, installation.owner_uid, private=True)
            )
            lock_fd = open_private_file(state_fd, _LOCK, installation.owner_uid)
            self._resources.callback(os.close, lock_fd)
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise JobError("Provider job state already owned") from None
            database_fd = open_private_file(state_fd, _DATABASE, installation.owner_uid)
            self._resources.callback(os.close, database_fd)
            self._connection = connect_pinned(
                state_fd,
                _DATABASE,
                ((_LOCK, lock_fd), (_DATABASE, database_fd)),
                self._resources,
                unsafe_file_error=JobError("Unsafe provider job state file"),
            )
            self._initialize()
            self._recover()
        except BaseException:
            self._closed = True
            self._resources.close()
            raise

    def _check(self) -> None:
        if self._closed or self._failed or threading.get_ident() != self._owner:
            raise JobError("Provider job store unavailable")

    def now(self) -> int:
        value = self._clock()
        integer(value, 0)
        if value >= 2**63:
            raise JobError("Job clock outside bounds")
        return value

    @contextmanager
    def _transaction(self) -> Iterator[None]:
        self._check()
        connection = self._connection
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield
            connection.execute("COMMIT")
        except BaseException as error:
            if isinstance(error, sqlite3.Error):
                self._failed = True
            try:
                if connection.in_transaction:
                    connection.execute("ROLLBACK")
                if connection.in_transaction:
                    raise JobError("Job rollback did not end transaction")
            except BaseException:
                self._failed = True
                raise
            raise

    def _initialize(self) -> None:
        db = self._connection
        if db.execute("PRAGMA journal_mode=WAL").fetchone() != ("wal",):
            raise JobError("Provider job WAL unavailable")
        db.execute("PRAGMA synchronous=FULL")
        if db.execute("PRAGMA synchronous").fetchone() != (2,):
            raise JobError("Provider job durability unavailable")
        version = db.execute("PRAGMA user_version").fetchone()
        if version == (0,):
            if db.execute("SELECT 1 FROM sqlite_schema LIMIT 1").fetchone() is not None:
                raise JobError("Invalid provider job schema")
            with self._transaction():
                db.execute(_CREATE)
                db.execute("PRAGMA user_version=1")
        elif version != (1,):
            raise JobError("Unsupported provider job schema")
        if not schema_matches(db, _SCHEMA) or db.execute(
            "PRAGMA quick_check"
        ).fetchall() != [("ok",)]:
            raise JobError("Invalid provider job state")
        if db.execute("SELECT count(*) FROM jobs").fetchone()[0] > self._max_jobs:
            raise JobError("Persisted job quota exceeded")

    @staticmethod
    def _record(row: tuple[object, ...]) -> JobRecord:
        request_id, data, digest, state, attempt, token, result, reason, updated = row
        if type(data) is not bytes:
            raise JobError("Invalid persisted job request")
        job = decode_job(data)
        if request_id != job.job_id or digest != job.sha256:
            raise JobError("Persisted job identity mismatch")
        if (
            type(state) is not str
            or type(attempt) is not int
            or type(updated) is not int
        ):
            raise JobError("Invalid persisted job state")
        integer(attempt, 0)
        integer(updated, 0)
        checked_token = JobStore._optional_text(token)
        checked_reason = JobStore._optional_text(reason)
        selected = JobState(state)
        if attempt > MAX_ATTEMPTS or ((attempt == 0) != (token is None)):
            raise JobError("Invalid persisted attempt")
        if checked_token is not None and len(checked_token) != 32:
            raise JobError("Invalid persisted attempt token")
        if updated < job.submitted_ns:
            raise JobError("Invalid persisted job history")
        artifact = JobStore._artifact(job, selected, result)
        return JobRecord(
            job, selected, attempt, checked_token, artifact, checked_reason, updated
        )

    @staticmethod
    def _optional_text(value: object) -> str | None:
        if value is not None and type(value) is not str:
            raise JobError("Invalid persisted job text")
        return value

    @staticmethod
    def _artifact(
        job: JobRequest, state: JobState, value: object
    ) -> ForecastArtifactRef | None:
        if (state is JobState.SUCCEEDED) != (value is not None):
            raise JobError("Invalid persisted result registration")
        if value is None:
            return None
        if type(value) is not str:
            raise JobError("Invalid persisted artifact")
        return ForecastArtifactRef(
            value,
            job.forecast.dataset,
            job.review.descriptor.provider_id,
            job.review.descriptor.model.revision or "",
        )

    def _read(self, job_id: str) -> JobRecord:
        row = self._connection.execute(_SELECT_JOB, (job_id,)).fetchone()
        if row is None:
            raise JobError("Unknown provider job")
        return self._record(row)

    def _write(
        self, record: JobRecord, state: JobState, now: int, *, reason: str | None = None
    ) -> None:
        self._connection.execute(
            "UPDATE jobs SET state=?, reason=?, updated_ns=? WHERE request_id=?",
            (state.value, reason, max(now, record.updated_ns), record.job.job_id),
        )

    def _expire(self, record: JobRecord, now: int) -> JobRecord:
        if record.state not in TERMINAL:
            if now < record.updated_ns:
                self._write(record, JobState.FAILED, now, reason="clock_regression")
            elif now >= record.job.deadline_ns:
                self._write(record, JobState.DEADLINE_EXPIRED, now, reason="deadline")
        return self._read(record.job.job_id)

    def _recover(self) -> None:
        with self._transaction():
            now = self.now()
            for row in self._connection.execute(
                "SELECT * FROM jobs ORDER BY request_id"
            ).fetchall():
                record = self._expire(self._record(row), now)
                if record.state is JobState.RUNNING:
                    self._write(record, JobState.INTERRUPTED, now, reason="restarted")

    def submit(self, job: JobRequest) -> JobRecord:
        data = encode_job(job)
        with self._transaction():
            row = self._connection.execute(_SELECT_JOB, (job.job_id,)).fetchone()
            if row is not None:
                existing = self._record(row)
                if existing.job != job:
                    raise JobError("Job identity conflict")
                return self._expire(existing, self.now())
            now = self.now()
            if not job.submitted_ns <= now < job.deadline_ns:
                raise JobError("Job admission deadline/clock rejected")
            if (
                self._connection.execute("SELECT count(*) FROM jobs").fetchone()[0]
                >= self._max_jobs
            ):
                raise JobError("Provider job quota reached")
            self._connection.execute(
                "INSERT INTO jobs VALUES (?, ?, ?, 'queued', 0, NULL, NULL, NULL, ?)",
                (job.job_id, data, job.sha256, now),
            )
            return self._read(job.job_id)

    def get(self, job_id: str) -> JobRecord:
        with self._transaction():
            return self._expire(self._read(job_id), self.now())

    def find(self, job_id: str) -> JobRecord | None:
        with self._transaction():
            row = self._connection.execute(_SELECT_JOB, (job_id,)).fetchone()
            return None if row is None else self._expire(self._record(row), self.now())

    def claim(self, job_id: str, *, resume: bool = False) -> JobRecord:
        claimed: JobRecord | None = None
        with self._transaction():
            now = self.now()
            record = self._expire(self._read(job_id), now)
            required = JobState.INTERRUPTED if resume else JobState.QUEUED
            if record.state is required and record.attempt >= MAX_ATTEMPTS:
                self._write(record, JobState.FAILED, now, reason="attempt_quota")
                claimed = self._read(job_id)
            elif record.state is required:
                changed = self._connection.execute(
                    "UPDATE jobs SET state='running', attempt=attempt+1, token=?, reason=NULL, updated_ns=? WHERE request_id=? AND state=? AND attempt=?",
                    (uuid.uuid4().hex, now, job_id, required.value, record.attempt),
                )
                if changed.rowcount != 1:
                    raise JobError("Provider job claim conflict")
                claimed = self._read(job_id)
        if claimed is None:
            raise JobError("Job is not ready for this execution")
        return claimed

    def cancel(self, job_id: str) -> JobRecord:
        with self._transaction():
            now = self.now()
            record = self._expire(self._read(job_id), now)
            if record.state not in TERMINAL:
                self._write(record, JobState.CANCELLED, now, reason="cancelled")
            return self._read(job_id)

    def finish(
        self,
        active: JobRecord,
        state: JobState | str,
        *,
        artifact: ForecastArtifactRef | None = None,
        reason: str | None = None,
    ) -> bool:
        target = JobState(state)
        if target not in TERMINAL and target is not JobState.INTERRUPTED:
            raise JobError("Invalid job completion state")
        if (target is JobState.SUCCEEDED) != (artifact is not None):
            raise JobError("Invalid success registration")
        if artifact is not None and (
            artifact.dataset != active.job.forecast.dataset
            or artifact.provider_id != active.job.review.descriptor.provider_id
            or artifact.model_revision != active.job.review.descriptor.model.revision
        ):
            raise JobError("Artifact does not match provider job")
        with self._transaction():
            now = self.now()
            record = self._expire(self._read(active.job.job_id), now)
            if record.job != active.job:
                return False
            changed = self._connection.execute(
                "UPDATE jobs SET state=?, result_sha256=?, reason=?, updated_ns=? WHERE request_id=? AND state='running' AND attempt=? AND token=?",
                (
                    target.value,
                    None if artifact is None else artifact.sha256,
                    reason,
                    max(now, record.updated_ns),
                    active.job.job_id,
                    active.attempt,
                    active.token,
                ),
            )
            return changed.rowcount == 1

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            self._resources.close()

    def __enter__(self) -> "JobStore":
        self._check()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()
