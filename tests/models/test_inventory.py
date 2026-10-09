"""Runtime enrollment and immutable staging reject drift and path substitution."""

import json
import sys
from dataclasses import replace
from hashlib import sha256
from pathlib import Path

import pytest

from scryntic.archive.canonical import canonical_json_bytes
from scryntic.models import inventory, provision
from scryntic.models.definitions import definition
from scryntic.models.inventory import RuntimeBundle, create_inventory, packages_path
from scryntic.models.manifest import RUNTIME_PACKAGES, TIMESFM_ASSETS


def enrolled(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path, str]:
    runtime, artifacts = tmp_path / "runtime", tmp_path / "artifacts"
    artifacts.mkdir()
    (artifacts / "weights").write_bytes(b"reviewed weights")
    artifact = ("weights", sha256(b"reviewed weights").hexdigest(), 16)

    packages = packages_path(runtime)
    packages.mkdir(parents=True)
    for name, version in RUNTIME_PACKAGES.items():
        info = packages / f"{name.replace('-', '_')}-{version}.dist-info"
        info.mkdir()
        (info / "METADATA").write_text(f"Name: {name}\nVersion: {version}\n")
    (packages / "adapter.py").write_bytes(b"approved adapter")
    lock = tmp_path / "lock"
    lock.write_bytes(b"reviewed lock")
    assets = replace(
        TIMESFM_ASSETS,
        artifacts=(artifact,),
        runtime_lock_sha256=sha256(lock.read_bytes()).hexdigest(),
    )
    monkeypatch.setattr(inventory, "TIMESFM_ASSETS", assets)
    monkeypatch.setattr(provision, "TIMESFM_ASSETS", assets)
    digest = provision.write_inventory(runtime, lock)
    return artifacts, runtime, digest


def test_inventory_enrollment_snapshot_and_repeat_verification(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    artifacts, runtime, approved = enrolled(tmp_path, monkeypatch)
    bundle = RuntimeBundle.read(artifacts, runtime, approved)
    root = tmp_path / "private"
    root.mkdir(mode=0o700)
    bundle.snapshot(root)
    assert (root / "model/weights").read_bytes() == b"reviewed weights"
    assert (root / "runtime/adapter.py").read_bytes() == b"approved adapter"
    assert (root / "runtime/adapter.py").stat().st_mode & 0o777 == 0o444
    assert (
        create_inventory(runtime, tmp_path / "lock")
        == (runtime / "inventory.json").read_bytes()
    )
    with pytest.raises(FileExistsError):
        provision.write_inventory(runtime, tmp_path / "lock")


@pytest.mark.parametrize("defect", ["inventory", "runtime", "artifact", "link"])
def test_post_enrollment_drift_fails_before_launch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, defect: str
) -> None:
    artifacts, runtime, approved = enrolled(tmp_path, monkeypatch)
    bundle = RuntimeBundle.read(artifacts, runtime, approved)
    if defect == "inventory":
        (runtime / "inventory.json").unlink()
        (runtime / "inventory.json").write_bytes(b"{}")
    elif defect == "artifact":
        (artifacts / "weights").write_bytes(b"hostile weights!")
    else:
        path = packages_path(runtime) / "adapter.py"
        path.unlink()
        if defect == "link":
            path.symlink_to("/etc/passwd")
        else:
            path.write_bytes(b"hostile adapter!")
    root = tmp_path / "staging"
    root.mkdir(mode=0o700)
    with pytest.raises((ValueError, OSError)):
        bundle.snapshot(root)


@pytest.mark.parametrize("name", ["../escape", "/etc/passwd", "a//b", "a/./b"])
def test_approved_inventory_still_rejects_unsafe_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    artifacts, runtime, _ = enrolled(tmp_path, monkeypatch)
    document = json.loads((runtime / "inventory.json").read_bytes())
    document["files"] = [[name, "a" * 64, 1]]
    raw = canonical_json_bytes(document)
    (runtime / "inventory.json").unlink()
    (runtime / "inventory.json").write_bytes(raw)
    approved = sha256(raw).hexdigest()
    with pytest.raises(ValueError, match="Unsafe inventory"):
        RuntimeBundle.read(artifacts, runtime, approved)


