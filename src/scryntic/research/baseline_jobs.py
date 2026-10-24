"""Fixed PI03 request/result codecs and adapters to the existing durable engine."""

from __future__ import annotations

import asyncio
import json
import os
import platform
import stat
from dataclasses import asdict, dataclass, field
from hashlib import sha256
from pathlib import Path
from typing import Any
from uuid import uuid4

from scryntic.archive.canonical import canonical_json_bytes
from scryntic.configuration.paths import Installation, directory
from scryntic.domain.validation import digest, integer
from scryntic.jobs.contracts import JobState
from scryntic.jobs.durable import DurableAttempt, DurableRecord
from scryntic.jobs.runner import DurableRunner, Executor
from scryntic.jobs.store import DurableJobStore, JobError
from scryntic.research.baselines import (
    ALGORITHMS,
    RESPONSE_BYTES,
    SOURCE_USE_SHA256,
    SPEC_SHA256,
    _pairs,
    body_sha256,
    eligible_indices,
    prefit_counts,
    rows,
    utc_ns,
    validate_input,
)
from scryntic.research.ledger import ResearchLedger

POLICY_SHA256 = "d75c7c0278d011b38c5e2bfaff151e7f3071b56edca9dce3d1d883294f3a14b8"
EXPECTED_INPUT_SHA256 = (
    "bf30c207a6e952cc3a033b053ec4ac651f69cd42a743e3172dc8e79e99c1aff3"
)


def workload_identity() -> str:
    """Pin application code and Python identically inside the read-only worker."""
    root = Path(__file__).resolve().parents[1]
    paths = sorted(root.rglob("*.py"))
    if not 0 < len(paths) <= 1024:
        raise ValueError("Unbounded PI03 application inventory")
    files = {}
    total = 0
    for path in paths:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 1024**2:
            raise ValueError("Unsafe PI03 application source")
        data = path.read_bytes()
        total += len(data)
        if total > 16 * 1024**2:
            raise ValueError("PI03 application inventory byte bound")
        files[path.relative_to(root).as_posix()] = sha256(data).hexdigest()
    return body_sha256({"python": platform.python_version(), "files": files})


@dataclass(frozen=True, slots=True)
class BaselineJob:
    algorithm: str
    input_sha256: str
    submitted_ns: int
    deadline_ns: int
    workload_sha256: str = field(default_factory=workload_identity)
    spec_sha256: str = SPEC_SHA256
    policy_sha256: str = POLICY_SHA256
    source_use_sha256: str = SOURCE_USE_SHA256
    schema: str = "scryntic.pi03.baseline-job.v1"

    def __post_init__(self) -> None:
        digest(self.input_sha256)
        digest(self.workload_sha256)
        integer(self.submitted_ns, 0)
        integer(self.deadline_ns, 1)
        if (
            self.input_sha256 != EXPECTED_INPUT_SHA256
            or self.algorithm not in ALGORITHMS
            or self.spec_sha256 != SPEC_SHA256
            or self.policy_sha256 != POLICY_SHA256
            or self.source_use_sha256 != SOURCE_USE_SHA256
            or self.schema != "scryntic.pi03.baseline-job.v1"
            or not 0 < self.deadline_ns - self.submitted_ns <= 180 * 10**9
            or self.deadline_ns >= 2**63
        ):
            raise ValueError("Unregistered PI03 fixed job")

    @property
    def job_id(self) -> str:
        return "pi03-" + body_sha256(
            {
                "algorithm": self.algorithm,
                "input_sha256": self.input_sha256,
                "spec_sha256": self.spec_sha256,
                "workload_sha256": self.workload_sha256,
            }
        )

    @property
    def sha256(self) -> str:
        return sha256(encode_job(self)).hexdigest()


def encode_job(job: BaselineJob) -> bytes:
    job.__post_init__()
    return canonical_json_bytes(asdict(job))


