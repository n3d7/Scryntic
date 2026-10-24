import json
from dataclasses import replace
from pathlib import Path

import pytest

from scryntic.jobs.durable import DurableAttempt
from scryntic.research.baseline_jobs import (
    EXPECTED_INPUT_SHA256,
    BaselineJob,
    BaselineJobStore,
    decode_job,
    encode_job,
)
from tests.normalization.helpers import installation


def job() -> BaselineJob:
    return BaselineJob("zero", EXPECTED_INPUT_SHA256, 100, 100 + 180 * 10**9)


def test_closed_fixed_job_contract_and_source_binding() -> None:
    value = job()
    assert decode_job(encode_job(value)) == value
    with pytest.raises(ValueError):
        replace(value, algorithm="arbitrary-training")
    raw = json.loads(encode_job(value))
    raw["final_access_allowed"] = True
    with pytest.raises(ValueError):
        decode_job(json.dumps(raw).encode())


def test_research_restart_uses_existing_fencing_and_retry_state(tmp_path: Path) -> None:
    target = installation(tmp_path)
    with BaselineJobStore(target, clock=lambda: 100) as store:
        store.submit(job())
        first = store.claim(job().job_id)
    with BaselineJobStore(target, clock=lambda: 101) as store:
        assert store.get(job().job_id).state.value == "interrupted"
        fresh = store.claim(job().job_id, resume=True)
        assert first.token is not None
        assert fresh.execution() != DurableAttempt(
            first.job, first.attempt, first.token
        )
        assert not store.finish(first, "failed", reason="late-result")
