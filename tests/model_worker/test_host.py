"""Mandatory release qualification on the authorized system-manager host.

Opt-in separates privilege-dependent qualification from portable unit gates.
Skipped tests are a qualification gap, never evidence that this profile passed.
"""

import asyncio
import os
import platform
import signal
import subprocess
import sys
import tempfile
import time

import pytest

from scryntic.archive.canonical import canonical_json_bytes
from scryntic.jobs.codec import decode_response
from scryntic.jobs.contracts import JobAttempt
from scryntic.model_worker import launcher
from scryntic.model_worker.launcher import CPUWorker
from scryntic.model_worker.profile import PROPERTIES, IsolationError
from tests.jobs.helpers import job

pytestmark = pytest.mark.skipif(
    os.environ.get("SCRYNTIC_F21_HOST") != "1",
    reason="F21 requires explicit authorized system-manager host qualification",
)


def attempt() -> JobAttempt:
    return JobAttempt(job(), 1, "a" * 32)


def test_effective_host_controls_and_f20_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("F21_SECRET_SENTINEL", "must-not-be-inherited")
    worker = CPUWorker()
    with tempfile.TemporaryFile() as secret:
        secret.write(b"F21-inherited-descriptor-sentinel")
        secret.flush()
        os.set_inheritable(secret.fileno(), True)
        result = asyncio.run(worker.probe(attempt()))
    decode_response(result, attempt())
    assert worker.last_controls is not None
    assert worker.last_controls["probes"] == "passed"
    assert "F21_SECRET_SENTINEL" not in worker.last_controls["environment"]
    print(
        canonical_json_bytes(
            {
                "profile": "f21-cpu-v1",
                "kernel": platform.release(),
                "python": platform.python_version(),
                "controls": worker.last_controls,
            }
        ).decode("utf-8")
    )


@pytest.mark.parametrize("mode", ["symlink", "hardlink", "flood", "cpu", "crash"])
def test_actual_host_failures_cannot_import_results(mode: str) -> None:
    worker = CPUWorker()
    operation = worker.probe(attempt(), mode)
    with pytest.raises(IsolationError):
        asyncio.run(operation)
    assert worker.last_controls is None


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
def test_actual_missing_control_prevents_worker_result(
    monkeypatch: pytest.MonkeyPatch,
    property_name: str,
) -> None:
    # Disposable negative launch specs only; the production policy is unchanged.
    monkeypatch.setattr(
        launcher,
        "PROPERTIES",
        tuple(
            value for value in PROPERTIES if not value.startswith(property_name + "=")
        ),
    )
    worker = CPUWorker()
    operation = worker.execute(attempt())
    with pytest.raises(IsolationError):
        asyncio.run(operation)
    assert worker.last_controls is None


def test_cancellation_cleanup_and_restart() -> None:
    async def exercise() -> None:
        worker = CPUWorker()
        task = asyncio.create_task(worker.probe(attempt(), "hold"))
        await asyncio.sleep(2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert worker.last_controls is None
        decode_response(await worker.execute(attempt()), attempt())

    start = time.monotonic()
    asyncio.run(exercise())
    assert time.monotonic() - start < 20
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


def _active_units() -> set[str]:
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
    return {line.split()[0] for line in status.stdout.decode("utf-8").splitlines()}


def test_abrupt_coordinator_death_has_bounded_worker_lifetime() -> None:
    before = _active_units()
    coordinator = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import asyncio; from tests.model_worker.test_host import attempt; "
            "from scryntic.model_worker.launcher import CPUWorker; "
            "asyncio.run(CPUWorker().probe(attempt(), 'hold'))",
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
        env={},
    )
    created: set[str] = set()
    try:
        ready = time.monotonic() + 10
        while not created and time.monotonic() < ready:
            assert coordinator.poll() is None
            created = _active_units() - before
            time.sleep(0.1)
        assert len(created) == 1
        os.kill(coordinator.pid, signal.SIGKILL)
        coordinator.wait(timeout=5)
        expiry = time.monotonic() + 35
        while created & _active_units() and time.monotonic() < expiry:
            time.sleep(0.2)
        assert not created & _active_units()
        # A new coordinator/attempt still satisfies the result contract.
        worker = CPUWorker()
        value = attempt()
        decode_response(asyncio.run(worker.execute(value)), value)
    finally:
        if coordinator.poll() is None:
            coordinator.kill()
            coordinator.wait(timeout=5)
        for unit in created:
            launcher._stop_unit(unit)
