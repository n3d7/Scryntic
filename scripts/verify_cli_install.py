"""Exercise the installed console script from a directory outside the checkout."""

from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import pty
import select
import shutil
import subprocess
import sys
import tempfile
import time
import tty
from pathlib import Path

_READ_ONLY_FIXTURE = "read-only fixture"
_RUN_KEY = "ctrl+r"
_STATUS_SELECTOR = "#status"
_WORKSTATION_CONFIG = 'profile = "workstation"\n'
_DENIED_WORKER_COMMAND = """
import sys
from unittest.mock import patch
from scryntic.cli import main
from scryntic.model_worker.launcher import CPUWorker
from scryntic.model_worker.profile import IsolationError

async def deny_launch(*args, **kwargs):
    raise IsolationError("INSTALL-SENTINEL: external worker launch denied")

with patch.object(CPUWorker, "_launch", deny_launch):
    sys.exit(main())
"""


def _installation(home: Path) -> Path:
    config = home / ".config" / "scryntic"
    for path in (
        config,
        config / "credentials",
        home / ".local" / "state" / "scryntic",
        home / ".local" / "state" / "scryntic" / "runtime",
    ):
        path.mkdir(parents=True, mode=0o700, exist_ok=True)
        path.chmod(0o700)
    document = config / "config.toml"
    document.write_text(_WORKSTATION_CONFIG)
    document.chmod(0o600)
    return document


def _command(
    console: Path,
    home: Path,
    cwd: Path,
    arguments: tuple[str, ...],
    expected_code: int = 0,
    *,
    expect_error: bool = False,
    deny_worker_launch: bool = False,
) -> dict[str, object]:
    environment = dict(os.environ)
    environment.pop("PYTHONPATH", None)
    environment.pop("PYTHONHOME", None)
    executable = (
        [str(console.parent / "python"), "-I", "-c", _DENIED_WORKER_COMMAND]
        if deny_worker_launch
        else [str(console)]
    )
    completed = subprocess.run(
        [*executable, "--home", str(home), *arguments],
        cwd=cwd,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        timeout=45,
    )
    assert completed.returncode == expected_code, (
        arguments[0],
        expected_code,
        completed.returncode,
    )
    has_result = expected_code in (0, 7) and not expect_error
    output = completed.stdout if has_result else completed.stderr
    assert "INSTALL-SENTINEL" not in completed.stdout + completed.stderr
    assert "Traceback" not in completed.stdout + completed.stderr
    assert not (completed.stderr if has_result else completed.stdout).strip()
    value = json.loads(output)
    assert type(value) is dict and value["version"] == 1
    assert value["ok"] is has_result
    if not arguments[0].startswith("--"):
        grouped = {"config", "sources", "sync", "data", "dataset", "providers", "jobs"}
        operation: str | None = arguments[0]
        if operation in grouped:
            operation = ".".join(arguments[:2])
        elif operation == "tui":
            operation = None
        assert value["operation"] == operation
    if expect_error and expected_code == 7:
        assert value["error"]["code"] == "operation_incomplete"
    if has_result:
        assert value["complete"] is (expected_code == 0)
    return value


def verify_console(console: Path, profile: str, scratch: Path) -> Path:
    """Check real public workflows and failures without remote networking."""
    assert console.is_file(), "Console entry point was not installed"
    home = scratch / "workstation"
    home.mkdir(mode=0o700)
    configuration = _installation(home)
    config = _command(console, home, scratch, ("config", "validate"))
    assert config["operation"] == "config.validate"
    assert isinstance(config["data"], dict)
    assert config["data"]["profile"] == "workstation"

    sources = _command(console, home, scratch, ("sources", "list"))
    assert sources["operation"] == "sources.list"
    assert isinstance(sources["data"], dict)
    assert isinstance(sources["data"]["sources"], list)
    assert {item["profile"] for item in sources["data"]["sources"]} == {
        "fixture",
        "bybit",
    }

    inspected_source = _command(
        console, home, scratch, ("sources", "inspect", "--profile", "fixture")
    )
    assert inspected_source["operation"] == "sources.inspect"

    providers = _command(console, home, scratch, ("providers", "list"))
    assert providers["operation"] == "providers.list"
    assert isinstance(providers["data"], dict)
    assert isinstance(providers["data"]["providers"], list)
    assert {item["name"] for item in providers["data"]["providers"]} == {
        "fake-persistence",
        "fake-trend",
        "timesfm-2.5",
    }

    status = _command(console, home, scratch, ("status",))
    assert status["operation"] == "status"
    assert isinstance(status["data"], dict)
    assert status["data"]["observation"] == "offline"
    assert status["data"]["readiness"] == "unknown"

    invalid = _command(console, home, scratch, ("--INSTALL-SENTINEL",), 2)
    assert isinstance(invalid["error"], dict)
    assert invalid["error"]["code"] == "invalid_request"
    configuration.write_text('log_level = "INSTALL-SENTINEL"\n')
    broken = _command(console, home, scratch, ("config", "validate"), 3)
    assert isinstance(broken["error"], dict)
    assert broken["error"]["code"] == "configuration_invalid"

    if profile in ("collector", "analysis"):
        configuration.write_text('profile = "collector"\n')
        collected = _command(
            console,
            home,
            scratch,
            ("collect", "--profile", "fixture", "--seconds", "1"),
        )
        assert isinstance(collected["data"], dict)
        fields = collected["data"]
        assert (
            fields["received"]
            == fields["durably_committed"]
            == fields["normalized"]
            == 2
        )
        assert fields["published"] >= 1 and fields["shutdown_complete"] is True
    configuration.write_text(_WORKSTATION_CONFIG)
    return home


