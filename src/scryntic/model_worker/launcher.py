"""One fixed CPU launch contract. No shell, user unit fragments or model loading."""

import asyncio
import os
import platform
import re
import signal
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from scryntic.archive.canonical import canonical_json_bytes
from scryntic.jobs.admission import ModelReview
from scryntic.jobs.codec import decode_response, encode_attempt
from scryntic.jobs.contracts import JobAttempt
from scryntic.jobs.fake import require_fixture
from scryntic.model_worker.controls import namespace_ids, verify_controls
from scryntic.model_worker.profile import (
    PROPERTIES,
    SYNTHETIC_CPU,
    WIRE_BYTES,
    CPUProfile,
    IsolationError,
)
from scryntic.model_worker.wire import document
from scryntic.models.definitions import definition
from scryntic.models.inventory import RuntimeBundle
from scryntic.models.window import ForecastWindow

_MANAGER = ["/usr/bin/systemctl", "--system", "--no-ask-password"]
_PATH = re.compile(r"/[A-Za-z0-9_./-]+")
_MODES = frozenset(
    {
        "execute",
        "probe",
        "hold",
        "cpu",
        "memory",
        "symlink",
        "hardlink",
        "flood",
        "crash",
    }
)


def _manager_environment() -> set[str]:
    # Manager is in the TCB. Never publish its output or secret values.
    result = subprocess.run(
        [*_MANAGER, "show-environment"],
        capture_output=True,
        timeout=5,
        env={},
        cwd="/",
        check=True,
    )
    if len(result.stdout) > 65_536:
        raise IsolationError("Manager environment exceeds bounds")
    names = {
        line.split(b"=", 1)[0].decode("ascii") for line in result.stdout.splitlines()
    }
    if any(
        re.fullmatch(r"[A-Za-z_]\w*", name, flags=re.ASCII) is None for name in names
    ):
        raise IsolationError("Invalid manager environment")
    return names


def _command(
    root: Path, request: Path, unit: str, profile: CPUProfile = SYNTHETIC_CPU
) -> list[str]:
    profile.require_owned()
    environment = dict(profile.environment)
    python = Path(sys.base_prefix).resolve()
    application = Path(__file__).resolve().parents[1]
    sources = [python, application, root, request]
    if any(_PATH.fullmatch(str(path)) is None for path in sources):
        raise IsolationError("Unsupported runtime path")
    unset = (
        _manager_environment()
        | {
            "INVOCATION_ID",
            "SYSTEMD_EXEC_PID",
            "JOURNAL_STREAM",
            "NOTIFY_SOCKET",
            "WATCHDOG_PID",
            "WATCHDOG_USEC",
            "CREDENTIALS_DIRECTORY",
            "LISTEN_FDS",
            "LISTEN_PID",
            "LISTEN_FDNAMES",
            "LOGNAME",
            "USER",
            "SHELL",
            "TERM",
            "LD_PRELOAD",
            "LD_LIBRARY_PATH",
            "PYTHONPATH",
            "PYTHONHOME",
        }
    ) - environment.keys()
    binds = (
        f"{python}:/python {application}:/app/scryntic "
        f"{request}:/input/request /usr/lib:/usr/lib "
        f"/sys/fs/cgroup/system.slice/{unit}:/control"
    )
    if Path("/usr/lib64").is_dir():
        binds += " /usr/lib64:/usr/lib64"
    if profile != SYNTHETIC_CPU:
        binds += f" {root / 'model'}:/model {root / 'runtime'}:/runtime"
    properties = [
        *(PROPERTIES if profile == SYNTHETIC_CPU else profile.properties),
        f"RootDirectory={root}",
        f"BindReadOnlyPaths={binds}",
        "Environment="
        + " ".join(f"{name}={value}" for name, value in environment.items()),
        "UnsetEnvironment=" + " ".join(sorted(unset)),
    ]
    return [
        "/usr/bin/systemd-run",
        "--system",
        "--no-ask-password",
        "--quiet",
        "--pipe",
        "--wait",
        "--collect",
        "--expand-environment=no",
        f"--unit={unit}",
        *(f"--property={value}" for value in properties),
        "/python/bin/python3.12",
        "-I",
        "-S",
        "-B",
        "/app/scryntic/model_worker/bootstrap.py",
    ]


async def _drain(stream: asyncio.StreamReader, maximum: int) -> bytes:
    data = bytearray()
    while chunk := await stream.read(4096):
        data.extend(chunk)
        if len(data) > maximum:
            raise IsolationError("Worker pipe exceeded bounds")
    return bytes(data)


def _stop_unit(unit: str) -> None:
    result = subprocess.run(
        [*_MANAGER, "stop", unit],
        capture_output=True,
        env={},
        cwd="/",
        timeout=5,
    )
    state = subprocess.run(
        [*_MANAGER, "show", "--property=ActiveState", "--value", unit],
        capture_output=True,
        env={},
        cwd="/",
        timeout=5,
    )
    # A collected transient unit no longer exists; absence is safe only when
    # manager access itself still works. Never swallow an authorization/bus error.
    if state.returncode != 0:
        subprocess.run(
            [*_MANAGER, "is-system-running"],
            capture_output=True,
            env={},
            cwd="/",
            timeout=5,
            check=True,
        )
    elif state.stdout.strip() not in (b"inactive", b"failed"):
        raise IsolationError("Worker cleanup could not be confirmed")
    if result.returncode != 0 and state.stdout.strip() not in (
        b"inactive",
        b"failed",
        b"",
    ):
        raise IsolationError("Worker cleanup failed")


