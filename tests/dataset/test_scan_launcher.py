"""Qualification never exposes SQL/paths, and reuses effective Linux controls."""

import os

import pytest

from scryntic.analytics.launcher import ScanDecoder
from scryntic.archive.canonical import canonical_json_bytes
from scryntic.imports.filesystem import sealed_bytes
from scryntic.imports.protocol import ImportError, ImportLimits


def test_candidate_selector_is_closed_before_native_launch() -> None:
    decoder = ScanDecoder(ImportLimits())
    with pytest.raises(ValueError, match="candidate"):
        decoder.qualify({}, b"", b"", "SELECT secret")


def test_deadline_kills_qualification_worker_without_partial_results() -> None:
    decoder = ScanDecoder(ImportLimits(wall_seconds=1))
    with pytest.raises(ImportError, match="Restricted"):
        decoder.qualify(
            {"protocol": 1, "operation": "cancel-probe"}, b"", b"", "pyarrow-scan"
        )


def test_qualification_uses_fixed_bootstrap_and_sealed_descriptors() -> None:
    decoder = ScanDecoder(ImportLimits())
    fd = sealed_bytes(canonical_json_bytes({"protocol": 1}))
    try:
        command = decoder.command((fd,), ("request",), "pyarrow-scan")
        assert "--unshare-net" in command
        assert "--disable-userns" in command
        assert "pi05" in command
        assert "--clearenv" in command
        assert all("duckdb" not in p for p in command)
        assert "--bind" not in command
    finally:
        os.close(fd)