START = 1_700_000_000_000_000_000
STEP = 60_000_000_000


def _publish_fixture(home: Path) -> Path:
    """Publish explicitly synthetic test history through installed F07 archives.

    Healthy timestamps and complete coverage below describe this closed fixture,
    never a physical host's clock evidence or real market completeness.
    """
    from decimal import Decimal

    from scryntic.application.archive import ArchiveLimits
    from scryntic.archive.normalized_parquet import NormalizedParquetArchive
    from scryntic.archive.raw_parquet import ParquetRawArchive
    from scryntic.configuration.paths import Installation
    from scryntic.domain.identity import InstrumentId
    from scryntic.domain.market import Instrument
    from scryntic.domain.raw import IngestionId, RawEnvelope, RawRecord
    from scryntic.domain.time import ClockSample, TimeQuality
    from scryntic.normalization.candle import (
        CandleNormalization,
        CandleSemantics,
        ParsedFakeCandle,
        inspect_fake_candle,
        normalize_parsed_candle,
    )
    from scryntic.normalization.sqlite_store import OutcomeKind, ProcessingOutcome
    from scryntic.publication.coordinator import PublicationCoordinator, Published
    from scryntic.publication.manifest import ManifestStorage
    from scryntic.publication.sqlite_store import PublicationLimits, PublicationStore

    instrument = Instrument(
        InstrumentId("fake-venue", "spot", "BTC-USDT"),
        "BTC",
        "USDT",
        Decimal("0.01"),
        Decimal("0.0001"),
        "BTC",
        "instrument-r1",
    )
    records: list[RawRecord] = []
    outcomes: dict[IngestionId, ProcessingOutcome] = {}
    semantics: dict[str, CandleSemantics] = {}
    predecessor: IngestionId | None = None
    for index in range(12):
        receipt = ClockSample(
            START + (index + 1) * STEP,
            (index + 1) * STEP,
            "closed-fixture",
            TimeQuality("fixture-clock", "healthy", 0, 0, 0),
        )
        payload = json.dumps(
            {
                "schema": {"name": "fake_candle", "major": 1, "minor": 0},
                "start_ns": START + index * STEP,
                "interval_ns": STEP,
                "open": "100.10",
                "high": "101.25",
                "low": "99.90",
                "close": str(Decimal(100) + Decimal(index) / 100),
                "volume": "12.3400",
                "finalized": True,
                "publication_time": None,
            },
            separators=(",", ":"),
        ).encode()
        record = RawRecord(
            IngestionId("wheel-smoke", "fixture-a", index + 1),
            RawEnvelope(
                source="fake-source",
                stream="candles",
                channel="public",
                adapter_version="1.0",
                receipt=receipt,
                payload=payload,
                payload_limit=8192,
                subject=instrument.identity,
            ),
        )
        parsed = inspect_fake_candle(record)
        assert isinstance(parsed, ParsedFakeCandle)
        normalized = normalize_parsed_candle(
            record, parsed, instrument, normalized_at_ns=receipt.wall_time_ns
        )
        assert isinstance(normalized, CandleNormalization)
        revision = normalized.semantics.revision()
        outcomes[record.identity] = ProcessingOutcome(
            identity=record.identity,
            predecessor=predecessor,
            raw_sha256=record.envelope.content_sha256,
            kind=OutcomeKind.ACCEPTED,
            semantic_revision=revision,
            rejection_code=None,
            rejection_field=None,
            input_schema=normalized.input_schema,
            instrument_schema=normalized.instrument_schema,
            output_schema=normalized.candle.schema,
            instrument_revision=normalized.instrument_revision,
            normalizer_version=normalized.candle.normalizer_version,
            receipt=receipt,
            normalized_at_ns=normalized.candle.normalized_at_ns,
        )
        semantics[revision] = normalized.semantics
        records.append(record)
        predecessor = record.identity

    class RawReader:
        def records_after(self, offset: int, *, limit: int) -> tuple[RawRecord, ...]:
            return tuple(
                record for record in records if record.identity.offset > offset
            )[:limit]

    class NormalizedReader:
        def outcome(self, identity: IngestionId) -> ProcessingOutcome | None:
            return outcomes.get(identity)

        def observation(self, revision: str) -> CandleSemantics | None:
            return semantics.get(revision)

    installation = Installation.workstation(home=home, environment={})
    archives = ArchiveLimits(32, 2_000_000, 2_000_000)
    with PublicationStore(installation, producer="wheel-smoke") as store:
        coordinator = PublicationCoordinator(
            RawReader(),
            NormalizedReader(),
            store,
            ParquetRawArchive(installation),
            NormalizedParquetArchive(installation),
            ManifestStorage(installation),
            installation,
            PublicationLimits(32, archives, archives, 200_000, 100),
        )
        assert isinstance(coordinator.publish_next(), Published)
    return installation.state_dir / "archive"


