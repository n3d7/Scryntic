"""Local commands validate capabilities and finish actual durable source work."""

import asyncio
import json
import time
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

import pytest

from scryntic.application.clock import SyncEvidence
from scryntic.application.local_operations import BackfillRequest, CollectRequest
from scryntic.application.operations import (
    ApplicationError,
    ApplicationResult,
    ErrorCode,
    Operation,
)
from scryntic.daemon.components import Services
from tests.application.test_data_operations import Context
from tests.normalization.helpers import installation


class FreshSynchronizationStatus:
    """Recorded timing evidence, independent of the test host's NTP state."""

    def read(self) -> SyncEvidence:
        return SyncEvidence(time.time_ns(), 0, 1_000_000, True)


def configured(tmp_path: Path, profile: str = "collector") -> Context:
    root = installation(tmp_path)
    path = root.config_dir / "config.toml"
    path.write_text(f'profile = "{profile}"\n')
    path.chmod(0o600)
    return Context(root)


def recorded_services(
    context: Context,
    request: CollectRequest | BackfillRequest,
    services_factory: Callable[..., Services],
) -> ApplicationResult:
    """Use the production finite lifecycle with explicit recorded dependencies."""
    from scryntic.workflows.collection_host import run_collection

    return run_collection(
        context.installation, request, services_factory=services_factory
    )


@pytest.mark.parametrize("cancelled", (False, True))
def test_fixed_child_deadline_and_repeated_cancel_reap_actual_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cancelled: bool
) -> None:
    import signal
    import sys

    import scryntic.workflows.local as local_module
    from scryntic.application.local_operations import CollectRequest

    processes: list[asyncio.subprocess.Process] = []

    async def spawn() -> asyncio.subprocess.Process:
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-I",
            "-c",
            "import signal,time;signal.signal(signal.SIGTERM,signal.SIG_IGN);time.sleep(30)",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        processes.append(process)
        return process

    monkeypatch.setattr(local_module, "_spawn_collection", spawn)
    monkeypatch.setattr(local_module, "_child_deadline_seconds", lambda _: 0.2)

    async def exercise() -> None:
        services = local_module.LocalWorkflows(configured(tmp_path))
        task = asyncio.create_task(services.execute(CollectRequest()))
        while not processes:
            await asyncio.sleep(0.01)
        if cancelled:
            await asyncio.sleep(0.05)
            task.cancel()
            await asyncio.sleep(0.01)
            task.cancel()
        expected = asyncio.CancelledError if cancelled else ApplicationError
        with pytest.raises(expected) as rejected:
            await asyncio.wait_for(task, 3)
        if not cancelled:
            assert isinstance(rejected.value, ApplicationError)
            assert rejected.value.code is ErrorCode.INCOMPLETE
        assert processes[0].returncode == -signal.SIGKILL
        assert services._process is None
        await services.aclose()

    asyncio.run(exercise())


def test_local_services_exist_without_interface_imports() -> None:
    import subprocess
    import sys

    script = """
import sys
class BlockOptional:
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in ('pyarrow', 'asyncssh', 'textual'):
            raise ModuleNotFoundError('Optional backend is unavailable')
sys.meta_path.insert(0, BlockOptional())
from scryntic.application.local_operations import ConfigValidateRequest
from scryntic.workflows.local import LocalWorkflows
from scryntic.workflows.collection_host import encode_request, decode_request
assert not {'pyarrow', 'asyncssh', 'textual'} & sys.modules.keys()
"""
    completed = subprocess.run(
        [sys.executable, "-I", "-c", script],
        capture_output=True,
        check=False,
        timeout=10,
    )
    assert completed.returncode == 0, completed.stderr


def test_missing_collector_adapter_fails_before_starting_any_owner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import scryntic.workflows.local as local_module

    context = configured(tmp_path)
    monkeypatch.setattr(local_module, "find_spec", lambda _: None)
    workflows = local_module.LocalWorkflows(context)
    request = CollectRequest()
    operation = workflows.execute(request)
    with pytest.raises(ApplicationError) as rejected:
        asyncio.run(operation)
    assert rejected.value.code is ErrorCode.UNAVAILABLE
    assert rejected.value.exit_code == 5
    assert workflows._process is None
    assert not (context.installation.state_dir / "spool").exists()
    assert not (context.installation.state_dir / "jobs").exists()


