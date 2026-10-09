"""Portable process/contract tests; these do not qualify the systemd boundary."""

import asyncio
import io
import os
import signal
import sys
from pathlib import Path
from typing import Any

import pytest

from scryntic.archive.canonical import canonical_json_bytes
from scryntic.jobs.codec import encode_response
from scryntic.jobs.contracts import JobAttempt
from scryntic.jobs.fake import calculate
from scryntic.model_worker import launcher
from scryntic.model_worker.launcher import CPUWorker, IsolatedFakeProvider
from scryntic.model_worker.profile import (
    FORECAST_CPU,
    PROPERTIES,
    SYNTHETIC_CPU,
    CPUProfile,
    IsolationError,
)
from scryntic.model_worker.wire import document
from tests.jobs.helpers import job, review
from tests.jobs.test_execution import services
from tests.model_worker.helpers import host


def attempt() -> JobAttempt:
    return JobAttempt(job(), 1, "a" * 32)


def test_fixed_spec_cannot_accept_configuration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        launcher, "_manager_environment", lambda: {"LD_PRELOAD", "PRIVATE_TOKEN"}
    )
    command = launcher._command(
        tmp_path, tmp_path / "request", "scryntic-model-test.service"
    )
    assert "--user" not in command
    assert "--no-ask-password" in command
    assert "--expand-environment=no" in command
    assert all("--property=" + property in command for property in PROPERTIES)
    assert (
        sum(item.startswith("--property=BindReadOnlyPaths=") for item in command) == 1
    )
    unset = next(
        item for item in command if item.startswith("--property=UnsetEnvironment=")
    )
    assert "LD_PRELOAD" in unset
    assert "PRIVATE_TOKEN" in unset
    assert command[-5:] == [
        "/python/bin/python3.12",
        "-I",
        "-S",
        "-B",
        "/app/scryntic/model_worker/bootstrap.py",
    ]
    unsafe = tmp_path / "unsafe%path"
    request_path = tmp_path / "request"
    with pytest.raises(IsolationError):
        launcher._command(unsafe, request_path, "unit")
    with pytest.raises(TypeError):
        CPUWorker(shell="dangerous")  # type: ignore[call-arg]


def test_missing_manager_rejects_before_spawn(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def missing(*args: Any) -> list[str]:
        raise IsolationError("private-secret-sentinel")

    async def spawn(*args: Any, **kwargs: Any) -> None:
        calls.append("spawn")

    monkeypatch.setattr(launcher, "_command", missing)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    operation = CPUWorker().execute(attempt())
    with pytest.raises(IsolationError) as error:
        asyncio.run(operation)
    assert not calls
    assert "sentinel" not in str(error.value)


@pytest.mark.parametrize("profile", [SYNTHETIC_CPU, FORECAST_CPU])
@pytest.mark.parametrize("lib64", [False, True])
def test_loader_aliases_bind_only_existing_readonly_libraries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, profile: CPUProfile, lib64: bool
) -> None:
    monkeypatch.setattr(launcher, "_manager_environment", set)
    is_dir = Path.is_dir
    monkeypatch.setattr(
        Path,
        "is_dir",
        lambda path: lib64 if path == Path("/usr/lib64") else is_dir(path),
    )
    command = launcher._command(
        tmp_path, tmp_path / "request", "scryntic-model-test.service", profile
    )
    binds = next(
        item.split("=", 1)[1]
        for item in command
        if item.startswith("--property=BindReadOnlyPaths=")
    ).split()
    libraries = {
        item
        for item in binds
        if item.split(":")[1] in {"/lib", "/lib64", "/usr/lib", "/usr/lib64"}
    }
    expected = {"/usr/lib:/usr/lib", "/usr/lib:/lib"}
    if lib64:
        expected.update({"/usr/lib64:/usr/lib64", "/usr/lib64:/lib64"})
    assert libraries == expected
    assert not any(item.startswith("--property=BindPaths=") for item in command)


def test_request_mount_point_exists_before_unit_launch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    launched: list[str] = []

    def command(root: Path, request: Path, unit: str) -> list[str]:
        target = root / "input/request"
        assert target.is_file()
        assert not target.is_symlink()
        assert target.read_bytes() == b""
        assert target.stat().st_mode & 0o222 == 0
        assert request.read_bytes()
        assert root.parent.stat().st_mode & 0o777 == 0o700
        for name in ("lib", "lib64"):
            assert (root / name).is_dir()
            assert not (root / name).is_symlink()
        return [unit]

    async def launch(
        self: CPUWorker,
        specification: list[str],
        unit: str,
        owner: dict[str, Any],
        value: JobAttempt,
    ) -> bytes:
        launched.append(unit)
        return b"portable-launch-sentinel"

    monkeypatch.setattr(launcher, "_command", command)
    monkeypatch.setattr(CPUWorker, "_launch", launch)
    assert asyncio.run(CPUWorker().execute(attempt())) == b"portable-launch-sentinel"
    assert len(launched) == 1