async def _verify_analysis(
    console: Path, scratch: Path, *, require_worker_success: bool
) -> None:
    import asyncssh

    server_home = scratch / "producer"
    server_home.mkdir(mode=0o700)
    _installation(server_home)
    published = await asyncio.to_thread(_publish_fixture, server_home)
    jail = scratch / "jail"
    jail.mkdir(mode=0o700)
    shutil.copytree(published, jail / "archive")
    home = scratch / "analysis"
    home.mkdir(mode=0o700)
    configuration = _installation(home)
    configuration.write_text(
        'profile="workstation"\n[capabilities]\nsynchronization=true\n'
        '[credentials.transfer]\nbackend="file"\nname="pull-key"\n'
    )
    client_key = asyncssh.generate_private_key("ssh-ed25519")
    host_key = asyncssh.generate_private_key("ssh-ed25519")
    private = configuration.parent / "credentials" / "pull-key"
    private.write_bytes(client_key.export_private_key())
    private.chmod(0o400)
    auth: list[str] = []

    class Server(asyncssh.SSHServer):
        def begin_auth(self, username: str) -> bool:
            auth.append(username)
            return True

        def public_key_auth_supported(self) -> bool:
            return True

        def validate_public_key(self, username: str, key: asyncssh.SSHKey) -> bool:
            return username == "reader" and key == client_key.convert_to_public()

    class ReadOnlyFiles(asyncssh.SFTPServer):
        def open(self, path: bytes, pflags: int, attrs: asyncssh.SFTPAttrs) -> object:
            if pflags != asyncssh.FXF_READ:
                raise asyncssh.SFTPPermissionDenied(_READ_ONLY_FIXTURE)
            return super().open(path, pflags, attrs)

        def write(self, file_obj: object, offset: int, data: bytes) -> int:
            raise asyncssh.SFTPPermissionDenied(_READ_ONLY_FIXTURE)

        def setstat(self, path: bytes, attrs: asyncssh.SFTPAttrs) -> None:
            raise asyncssh.SFTPPermissionDenied(_READ_ONLY_FIXTURE)

        lsetstat = setstat
        mkdir = setstat

        def fsetstat(self, file_obj: object, attrs: asyncssh.SFTPAttrs) -> None:
            raise asyncssh.SFTPPermissionDenied(_READ_ONLY_FIXTURE)

        def remove(self, path: bytes) -> None:
            raise asyncssh.SFTPPermissionDenied(_READ_ONLY_FIXTURE)

        rmdir = remove

        def rename(self, oldpath: bytes, newpath: bytes) -> None:
            raise asyncssh.SFTPPermissionDenied(_READ_ONLY_FIXTURE)

        posix_rename = rename
        link = rename
        symlink = rename

    listener = await asyncssh.create_server(
        Server,
        "127.0.0.1",
        0,
        server_host_keys=[host_key],
        sftp_factory=lambda channel: ReadOnlyFiles(channel, chroot=os.fsencode(jail)),
        encoding=None,
    )

    async def command(
        arguments: tuple[str, ...],
        expected_code: int = 0,
        *,
        expect_error: bool = False,
    ) -> dict[str, object]:
        return await asyncio.to_thread(
            _command,
            console,
            home,
            scratch,
            arguments,
            expected_code,
            expect_error=expect_error,
            deny_worker_launch=not require_worker_success,
        )

    try:
        descriptor = {
            "version": 1,
            "endpoint": {
                "name": "fixture",
                "host": "127.0.0.1",
                "port": listener.get_port(),
                "username": "reader",
                "remote_root": "/archive",
                "host_key": host_key.export_public_key().decode().strip(),
                "producer": "wheel-smoke",
                "epoch": "fixture-a",
            },
            "trust": {"mode": "genesis"},
        }
        enrollment = configuration.parent / "fixture-enrollment"
        enrollment.write_text(json.dumps(descriptor))
        enrollment.chmod(0o600)
        await command(("sync", "enroll", "--descriptor", "fixture-enrollment"))
        await command(("sync", "status", "--name", "fixture"))
        pulled = await command(("sync", "pull", "--name", "fixture"))
        assert auth == ["reader"]
        assert isinstance(pulled["data"], dict)
        imported = pulled["data"]["imported"]
        assert isinstance(imported, list) and len(imported) == 1
        accepted = imported[0]["manifest_hash"]
        assert isinstance(accepted, str)
    finally:
        listener.close()
        await listener.wait_closed()

    await command(("data", "inspect", "--manifest-sha256", accepted))
    from scryntic.configuration.paths import Installation
    from scryntic.dataset.recipes import CoverageClaim
    from scryntic.domain.identity import InstrumentId
    from scryntic.domain.time import ClockSample, TimeQuality
    from scryntic.jobs.store import JobStore

    captured = ClockSample(
        START - STEP,
        1,
        "closed-fixture",
        TimeQuality("fixture-clock", "healthy", 0, 0, 0),
    )
    claim = CoverageClaim(
        InstrumentId("fake-venue", "spot", "BTC-USDT"),
        STEP,
        START,
        START + 12 * STEP,
        "complete",
        captured,
    )
    coverage = configuration.parent / "fixture-coverage"
    coverage.write_text(json.dumps({"version": 1, "intervals": [claim.projection()]}))
    coverage.chmod(0o600)
    built = await command(
        (
            "dataset",
            "build",
            "--manifests",
            accepted,
            "--coverage",
            "require-complete",
            "--coverage-leaf",
            "fixture-coverage",
            "--label-horizon-steps",
            "1",
        )
    )
    assert isinstance(built["data"], dict)
    assert isinstance(built["data"]["dataset"], dict)
    dataset = built["data"]["dataset"]["manifest_sha256"]
    assert isinstance(dataset, str)
    inspected = await command(("dataset", "inspect", "--manifest-sha256", dataset))
    assert isinstance(inspected["data"], dict) and len(inspected["data"]["rows"]) == 12
    await command(
        (
            "dataset",
            "export",
            "--manifest-sha256",
            dataset,
            "--destination",
            "dataset-export",
        )
    )
    await command(
        (
            "replay",
            "--manifest-sha256",
            dataset,
            "--start-ns",
            str(START),
            "--validation-start-ns",
            str(START + 4 * STEP),
            "--test-start-ns",
            str(START + 8 * STEP),
            "--end-ns",
            str(START + 12 * STEP),
            "--destination",
            "replay-export",
        )
    )

    worker_code = 0 if require_worker_success else 7
    worker_state = "succeeded" if require_worker_success else "failed"

    async def submit(
        job_id: str, operation: tuple[str, ...] = ("jobs", "submit")
    ) -> None:
        submitted = await command(
            (
                *operation,
                "--dataset",
                dataset,
                "--job-id",
                job_id,
                "--start-ns",
                str(START),
                "--end-ns",
                str(START + 12 * STEP),
            ),
            worker_code if operation == ("analyze",) else 0,
        )
        if operation == ("analyze",):
            assert isinstance(submitted["data"], dict)
            assert submitted["data"]["job"]["state"] == worker_state

    await submit("wheel-job")
    await command(("jobs", "list"))
    await command(("jobs", "status", "--job-id", "wheel-job"))
    ran = await command(("jobs", "run", "--job-id", "wheel-job"), worker_code)
    assert isinstance(ran["data"], dict) and ran["data"]["job"]["state"] == worker_state
    if not require_worker_success:
        assert ran["data"]["job"]["reason"] == "provider_failed"
        assert ran["data"]["job"]["artifact"] is None
    await command(
        ("jobs", "result", "--job-id", "wheel-job"),
        0 if require_worker_success else 7,
        expect_error=not require_worker_success,
    )
    await submit("wheel-analyze", ("analyze",))
    await submit("wheel-cancel")
    cancelled = await command(("jobs", "cancel", "--job-id", "wheel-cancel"))
    assert (
        isinstance(cancelled["data"], dict)
        and cancelled["data"]["job"]["state"] == "cancelled"
    )
    await submit("wheel-resume")
    installation = Installation.workstation(home=home, environment={})
    with JobStore(installation) as store:
        store.claim("wheel-resume")
    resumed = await command(("jobs", "resume", "--job-id", "wheel-resume"), worker_code)
    assert (
        isinstance(resumed["data"], dict)
        and resumed["data"]["job"]["state"] == worker_state
    )


