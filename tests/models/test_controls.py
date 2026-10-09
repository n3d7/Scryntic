"""Untrusted evidence validation for the real CPU profile, not host evidence."""

import asyncio
from dataclasses import replace
from pathlib import Path
from threading import Event
from typing import Any

import pytest

from scryntic.application.providers import ForecastPoint, ForecastResult
from scryntic.archive.canonical import canonical_json_bytes
from scryntic.jobs.codec import encode_response
from scryntic.jobs.contracts import JobAttempt
from scryntic.model_worker.controls import expected_limits, verify_controls
from scryntic.model_worker.launcher import CPUWorker
from scryntic.model_worker.profile import FORECAST_CPU, IsolationError
from scryntic.models.definitions import definition, policy
from scryntic.models.inventory import RuntimeBundle
from tests.jobs.helpers import job
from tests.model_worker.helpers import host, report


def cpu_report() -> dict[str, Any]:
    value = report()
    value["limits"] = expected_limits(FORECAST_CPU)
    value["cgroup"]["memory.max"] = str(FORECAST_CPU.memory)
    value["cgroup"]["pids.max"] = str(FORECAST_CPU.tasks)
    value["readonly"] = dict.fromkeys(FORECAST_CPU.readonly, True)
    value["environment"] = dict(FORECAST_CPU.environment)
    value["thread_policy"] = "pthread-only-bounded"
    return value


def test_real_evidence_requires_cpu_profile_and_thread_policy() -> None:
    verify_controls(cpu_report(), host(), FORECAST_CPU)
    value, owner = cpu_report(), host()
    with pytest.raises(IsolationError):
        verify_controls(value, owner)


@pytest.mark.parametrize("field", list(cpu_report()))
def test_missing_real_control_rejects(field: str) -> None:
    value = cpu_report()
    del value[field]
    owner = host()
    with pytest.raises(IsolationError):
        verify_controls(value, owner, FORECAST_CPU)


@pytest.mark.parametrize("failure", [False, True])
def test_snapshot_cancellation_joins_thread_and_preserves_cancellation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: bool
) -> None:
    entered, release, finished = Event(), Event(), Event()

    def snapshot(self: RuntimeBundle, root: Path) -> None:
        entered.set()
        assert release.wait(5)
        finished.set()
        if failure:
            raise ValueError("snapshot drift during cancellation")

    monkeypatch.setattr(RuntimeBundle, "snapshot", snapshot)
    bundle = RuntimeBundle(Path("/model"), Path("/runtime"), "d" * 64, ())
    worker = CPUWorker(bundle=bundle, model_id="timesfm-2.5")

    async def exercise() -> None:
        task = asyncio.create_task(worker._snapshot(tmp_path))
        assert await asyncio.to_thread(entered.wait, 5)
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert finished.is_set()

    asyncio.run(exercise())


def response_fixture() -> tuple[CPUWorker, JobAttempt, dict[str, Any]]:
    bundle = RuntimeBundle(Path("/model"), Path("/runtime"), "d" * 64, ())
    worker = CPUWorker(bundle=bundle, model_id="timesfm-2.5")
    review = definition("timesfm-2.5").review(bundle.inventory_sha256)
    value = replace(job(), review=review, policy=policy((review,)))
    attempt = JobAttempt(value, 1, "a" * 32)
    points = tuple(
        ForecastPoint(
            value.inputs.verified.last_start_ns + (i + 1) * value.forecast.frequency_ns,
            2.0,
        )
        for i in range(value.forecast.horizon)
    )
    result = ForecastResult(
        value.job_id,
        value.forecast.dataset,
        review.descriptor.provider_id,
        review.descriptor.model.revision or "",
        points,
    )
    document: dict[str, Any] = {
        "version": 2,
        "controls": cpu_report(),
        "response": encode_response(attempt, result).decode(),
        "usage": {"elapsed_ns": 1, "cpu_ns": 1, "max_rss_kib": 1},
    }
    return worker, attempt, document


def test_real_response_revalidates_controls_usage_and_f20_contract() -> None:
    worker, attempt, value = response_fixture()
    assert (
        worker._decode_worker(canonical_json_bytes(value), host(), attempt)
        == value["response"].encode()
    )
    assert worker.last_usage == value["usage"]
    assert worker.last_controls == value["controls"]


@pytest.mark.parametrize(
    "defect",
    [
        "version",
        "usage-extra",
        "usage-type",
        "elapsed_ns",
        "cpu_ns",
        "max_rss_kib",
        "response",
    ],
)
def test_malformed_or_unbounded_real_response_is_rejected(defect: str) -> None:
    worker, attempt, value = response_fixture()
    if defect == "version":
        value["version"] = 1
    elif defect == "usage-extra":
        value["usage"]["unreviewed"] = 1
    elif defect == "usage-type":
        value["usage"]["elapsed_ns"] = True
    elif defect == "response":
        value["response"] = "{}"
    else:
        value["usage"][defect] = 2**62
    raw, owner = canonical_json_bytes(value), host()
    with pytest.raises((IsolationError, ValueError)):
        worker._decode_worker(raw, owner, attempt)
