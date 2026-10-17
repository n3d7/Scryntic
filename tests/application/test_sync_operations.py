"""Trusted synchronization enrollment and actual owned pull completion."""

import asyncio
import json
import shutil
import threading
from collections.abc import Callable
from dataclasses import asdict, replace
from pathlib import Path
from types import TracebackType
from typing import TypeVar

import pytest

from scryntic.application.operations import ApplicationError, ErrorCode, Operation
from scryntic.configuration.paths import Installation
from scryntic.configuration.values import Configuration
from scryntic.sync.catalog import PullCatalog
from scryntic.sync.model import Enrollment, PullError, PullLimits
from tests.normalization.helpers import installation
from tests.sync.helpers import enrollment

T = TypeVar("T")


class Context:
    def __init__(self, root: Path, *, enabled: bool = True) -> None:
        self.installation = installation(root)
        path = self.installation.config_dir / "config.toml"
        path.write_text(
            'profile="workstation"\n[capabilities]\nsynchronization='
            + str(enabled).lower()
            + "\n"
            + (
                '[credentials.transfer]\nbackend="file"\nname="pull-key"\n'
                if enabled
                else ""
            )
        )
        path.chmod(0o600)

    async def run_io(self, operation: Callable[[], T]) -> T:
        return await asyncio.to_thread(operation)

    def read_config_json(self, leaf: str, maxbytes: int) -> object:
        value = (self.installation.config_dir / leaf).read_bytes()
        assert len(value) <= maxbytes
        return json.loads(value)


def descriptor(
    context: Context,
    endpoint: Enrollment | None = None,
    trust: dict[str, object] | None = None,
) -> Path:
    value = {
        "version": 1,
        "endpoint": asdict(endpoint or replace(enrollment(), name="test")),
        "trust": trust or {"mode": "genesis"},
    }
    path = context.installation.config_dir / "enrollment"
    path.write_text(json.dumps(value))
    path.chmod(0o600)
    return path


def test_sync_forms_reject_paths_and_unbounded_wall_time() -> None:
    from scryntic.application.sync_operations import WORKFLOWS

    enroll = next(w for w in WORKFLOWS if w.operation is Operation.SYNC_ENROLL)
    pull = next(w for w in WORKFLOWS if w.operation is Operation.SYNC_PULL)
    with pytest.raises(ApplicationError):
        enroll.request({"descriptor": "../credentials"})
    with pytest.raises(ApplicationError):
        pull.request({"wall_seconds": "601"})


def test_capability_rejection_happens_before_catalog_creation(tmp_path: Path) -> None:
    from scryntic.application.sync_operations import SyncStatusRequest
    from scryntic.workflows.synchronization import SynchronizationWorkflows

    context = Context(tmp_path, enabled=False)

    async def exercise() -> None:
        services = SynchronizationWorkflows(context)
        request = SyncStatusRequest("test")
        with pytest.raises(ApplicationError) as failure:
            await services.execute(request)
        assert failure.value.code is ErrorCode.UNSUPPORTED
        await services.aclose()

    asyncio.run(exercise())
    assert list(context.installation.state_dir.iterdir()) == []


def test_explicit_genesis_enrollment_and_offline_status_need_no_private_key(
    tmp_path: Path,
) -> None:
    from scryntic.application.sync_operations import (
        SyncEnrollRequest,
        SyncStatusRequest,
    )
    from scryntic.workflows.synchronization import SynchronizationWorkflows

    context = Context(tmp_path)
    descriptor(context)

    async def exercise() -> None:
        services = SynchronizationWorkflows(context)
        try:
            await services.execute(SyncEnrollRequest("enrollment"))
            status = await services.execute(SyncStatusRequest("test"))
            document = json.loads(status.payload)
            assert document["anchor"]["sequence"] == 0
            assert document["freshness"] == "unknown"
            assert document["observation"] == "offline"
            assert "host_key" not in status.payload.decode()
            assert "pull-key" not in status.payload.decode()
        finally:
            await services.aclose()

    asyncio.run(exercise())