def decode_job(data: bytes) -> BaselineJob:
    if type(data) is not bytes or not 0 < len(data) <= 65536:
        raise ValueError("PI03 job byte bound")
    raw = json.loads(data, object_pairs_hook=_pairs)
    if type(raw) is not dict or set(raw) != set(BaselineJob.__dataclass_fields__):
        raise ValueError("Unknown PI03 job fields")
    job = BaselineJob(**raw)
    if encode_job(job) != data:
        raise ValueError("Noncanonical PI03 job")
    return job


def encode_attempt(attempt: DurableAttempt[BaselineJob]) -> dict[str, Any]:
    attempt.__post_init__()
    return {
        "job": asdict(attempt.job),
        "number": attempt.number,
        "token": attempt.token,
    }


def decode_attempt(value: Any) -> DurableAttempt[BaselineJob]:
    if type(value) is not dict or set(value) != {"job", "number", "token"}:
        raise ValueError("Invalid PI03 attempt")
    return DurableAttempt(
        decode_job(canonical_json_bytes(value["job"])), value["number"], value["token"]
    )


def encode_response(
    attempt: DurableAttempt[BaselineJob], result: dict[str, Any]
) -> bytes:
    data = canonical_json_bytes(
        {
            "schema": "scryntic.pi03.baseline-response.v1",
            "job_sha256": attempt.job.sha256,
            "attempt": attempt.number,
            "token": attempt.token,
            "result": result,
        }
    )
    if len(data) > RESPONSE_BYTES:
        raise ValueError("PI03 response byte bound")
    return data


def decode_response(
    data: bytes, attempt: DurableAttempt[BaselineJob]
) -> dict[str, Any]:
    if type(data) is not bytes or not 0 < len(data) <= RESPONSE_BYTES:
        raise ValueError("PI03 response byte bound")
    raw = json.loads(data, object_pairs_hook=_pairs)
    if (
        type(raw) is not dict
        or set(raw) != {"schema", "job_sha256", "attempt", "token", "result"}
        or raw["schema"] != "scryntic.pi03.baseline-response.v1"
        or raw["job_sha256"] != attempt.job.sha256
        or type(raw["attempt"]) is not int
        or raw["attempt"] != attempt.number
        or raw["token"] != attempt.token
    ):
        raise ValueError("Mismatched or stale PI03 response")
    result = raw["result"]
    if (
        type(result) is not dict
        or result.get("schema") != "scryntic.pi03.baseline-result.v1"
        or result.get("algorithm") != attempt.job.algorithm
        or result.get("spec_sha256") != SPEC_SHA256
        or result.get("source_use_sha256") != SOURCE_USE_SHA256
        or result.get("input_sha256") != attempt.job.input_sha256
        or result.get("workload_sha256") != attempt.job.workload_sha256
        or result.get("final_labels_accessed") is not False
        or result.get("promotion_allowed") is not False
        or result.get("evidence_class") != "historical-reconstruction"
        or encode_response(attempt, result) != data
    ):
        raise ValueError("Unqualified PI03 result")
    from scryntic.research.baseline_results import validate_result

    validate_result(result, attempt.job.algorithm, attempt.job.input_sha256)
    return result


@dataclass(frozen=True, slots=True)
class BaselineArtifact:
    sha256: str
    job_sha256: str

    def __post_init__(self) -> None:
        digest(self.sha256)
        digest(self.job_sha256)


class BaselineJobStore(DurableJobStore[BaselineJob, BaselineArtifact]):
    _lock_name = "pi03-jobs.lock"
    _database_name = "pi03-jobs.sqlite3"

    def _encode_job(self, job: BaselineJob) -> bytes:
        return encode_job(job)

    def _decode_job(self, data: bytes) -> BaselineJob:
        return decode_job(data)

    def _artifact(
        self, job: BaselineJob, state: JobState, value: object
    ) -> BaselineArtifact | None:
        if (state is JobState.SUCCEEDED) != (value is not None):
            raise JobError("Invalid PI03 result registration")
        if value is None:
            return None
        if type(value) is not str:
            raise JobError("Invalid PI03 result identity")
        return BaselineArtifact(value, job.sha256)

    def _validate_artifact(
        self,
        active: DurableRecord[BaselineJob, BaselineArtifact],
        artifact: BaselineArtifact,
    ) -> None:
        if artifact.job_sha256 != active.job.sha256:
            raise JobError("PI03 result job mismatch")


