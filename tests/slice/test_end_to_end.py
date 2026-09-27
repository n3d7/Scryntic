"""The F09 slice crosses all disk-backed production boundaries."""

from pathlib import Path
from typing import TypedDict

from scryntic.application.analysis import ForecastArtifactRef
from scryntic.composition import run_fake_slice
from scryntic.configuration.paths import Installation
from scryntic.dataset.snapshot import DatasetReader
from scryntic.forecast.artifact import ImmutableForecastStore


class _RunArguments(TypedDict):
    code_revision: str
    dependency_lock_sha256: str
    horizon: int


def _installation(tmp_path: Path) -> Installation:
    home = tmp_path / "home"
    home.mkdir(mode=0o700)
    installation = Installation.workstation(home=home, environment={})
    for path in (
        installation.config_dir,
        installation.state_dir,
        installation.runtime_dir,
        installation.credential_dir,
    ):
        path.mkdir(parents=True, mode=0o700, exist_ok=True)
        path.chmod(0o700)
    return installation


def _values(installation: Installation, reference: ForecastArtifactRef) -> list[str]:
    artifact = ImmutableForecastStore(installation).read(reference)
    return [point["value"] for point in artifact["result"]["points"]]


def test_complete_slice_restarts_and_swaps_provider(tmp_path: Path) -> None:
    installation = _installation(tmp_path)
    arguments: _RunArguments = {
        "code_revision": "f09-test",
        "dependency_lock_sha256": "d" * 64,
        "horizon": 2,
    }
    first = run_fake_slice(
        installation, epoch="restart-a", provider_name="persistence", **arguments
    )
    first_rows = DatasetReader(installation).read_table(first.dataset).to_pylist()
    first_values = _values(installation, first.forecast)
    first_manifest = DatasetReader(installation).read_manifest(first.dataset)

    # The first run has closed the ingestion, normalization and publication stores.
    second = run_fake_slice(
        installation, epoch="restart-b", provider_name="persistence", **arguments
    )
    second_rows = DatasetReader(installation).read_table(second.dataset).to_pylist()
    second_manifest = DatasetReader(installation).read_manifest(second.dataset)
    assert first_rows == second_rows
    assert (
        first_values == _values(installation, second.forecast) == ["101.25", "101.25"]
    )
    assert first.dataset.manifest_sha256 != second.dataset.manifest_sha256
    assert len(first_manifest["inputs"]) == 1
    assert len(second_manifest["inputs"]) == 2
    assert first.forecast.sha256 != second.forecast.sha256

    swapped = run_fake_slice(
        installation, epoch="restart-c", provider_name="trend", **arguments
    )
    assert (
        DatasetReader(installation).read_table(swapped.dataset).to_pylist()
        == first_rows
    )
    assert _values(installation, swapped.forecast) == ["101.75", "102.25"]
    assert swapped.forecast.provider_id == "f09-trend"
    assert len(list(installation.state_dir.rglob("*.sqlite3"))) >= 3
