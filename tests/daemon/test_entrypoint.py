import os
import subprocess
import sys


def test_invalid_bootstrap_options_do_not_echo_raw_arguments() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "scryntic.daemon", "--SENTINEL-secret-payload"],
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONPATH": "src"},
        timeout=3,
    )
    assert result.returncode == 2
    assert "SENTINEL" not in result.stderr
    assert "Traceback" not in result.stderr
    assert '"state":"failed"' in result.stderr
