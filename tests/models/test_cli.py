"""Operator evaluation refuses excluded real-format data before any model launch."""

import json
import sys
from dataclasses import asdict
from hashlib import sha256
from pathlib import Path

import pytest

from scripts import check_f22_model
from scryntic.application.dto import BuildDatasetRequest
from scryntic.configuration.paths import Installation
from scryntic.dataset.recipes import RecipePolicy
from scryntic.dataset.service import F18_RECIPE_SCHEMA, DatasetService
from scryntic.imports.catalog import ImportCatalog
from scryntic.replay.contracts import ReplayConfig
from tests.dataset.test_imported_recipes import _publish
from tests.normalization.helpers import DEFAULT_RECEIPT, installation
from tests.replay.helpers import START, STEP, sources


def test_evaluation_command_never_launches_for_quality_excluded_input(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data, incoming = _publish(tmp_path, sources())
    root = installation(tmp_path / "workstation")
    lock = sha256(Path("uv.lock").read_bytes()).hexdigest()
    with ImportCatalog(root) as catalog:
        imported = catalog.accept(data, incoming, DEFAULT_RECEIPT)
        reference = (
            DatasetService(
                catalog, root, code_revision="fixture", dependency_lock_sha256=lock
            )
            .build(
                BuildDatasetRequest((imported.manifest_hash,), F18_RECIPE_SCHEMA),
                policy=RecipePolicy(label_horizon_steps=1),
            )
            .dataset
        )
    capture = tmp_path / "capture"
    capture.mkdir()
    (capture / "capture.json").write_text(
        json.dumps(
            {
                "dataset": asdict(reference),
                "replay": ReplayConfig(
                    START, START + 5 * STEP, START + 9 * STEP, START + 13 * STEP
                ).projection(),
            }
        )
    )
    configuration = tmp_path / "model.toml"
    configuration.write_text('[model]\nselected = "fake-persistence"\n')
    output = tmp_path / "comparison.json"
    monkeypatch.setattr(
        Installation, "workstation", classmethod(lambda cls, **kwargs: root)
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "check_f22_model",
            "--configuration",
            str(configuration),
            "--dataset-root",
            str(capture),
            "--output",
            str(output),
            "--code-revision",
            "fixture",
        ],
    )
    with pytest.raises(ValueError, match="No comparable quality-eligible"):
        check_f22_model.main()
    assert not output.exists()
    assert not (root.state_dir / "jobs").exists()