def test_inventory_approval_is_required_and_package_drift_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    artifacts, runtime, _ = enrolled(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="explicitly approved"):
        RuntimeBundle.read(artifacts, runtime, "a" * 64)
    (packages_path(runtime) / "unknown-1.dist-info").mkdir()
    (packages_path(runtime) / "unknown-1.dist-info/METADATA").write_text(
        "Name: unknown\nVersion: 1\n"
    )
    with pytest.raises(ValueError, match="versions"):
        create_inventory(runtime, tmp_path / "lock")


@pytest.mark.parametrize(
    "defect",
    [
        "version",
        "lock",
        "packages",
        "extra",
        "noncanonical",
        "duplicate",
        "entry",
        "empty",
    ],
)
def test_even_operator_approved_inventory_must_satisfy_closed_contract(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, defect: str
) -> None:
    artifacts, runtime, _ = enrolled(tmp_path, monkeypatch)
    path = runtime / "inventory.json"
    value = json.loads(path.read_bytes())
    if defect == "version":
        value["version"] = True
    elif defect == "lock":
        value["lock_sha256"] = "a" * 64
    elif defect == "packages":
        value["packages"] = {}
    elif defect == "extra":
        value["loader"] = "unreviewed"
    elif defect == "duplicate":
        value["files"].append(value["files"][0])
        value["files"].sort()
    elif defect == "entry":
        value["files"][0] = ["adapter.py", "a" * 64]
    elif defect == "empty":
        value["files"] = []
    raw = (
        json.dumps(value, indent=2).encode()
        if defect == "noncanonical"
        else canonical_json_bytes(value)
    )
    path.unlink()
    path.write_bytes(raw)
    approved = sha256(raw).hexdigest()
    with pytest.raises(ValueError):
        RuntimeBundle.read(artifacts, runtime, approved)


def test_enrollment_rejects_unreviewed_lock_and_directory_links(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, runtime, _ = enrolled(tmp_path, monkeypatch)
    lock = tmp_path / "lock"
    lock.write_bytes(b"different dependency resolution")
    with pytest.raises(ValueError, match="Unreviewed runtime lock"):
        create_inventory(runtime, lock)
    lock.write_bytes(b"reviewed lock")
    (packages_path(runtime) / "external").symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(ValueError, match="Runtime directory link"):
        create_inventory(runtime, lock)


def test_operator_artifact_import_rejects_partial_and_existing_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    artifacts, _, _ = enrolled(tmp_path, monkeypatch)
    target = tmp_path / "approved"
    provision.import_artifacts(artifacts, target)
    assert (target / "weights").read_bytes() == b"reviewed weights"
    with pytest.raises(FileExistsError):
        provision.import_artifacts(artifacts, target)
    (artifacts / "weights").write_bytes(b"unreviewed model")
    failed = tmp_path / "failed"
    with pytest.raises(ValueError):
        provision.import_artifacts(artifacts, failed)
    assert not failed.exists()


def test_operator_cli_lists_enrolls_imports_and_verifies_without_vendor_imports(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    artifacts, runtime, approved = enrolled(tmp_path, monkeypatch)
    selected = replace(
        definition("timesfm-2.5"),
        assets=RuntimeBundle.read(artifacts, runtime, approved).assets,
    )
    monkeypatch.setattr(provision, "definition", lambda name: selected)
    monkeypatch.setattr(sys, "argv", ["provision", "list"])
    provision.main()
    assert "fake-persistence" in capsys.readouterr().out
    destination = tmp_path / "imported"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "provision",
            "import-artifacts",
            "--source",
            str(artifacts),
            "--destination",
            str(destination),
        ],
    )
    provision.main()
    assert "weights" in capsys.readouterr().out
    (runtime / "inventory.json").unlink()
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "provision",
            "inventory",
            "--runtime",
            str(runtime),
            "--lock",
            str(tmp_path / "lock"),
        ],
    )
    provision.main()
    assert json.loads(capsys.readouterr().out)["runtime_sha256"] == approved
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "provision",
            "verify",
            "--artifacts",
            str(destination),
            "--runtime",
            str(runtime),
            "--runtime-sha256",
            approved,
        ],
    )
    provision.main()
    assert json.loads(capsys.readouterr().out)["files"] == 26
    assert "torch" not in sys.modules