def verify_analysis(
    console: Path, scratch: Path, *, require_worker_success: bool = True
) -> None:
    """Require real worker success by default; explicit denial exercises failure."""
    asyncio.run(
        _verify_analysis(
            console, scratch, require_worker_success=require_worker_success
        )
    )


async def _verify_independent(home: Path) -> None:
    """Use installed application services without importing the CLI module."""
    from scryntic.application.local_operations import (
        ConfigValidateRequest,
        SourceInspectRequest,
    )
    from scryntic.configuration.paths import Installation
    from scryntic.use_cases import ApplicationServices

    assert "scryntic.cli" not in sys.modules
    _installation(home)
    services = ApplicationServices(Installation.workstation(home=home, environment={}))
    try:
        validated = await services.execute(ConfigValidateRequest())
        assert validated.complete
        inspected = await services.execute(SourceInspectRequest("fixture"))
        assert inspected.complete and b"fake-source" in inspected.payload
        assert "scryntic.cli" not in sys.modules
    finally:
        await services.aclose()


def verify_base_optional_boundaries(console: Path, home: Path, cwd: Path) -> None:
    """Require real missing adapters to report fixed unavailable errors."""
    from scryntic.application.operations import ErrorCode

    for module in ("pyarrow", "asyncssh", "textual"):
        assert importlib.util.find_spec(module) is None
        assert module not in sys.modules
    configuration = _installation(home)
    missing_tui = _command(console, home, cwd, ("tui",), 5)
    assert isinstance(missing_tui["error"], dict)
    assert missing_tui["error"]["code"] == ErrorCode.UNAVAILABLE.value
    configuration.write_text('profile = "collector"\n')
    try:
        missing_archive = _command(
            console, home, cwd, ("collect", "--profile", "fixture", "--seconds", "1"), 5
        )
        assert isinstance(missing_archive["error"], dict)
        assert missing_archive["error"]["code"] == ErrorCode.UNAVAILABLE.value
    finally:
        configuration.write_text(_WORKSTATION_CONFIG)


