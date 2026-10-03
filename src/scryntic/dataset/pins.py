"""Durable exact retention roots; retirement never deletes source artifacts."""

import os
import tempfile
from pathlib import Path
from typing import Any

from scryntic.archive.canonical import canonical_json_bytes
from scryntic.configuration.paths import Installation
from scryntic.dataset.storage import DatasetStorage
from scryntic.domain.dataset import DatasetRef
from scryntic.imports.protocol import ImportError


def pin_document(reference: DatasetRef, manifest: dict[str, Any]) -> dict[str, Any]:
    return {
        "protocol": 1,
        "dataset_manifests": [reference.manifest_sha256],
        "publication_manifests": sorted(
            item["manifest_sha256"] for item in manifest["inputs"]
        ),
        "objects": sorted(
            {
                manifest["parquet"]["sha256"],
                *(
                    obj["sha256"]
                    for item in manifest["inputs"]
                    for obj in item["objects"]
                ),
            }
        ),
        "coverage_sha256": manifest.get("coverage_sha256"),
    }


class DatasetPins:
    def __init__(self, installation: Installation) -> None:
        self._installation = installation
        self._storage = DatasetStorage(installation)
        self._root = self._storage.root / "pins"

    def _path(self, reference: DatasetRef) -> Path:
        return self._root / f"{reference.manifest_sha256}.json"

    def create(self, reference: DatasetRef, manifest: dict[str, Any]) -> None:
        data = canonical_json_bytes(pin_document(reference, manifest))
        staging = self._storage._prepare(self._installation)
        self._storage._ensure_directory(self._root)
        fd, name = tempfile.mkstemp(dir=staging, prefix=".pin-")
        path = Path(name)
        try:
            with os.fdopen(fd, "wb") as file:
                file.write(data)
                file.flush()
                os.fsync(file.fileno())
            path.chmod(0o400)
            try:
                os.link(path, self._path(reference), follow_symlinks=False)
            except FileExistsError:
                self.read(reference, manifest)
            self._storage._fsync_directory(self._root)
        finally:
            path.unlink(missing_ok=True)
            self._storage._fsync_directory(staging)

    def read(self, reference: DatasetRef, manifest: dict[str, Any]) -> dict[str, Any]:
        expected = pin_document(reference, manifest)
        try:
            data = self._storage._read(self._path(reference), max_bytes=65536)
            if data != canonical_json_bytes(expected):
                raise ValueError("Pin mismatch")
            return expected
        except Exception:
            raise ImportError("Dataset pin unavailable") from None

    def retire(self, reference: DatasetRef, manifest: dict[str, Any]) -> None:
        self.read(reference, manifest)
        self._path(reference).unlink()
        self._storage._fsync_directory(self._root)
