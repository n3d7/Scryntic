"""Fixed, unprivileged Linux decoder launch and size/deadline bounded pipes."""

import os
import platform
import selectors
import signal
import subprocess
import sys
import sysconfig
import time
from pathlib import Path

from scryntic.imports.protocol import ImportError, ImportLimits


def _terminate_process(process: subprocess.Popen[bytes]) -> None:
    # PID namespace lifetime also kills descendants; group termination covers
    # launcher/bootstrap failures before the namespace exists.
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    process.wait()
    for pipe in (process.stdout, process.stderr):
        if pipe is not None:
            pipe.close()


def bounded_process(
    command: list[str], descriptors: tuple[int, ...], limits: ImportLimits
) -> bytes:
    """No communicate() accumulation or wait for attacker-controlled pipe EOF."""
    process: subprocess.Popen[bytes] | None = None
    try:
        process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            close_fds=True,
            pass_fds=descriptors,
            start_new_session=True,
            env={},
            cwd="/",
        )
        if process.stdin is None or process.stdout is None or process.stderr is None:
            raise ImportError("Decoder IPC unavailable")
        process.stdin.close()
        deadline = time.monotonic() + limits.wall_seconds
        output = bytearray()
        errors = 0
        with selectors.DefaultSelector() as selector:
            for stream in (process.stdout, process.stderr):
                os.set_blocking(stream.fileno(), False)
                selector.register(stream, selectors.EVENT_READ)
            while selector.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise ImportError("Decoder deadline exceeded")
                for key, _ in selector.select(min(remaining, 0.1)):
                    data = os.read(key.fd, min(4096, limits.max_message_bytes + 1))
                    if not data:
                        selector.unregister(key.fd)
                        continue
                    if key.fileobj is process.stdout:
                        output.extend(data)
                    else:
                        errors += len(data)
                    if (
                        len(output) > limits.max_message_bytes
                        or errors > limits.max_message_bytes
                    ):
                        raise ImportError("Decoder message limit exceeded")
            remaining = deadline - time.monotonic()
            if remaining <= 0 or process.wait(timeout=remaining) != 0:
                raise ImportError("Restricted decoder failed")
        return bytes(output)
    except Exception:
        raise ImportError("Restricted decoder failed") from None
    finally:
        if process is not None:
            _terminate_process(process)


class LinuxDecoder:
    """No shell/unit/runtime policy or codec selected by hostile input."""

    def __init__(self, limits: ImportLimits) -> None:
        self.limits = limits

    def _command(self, inputs: tuple[int, ...], *, probe: bool) -> list[str]:
        if platform.system() != "Linux" or platform.machine() != "x86_64":
            raise ImportError("Unsupported decoder host")
        python_root = Path(sys.base_prefix).resolve()
        packages = Path(sysconfig.get_path("purelib"))
        application = Path(__file__).resolve().parents[1]
        command = [
            "/usr/bin/bwrap",
            "--unshare-user",
            "--unshare-ipc",
            "--unshare-pid",
            "--unshare-net",
            "--unshare-uts",
            "--unshare-cgroup",
            "--disable-userns",
            "--assert-userns-disabled",
            "--uid",
            "65534",
            "--gid",
            "65534",
            "--cap-drop",
            "ALL",
            "--die-with-parent",
            "--new-session",
            "--clearenv",
            "--ro-bind",
            str(python_root),
            "/python",
            "--ro-bind",
            "/usr/lib64",
            "/usr/lib64",
            "--symlink",
            "usr/lib64",
            "/lib64",
            "--ro-bind",
            "/usr/lib",
            "/usr/lib",
            "--symlink",
            "usr/lib",
            "/lib",
            "--ro-bind",
            str(application),
            "/app/scryntic",
            "--dir",
            "/packages",
        ]
        # Only the selected native codec closure, never the coordinator's full
        # venv, home, SSH directory or developer toolchain.
        for name in ("pyarrow",):
            source = packages / name
            if not source.is_dir() or source.is_symlink():
                raise ImportError("Decoder runtime unavailable")
            command.extend(("--ro-bind", str(source), f"/packages/{name}"))
        command.extend(("--dir", "/input"))
        names = () if probe else ("manifest", "raw", "normalized")
        for fd, name in zip(inputs, names, strict=True):
            command.extend(
                ("--perms", "0400", "--ro-bind-data", str(fd), f"/input/{name}")
            )
        command.extend(
            (
                "--remount-ro",
                "/",
                "--chdir",
                "/",
                "--setenv",
                "OPENBLAS_NUM_THREADS",
                "1",
                "--setenv",
                "OMP_NUM_THREADS",
                "1",
                "--setenv",
                "ARROW_NUM_THREADS",
                "1",
                "--setenv",
                "LC_ALL",
                "C",
                "--setenv",
                "MALLOC_CONF",
                "background_thread:false",
                "--setenv",
                "ARROW_DEFAULT_MEMORY_POOL",
                "system",
                "/python/bin/python3.12",
                "-I",
                "-S",
                "-B",
                "/app/scryntic/imports/bootstrap.py",
                str(self.limits.memory_bytes),
                str(self.limits.cpu_seconds),
            )
        )
        command.extend(
            ["probe"]
            if probe
            else [
                str(self.limits.max_encoded_bytes),
                str(self.limits.max_decoded_bytes),
                str(self.limits.max_records),
            ]
        )
        return command

    def decode(self, inputs: tuple[int, int, int]) -> bytes:
        return bounded_process(self._command(inputs, probe=False), inputs, self.limits)

    def probe(self) -> None:
        output = bounded_process(self._command((), probe=True), (), self.limits)
        if output != b'{"isolation":"verified","protocol":1}':
            raise ImportError("Decoder host qualification failed")
