"""Accepted bytes remain restricted through reproducible build and inspection."""

import json
from dataclasses import replace
from pathlib import Path

import pytest

from scryntic.application.dto import BuildDatasetRequest
from scryntic.dataset.service import F18_RECIPE_SCHEMA, DatasetService
from scryntic.domain.identity import Version
from scryntic.imports.catalog import ImportCatalog
from scryntic.imports.protocol import ImportError
from tests.imports.test_catalog import published_inputs
from tests.normalization.helpers import DEFAULT_RECEIPT, installation


def test_imported_build_inspect_export_reopen_and_pin(tmp_path: Path) -> None:
    data, incoming = published_inputs(tmp_path)
    root = installation(tmp_path / "workstation")
    with ImportCatalog(root) as catalog:
        imported = catalog.accept(data, incoming, DEFAULT_RECEIPT)
        service = DatasetService(
            catalog, root, code_revision="fixture", dependency_lock_sha256="d" * 64
        )
        request = BuildDatasetRequest((imported.manifest_hash,), F18_RECIPE_SCHEMA)
        first = service.build(request).dataset
        second = service.build(request).dataset
        assert first == second
        rows = service.inspect(first)
        assert len(rows) == first.row_count == 1
        assert rows[0]["close"] == "100.750000000000000000"
        manifest, parquet = service.export(first)
        provenance = json.loads(manifest)
        assert provenance["imports"][0]["receipt"]["wall_time_ns"] == (
            DEFAULT_RECEIPT.wall_time_ns
        )
        assert provenance["inputs"][0]["manifest_sha256"] == imported.manifest_hash
        assert parquet[:4] == b"PAR1"
        pins = service.pins(first)
        assert imported.manifest_hash in pins["publication_manifests"]
        assert first.manifest_sha256 in pins["dataset_manifests"]
        assert {item["sha256"] for item in provenance["inputs"][0]["objects"]} <= set(
            pins["objects"]
        )
    with ImportCatalog(root) as catalog:
        service = DatasetService(
            catalog, root, code_revision="fixture", dependency_lock_sha256="d" * 64
        )
        assert service.inspect(first) == rows
        assert service.pins(first) == pins
        service.retire(first)
        with pytest.raises(ImportError):
            service.pins(first)
        assert service.inspect(first) == rows


def test_coordinator_never_imports_native_decoder(tmp_path: Path) -> None:
    # A fresh coordinator can build/inspect without importing a native codec.
    import subprocess
    import sys

    data, incoming = published_inputs(tmp_path)
    root = installation(tmp_path / "workstation")
    with ImportCatalog(root) as catalog:
        imported = catalog.accept(data, incoming, DEFAULT_RECEIPT)
    program = f"""
import sys
class Deny:
    def find_spec(self, fullname, path=None, target=None):
        if fullname.startswith("pyarrow"):
            raise RuntimeError("coordinator native decoding")
sys.meta_path.insert(0, Deny())
from pathlib import Path
from tests.normalization.helpers import installation
from scryntic.imports.catalog import ImportCatalog
from scryntic.dataset.service import DatasetService, F18_RECIPE_SCHEMA
from scryntic.application.dto import BuildDatasetRequest
root = installation(Path({str(tmp_path / "workstation")!r}))
with ImportCatalog(root) as catalog:
    service = DatasetService(catalog, root, code_revision="fixture", dependency_lock_sha256="d"*64)
    ref = service.build(BuildDatasetRequest(({imported.manifest_hash!r},), F18_RECIPE_SCHEMA)).dataset
    assert service.inspect(ref)[0]["close"] == "100.750000000000000000"
    assert service.export(ref)[1][:4] == b"PAR1"
"""
    result = subprocess.run([sys.executable, "-c", program], capture_output=True)
    assert result.returncode == 0, result.stderr.decode()


def test_unknown_major_and_unknown_input_fail_before_launch(tmp_path: Path) -> None:
    root = installation(tmp_path)
    with ImportCatalog(root) as catalog:
        service = DatasetService(
            catalog, root, code_revision="fixture", dependency_lock_sha256="d" * 64
        )
        unknown_input = BuildDatasetRequest(("a" * 64,), F18_RECIPE_SCHEMA)
        with pytest.raises(ImportError):
            service.build(unknown_input)
        unknown_major = BuildDatasetRequest(
            ("a" * 64,), replace(F18_RECIPE_SCHEMA, version=Version(2, 0))
        )
        with pytest.raises(ValueError, match="Unsupported schema"):
            service.build(unknown_major)
        assert not (root.state_dir / "datasets").exists()