async def _discard(stream: asyncio.StreamReader) -> None:
    while chunk := await stream.read(4096):
        del chunk  # Killed process pipes: never retain diagnostic bytes.


async def _cleanup(
    process: asyncio.subprocess.Process,
    unit: str,
    drains: list[asyncio.Task[bytes]],
) -> None:
    try:
        await asyncio.to_thread(_stop_unit, unit)
    finally:
        if process.returncode is None:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        for task in drains:
            task.cancel()
        await asyncio.gather(*drains, return_exceptions=True)
        # asyncio's subprocess transport can wait for pipe EOF even after
        # waitpid. Drain bounded kernel/transport buffers after killing writers.
        async with asyncio.timeout(5):
            await asyncio.gather(
                *(
                    _discard(stream)
                    for stream in (process.stdout, process.stderr)
                    if stream is not None
                )
            )
            await process.wait()


async def _join_cleanup(
    process: asyncio.subprocess.Process,
    unit: str,
    drains: list[asyncio.Task[bytes]],
) -> None:
    cleanup = asyncio.create_task(_cleanup(process, unit, drains))
    cancelled = False
    while not cleanup.done():
        try:
            await asyncio.shield(cleanup)
        except asyncio.CancelledError:
            cancelled = True
    cleanup.result()
    if cancelled:
        raise asyncio.CancelledError


