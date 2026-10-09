"""Real offline TimesFM qualification; skipped means unqualified, never passed."""

import asyncio
import os
import platform
import signal
import subprocess
import sys
import tempfile
import time
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from scryntic.application.analysis import VerifiedDataset
from scryntic.archive.canonical import canonical_json_bytes
from scryntic.domain.dataset import DatasetRef
from scryntic.jobs.codec import decode_response
from scryntic.jobs.contracts import ForecastInputs, JobAttempt
from scryntic.model_worker import launcher
from scryntic.model_worker.launcher import CPUWorker
from scryntic.model_worker.profile import FORECAST_CPU, CPUProfile, IsolationError
from scryntic.models.definitions import definition, policy
from scryntic.models.inventory import RuntimeBundle
from scryntic.models.selection import ModelSelection
from scryntic.models.window import ForecastWindow
from tests.jobs.helpers import job

pytestmark = pytest.mark.skipif(
    os.environ.get("SCRYNTIC_F22_HOST") != "1",
    reason="F22 requires authorized system-manager and provisioned real CPU runtime",
)


def workload() -> tuple[CPUWorker, JobAttempt, ForecastWindow]:
    selection = ModelSelection.read(Path(os.environ["SCRYNTIC_F22_CONFIG"]))
    assert selection.selected == "timesfm-2.5"
    assert selection.artifacts is not None
    assert selection.runtime is not None
    assert selection.runtime_sha256 is not None
    bundle = RuntimeBundle.read(
        selection.artifacts, selection.runtime, selection.runtime_sha256
    )
    model = definition(selection.selected)
    review = model.review(bundle.inventory_sha256)
    value = replace(
        job(),
        review=review,
        policy=policy((review,)),
        deadline_ns=job().submitted_ns + 240 * 10**9,
    )
    last = value.inputs.verified.last_start_ns
    step = value.forecast.frequency_ns
    reference = DatasetRef(
        value.forecast.dataset.manifest_sha256, value.forecast.dataset.schema, 512
    )
    closes = tuple(100.0 + i / 100 for i in range(512))
    value = replace(
        value,
        forecast=replace(value.forecast, dataset=reference, horizon=24),
        inputs=ForecastInputs(VerifiedDataset(reference, last, step), closes[-2:]),
    )
    window = ForecastWindow(
        reference, tuple(last - (511 - i) * step for i in range(512)), closes, step
    )
    return (
        CPUWorker(bundle=bundle, model_id=model.name),
        JobAttempt(value, 1, "a" * 32),
        window,
    )


def test_real_runtime_offline_inference_and_effective_controls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    worker, attempt, window = workload()
    monkeypatch.setenv("HF_TOKEN", "F22-must-not-be-inherited")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "F22-must-not-be-inherited")
    with tempfile.TemporaryFile() as secret:
        secret.write(b"F22-inherited-descriptor-sentinel")
        secret.flush()
        os.set_inheritable(secret.fileno(), True)
        first = decode_response(
            asyncio.run(worker.probe_window(attempt, window)), attempt
        )
    controls, usage = worker.last_controls, worker.last_usage
    assert controls is not None
    assert usage is not None
    assert controls["probes"] == "passed"
    assert controls["thread_policy"] == "pthread-only-bounded"
    assert "HF_TOKEN" not in controls["environment"]
    second = decode_response(
        asyncio.run(worker.execute_window(attempt, window)), attempt
    )
    assert first.points == second.points
    evidence: dict[str, Any] = {
        "profile": FORECAST_CPU.name,
        "kernel": platform.release(),
        "python": platform.python_version(),
        "controls": controls,
        "resource_use": usage,
    }
    print(canonical_json_bytes(evidence).decode())


@pytest.mark.parametrize(
    "mode", ["symlink", "hardlink", "flood", "cpu", "memory", "crash"]
)
def test_real_profile_failures_cannot_import_results(mode: str) -> None:
    worker, attempt, window = workload()
    operation = worker.probe_window(attempt, window, mode)
    with pytest.raises(IsolationError):
        asyncio.run(operation)
    assert worker.last_controls is None
    assert worker.last_usage is None


@pytest.mark.parametrize(
    "property_name",
    [
        "PrivateNetwork",
        "PrivatePIDs",
        "MemoryMax",
        "MemorySwapMax",
        "TasksMax",
        "CPUQuota",
        "LimitAS",
    ],
)
def test_real_missing_control_fails_before_runtime_result(
    monkeypatch: pytest.MonkeyPatch, property_name: str
) -> None:
    original = launcher._command

    def missing(
        root: Path, request: Path, unit: str, profile: CPUProfile = FORECAST_CPU
    ) -> list[str]:
        command = original(root, request, unit, profile)
        return [
            value
            for value in command
            if not value.startswith("--property=" + property_name + "=")
        ]

    monkeypatch.setattr(launcher, "_command", missing)
    worker, attempt, window = workload()
    operation = worker.execute_window(attempt, window)
    with pytest.raises(IsolationError):
        asyncio.run(operation)
    assert worker.last_controls is None


def test_real_cancel_cleanup_and_restart() -> None:
    worker, attempt, window = workload()

    async def exercise() -> None:
        task = asyncio.create_task(worker.probe_window(attempt, window, "hold"))
        # Snapshot copying is also cancellation-safe and must be joined.
        await asyncio.sleep(3)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        decode_response(await worker.execute_window(attempt, window), attempt)

    asyncio.run(exercise())
    status = subprocess.run(
        [
            "/usr/bin/systemctl",
            "--system",
            "list-units",
            "--no-legend",
            "--plain",
            "--state=running",
            "scryntic-model-*.service",
        ],
        capture_output=True,
        timeout=5,
        check=True,
    )
    assert not status.stdout.strip()


def _units() -> set[str]:
    result = subprocess.run(
        [
            "/usr/bin/systemctl",
            "--system",
            "list-units",
            "--no-legend",
            "--plain",
            "--state=running",
            "scryntic-model-*.service",
        ],
        capture_output=True,
        timeout=5,
        check=True,
    )
    return {line.split()[0] for line in result.stdout.decode().splitlines()}


def test_real_abrupt_coordinator_death_has_bounded_worker_lifetime() -> None:
    before = _units()
    process = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import asyncio; from tests.models.test_host import workload; w,a,c=workload(); asyncio.run(w.probe_window(a,c,'hold'))",
        ],
        env={"SCRYNTIC_F22_CONFIG": os.environ["SCRYNTIC_F22_CONFIG"]},
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    try:
        deadline = time.monotonic() + 30
        launched: set[str] = set()
        while time.monotonic() < deadline and process.poll() is None:
            launched = _units() - before
            if launched:
                break
            time.sleep(0.1)
        assert launched
        os.killpg(process.pid, signal.SIGKILL)
        process.wait(timeout=5)
        deadline = time.monotonic() + FORECAST_CPU.wall_seconds + 5
        while time.monotonic() < deadline and launched & _units():
            time.sleep(0.5)
        assert not launched & _units()
    finally:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=5)