class BaselineArtifacts:
    def __init__(self, installation: Installation) -> None:
        self.installation = installation

    def read(self, reference: BaselineArtifact) -> bytes:
        with directory(
            self.installation.state_dir, self.installation.owner_uid, private=True
        ) as root:
            fd = os.open(
                "pi03-result-" + reference.sha256,
                os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC,
                dir_fd=root,
            )
            try:
                info = os.fstat(fd)
                if (
                    not stat.S_ISREG(info.st_mode)
                    or info.st_nlink != 1
                    or info.st_uid != self.installation.owner_uid
                    or stat.S_IMODE(info.st_mode) != 0o600
                    or not 0 < info.st_size <= RESPONSE_BYTES
                ):
                    raise JobError("Unsafe PI03 result object")
                with os.fdopen(fd, "rb", closefd=False) as stream:
                    data = stream.read(RESPONSE_BYTES + 1)
            finally:
                os.close(fd)
        if sha256(data).hexdigest() != reference.sha256:
            raise JobError("PI03 immutable result digest mismatch")
        return data

    def write(self, job: BaselineJob, data: bytes) -> BaselineArtifact:
        if not 0 < len(data) <= RESPONSE_BYTES:
            raise JobError("PI03 result byte bound")
        reference = BaselineArtifact(sha256(data).hexdigest(), job.sha256)
        try:
            if self.read(reference) != data:
                raise JobError("PI03 result identity conflict")
            return reference
        except FileNotFoundError:
            pass
        with directory(
            self.installation.state_dir, self.installation.owner_uid, private=True
        ) as root:
            temporary = "pi03-stage-" + uuid4().hex
            fd = os.open(
                temporary,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                0o600,
                dir_fd=root,
            )
            try:
                with os.fdopen(fd, "wb", closefd=False) as stream:
                    stream.write(data)
                    stream.flush()
                    os.fsync(fd)
                os.replace(
                    temporary,
                    "pi03-result-" + reference.sha256,
                    src_dir_fd=root,
                    dst_dir_fd=root,
                )
                os.fsync(root)
            finally:
                os.close(fd)
                try:
                    os.unlink(temporary, dir_fd=root)
                except FileNotFoundError:
                    pass
        return reference


class BaselineExecutor:
    def __init__(self, data: bytes) -> None:
        from scryntic.model_worker.launcher import CPUWorker

        self.worker = CPUWorker()
        self.data = data

    async def execute(self, attempt: DurableAttempt[BaselineJob]) -> bytes:
        return await self.worker.execute_baseline(attempt, self.data)


