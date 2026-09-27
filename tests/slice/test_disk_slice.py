"""F09 must use the durable F05-F08 path, including after reopening state."""

from pathlib import Path

from scryntic.composition import build_fake_dataset
from scryntic.configuration.paths import Installation
from scryntic.dataset.snapshot import DatasetReader


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


def test_real_disk_slice_reopens_and_retains_logical_rows(tmp_path: Path) -> None:
    installation = _installation(tmp_path)
    kwargs = {
        "code_revision": "f09-test",
        "dependency_lock_sha256": "d" * 64,
    }

    first = build_fake_dataset(installation, epoch="epoch-a", **kwargs)
    reader = DatasetReader(installation)
    first_rows = reader.read_table(first).to_pylist()
    first_manifest = reader.read_manifest(first)

    second = build_fake_dataset(installation, epoch="epoch-b", **kwargs)
    second_rows = reader.read_table(second).to_pylist()
    second_manifest = reader.read_manifest(second)

    assert len(list(installation.state_dir.rglob("*.sqlite3"))) >= 3
    assert first.row_count == second.row_count == 2
    assert first_rows == second_rows
    assert first.manifest_sha256 != second.manifest_sha256
    assert len(first_manifest["inputs"]) == 1
    assert len(second_manifest["inputs"]) == 2
    assert all(len(row["evidence"]) == 2 for row in second_manifest["rows"])