@pytest.mark.parametrize("stream", ("stdout", "stderr"))
def test_collection_host_output_is_bounded_and_reaped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stream: str
) -> None:
    import sys

    import scryntic.workflows.local as local_module

    processes: list[asyncio.subprocess.Process] = []

    async def spawn() -> asyncio.subprocess.Process:
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-I",
            "-c",
            f"import sys,time;sys.{stream}.buffer.write(b'x'*(4*1024*1024));"
            f"sys.{stream}.flush();time.sleep(30)",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        processes.append(process)
        return process

    monkeypatch.setattr(local_module, "_spawn_collection", spawn)

    async def exercise() -> None:
        workflows = local_module.LocalWorkflows(configured(tmp_path))
        request = CollectRequest()
        operation = workflows.execute(request)
        with pytest.raises(ApplicationError) as rejected:
            await asyncio.wait_for(operation, 3)
        assert rejected.value.code is ErrorCode.INCOMPLETE
        assert processes[0].returncode is not None
        assert workflows._process is None

    asyncio.run(exercise())


def test_collection_host_requires_closed_bounded_descriptor(tmp_path: Path) -> None:
    from dataclasses import asdict

    from scryntic.application.local_operations import CollectRequest
    from scryntic.workflows.collection_host import decode_request

    context = configured(tmp_path)
    installation_value = asdict(context.installation)
    installation_value = {
        key: str(value) if isinstance(value, Path) else value
        for key, value in installation_value.items()
    }
    value = {
        "version": 1,
        "installation": installation_value,
        "operation": "collect",
        "request": asdict(CollectRequest()),
    }
    target, request = decode_request(json.dumps(value).encode())
    assert target == context.installation
    assert request == CollectRequest()
    for mutated in (
        value | {"command": "private"},
        value | {"installation": installation_value | {"owner_uid": 0}},
        value | {"request": asdict(CollectRequest()) | {"seconds": 999}},
    ):
        raw = json.dumps(mutated).encode()
        with pytest.raises((ValueError, ApplicationError)):
            decode_request(raw)


def test_local_registry_rejects_unbounded_or_unsupported_requests() -> None:
    from scryntic.application.local_operations import WORKFLOWS

    workflows = {workflow.operation: workflow for workflow in WORKFLOWS}
    for operation, fields in (
        (Operation.COLLECT, {"seconds": "301"}),
        (Operation.COLLECT, {"symbol": "BTCUSDT/secret"}),
        (Operation.BACKFILL, {"start_ns": "1", "end_ns": "2"}),
        (Operation.SOURCES_INSPECT, {"profile": "downloaded-plugin"}),
    ):
        with pytest.raises(ApplicationError) as rejected:
            workflows[operation].request(fields)
        assert rejected.value.code is ErrorCode.INVALID


def test_validate_and_offline_status_report_only_observed_evidence(
    tmp_path: Path,
) -> None:
    from scryntic.application.local_operations import (
        ConfigValidateRequest,
        StatusRequest,
    )
    from scryntic.workflows.local import LocalWorkflows

    context = configured(tmp_path, "workstation")
    services = LocalWorkflows(context)

    async def run() -> None:
        validation = json.loads(
            (await services.execute(ConfigValidateRequest())).payload
        )
        assert validation["profile"] == "workstation"
        assert validation["enabled"] == ["local_analysis"]
        status = json.loads((await services.execute(StatusRequest())).payload)
        assert status["liveness"] == status["readiness"] == "unknown"
        assert status["observation"] == "offline"
        assert "healthy" not in status
        assert not (context.installation.state_dir / "spool").exists()

    asyncio.run(run())


def test_source_descriptors_are_offline_and_truthful(tmp_path: Path) -> None:
    from scryntic.application.local_operations import (
        SourceInspectRequest,
        SourcesListRequest,
    )
    from scryntic.workflows.local import LocalWorkflows

    services = LocalWorkflows(configured(tmp_path))

    async def run() -> None:
        listed = json.loads((await services.execute(SourcesListRequest())).payload)
        assert {item["profile"] for item in listed["sources"]} == {"fixture", "bybit"}
        fixture = json.loads(
            (await services.execute(SourceInspectRequest("fixture"))).payload
        )
        assert fixture["network"] is False
        assert fixture["capabilities"][0]["operations"] == ["history"]
        bybit = json.loads(
            (await services.execute(SourceInspectRequest("bybit"))).payload
        )
        assert bybit["network"] is True
        assert any("stream" in item["operations"] for item in bybit["capabilities"])
        assert any("history" in item["operations"] for item in bybit["capabilities"])

    asyncio.run(run())


