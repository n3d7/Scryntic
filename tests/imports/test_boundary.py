"""Hostile imports must have a separate coordinator/decoder boundary."""

import importlib.util
import subprocess
import sys


def test_restricted_import_boundary_exists() -> None:
    assert importlib.util.find_spec("scryntic.imports") is not None


def test_coordinator_import_never_loads_native_decoders() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import scryntic.imports.protocol; import sys; assert 'pyarrow' not in sys.modules",
        ],
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr.decode()
