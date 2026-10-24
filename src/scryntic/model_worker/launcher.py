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
from typing import Any, Literal, cast
from uuid import uuid4

from scryntic.archive.canonical import canonical_json_bytes
from scryntic.jobs.admission import ModelReview
from scryntic.jobs.codec import decode_response, encode_attempt
from scryntic.jobs.contracts import JobAttempt, JobRequest
from scryntic.jobs.durable import DurableAttempt
from scryntic.jobs.fake import require_fixture
from scryntic.model_worker.controls import namespace_ids, verify_controls
from scryntic.model_worker.profile import (
    BASELINE_CPU,
    FORECAST_CPU,
    PROPERTIES,
    SYNTHETIC_CPU,
    WIRE_BYTES,
    WRITABLE_MOUNTS,
    CPUProfile,
    IsolationError,
)
from scryntic.model_worker.wire import document
from scryntic.models.definitions import definition
from scryntic.models.inventory import RuntimeBundle
from scryntic.models.window import ForecastWindow
from scryntic.research.baseline_jobs import BaselineJob
from scryntic.research.baselines import REQUEST_BYTES, RESPONSE_BYTES

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
            # Generated per service, even when absent from show-environment.
            "MEMORY_PRESSURE_WATCH",
            "MEMORY_PRESSURE_WRITE",
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
        f"{request}:/input/request /usr/lib:/usr/lib /usr/lib:/lib "
        f"/sys/fs/cgroup/system.slice/{unit}:/control"
    )
    if Path("/usr/lib64").is_dir():
        binds += " /usr/lib64:/usr/lib64 /usr/lib64:/lib64"
    if profile == FORECAST_CPU:
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
        self._baseline_active = False
        self._baseline_probe = False

    @property
    def profile(self) -> CPUProfile:
        if self._baseline_active:
            return BASELINE_CPU
        return self._model.profile if self._model is not None else SYNTHETIC_CPU

    async def execute_baseline(
        self, attempt: DurableAttempt[BaselineJob], data: bytes
    ) -> bytes:
        if self._baseline_active or self._model is not None:
            raise IsolationError("PI03 worker already active or bound to a model")
        self._baseline_active = True
        try:
            return await self._run(attempt, "execute", baseline=data)
        finally:
            self._baseline_active = False

    async def probe_baseline(
        self, attempt: DurableAttempt[BaselineJob], data: bytes
    ) -> bytes:
        """Qualify the fixed profile and input binding without forming labels or fitting."""
        if self._baseline_active or self._model is not None:
            raise IsolationError("PI03 worker already active or bound to a model")
        self._baseline_active = self._baseline_probe = True
        try:
            return await self._run(attempt, "baseline-probe", baseline=data)
        finally:
            self._baseline_active = self._baseline_probe = False

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

    def _verify_baseline(
        self, attempt: DurableAttempt[BaselineJob], data: bytes
    ) -> None:
        from hashlib import sha256

        from scryntic.research.baseline_jobs import workload_identity
        from scryntic.research.baselines import validate_input

        if type(attempt.job) is not BaselineJob or self._model is not None:
            raise IsolationError("Unregistered fixed PI03 workload")
        attempt.job.__post_init__()
        if attempt.job.workload_sha256 != workload_identity():
            raise IsolationError("PI03 application inventory changed")
        validate_input(data)
        if sha256(data).hexdigest() != attempt.job.input_sha256:
            raise IsolationError("PI03 immutable input mismatch")

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
        self,
        attempt: JobAttempt | DurableAttempt[BaselineJob],
        mode: str,
        window: ForecastWindow | None = None,
        *,
        baseline: bytes | None = None,
    ) -> bytes:
        self.last_controls = None
        self.last_usage = None
        try:
            if platform.system() != "Linux" or platform.machine() != "x86_64":
                raise IsolationError("Unsupported CPU worker host")
            if baseline is None:
                if not isinstance(attempt.job, JobRequest):
                    raise IsolationError("Unexpected forecast job")
                self._verify_workload(attempt, window)
                data = encode_attempt(attempt)
            else:
                from scryntic.research.baseline_jobs import (
                    encode_attempt as baseline_attempt,
                )

                self._verify_baseline(
                    cast(DurableAttempt[BaselineJob], attempt), baseline
                )
                data = canonical_json_bytes(
                    baseline_attempt(cast(DurableAttempt[BaselineJob], attempt))
                )
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
                    "lib",
                    "lib64",
                    "tmp",
                    "var/tmp",
                    "home",
                    "output",
                ):
                    (root / name).mkdir(parents=True, exist_ok=True)
                for name in WRITABLE_MOUNTS:
                    (root / name.removeprefix("/")).mkdir(parents=True, exist_ok=True)
                # Namespace setup cannot always create an inode beneath the
                # protected root. BindReadOnlyPaths replaces this empty target.
                (root / "input/request").touch(mode=0o444, exist_ok=False)
                # Reuse the selected host libraries through read-only binds;
                # temporary symlinks can be unreadable to systemd under SELinux.
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
                if baseline is not None:
                    request_value = {
                        "baseline_attempt": data.decode("utf-8"),
                        "host": host,
                        "mode": mode,
                        "baseline_input": baseline.decode("utf-8"),
                    }
                    if len(canonical_json_bytes(request_value)) > REQUEST_BYTES:
                        raise IsolationError("PI03 worker request byte limit")
                request.write_bytes(canonical_json_bytes(request_value))
                request.chmod(0o444)  # private 0700 ancestor; read-only unit bind
                unit = "scryntic-model-" + uuid4().hex + ".service"
                if self._model is None and baseline is None:
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
        attempt: JobAttempt | DurableAttempt[BaselineJob],
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
                asyncio.create_task(
                    _drain(
                        process.stdout,
                        2 * RESPONSE_BYTES + 65536
                        if self._baseline_active
                        else WIRE_BYTES,
                    )
                ),
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
        self,
        output: bytes,
        host: dict[str, Any],
        attempt: JobAttempt | DurableAttempt[BaselineJob],
    ) -> bytes:
        if self._baseline_probe:
            return self._decode_baseline_probe(
                output, host, cast(DurableAttempt[BaselineJob], attempt)
            )
        fields = "version controls response" + (
            " usage" if self._model is not None or self._baseline_active else ""
        )
        value = document(
            output,
            fields,
            maximum=2 * RESPONSE_BYTES + 65536 if self._baseline_active else WIRE_BYTES,
        )
        if (
            type(value["version"]) is not int
            or value["version"]
            != (3 if self._baseline_active else 2 if self._model is not None else 1)
            or type(value["response"]) is not str
        ):
            raise IsolationError("Unsupported worker response")
        verify_controls(value["controls"], host, self.profile)
        if self._model is not None or self._baseline_active:
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
        if self._baseline_active:
            from scryntic.research.baseline_jobs import (
                decode_response as baseline_response,
            )

            baseline_result = baseline_response(
                response, cast(DurableAttempt[BaselineJob], attempt)
            )
            from hashlib import sha256

            if (
                baseline_result["resource_usage"] != value["usage"]
                or baseline_result["controls_sha256"]
                != sha256(canonical_json_bytes(value["controls"])).hexdigest()
            ):
                raise IsolationError("PI03 control or usage report binding mismatch")
        else:
            decode_response(response, cast(JobAttempt, attempt))
        self.last_controls = value["controls"]
        return response

    def _decode_baseline_probe(
        self, output: bytes, host: dict[str, Any], attempt: DurableAttempt[BaselineJob]
    ) -> bytes:
        value = document(output, "version controls probe usage")
        if type(value["version"]) is not int or value["version"] != 3:
            raise IsolationError("Unexpected PI03 qualification version")
        verify_controls(value["controls"], host, self.profile)
        probe = value["probe"]
        expected = {
            "schema": "scryntic.pi03.worker-probe.v1",
            "job_sha256": attempt.job.sha256,
            "input_sha256": attempt.job.input_sha256,
            "model_fitted": False,
            "final_labels_accessed": False,
        }
        usage = value["usage"]
        maxima = {
            "elapsed_ns": self.profile.wall_seconds * 10**9,
            "cpu_ns": self.profile.cpu_seconds * 10**9,
            "max_rss_kib": self.profile.memory // 1024,
        }
        if (
            type(probe) is not dict
            or probe != expected
            or probe["model_fitted"] is not False
            or probe["final_labels_accessed"] is not False
            or type(usage) is not dict
            or set(usage) != set(maxima)
            or any(
                type(usage[k]) is not int or not 0 <= usage[k] <= maximum
                for k, maximum in maxima.items()
            )
        ):
            raise IsolationError("Invalid bounded PI03 qualification report")
        self.last_controls = value["controls"]
        self.last_usage = usage
        return canonical_json_bytes(probe)


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
