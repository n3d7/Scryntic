"""A non-forecast workload uses exactly the same persisted fencing engine."""

import json
from dataclasses import asdict, dataclass
from hashlib import sha256
from pathlib import Path

from scryntic.jobs.durable import DurableRecord
from scryntic.jobs.store import DurableJobStore
from tests.normalization.helpers import installation


@dataclass(frozen=True)
class Request:
    job_id: str = "bounded-task"
    submitted_ns: int = 100
    deadline_ns: int = 1000

    @property
    def sha256(self) -> str:
        return sha256(json.dumps(asdict(self), sort_keys=True).encode()).hexdigest()


@dataclass(frozen=True)
class Artifact:
    sha256: str


class Store(DurableJobStore[Request, Artifact]):
    def _encode_job(self, job: Request) -> bytes:
        return json.dumps(asdict(job), sort_keys=True).encode()

    def _decode_job(self, data: bytes) -> Request:
        return Request(**json.loads(data))

    def _artifact(self, job: Request, state: object, value: object) -> Artifact | None:
        return Artifact(str(value)) if value is not None else None

    def _validate_artifact(
        self, active: DurableRecord[Request, Artifact], artifact: Artifact
    ) -> None:
        assert len(artifact.sha256) == 64


def test_nonforecast_restart_and_late_result_fencing(tmp_path: Path) -> None:
    target = installation(tmp_path)
    with Store(target, clock=lambda: 100) as first:
        first.submit(Request())
        abandoned = first.claim("bounded-task")
    with Store(target, clock=lambda: 101) as recovered:
        assert recovered.get("bounded-task").state.value == "interrupted"
        fresh = recovered.claim("bounded-task", resume=True)
        assert fresh.token != abandoned.token
        assert not recovered.finish(abandoned, "succeeded", artifact=Artifact("a" * 64))
        recovered.cancel("bounded-task")
        assert not recovered.finish(fresh, "succeeded", artifact=Artifact("b" * 64))