async def _verify_tui(home: Path) -> None:
    """Exercise generated forms against real services, without starting a daemon."""
    from textual.widgets import DataTable, Input, OptionList, Select, Static

    from scryntic.configuration.paths import Installation
    from scryntic.tui import ScrynticApp
    from scryntic.use_cases import ApplicationServices, available_workflows

    installation = Installation.workstation(home=home, environment={})
    _installation(home)
    workflows = available_workflows()
    assert len(workflows) == 23
    assert len({workflow.operation for workflow in workflows}) == 23
    app = ScrynticApp(ApplicationServices(installation), workflows)
    async with app.run_test(size=(100, 36)) as pilot:
        assert app.query_one("#workflows", OptionList).option_count == 23
        await pilot.press(_RUN_KEY)
        await pilot.pause()
        assert app.query_one("#results", DataTable).row_count > 0
        assert "Complete" in str(app.query_one(_STATUS_SELECTOR, Static).render())

        async def choose(operation: str) -> None:
            await pilot.press("ctrl+f")
            app.query_one("#workflow-search", Input).value = operation
            await pilot.pause()
            navigation = app.query_one("#workflows", OptionList)
            assert navigation.option_count > 0
            navigation.highlighted = navigation.get_option_index(operation)
            navigation.focus()
            await pilot.press("enter")
            await pilot.pause()

        for workflow in workflows:
            await choose(workflow.operation.value)
            assert app._selected == workflow
            for field in workflow.fields:
                control = app.query_one("#field-" + field.name.replace("_", "-"))
                if field.choices:
                    assert (
                        isinstance(control, Select) and control.value in field.choices
                    )
                else:
                    assert isinstance(control, Input) and control.value == field.default

        await choose("sources.inspect")
        app.query_one("#field-profile", Select).value = "bybit"
        await pilot.press(_RUN_KEY)
        await pilot.pause()
        assert "Complete" in str(app.query_one(_STATUS_SELECTOR, Static).render())
        assert any("bybit" in value for _, value in app._rows)

        await choose("collect")
        app.query_one("#field-seconds", Input).value = "0"
        await pilot.press(_RUN_KEY)
        await pilot.pause()
        assert app.query_one(_STATUS_SELECTOR, Static).has_class("error")

        await choose("jobs.cancel")
        app.query_one("#field-job-id", Input).value = "wheel-tui-job"
        await pilot.press(_RUN_KEY)
        await pilot.pause()
        assert app.screen.query_one("#confirm-back")
        await pilot.press("escape")
        await pilot.pause()
        await pilot.press("?")
        await pilot.pause()
        assert app.screen.query_one("#help-close")
        await pilot.press("escape")
        await app.action_quit()


