"""Supported history, tamper rejection and independent result validation."""

import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from scryntic.application.dto import BuildDatasetRequest
from scryntic.archive.canonical import canonical_json_bytes
from scryntic.dataset.service import F18_RECIPE_SCHEMA, DatasetService
from scryntic.dataset.snapshot import (
    CANDLE_RECIPE_SCHEMA,
    DatasetBuilder,
    DatasetBuildError,
    DatasetReader,
)
from scryntic.dataset.storage import DatasetStorage, DatasetStorageError
from scryntic.domain.identity import SchemaRef, Version
from scryntic.imports.catalog import ImportCatalog
from scryntic.imports.protocol import ImportError
from scryntic.publication.coordinator import Published
from scryntic.publication.reader import PublicationReader
from tests.imports.test_catalog import published_inputs
from tests.normalization.helpers import DEFAULT_RECEIPT, installation
from tests.publication.helpers import configured_coordinator, limits


def test_old_supported_dataset_ref_uses_restricted_inspection(tmp_path: Path) -> None:
    bundle = configured_coordinator(tmp_path / "collector")
    try:
        published = bundle.coordinator.publish_next()
        assert isinstance(published, Published)
        ref = (
            DatasetBuilder(
                PublicationReader(bundle.store, bundle.root, limits()),
                bundle.root,
                code_revision="fixture",
                dependency_lock_sha256="d" * 64,
            )
            .build(
                BuildDatasetRequest(
                    (published.manifest.manifest_hash,), CANDLE_RECIPE_SCHEMA
                )
            )
            .dataset
        )
        original = DatasetReader(bundle.root).read_table(ref).to_pylist()
        with ImportCatalog(bundle.root) as catalog:
            service = DatasetService(
                catalog,
                bundle.root,
                code_revision="fixture",
                dependency_lock_sha256="d" * 64,
            )
            assert service.inspect(ref)[0]["close"] == str(original[0]["close"])
            assert service.read_manifest(ref) == DatasetReader(
                bundle.root
            ).read_manifest(ref)
            for version in (Version(2, 0), Version(1, 2)):
                unknown_ref = replace(ref, schema=SchemaRef(ref.schema.name, version))
                with pytest.raises(ValueError, match="Unsupported schema"):
                    service.inspect(unknown_ref)
    finally:
        bundle.close()


def test_tampering_after_acceptance_and_dataset_write_fails_closed(
    tmp_path: Path,
) -> None:
    data, incoming = published_inputs(tmp_path)
    root = installation(tmp_path / "workstation")
    with ImportCatalog(root) as catalog:
        imported = catalog.accept(data, incoming, DEFAULT_RECEIPT)
        service = DatasetService(
            catalog, root, code_revision="fixture", dependency_lock_sha256="d" * 64
        )
        request = BuildDatasetRequest((imported.manifest_hash,), F18_RECIPE_SCHEMA)
        ref = service.build(request).dataset
        manifest = service.read_manifest(ref)
        descriptor = manifest["inputs"][0]["objects"][0]
        path = root.state_dir / "imports/objects" / descriptor["sha256"]
        path.chmod(0o600)
        path.write_bytes(b"tampered accepted input")
        path.chmod(0o400)
        with pytest.raises(ImportError):
            service.build(request)
        output = (
            root.state_dir
            / "datasets/objects/sha256"
            / manifest["parquet"]["sha256"][:2]
            / (manifest["parquet"]["sha256"] + ".parquet")
        )
        output.chmod(0o600)
        output.write_bytes(b"tampered dataset")
        output.chmod(0o400)
        with pytest.raises(DatasetStorageError, match="hash mismatch"):
            service.export(ref)


