"""The application path keeps native files restricted and requires active F18 pins."""

import json
import subprocess
import sys
from dataclasses import replace
from hashlib import sha256
from pathlib import Path

import pytest

from scryntic.application.dto import BuildDatasetRequest
from scryntic.dataset.recipes import RecipePolicy
from scryntic.dataset.service import F18_RECIPE_SCHEMA, DatasetService
from scryntic.imports.catalog import ImportCatalog
from scryntic.imports.protocol import ImportError
from scryntic.replay import service as replay_module
from scryntic.replay.service import ReplayService
from tests.dataset.test_imported_recipes import _publish
from tests.normalization.helpers import DEFAULT_RECEIPT, installation
from tests.replay.helpers import sources
from tests.replay.test_reader import config


def test_restricted_snapshot_replay_report_repeatability_and_pin(
    tmp_path: Path,
) -> None:
    data, incoming = _publish(tmp_path, sources())
    root = installation(tmp_path / "workstation")
    with ImportCatalog(root) as catalog:
        imported = catalog.accept(data, incoming, DEFAULT_RECEIPT)
        datasets = DatasetService(
            catalog, root, code_revision="fixture", dependency_lock_sha256="d" * 64
        )
        reference = datasets.build(
            BuildDatasetRequest((imported.manifest_hash,), F18_RECIPE_SCHEMA),
            policy=RecipePolicy(label_horizon_steps=1),
        ).dataset
        pins = datasets.pins(reference)
        replay = ReplayService(
            datasets, code_revision="f19-fixture", dependency_lock_sha256="d" * 64
        )
        first = replay.evaluate(reference, config())
        assert first == replay.evaluate(reference, config())
        assert first.sha256 == sha256(first.canonical_bytes).hexdigest()
        report = json.loads(first.canonical_bytes)
        assert report["dataset"]["manifest_sha256"] == reference.manifest_sha256
        assert (
            report["dataset"]["provenance"]["inputs"][0]["manifest_sha256"]
            == imported.manifest_hash
        )
        assert report["dataset"]["pins"] == pins == datasets.pins(reference)
        assert report["metrics"][0]["mae"] == "0.01"
        program = f"""
import sys
class Deny:
    def find_spec(self, fullname, path=None, target=None):
        if fullname.startswith(('pyarrow', 'scryntic.sources', 'scryntic.providers')):
            raise RuntimeError('unexpected coordinator import: ' + fullname)
sys.meta_path.insert(0, Deny())
from pathlib import Path
from tests.normalization.helpers import installation
from scryntic.imports.catalog import ImportCatalog
from scryntic.dataset.service import DatasetService
from scryntic.dataset.schemas import EVOLVED_DATASET_SCHEMA
from scryntic.domain.dataset import DatasetRef
from scryntic.replay.contracts import ReplayConfig
from scryntic.replay.service import ReplayService
root = installation(Path({str(tmp_path / "workstation")!r}))
with ImportCatalog(root) as catalog:
    datasets = DatasetService(catalog, root, code_revision='fixture', dependency_lock_sha256='d'*64)
    ref = DatasetRef({reference.manifest_sha256!r}, EVOLVED_DATASET_SCHEMA, {reference.row_count})
    replay = ReplayService(datasets, code_revision='f19-fixture', dependency_lock_sha256='d'*64)
    cfg = ReplayConfig(**{config().projection()!r})
    print(replay.evaluate(ref, cfg).sha256)
"""
        # The catalog holds an exclusive process lock; release it before reopening.
        catalog.close()
        child = subprocess.run([sys.executable, "-c", program], capture_output=True)
        assert child.returncode == 0, child.stderr.decode()
        assert child.stdout.decode().strip() == first.sha256
        datasets.retire(reference)
        configuration = config()
        with pytest.raises(ImportError):
            replay.evaluate(reference, configuration)


def test_empty_snapshot_still_uses_restricted_inspection(tmp_path: Path) -> None:
    data, incoming = _publish(tmp_path, sources())
    root = installation(tmp_path / "workstation")
    with ImportCatalog(root) as catalog:
        imported = catalog.accept(data, incoming, DEFAULT_RECEIPT)
        datasets = DatasetService(
            catalog, root, code_revision="fixture", dependency_lock_sha256="d" * 64
        )
        request = BuildDatasetRequest((imported.manifest_hash,), F18_RECIPE_SCHEMA)
        reference = datasets.build(
            request, policy=RecipePolicy(coverage="exclude", label_horizon_steps=1)
        ).dataset
        replay = ReplayService(
            datasets, code_revision="f19-fixture", dependency_lock_sha256="d" * 64
        )
        result = json.loads(replay.evaluate(reference, config()).canonical_bytes)
        assert result["counts"]["candidates"] == 0
        assert result["status"] == "no-training-data"
        assert result["dataset_exclusions"]
        oversized = replace(reference, row_count=10_001)
        configuration = config()
        with pytest.raises(ValueError, match="bounded"):
            replay.evaluate(oversized, configuration)


def test_report_size_limit_is_enforced(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data, incoming = _publish(tmp_path, sources())
    root = installation(tmp_path / "workstation")
    with ImportCatalog(root) as catalog:
        imported = catalog.accept(data, incoming, DEFAULT_RECEIPT)
        datasets = DatasetService(
            catalog, root, code_revision="fixture", dependency_lock_sha256="d" * 64
        )
        reference = datasets.build(
            BuildDatasetRequest((imported.manifest_hash,), F18_RECIPE_SCHEMA),
            policy=RecipePolicy(label_horizon_steps=1),
        ).dataset
        replay = ReplayService(
            datasets, code_revision="f19-fixture", dependency_lock_sha256="d" * 64
        )
        monkeypatch.setattr(replay_module, "MAX_RESULT_BYTES", 1)
        configuration = config()
        with pytest.raises(ValueError, match="byte limit"):
            replay.evaluate(reference, configuration)