def test_disabled_collection_fails_before_opening_any_collector(tmp_path: Path) -> None:
    from scryntic.application.local_operations import CollectRequest
    from scryntic.workflows.local import LocalWorkflows

    context = configured(tmp_path, "workstation")
    services = LocalWorkflows(context)
    request = CollectRequest(profile="bybit", seconds=1)
    operation = services.execute(request)
    with pytest.raises(ApplicationError) as rejected:
        asyncio.run(operation)
    assert rejected.value.code is ErrorCode.CONFIGURATION
    assert not (context.installation.state_dir / "spool").exists()
    assert not (context.installation.state_dir / "jobs").exists()


def test_finite_fixture_collection_flushes_and_releases_owners(tmp_path: Path) -> None:
    from scryntic.application.local_operations import CollectRequest
    from scryntic.ingestion.sqlite_spool import DurableIngestor
    from scryntic.workflows.local import LocalWorkflows

    context = configured(tmp_path)
    services = LocalWorkflows(context)
    completed = asyncio.run(
        services.execute(CollectRequest(profile="fixture", seconds=1))
    )
    fields = json.loads(completed.payload)
    assert completed.complete
    # The real child retains the host's clock degradation, when present.
    assert set(fields["faults"]) <= {"clock"}
    assert (
        fields["received"] == fields["durably_committed"] == fields["normalized"] == 2
    )
    assert fields["published"] == 2
    assert fields["shutdown_complete"] is True
    with DurableIngestor(
        context.installation,
        producer="cli",
        epoch="cli-v1",
        capacity=64,
        max_payload_bytes=8192,
    ):
        pass


@pytest.mark.parametrize("failed", (False, True))
def test_fixture_completion_preserves_real_faults_and_offline_health(
    tmp_path: Path, failed: bool
) -> None:
    from scryntic.application.sources import HistoryRequest
    from scryntic.daemon.config import DaemonConfig
    from scryntic.daemon.health import Fault

    class ObservedFixture(Services):
        async def _run_fixture(self) -> None:
            if failed:
                raise ValueError("Recorded fixture source failure")
            await super()._run_fixture()

    owners: list[Services] = []

    def factory(
        config: DaemonConfig, *, history_request: HistoryRequest | None = None
    ) -> Services:
        owner = ObservedFixture(
            replace(config, clock_interval_s=0.01),
            history_request=history_request,
            synchronization_status=FreshSynchronizationStatus(),
        )
        owners.append(owner)
        return owner

    completed = recorded_services(
        configured(tmp_path),
        CollectRequest(profile="fixture", seconds=1),
        factory,
    )
    fields = json.loads(completed.payload)
    assert completed.complete is not failed
    assert fields["faults"] == (["source"] if failed else [])
    assert fields["shutdown_complete"] is True
    health = owners[0].health()
    assert Fault.SOURCE in health.faults
    assert health.live is False
    assert health.streams[0].connected is False


@pytest.mark.parametrize("stale", (False, True), ids=("missing", "stale"))
def test_fixture_completion_retains_unqualified_clock_evidence(
    tmp_path: Path, stale: bool
) -> None:
    from scryntic.application.sources import HistoryRequest
    from scryntic.daemon.config import DaemonConfig
    from scryntic.daemon.health import ClockStatus, Fault, HealthSnapshot, State

    class UnqualifiedStatus:
        def read(self) -> SyncEvidence | None:
            if not stale:
                return None
            return SyncEvidence(time.time_ns() - 600_000_000_000, 0, 1_000_000, True)

    active_health: list[HealthSnapshot] = []
    owners: list[Services] = []

    class ObservedFixture(Services):
        def stop_intake(self) -> None:
            if not active_health:
                active_health.append(self.health())
            super().stop_intake()

    def factory(
        config: DaemonConfig, *, history_request: HistoryRequest | None = None
    ) -> Services:
        owner = ObservedFixture(
            replace(config, clock_interval_s=0.01),
            history_request=history_request,
            synchronization_status=UnqualifiedStatus(),
        )
        owners.append(owner)
        return owner

    completed = recorded_services(
        configured(tmp_path),
        CollectRequest(profile="fixture", seconds=1),
        factory,
    )
    fields = json.loads(completed.payload)
    assert completed.complete
    assert fields["faults"] == ["clock"]
    assert (
        fields["received"]
        == fields["durably_committed"]
        == fields["normalized"]
        == fields["published"]
        == 2
    )
    assert fields["shutdown_complete"] is True
    observed = active_health[0]
    assert observed.state is State.DEGRADED
    assert observed.clock_status is ClockStatus.UNKNOWN
    assert Fault.CLOCK in observed.faults
    assert observed.live
    assert observed.ready is False
    closed = owners[0].health()
    assert Fault.CLOCK in closed.faults
    assert closed.clock_status is ClockStatus.UNKNOWN
    assert closed.live is closed.ready is False