def test_unavailable_import_owner_returns_closed_application_error(
    tmp_path: Path,
) -> None:
    from scryntic.application.sync_operations import SyncStatusRequest
    from scryntic.workflows.synchronization import SynchronizationWorkflows

    context = Context(tmp_path)

    async def exercise() -> None:
        services = SynchronizationWorkflows(context)
        try:
            request = SyncStatusRequest("test")
            with pytest.raises(ApplicationError) as failure:
                await services.execute(request)
            assert failure.value.code is ErrorCode.UNAVAILABLE
        finally:
            await services.aclose()

    with PullCatalog(context.installation):
        asyncio.run(exercise())


def test_unknown_trust_never_creates_enrollment(tmp_path: Path) -> None:
    from scryntic.application.sync_operations import SyncEnrollRequest
    from scryntic.workflows.synchronization import SynchronizationWorkflows

    context = Context(tmp_path)
    descriptor(context, trust={"mode": "discover-latest"})

    async def exercise() -> None:
        services = SynchronizationWorkflows(context)
        try:
            request = SyncEnrollRequest("enrollment")
            with pytest.raises(ApplicationError) as failure:
                await services.execute(request)
            assert failure.value.code is ErrorCode.CONFIGURATION
        finally:
            await services.aclose()

    asyncio.run(exercise())
    with PullCatalog(context.installation) as catalog:
        with pytest.raises(PullError):
            catalog.enrollment("test")


def test_explicit_checkpoint_is_trust_only_until_actual_pull(tmp_path: Path) -> None:
    from scryntic.application.sync_operations import (
        SyncEnrollRequest,
        SyncStatusRequest,
    )
    from scryntic.workflows.synchronization import SynchronizationWorkflows
    from tests.sync.test_catalog import published_chain

    chain, _ = published_chain(tmp_path / "published")
    context = Context(tmp_path / "client")
    descriptor(context, trust={"mode": "checkpoint", "manifest": json.loads(chain[0])})

    async def exercise() -> None:
        services = SynchronizationWorkflows(context)
        try:
            await services.execute(SyncEnrollRequest("enrollment"))
            status = await services.execute(SyncStatusRequest("test"))
            assert json.loads(status.payload)["anchor"]["sequence"] == 1
            assert json.loads(status.payload)["freshness"] == "unknown"
        finally:
            await services.aclose()

    asyncio.run(exercise())
    with PullCatalog(context.installation) as catalog:
        assert catalog.count() == 0


def test_pull_wall_deadline_closes_transport_without_advancing_anchor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import scryntic.workflows.synchronization as workflows
    from scryntic.application.sync_operations import SyncEnrollRequest, SyncPullRequest
    from scryntic.workflows.synchronization import SynchronizationWorkflows

    settled = threading.Event()

    class WaitingRemote:
        def __init__(
            self,
            endpoint: Enrollment,
            installation: Installation,
            configuration: Configuration,
            limits: PullLimits,
        ) -> None:
            self.enrollment = endpoint

        async def __aenter__(self) -> "WaitingRemote":
            return self

        async def __aexit__(
            self,
            exc_type: type[BaseException] | None,
            exc_value: BaseException | None,
            traceback: TracebackType | None,
        ) -> None:
            await asyncio.sleep(0.02)
            settled.set()

    async def waiting(*args: object) -> None:
        await asyncio.Event().wait()

    monkeypatch.setattr(workflows, "SFTPRemote", WaitingRemote)
    monkeypatch.setattr(workflows, "pull", waiting)
    context = Context(tmp_path)
    descriptor(context)

    async def exercise() -> None:
        services = SynchronizationWorkflows(context)
        try:
            await services.execute(SyncEnrollRequest("enrollment"))
            request = SyncPullRequest("test", wall_seconds=1)
            with pytest.raises(ApplicationError) as failure:
                await services.execute(request)
            assert failure.value.code is ErrorCode.UNAVAILABLE
            assert settled.is_set()
        finally:
            await services.aclose()

    asyncio.run(exercise())
    with PullCatalog(context.installation) as catalog:
        assert catalog.anchor("test").sequence == 0