def test_worker_result_cannot_change_exact_accepted_provenance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data, incoming = published_inputs(tmp_path)
    root = installation(tmp_path / "workstation")
    with ImportCatalog(root) as catalog:
        imported = catalog.accept(data, incoming, DEFAULT_RECEIPT)
        service = DatasetService(
            catalog, root, code_revision="fixture", dependency_lock_sha256="d" * 64
        )
        launch = service._launch

        def changed(
            request: dict[str, Any], files: tuple[int, ...], names: tuple[str, ...]
        ) -> dict[str, Any]:
            response = launch(request, files, names)
            response["manifest"]["inputs"][0]["objects"][0]["sha256"] = "f" * 64
            return response

        monkeypatch.setattr(service, "_launch", changed)
        request = BuildDatasetRequest((imported.manifest_hash,), F18_RECIPE_SCHEMA)
        with pytest.raises(ImportError, match="result rejected"):
            service.build(request)
        assert not (root.state_dir / "datasets").exists()


def test_legacy_reader_cannot_decode_new_imported_old_schema(tmp_path: Path) -> None:
    data, incoming = published_inputs(tmp_path)
    root = installation(tmp_path / "workstation")
    with ImportCatalog(root) as catalog:
        imported = catalog.accept(data, incoming, DEFAULT_RECEIPT)
        service = DatasetService(
            catalog, root, code_revision="fixture", dependency_lock_sha256="d" * 64
        )
        ref = service.build(
            BuildDatasetRequest((imported.manifest_hash,), CANDLE_RECIPE_SCHEMA)
        ).dataset
        # Even a legacy dataset schema does not grant coordinator parser trust.
        legacy_reader = DatasetReader(root)
        with pytest.raises(
            DatasetBuildError, match="manifest disagree|restricted inspection"
        ):
            legacy_reader.read_table(ref)
        assert service.inspect(ref)[0]["close"] == "100.750000000000000000"


def test_first_pin_directory_entry_is_synced_before_acknowledgment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data, incoming = published_inputs(tmp_path)
    root = installation(tmp_path / "workstation")
    synced: list[tuple[Path, tuple[str, ...]]] = []
    fsync_directory = DatasetStorage._fsync_directory

    def track(path: Path) -> None:
        fsync_directory(path)
        synced.append((path, tuple(item.name for item in path.iterdir())))

    monkeypatch.setattr(DatasetStorage, "_fsync_directory", staticmethod(track))
    with ImportCatalog(root) as catalog:
        imported = catalog.accept(data, incoming, DEFAULT_RECEIPT)
        service = DatasetService(
            catalog, root, code_revision="fixture", dependency_lock_sha256="d" * 64
        )
        ref = service.build(
            BuildDatasetRequest((imported.manifest_hash,), F18_RECIPE_SCHEMA)
        ).dataset
        assert any(
            path == root.state_dir and "datasets" in names for path, names in synced
        )
        parent_sync = next(
            i
            for i, (path, names) in enumerate(synced)
            if path == root.state_dir / "datasets" and "pins" in names
        )
        child_sync = next(
            i
            for i, (path, names) in enumerate(synced)
            if path == root.state_dir / "datasets/pins"
            and ref.manifest_sha256 + ".json" in names
        )
        assert parent_sync < child_sync
        assert service.pins(ref)["dataset_manifests"] == [ref.manifest_sha256]


def test_worker_cannot_return_untyped_inspection_values(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data, incoming = published_inputs(tmp_path)
    root = installation(tmp_path / "workstation")
    with ImportCatalog(root) as catalog:
        imported = catalog.accept(data, incoming, DEFAULT_RECEIPT)
        service = DatasetService(
            catalog, root, code_revision="fixture", dependency_lock_sha256="d" * 64
        )
        ref = service.build(
            BuildDatasetRequest((imported.manifest_hash,), F18_RECIPE_SCHEMA)
        ).dataset
        analytical = service._decoder.analytical

        def changed(
            inputs: tuple[int, ...], names: tuple[str, ...], output_limit: int
        ) -> bytes:
            response = json.loads(analytical(inputs, names, output_limit))
            response["rows"][0]["close"] = True
            return canonical_json_bytes(response)

        monkeypatch.setattr(service._decoder, "analytical", changed)
        with pytest.raises(ImportError, match="decimal"):
            service.inspect(ref)