def test_collection_cancel_joins_actual_owners(tmp_path: Path) -> None:
    from scryntic.application.local_operations import CollectRequest
    from scryntic.workflows.local import LocalWorkflows

    services = LocalWorkflows(configured(tmp_path))

    async def run() -> None:
        worker = asyncio.create_task(
            services.execute(CollectRequest(profile="fixture", seconds=30))
        )
        await asyncio.sleep(0.2)
        await services.cancel()
        with pytest.raises(ApplicationError) as rejected:
            await asyncio.wait_for(worker, 10)
        assert rejected.value.code is ErrorCode.CANCELLED
        await services.aclose()

    asyncio.run(run())


def test_cancel_can_join_an_offline_request_that_finishes_concurrently(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import threading

    from scryntic.application.local_operations import ConfigValidateRequest
    from scryntic.application.operations import Request
    from scryntic.workflows.local import LocalWorkflows

    workflows = LocalWorkflows(configured(tmp_path))
    started = threading.Event()
    release = threading.Event()
    execute = workflows._execute

    def blocked(request: Request) -> ApplicationResult:
        started.set()
        assert release.wait(3)
        return execute(request)

    monkeypatch.setattr(workflows, "_execute", blocked)

    async def exercise() -> None:
        operation = asyncio.create_task(workflows.execute(ConfigValidateRequest()))
        assert await asyncio.to_thread(started.wait, 3)
        cancellation = asyncio.create_task(workflows.cancel())
        await asyncio.sleep(0.05)
        release.set()
        assert (await operation).complete
        await asyncio.wait_for(cancellation, 3)
        await workflows.aclose()

    asyncio.run(exercise())


@pytest.mark.parametrize("phase", ("startup", "drain", "stalled"))
def test_failed_collection_host_exits_and_releases_actual_owners(
    tmp_path: Path, phase: str
) -> None:
    import subprocess
    import sys

    from scryntic.ingestion.sqlite_spool import DurableIngestor
    from scryntic.jobs.store import JobStore
    from scryntic.workflows.collection_host import encode_request

    context = configured(tmp_path)
    script = """
import functools
import sys
import threading
from scryntic.daemon.components import Services
from scryntic.daemon.foreground import run_foreground
from scryntic.daemon.runtime import LifecycleLimits
from scryntic.workflows import collection_host as host
phase = sys.argv[1]
installation, request = host.decode_request(sys.stdin.buffer.read())
class Observed(Services):
    async def start(self):
        await super().start()
        if phase == 'startup':
            raise RuntimeError('private startup fault')
    async def close(self):
        await super().close()
        (installation.state_dir / 'closed').touch()
def fail(stage):
    if stage == 'after_reservation_commit':
        if phase == 'stalled':
            threading.Event().wait()
        raise RuntimeError('private publication fault')
def factory(config, *, history_request=None):
    return Observed(config, history_request=history_request, publication_fault=fail)
host.run_foreground = functools.partial(run_foreground, limits=LifecycleLimits(
    startup_s=3, shutdown_s=0.3, poll_s=0.01))
value = host.run_collection(installation, request, services_factory=factory)
raise RuntimeError('Failed collection was acknowledged')
"""
    completed = subprocess.run(
        [sys.executable, "-I", "-c", script, phase],
        input=encode_request(context.installation, CollectRequest()),
        capture_output=True,
        timeout=10,
        check=False,
    )
    assert completed.returncode == 1
    assert completed.stdout == b""
    assert b"private" not in completed.stderr
    if phase != "stalled":
        assert (context.installation.state_dir / "closed").exists()
    with JobStore(context.installation):
        pass
    with DurableIngestor(
        context.installation,
        producer="cli",
        epoch="cli-v1",
        capacity=64,
        max_payload_bytes=8192,
    ):
        pass
    # Restart uses the real host and can recover publication reservations.
    from scryntic.workflows.local import LocalWorkflows

    restarted = asyncio.run(
        LocalWorkflows(context).execute(CollectRequest(epoch="restart-v1"))
    )
    fields = json.loads(restarted.payload)
    # Replaying fixture origins can truthfully retain a source fault; completed
    # drain proves recovered committed data and owner release independently.
    assert fields["shutdown_complete"]
    assert fields["received"] == 2
    assert fields["durably_committed"] == fields["normalized"] == fields["published"]
    assert "storage" not in fields["faults"]


def test_backfill_uses_actual_history_and_persists_explicit_coverage(
    tmp_path: Path,
) -> None:
    from scryntic.application.local_operations import BackfillRequest
    from scryntic.application.sources import HistoryRequest
    from scryntic.daemon.components import Services
    from scryntic.daemon.config import DaemonConfig
    from scryntic.publication.sqlite_store import PublicationStore
    from scryntic.sources.bybit import BybitHistoricalSource
    from tests.sources.test_bybit import FixedClock, FixtureClient, fixture, sample

    start = 1_700_000_100_000_000_000
    step = 60_000_000_000
    client = FixtureClient(
        {
            "/v5/market/instruments-info": [fixture("spot-instruments.json")],
            "/v5/market/kline": [fixture("klines-page-1.json")],
        }
    )
    history = BybitHistoricalSource(
        client=client, clock=FixedClock(sample(wall_ns=start + 10 * step))
    )

    def services(
        config: DaemonConfig, *, history_request: HistoryRequest | None = None
    ) -> Services:
        return Services(
            config, history_request=history_request, history_factory=lambda _: history
        )

    context = configured(tmp_path)
    completed = recorded_services(
        context, BackfillRequest(start, start + 2 * step, seconds=5), services
    )
    fields = json.loads(completed.payload)
    assert completed.complete, fields
    assert "source" not in fields["faults"]
    assert fields["coverage"] == "complete"
    assert (
        fields["repaired_candles"]
        == fields["durably_committed"]
        == fields["normalized"]
        == 2
    )
    assert fields["pages"] == 1
    assert fields["shutdown_complete"]
    with PublicationStore(context.installation, producer="cli") as store:
        checkpoint = store.status().checkpoint
        assert checkpoint is not None
        assert checkpoint.offset == 2


def test_source_configuration_failure_cannot_report_completed_collection(
    tmp_path: Path,
) -> None:
    from scryntic.application.local_operations import CollectRequest
    from scryntic.application.sources import HistoryRequest
    from scryntic.daemon.components import Services
    from scryntic.daemon.config import DaemonConfig
    from scryntic.sources.bybit import BybitHistoricalSource
    from tests.sources.test_bybit import FixedClock, FixtureClient, fixture, sample

    client = FixtureClient(
        {"/v5/market/instruments-info": [fixture("spot-instruments.json")]}
    )
    history = BybitHistoricalSource(
        client=client, clock=FixedClock(sample(wall_ns=1_700_000_300_000_000_000))
    )

    def services(
        config: DaemonConfig, *, history_request: HistoryRequest | None = None
    ) -> Services:
        return Services(
            config, history_request=history_request, history_factory=lambda _: history
        )

    completed = recorded_services(
        configured(tmp_path),
        CollectRequest(profile="bybit", symbol="MISSING", seconds=1),
        services,
    )
    assert not completed.complete
    assert json.loads(completed.payload)["received"] == 0


def test_transient_history_failure_is_a_source_fault_not_storage(
    tmp_path: Path,
) -> None:
    from typing import Any

    from scryntic.application.local_operations import BackfillRequest
    from scryntic.application.sources import HistoryRequest, SourceTransientError
    from scryntic.daemon.components import Services
    from scryntic.daemon.config import DaemonConfig
    from scryntic.sources.bybit import BybitHistoricalSource
    from tests.sources.test_bybit import FixedClock, FixtureClient, fixture, sample

    class Client(FixtureClient):
        async def get(self, path: str, params: dict[str, str | int]) -> dict[str, Any]:
            if path == "/v5/market/kline":
                raise SourceTransientError("private upstream response")
            return await super().get(path, params)

    start = 1_700_000_100_000_000_000
    client = Client({"/v5/market/instruments-info": [fixture("spot-instruments.json")]})
    history = BybitHistoricalSource(
        client=client, clock=FixedClock(sample(wall_ns=start + 600_000_000_000))
    )

    def services(
        config: DaemonConfig, *, history_request: HistoryRequest | None = None
    ) -> Services:
        return Services(
            config, history_request=history_request, history_factory=lambda _: history
        )

    completed = recorded_services(
        configured(tmp_path),
        BackfillRequest(start, start + 120_000_000_000, seconds=5),
        services,
    )
    fields = json.loads(completed.payload)
    assert not completed.complete
    assert "source" in fields["faults"]
    assert "storage" not in fields["faults"]
    assert b"private" not in completed.payload
