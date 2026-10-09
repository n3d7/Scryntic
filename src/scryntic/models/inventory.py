"""Reviewed runtime inventory; no package code is imported by the coordinator."""

import json
import os
from dataclasses import dataclass
from hashlib import sha256
from importlib.metadata import distributions
from pathlib import Path
from typing import Any

from scryntic.archive.canonical import canonical_json_bytes
from scryntic.domain.validation import digest
from scryntic.models.artifacts import file_identity, regular_file, snapshot_file
from scryntic.models.manifest import TIMESFM_ASSETS, ModelAssets

MAX_INVENTORY_BYTES = 8 * 1024**2
MAX_RUNTIME_BYTES = 4 * 1024**3
MAX_FILES = 40_000


def packages_path(runtime: Path) -> Path:
    return runtime / "lib/python3.12/site-packages"


def create_inventory(
    runtime: Path, lock: Path, assets: ModelAssets | None = None
) -> bytes:
    assets = TIMESFM_ASSETS if assets is None else assets
    if file_identity(lock)[0] != assets.runtime_lock_sha256:
        raise ValueError("Unreviewed runtime lock")
    packages = packages_path(runtime)
    identities = {
        d.metadata["Name"].lower().replace("_", "-"): d.version
        for d in distributions(path=[str(packages)])
    }
    if identities != dict(assets.runtime_packages):
        raise ValueError("Installed model runtime versions differ from reviewed pins")
    files: list[list[Any]] = []
    total = 0
    for directory, dirs, names in os.walk(packages, followlinks=False):
        dirs[:] = sorted(name for name in dirs if name != "__pycache__")
        if any((Path(directory) / name).is_symlink() for name in dirs):
            raise ValueError("Runtime directory link")
        for name in sorted(names):
            path = Path(directory) / name
            checksum, size = file_identity(path)
            total += size
            if total > MAX_RUNTIME_BYTES or len(files) >= MAX_FILES:
                raise ValueError("Runtime inventory exceeds bounds")
            files.append([path.relative_to(packages).as_posix(), checksum, size])
    document: dict[str, Any] = {
        "version": 1,
        "lock_sha256": assets.runtime_lock_sha256,
        "packages": identities,
        "files": sorted(files),
    }
    value = canonical_json_bytes(document)
    if len(value) > MAX_INVENTORY_BYTES:
        raise ValueError("Runtime inventory exceeds byte bounds")
    return value


def _inventory_files(rows: Any) -> list[tuple[str, str, int]]:
    if type(rows) is not list or not 1 <= len(rows) <= MAX_FILES:
        raise ValueError("Unbounded runtime inventory")
    files: list[tuple[str, str, int]] = []
    names: set[str] = set()
    total = 0
    for item in rows:
        if type(item) is not list or len(item) != 3:
            raise ValueError("Invalid inventory entry")
        name, checksum, size = item
        if (
            type(name) is not str
            or not 1 <= len(name) <= 512
            or Path(name).is_absolute()
            or any(p in (".", "..", "") for p in name.split("/"))
            or name in names
            or type(size) is not int
            or not 0 <= size <= MAX_RUNTIME_BYTES
        ):
            raise ValueError("Unsafe inventory path or size")
        digest(checksum)
        names.add(name)
        total += size
        files.append((name, checksum, size))
    if total > MAX_RUNTIME_BYTES or rows != sorted(rows):
        raise ValueError("Noncanonical or unbounded runtime files")
    return files


@dataclass(frozen=True, slots=True)
class RuntimeBundle:
    artifacts: Path
    runtime: Path
    inventory_sha256: str
    files: tuple[tuple[str, str, int], ...]
    assets: ModelAssets = TIMESFM_ASSETS

    @classmethod
    def read(
        cls,
        artifacts: Path,
        runtime: Path,
        approved_sha256: str,
        assets: ModelAssets | None = None,
    ) -> "RuntimeBundle":
        assets = TIMESFM_ASSETS if assets is None else assets
        digest(approved_sha256)
        with regular_file(runtime / "inventory.json") as descriptor:
            raw = os.read(descriptor, MAX_INVENTORY_BYTES + 1)
        if len(raw) > MAX_INVENTORY_BYTES:
            raise ValueError("Runtime inventory exceeds byte bounds")
        if sha256(raw).hexdigest() != approved_sha256:
            raise ValueError("Runtime inventory is not explicitly approved")
        value: dict[str, Any] = json.loads(raw)
        if (
            type(value) is not dict
            or set(value) != {"version", "lock_sha256", "packages", "files"}
            or canonical_json_bytes(value) != raw
        ):
            raise ValueError("Invalid canonical runtime inventory")
        if (
            type(value["version"]) is not int
            or value["version"] != 1
            or value["lock_sha256"] != assets.runtime_lock_sha256
            or type(value["packages"]) is not dict
            or value["packages"] != dict(assets.runtime_packages)
        ):
            raise ValueError("Unreviewed runtime inventory identity")
        files = _inventory_files(value["files"])
        for name, checksum, size in assets.artifacts:
            if file_identity(artifacts / name, size) != (checksum, size):
                raise ValueError("Model artifacts do not match approved revision")
        return cls(artifacts, runtime, sha256(raw).hexdigest(), tuple(files), assets)

    def snapshot(self, root: Path) -> None:
        if (
            file_identity(self.runtime / "inventory.json", MAX_INVENTORY_BYTES)[0]
            != self.inventory_sha256
        ):
            raise ValueError("Reviewed runtime inventory changed")
        model, packages = root / "model", root / "runtime"
        model.mkdir(mode=0o755)
        packages.mkdir(mode=0o755)
        for name, checksum, size in self.assets.artifacts:
            snapshot_file(self.artifacts / name, model / name, checksum, size)
        for name, checksum, size in self.files:
            target = packages / name
            target.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
            snapshot_file(packages_path(self.runtime) / name, target, checksum, size)