@pytest.mark.parametrize("cancel_cleanup", [False, True])
def test_cancel_joins_worker_transport_cleanup_after_awaiter_cancellation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cancel_cleanup: bool
) -> None:
    import scryntic.workflows.synchronization as workflows
    from scryntic.application.sync_operations import SyncEnrollRequest, SyncPullRequest
    from scryntic.workflows.synchronization import SynchronizationWorkflows

    entered, settled = threading.Event(), threading.Event()
    cleanup_started, release_cleanup = threading.Event(), threading.Event()

    class WaitingRemote:
        def __init__(
            self,
            endpoint: Enrollment,
            installation: Installation,
            configuration: Configuration,
            limits: PullLimits,
        ) -> None:
            self.enrollment = endpoint

        async def __aenter__(self) -> "WaitingRemote":
            return self

        async def __aexit__(
            self,
            exc_type: type[BaseException] | None,
            exc_value: BaseException | None,
            traceback: TracebackType | None,
        ) -> None:
            cleanup_started.set()
            assert await asyncio.to_thread(release_cleanup.wait, 3)
            settled.set()

    async def waiting(*args: object) -> None:
        entered.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(workflows, "SFTPRemote", WaitingRemote)
    monkeypatch.setattr(workflows, "pull", waiting)
    context = Context(tmp_path)
    descriptor(context)

    async def exercise() -> None:
        services = SynchronizationWorkflows(context)
        await services.execute(SyncEnrollRequest("enrollment"))
        presentation = asyncio.create_task(services.execute(SyncPullRequest("test")))
        assert await asyncio.to_thread(entered.wait, 3)
        presentation.cancel()
        with pytest.raises(asyncio.CancelledError):
            await presentation
        assert not settled.is_set()
        closing = asyncio.create_task(services.cancel())
        assert await asyncio.to_thread(cleanup_started.wait, 3)
        if cancel_cleanup:
            closing.cancel()
            await asyncio.sleep(0)
            closing.cancel()
            await asyncio.sleep(0)
            assert not closing.done()
        release_cleanup.set()
        if cancel_cleanup:
            with pytest.raises(asyncio.CancelledError):
                await closing
        else:
            await closing
        assert settled.is_set()
        await services.aclose()

    asyncio.run(exercise())


def test_real_sftp_workflow_pulls_without_returning_credentials(tmp_path: Path) -> None:
    from scryntic.application.sync_operations import SyncEnrollRequest, SyncPullRequest
    from scryntic.publication.coordinator import Published
    from scryntic.workflows.synchronization import SynchronizationWorkflows
    from tests.publication.helpers import configured_coordinator
    from tests.sync.test_sftp import endpoint as served_endpoint

    bundle = configured_coordinator(tmp_path / "server")
    try:
        assert isinstance(bundle.coordinator.publish_next(), Published)
        network = tmp_path / "network"
        network.mkdir(mode=0o700)

        async def exercise() -> None:
            async with served_endpoint(network) as (adapter, auth, remote_root, _):
                shutil.copytree(
                    bundle.root.state_dir / "archive", remote_root, dirs_exist_ok=True
                )
                context = Context(tmp_path / "client")
                private = adapter._installation.credential_dir / "pull-key"
                key = context.installation.credential_dir / "pull-key"
                key.write_bytes(private.read_bytes())
                key.chmod(0o400)
                endpoint = replace(
                    adapter.enrollment, producer="collector-a", epoch="epoch-a"
                )
                descriptor(context, endpoint)
                services = SynchronizationWorkflows(context)
                try:
                    await services.execute(SyncEnrollRequest("enrollment"))
                    accepted = await services.execute(SyncPullRequest("test"))
                    value = json.loads(accepted.payload)
                    assert accepted.complete
                    assert value["anchor"]["sequence"] == 1
                    assert len(value["imported"]) == 1
                    assert auth == ["reader"]
                    assert "PRIVATE KEY" not in accepted.payload.decode()
                    assert "pull-key" not in accepted.payload.decode()
                finally:
                    await services.aclose()

        asyncio.run(exercise())
    finally:
        bundle.close()