def verify_tui_console(console: Path, home: Path, cwd: Path) -> None:
    """Launch the actual installed entry point on a PTY and quit by keyboard."""
    _installation(home)
    environment = dict(os.environ)
    environment.pop("PYTHONPATH", None)
    environment.pop("PYTHONHOME", None)
    environment.update(TERM="xterm-256color", COLUMNS="100", LINES="36")
    master, slave = pty.openpty()
    tty.setraw(slave)
    captured = bytearray()
    process = subprocess.Popen(
        [str(console), "--home", str(home), "tui"],
        cwd=cwd,
        env=environment,
        stdin=slave,
        stdout=slave,
        stderr=slave,
    )
    os.close(slave)

    def receive(expected: bytes) -> None:
        deadline = time.monotonic() + 15
        while expected not in captured:
            assert process.poll() is None, "TUI exited before expected screen"
            assert time.monotonic() < deadline, "TUI screen did not become ready"
            if select.select([master], [], [], 0.1)[0]:
                captured.extend(os.read(master, 65_536))
                assert len(captured) < 2_000_000, "Unexpected unbounded terminal output"

    def send(key: bytes) -> None:
        os.write(master, key)
        # Let focus and layout messages settle before sending the next key.
        deadline = time.monotonic() + 0.3
        while time.monotonic() < deadline:
            if select.select([master], [], [], 0.05)[0]:
                captured.extend(os.read(master, 65_536))

    try:
        receive(b"WORKFLOWS")
        send(b"\x06")
        send(b"providers.list")
        receive(b"providers.list")
        send(b"\t")
        send(b"\r")
        send(b"?")
        receive(b"SCRYNTIC / WORKSPACE HELP")
        send(b"\x1b")
        os.write(master, b"\x11")
        assert process.wait(timeout=15) == 0
        assert b"Traceback" not in captured
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=5)
        os.close(master)


def main() -> None:
    import scryntic

    assert len(sys.argv) == 2 and sys.argv[1] in {
        "base",
        "collector",
        "analysis",
        "analysis-host",
        "tui",
    }
    assert (
        Path(scryntic.__file__).resolve().is_relative_to(Path(sys.prefix).resolve())
    ), "Smoke check must use the installed wheel, not a checkout"
    console = Path(sys.executable).parent / "scryntic"
    role = sys.argv[1]
    profile = "analysis" if role == "analysis-host" else role
    with tempfile.TemporaryDirectory(prefix="scryntic-cli-smoke-") as scratch:
        root = Path(scratch)
        home = verify_console(console, profile, root)
        asyncio.run(_verify_independent(home))
        if profile == "base":
            verify_base_optional_boundaries(console, home, root)
        if profile == "analysis":
            verify_analysis(
                console, root, require_worker_success=role == "analysis-host"
            )
        if profile == "tui":
            asyncio.run(_verify_tui(home))
            verify_tui_console(console, home, root)
    print(f"Verified installed {role} CLI workflows and fixed diagnostics")
    if role == "analysis":
        print("F24 real-host worker success remains unqualified; run analysis-host")


if __name__ == "__main__":
    main()