class BaselineService(DurableRunner[BaselineJob, BaselineArtifact]):
    def _executor(self, job: BaselineJob) -> Executor[BaselineJob]:
        return self._validate(job)

    def __init__(self, store: BaselineJobStore, data: bytes) -> None:
        super().__init__(store, max_active=1)
        self.input = validate_input(data)
        self.data_sha256 = sha256(data).hexdigest()
        if self.data_sha256 != EXPECTED_INPUT_SHA256:
            raise JobError("PI03 input is not the pre-fit registered source projection")
        self.counts = prefit_counts(data)
        self.executor = BaselineExecutor(data)
        self.artifacts = BaselineArtifacts(store.installation)
        self._require_reservation()

    def _require_reservation(self) -> None:
        if not ResearchLedger.active(self._store.installation):
            raise JobError("PI03 requires an active PI01 ledger")
        with ResearchLedger(self._store.installation) as ledger:
            reservation = ledger.reservation
            if (
                reservation.policy_sha256 != POLICY_SHA256
                or reservation.final_start_ns != utc_ns("2026-11-01T00:00:00Z")
                or reservation.final_end_ns != utc_ns("2030-01-01T00:00:00Z")
                or reservation.development_end_ns != utc_ns("2026-10-01T00:00:00Z")
            ):
                raise JobError("PI03 reservation policy mismatch")

    def _validate(self, job: BaselineJob) -> Executor[BaselineJob]:
        self._require_reservation()
        if self._closing or job.input_sha256 != self.data_sha256:
            raise JobError("PI03 input admission changed")
        if job.workload_sha256 != workload_identity():
            raise JobError("PI03 application workload changed since admission")
        job.__post_init__()
        return self.executor

    def submit(self, algorithm: str) -> DurableRecord[BaselineJob, BaselineArtifact]:
        now = self._store.now()
        job = BaselineJob(algorithm, self.data_sha256, now, now + 180 * 10**9)
        existing = self._store.find(job.job_id)
        if existing is not None:
            self._validate(existing.job)
            return existing
        self._validate(job)
        self._audit(job, "registered", 0)
        return self._store.submit(job)

    def find(
        self, algorithm: str
    ) -> DurableRecord[BaselineJob, BaselineArtifact] | None:
        now = self._store.now()
        job = BaselineJob(algorithm, self.data_sha256, now, now + 180 * 10**9)
        return self._store.find(job.job_id)

    async def cancel_and_wait(
        self, job_id: str
    ) -> DurableRecord[BaselineJob, BaselineArtifact]:
        running = self._active.get(job_id)
        self.cancel(job_id)
        if running is not None:
            await asyncio.gather(running, return_exceptions=True)
        return self._store.get(job_id)

    def _accept(
        self, active: DurableRecord[BaselineJob, BaselineArtifact], data: bytes
    ) -> None:
        from scryntic.research.baseline_results import FrozenPredictor
        from scryntic.research.baseline_scores import score_rows

        current = self._store.get(active.job.job_id)
        if (
            current.state is not JobState.RUNNING
            or current.attempt != active.attempt
            or current.token != active.token
        ):
            return

        if self.executor.worker.last_controls is None:
            raise JobError("PI03 worker controls were not verified")
        result = decode_response(data, active.execution())
        if result.get("prefit_counts_sha256") != body_sha256(self.counts):
            raise JobError("PI03 pre-fit counts changed")
        if result["dependence_days"] != self.counts["training_dependence_days"]:
            raise JobError("PI03 registered dependence span changed")
        for asset in self.input["spec"]["instruments"]:
            for horizon, counts in self.counts["horizons"].items():
                score = result["scores"][asset][horizon]
                if (
                    score["count"] != counts["paired_origins_per_asset"]
                    or score["evaluation_exclusions"] != counts["exclusions"]
                ):
                    raise JobError("PI03 pair/exclusion denominator mismatch")
                training, exclusions = eligible_indices(self.input, int(horizon), True)
                if (
                    score["training_rows"] != len(training)
                    or score["training_exclusions"] != exclusions
                ):
                    raise JobError("PI03 training eligibility changed")
                recomputed = score_rows(
                    FrozenPredictor(score["model_parameters"]),
                    rows(self.input, asset, int(horizon), False),
                )
                if any(score[key] != value for key, value in recomputed.items()):
                    raise JobError(
                        "PI03 independent score/prediction recomputation mismatch"
                    )
        if self._store.get(active.job.job_id).state is JobState.RUNNING:
            reference = self.artifacts.write(active.job, data)
            self._store.finish(active, JobState.SUCCEEDED, artifact=reference)

    def result(self, job_id: str) -> dict[str, Any]:
        record = self._store.get(job_id)
        if (
            record.state is not JobState.SUCCEEDED
            or record.artifact is None
            or record.token is None
        ):
            raise JobError("PI03 job has no accepted result")
        return decode_response(
            self.artifacts.read(record.artifact),
            DurableAttempt(record.job, record.attempt, record.token),
        )