class CPUWorker:
    """Operator-owned system-manager permission required; no elevation fallback."""

    def __init__(
        self, *, bundle: RuntimeBundle | None = None, model_id: str | None = None
    ) -> None:
        if (bundle is None) != (model_id is None):
            raise IsolationError("Incomplete fixed model workload")
        self._bundle = bundle
        self._model = definition(model_id) if model_id is not None else None
        if self._model is not None and self._model.adapter is None:
            raise IsolationError("Runtime workload requires a registered adapter")
        self.last_controls: dict[str, Any] | None = None
        self.last_usage: dict[str, int] | None = None

    @property
    def profile(self) -> CPUProfile:
        return self._model.profile if self._model is not None else SYNTHETIC_CPU

    async def execute_window(
        self, attempt: JobAttempt, window: ForecastWindow
    ) -> bytes:
        window.validate(attempt.job)
        return await self._run(attempt, "execute", window)

    async def execute(self, attempt: JobAttempt) -> bytes:
        return await self._run(attempt, "execute")

    async def probe(self, attempt: JobAttempt, mode: str = "probe") -> bytes:
        if mode not in _MODES - {"execute"}:
            raise IsolationError("Unknown fixed qualification operation")
        return await self._run(attempt, mode)

    async def probe_window(
        self, attempt: JobAttempt, window: ForecastWindow, mode: str = "probe"
    ) -> bytes:
        if mode not in _MODES - {"execute"}:
            raise IsolationError("Unknown fixed qualification operation")
        window.validate(attempt.job)
        return await self._run(attempt, mode, window)

    def _verify_workload(
        self, attempt: JobAttempt, window: ForecastWindow | None
    ) -> None:
        # Only application-owned fixed workloads reach the launcher.
        algorithm: Literal["persistence", "trend"] = (
            "trend"
            if attempt.job.review.descriptor.model.revision == "f20-fixed-trend-v1"
            else "persistence"
        )
        if self._model is None:
            require_fixture(attempt.job.review, algorithm)
        elif (
            self._bundle is None
            or window is None
            or self._bundle.assets != self._model.assets
            or attempt.job.review != self._model.review(self._bundle.inventory_sha256)
        ):
            raise IsolationError("Unverified registered model workload")
        if attempt.job.review.descriptor.execution != "local":
            raise IsolationError("CPU worker requires local admission")

    async def _snapshot(self, root: Path) -> None:
        if self._bundle is not None:
            # Join cancellation during snapshotting before removing its
            # private ancestor; no orphan thread can recreate staging.
            snapshot = asyncio.create_task(
                asyncio.to_thread(self._bundle.snapshot, root)
            )
            try:
                await asyncio.shield(snapshot)
            except asyncio.CancelledError:
                while not snapshot.done():
                    try:
                        await asyncio.shield(snapshot)
                    except asyncio.CancelledError:
                        pass
                    except Exception:
                        break
                snapshot.exception()  # Observe snapshot failure; preserve caller cancellation.
                raise

    async def _run(
        self, attempt: JobAttempt, mode: str, window: ForecastWindow | None = None
    ) -> bytes:
        self.last_controls = None
        self.last_usage = None
        try:
            if platform.system() != "Linux" or platform.machine() != "x86_64":
                raise IsolationError("Unsupported CPU worker host")
            self._verify_workload(attempt, window)
            data = encode_attempt(attempt)
            with tempfile.TemporaryDirectory(prefix="scryntic-model-") as temporary:
                staging = Path(temporary)
                root = staging / "root"
                root.mkdir(mode=0o755)
                for name in (
                    "python",
                    "app/scryntic",
                    "input",
                    "control",
                    "usr/lib",
                    "usr/lib64",
                    "tmp",
                    "home",
                    "output",
                ):
                    (root / name).mkdir(parents=True, exist_ok=True)
                (root / "lib").symlink_to("usr/lib")
                (root / "lib64").symlink_to("usr/lib64")
                await self._snapshot(root)
                canary = staging / "host-secret"
                canary.write_bytes(b"F21-private-host-canary")
                host: dict[str, Any] = {
                    "uid": os.getuid(),
                    "namespaces": namespace_ids(),
                    "canary": str(canary),
                }
                request = staging / "request"
                request_value: dict[str, Any] = {
                    "attempt": data.decode("utf-8"),
                    "host": host,
                    "mode": mode,
                }
                if self._model is not None and window is not None:
                    request_value.update(
                        model=self._model.name, context=window.projection()
                    )
                request.write_bytes(canonical_json_bytes(request_value))
                request.chmod(0o444)  # private 0700 ancestor; read-only unit bind
                unit = "scryntic-model-" + uuid4().hex + ".service"
                if self._model is None:
                    command = await asyncio.to_thread(_command, root, request, unit)
                else:
                    command = await asyncio.to_thread(
                        _command, root, request, unit, self.profile
                    )
                return await self._launch(command, unit, host, attempt)
        except asyncio.CancelledError:
            self.last_controls = None
            self.last_usage = None
            raise
        except Exception:
            self.last_controls = None
            self.last_usage = None
            raise IsolationError(
                "Required CPU worker isolation or execution failed"
            ) from None

    async def _launch(
        self,
        command: list[str],
        unit: str,
        host: dict[str, Any],
        attempt: JobAttempt,
    ) -> bytes:
        spawn = asyncio.create_task(
            asyncio.create_subprocess_exec(
                *command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env={},
                cwd="/",
                close_fds=True,
                start_new_session=True,
            )
        )
        # Cancellation can arrive while the subprocess transport is being
        # created. Join that creation and clean up instead of orphaning a unit.
        try:
            process = await asyncio.shield(spawn)
        except asyncio.CancelledError:
            while not spawn.done():
                try:
                    await asyncio.shield(spawn)
                except asyncio.CancelledError:
                    pass
            await _join_cleanup(spawn.result(), unit, [])
            raise
        drains: list[asyncio.Task[bytes]] = []
        try:
            if process.stdout is None or process.stderr is None:
                raise IsolationError("Worker IPC unavailable")
            drains = [
                asyncio.create_task(_drain(process.stdout, WIRE_BYTES)),
                asyncio.create_task(_drain(process.stderr, 4096)),
            ]
            async with asyncio.timeout(self.profile.wall_seconds + 5):
                output, _ = await asyncio.gather(*drains)
                if await process.wait() != 0:
                    raise IsolationError("Worker failed")
            return self._decode_worker(output, host, attempt)
        finally:
            await _join_cleanup(process, unit, drains)

    def _decode_worker(
        self, output: bytes, host: dict[str, Any], attempt: JobAttempt
    ) -> bytes:
        fields = "version controls response" + (
            " usage" if self._model is not None else ""
        )
        value = document(output, fields)
        if (
            type(value["version"]) is not int
            or value["version"] != (2 if self._model is not None else 1)
            or type(value["response"]) is not str
        ):
            raise IsolationError("Unsupported worker response")
        verify_controls(value["controls"], host, self.profile)
        if self._model is not None:
            usage = value["usage"]
            maxima = {
                "elapsed_ns": (self.profile.wall_seconds + 5) * 10**9,
                "cpu_ns": self.profile.cpu_seconds * 10**9,
                "max_rss_kib": self.profile.memory // 1024,
            }
            if (
                type(usage) is not dict
                or set(usage) != set(maxima)
                or any(
                    type(usage[k]) is not int or not 0 <= usage[k] <= maximum
                    for k, maximum in maxima.items()
                )
            ):
                raise IsolationError("Invalid bounded worker resource report")
            self.last_usage = usage
        response = value["response"].encode("utf-8", "strict")
        decode_response(response, attempt)
        self.last_controls = value["controls"]
        return response


class IsolatedFakeProvider:
    """F20 JobProvider adapter; admission and durable state still belong to F20."""

    def __init__(
        self,
        review: ModelReview,
        *,
        algorithm: Literal["persistence", "trend"] = "persistence",
    ) -> None:
        require_fixture(review, algorithm)
        if review.descriptor.execution != "local":
            raise IsolationError("CPU worker requires local admission")
        self.review = review
        self.worker = CPUWorker()

    async def execute(self, attempt: JobAttempt) -> bytes:
        if attempt.job.review != self.review:
            raise IsolationError("Worker admission changed")
        return await self.worker.execute(attempt)