def test_missing_effective_controls_never_invoke_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from scryntic.imports import bootstrap as parser_bootstrap
    from scryntic.jobs import fake
    from scryntic.jobs.codec import encode_attempt
    from scryntic.model_worker import bootstrap, controls

    calls: list[str] = []
    data = canonical_json_bytes(
        {
            "attempt": encode_attempt(attempt()).decode("utf-8"),
            "host": host(),
            "mode": "execute",
        }
    )

    def missing(owner: dict[str, Any]) -> dict[str, Any]:
        raise IsolationError("Missing control")

    monkeypatch.setattr(sys, "path", sys.path.copy())
    monkeypatch.setattr("builtins.open", lambda *args, **kwargs: io.BytesIO(data))
    monkeypatch.setattr(parser_bootstrap, "restrict_syscalls", lambda: None)
    monkeypatch.setattr(controls, "effective_controls", missing)
    monkeypatch.setattr(fake, "calculate", lambda *args: calls.append("provider"))
    with pytest.raises(IsolationError):
        bootstrap.main()
    assert not calls


@pytest.mark.parametrize(
    "program",
    [
        "import sys; sys.stdout.write('x'*200000)",
        "import sys; sys.stderr.write('x'*10000)",
        "print('malformed-secret-sentinel')",
        "raise SystemExit(1)",
    ],
)
def test_real_pipe_failures_are_bounded_and_cleaned(
    monkeypatch: pytest.MonkeyPatch,
    program: str,
) -> None:
    stopped: list[str] = []
    monkeypatch.setattr(launcher, "_stop_unit", stopped.append)

    async def exercise() -> None:
        worker = CPUWorker()
        command = [sys.executable, "-I", "-c", program]
        owner = host()
        value = attempt()
        with pytest.raises((IsolationError, ValueError)):
            await worker._launch(command, "unit", owner, value)

    asyncio.run(exercise())
    assert stopped == ["unit"]


def test_cancellation_joins_unit_cleanup_even_when_repeated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stopped: list[str] = []
    pids: list[int] = []
    real_spawn = asyncio.create_subprocess_exec
    monkeypatch.setattr(launcher, "_stop_unit", stopped.append)

    async def spawn(*args: Any, **kwargs: Any) -> asyncio.subprocess.Process:
        process = await real_spawn(*args, **kwargs)
        pids.append(process.pid)
        await asyncio.sleep(0.02)
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)

    async def exercise() -> None:
        task = asyncio.create_task(
            CPUWorker()._launch(
                [sys.executable, "-I", "-c", "import time; time.sleep(120)"],
                "unit",
                host(),
                attempt(),
            )
        )
        while not pids:
            await asyncio.sleep(0)
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(exercise())
    assert stopped == ["unit"]
    with pytest.raises(ProcessLookupError):
        os.kill(pids[0], signal.SIGCONT)


def test_f20_service_preserves_validation_and_durable_acceptance(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def execute(self: CPUWorker, value: JobAttempt) -> bytes:
        return encode_response(value, calculate(value, value.job.review))

    monkeypatch.setattr(CPUWorker, "execute", execute)
    _, store, _, service = services(tmp_path, provider=IsolatedFakeProvider(review()))
    try:
        service.submit_job(job())
        record = asyncio.run(service.run("job-a"))
        assert record.state.value == "succeeded"
        assert service.result("job-a") == record.artifact
    finally:
        store.close()


@pytest.mark.parametrize(
    "data", [b'{"a":1,"a":2}', b'{"a":NaN}', b'{ "a":1}', b"[]", b"{}", b"x" * 200000]
)
def test_launcher_wire_is_closed_and_bounded(data: bytes) -> None:
    with pytest.raises(IsolationError):
        document(data, "a")


def test_only_approved_local_fixture_and_fixed_probes() -> None:
    model = review("remote")
    operation = CPUWorker().probe(attempt(), "shell")
    with pytest.raises(IsolationError):
        IsolatedFakeProvider(model)
    with pytest.raises(IsolationError):
        asyncio.run(operation)
