import os
import signal
import subprocess
import sys
import time

_PROGRAM = """
import asyncio
import time
from dataclasses import replace
from scryntic.daemon.foreground import run_foreground
from scryntic.daemon.health import HealthSnapshot, State
from scryntic.daemon.runtime import LifecycleLimits
class Services:
    async def start(self):
        print("ready", flush=True)
        if EXIT_CONTROL:
            raise SystemExit("SENTINEL-private-control-payload")
        if BLOCK_LOOP:
            time.sleep(30)
    def stop_intake(self):
        pass
    async def drain(self):
        if STALL:
            await asyncio.to_thread(time.sleep, 30)
    async def close(self):
        pass
    def health(self):
        return HealthSnapshot(state=State.HEALTHY)
raise SystemExit(run_foreground(Services(), limits=LifecycleLimits(shutdown_s=0.1)))
"""


def _child(
    stall: bool, *, block_loop: bool = False, exit_control: bool = False
) -> subprocess.Popen[str]:
    return subprocess.Popen(
        [
            sys.executable,
            "-c",
            _PROGRAM.replace("STALL", repr(stall))
            .replace("BLOCK_LOOP", repr(block_loop))
            .replace("EXIT_CONTROL", repr(exit_control)),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env={**os.environ, "PYTHONPATH": "src"},
    )


def test_foreground_sigterm_drains_and_exits() -> None:
    child = _child(False)
    try:
        assert child.stdout is not None
        assert child.stdout.readline() == "ready\n"
        child.send_signal(signal.SIGTERM)
        stdout, stderr = child.communicate(timeout=3)
        assert child.returncode == 0, stderr
        assert "Traceback" not in stderr
        assert stdout == ""
    finally:
        if child.poll() is None:
            child.kill()
        child.wait()


def test_foreground_does_not_wait_for_stalled_executor_on_exit() -> None:
    child = _child(True)
    try:
        assert child.stdout is not None
        assert child.stdout.readline() == "ready\n"
        started = time.monotonic()
        child.send_signal(signal.SIGINT)
        _, stderr = child.communicate(timeout=3)
        assert child.returncode == 1
        assert time.monotonic() - started < 2
        assert "Traceback" not in stderr
        assert "Task was destroyed" not in stderr
    finally:
        if child.poll() is None:
            child.kill()
        child.wait()


def test_foreground_watchdog_exits_with_blocked_event_loop() -> None:
    child = _child(False, block_loop=True)
    try:
        assert child.stdout is not None
        assert child.stdout.readline() == "ready\n"
        started = time.monotonic()
        child.send_signal(signal.SIGTERM)
        _, stderr = child.communicate(timeout=3)
        assert child.returncode == 1
        assert time.monotonic() - started < 2
        assert "Traceback" not in stderr
    finally:
        if child.poll() is None:
            child.kill()
        child.wait()


def test_repeated_process_signals_exit_with_failure() -> None:
    child = _child(True)
    try:
        assert child.stdout is not None
        assert child.stdout.readline() == "ready\n"
        child.send_signal(signal.SIGINT)
        child.send_signal(signal.SIGTERM)
        _, stderr = child.communicate(timeout=3)
        assert child.returncode == 1
        assert "Traceback" not in stderr
        assert "Task was destroyed" not in stderr
    finally:
        if child.poll() is None:
            child.kill()
        child.wait()


def test_untrusted_process_control_exception_exits_without_payload() -> None:
    child = _child(False, exit_control=True)
    try:
        _, stderr = child.communicate(timeout=3)
        assert child.returncode == 1
        assert "SENTINEL" not in stderr
        assert "Traceback" not in stderr
    finally:
        if child.poll() is None:
            child.kill()
        child.wait()
