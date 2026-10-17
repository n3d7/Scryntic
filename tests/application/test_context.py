import asyncio
import os
import threading
from hashlib import sha256
from pathlib import Path

import pytest

from scryntic.application.operations import ApplicationError
from scryntic.configuration.paths import Installation
from scryntic.workflows.context import WorkflowContext


def provision(tmp_path: Path) -> Installation:
    installation = Installation.workstation(home=tmp_path, environment={})
    for path in (
        installation.config_dir,
        installation.state_dir,
        installation.runtime_dir,
    ):
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
        path.chmod(0o700)
    return installation


def test_protected_descriptors_and_no_overwrite(tmp_path: Path) -> None:
    ctx = WorkflowContext(provision(tmp_path))
    leaf = ctx.installation.config_dir / "plan"
    leaf.write_text('{"limit":1}')
    leaf.chmod(0o600)
    assert ctx.read_config_json("plan", 4096) == {"limit": 1}
    with pytest.raises(ApplicationError):
        ctx.read_config_json("../plan", 4096)
    leaf.chmod(0o644)
    with pytest.raises(ApplicationError):
        ctx.read_config_json("plan", 4096)
    summary = ctx.export_bundle("snapshot", {"replay.json": b"{}"})
    assert summary["complete"] is True
    with pytest.raises(ApplicationError):
        ctx.export_bundle("snapshot", {"replay.json": b"overwrite"})
    assert (
        ctx.installation.state_dir / "exports/snapshot/replay.json"
    ).read_bytes() == b"{}"


@pytest.mark.parametrize("cancel_close", [False, True])
def test_cancelled_awaiter_retains_owner_until_closed(
    tmp_path: Path, cancel_close: bool
) -> None:
    async def scenario() -> None:
        ctx = WorkflowContext(provision(tmp_path))
        started, release, settled = (
            threading.Event(),
            threading.Event(),
            threading.Event(),
        )

        def operation() -> int:
            started.set()
            release.wait(2)
            settled.set()
            return 1

        task = asyncio.create_task(ctx.run_io(operation))
        async with asyncio.timeout(3):
            while not started.is_set():
                await asyncio.sleep(0.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        closing = asyncio.create_task(ctx.aclose())
        await asyncio.sleep(0.01)
        assert not closing.done()
        if cancel_close:
            closing.cancel()
            await asyncio.sleep(0)
            closing.cancel()
            await asyncio.sleep(0)
            assert not closing.done()
        release.set()
        if cancel_close:
            with pytest.raises(asyncio.CancelledError):
                await closing
        else:
            await closing
        assert settled.is_set()

    asyncio.run(scenario())


def test_export_disk_failure_preserves_an_incomplete_destination(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx = WorkflowContext(provision(tmp_path))
    original = os.fsync
    calls = 0

    def failing(fd: int) -> None:
        nonlocal calls
        calls += 1
        if calls == 3:
            raise OSError("SENTINEL_PRIVATE_STORAGE_FAILURE")
        original(fd)

    monkeypatch.setattr(os, "fsync", failing)
    with pytest.raises(ApplicationError) as error:
        ctx.export_bundle("partial", {"replay.json": b"{}"})
    assert "SENTINEL" not in str(error.value)
    assert not (ctx.installation.state_dir / "exports/partial/complete.json").exists()
    with pytest.raises(ApplicationError):
        ctx.export_bundle("partial", {"replay.json": b"{}"})


def test_installed_dependency_pin_matches_reviewed_lock() -> None:
    from scryntic.workflows.context import LOCK_SHA256

    assert LOCK_SHA256 == sha256(Path("uv.lock").read_bytes()).hexdigest()
