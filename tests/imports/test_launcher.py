"""Fixed launcher and bounded pipe supervision failures."""

import sys
import time

import pytest

from scryntic.imports.launcher import LinuxDecoder, bounded_process
from scryntic.imports.protocol import ImportError, ImportLimits


@pytest.mark.parametrize(
    "program",
    [
        "print('x' * 10000)",
        "import time; time.sleep(10)",
        "raise RuntimeError('secret-sentinel')",
    ],
)
def test_timeout_flood_and_error_fail_closed(program: str) -> None:
    prepared_import_limits = ImportLimits(max_message_bytes=64, wall_seconds=1)
    with pytest.raises(ImportError) as caught:
        bounded_process(
            [sys.executable, "-I", "-c", program], (), prepared_import_limits
        )
    assert "sentinel" not in str(caught.value)


def test_current_host_controls_are_enforced(monkeypatch: pytest.MonkeyPatch) -> None:
    # Qualification is explicit: unavailable controls fail this test, never skip.
    monkeypatch.setenv("F16_SECRET_SENTINEL", "must-not-reach-worker")
    LinuxDecoder(ImportLimits()).probe()


def test_diagnostic_flood_is_bounded() -> None:
    prepared_import_limits = ImportLimits(max_message_bytes=64)
    with pytest.raises(ImportError):
        bounded_process(
            [sys.executable, "-I", "-c", "import sys; sys.stderr.write('x'*10000)"],
            (),
            prepared_import_limits,
        )


def test_descendant_holding_pipe_cannot_extend_deadline() -> None:
    start = time.monotonic()
    prepared_import_limits = ImportLimits(wall_seconds=1)
    with pytest.raises(ImportError):
        bounded_process(
            [
                sys.executable,
                "-I",
                "-c",
                "import os,time; p=os.fork(); time.sleep(10) if p==0 else os._exit(0)",
            ],
            (),
            prepared_import_limits,
        )
    assert time.monotonic() - start < 5
