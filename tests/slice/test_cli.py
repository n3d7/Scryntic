"""The CLI only parses arguments and invokes F09 composition."""

import json
from pathlib import Path

import pytest

from scryntic.application.analysis import ForecastArtifactRef
from scryntic.cli import main
from scryntic.composition import SliceResult
from scryntic.configuration.paths import Installation
from scryntic.dataset.snapshot import DATASET_SCHEMA
from scryntic.domain.dataset import DatasetRef


def test_cli_forwards_configuration_and_prints_refs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    lock = tmp_path / "uv.lock"
    lock.write_bytes(b"fixed-lock")
    home = tmp_path / "home"
    home.mkdir()
    dataset = DatasetRef("a" * 64, DATASET_SCHEMA, 2)
    forecast = ForecastArtifactRef("b" * 64, dataset, "f09-trend", "f09-trend-v1")
    calls: list[tuple[Installation, dict[str, object]]] = []

    def fake_run(installation: Installation, **kwargs: object) -> SliceResult:
        calls.append((installation, kwargs))
        return SliceResult(dataset, forecast)

    monkeypatch.setattr("scryntic.cli.run_fake_slice", fake_run)
    assert (
        main(
            [
                "--home",
                str(home),
                "--epoch",
                "cli-a",
                "--lock-file",
                str(lock),
                "--provider",
                "trend",
                "--horizon",
                "3",
            ]
        )
        == 0
    )
    assert calls[0][1]["epoch"] == "cli-a"
    assert calls[0][1]["provider_name"] == "trend"
    assert calls[0][1]["horizon"] == 3
    assert isinstance(calls[0][1]["dependency_lock_sha256"], str)
    assert len(calls[0][1]["dependency_lock_sha256"]) == 64
    assert json.loads(capsys.readouterr().out) == {
        "dataset_manifest_sha256": "a" * 64,
        "dataset_rows": 2,
        "forecast_artifact_sha256": "b" * 64,
        "provider_id": "f09-trend",
        "model_revision": "f09-trend-v1",
    }
