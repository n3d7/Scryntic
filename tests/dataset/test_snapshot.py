"""Exact F07 inputs become immutable, reproducible logical snapshots."""

from dataclasses import replace
from pathlib import Path

import pytest

from scryntic.application.dto import BuildDatasetRequest
from scryntic.archive.canonical import PublicationInput
from scryntic.archive.normalized_parquet import NORMALIZED_PARQUET_SCHEMA
from scryntic.dataset.snapshot import (
    CANDLE_RECIPE_SCHEMA,
    DatasetBuilder,
    DatasetBuildError,
    DatasetReader,
)
from scryntic.domain.identity import SchemaRef, Version
from scryntic.publication.coordinator import Published
from scryntic.publication.reader import PublicationReader
from tests.publication.helpers import CoordinatorBundle, configured_coordinator, limits


def _fixture(tmp_path: Path) -> tuple[CoordinatorBundle, Published, DatasetBuilder]:
    bundle = configured_coordinator(
        tmp_path, offsets=(2, 5), epochs=("epoch-a", "epoch-a")
    )
    published = bundle.coordinator.publish_next()
    assert isinstance(published, Published)
    reader = PublicationReader(bundle.store, bundle.root, limits())
    builder = DatasetBuilder(
        reader,
        bundle.root,
        code_revision="test-revision",
        dependency_lock_sha256="d" * 64,
    )
    return bundle, published, builder


def test_rebuild_keeps_logical_rows_order_and_full_input_provenance(
    tmp_path: Path,
) -> None:
    bundle, published, builder = _fixture(tmp_path)
    try:
        request = BuildDatasetRequest(
            (published.manifest.manifest_hash,), CANDLE_RECIPE_SCHEMA
        )
        first = builder.build(request).dataset
        second = builder.build(request).dataset
        dataset_reader = DatasetReader(bundle.root)
        first_rows = dataset_reader.read_table(first).to_pylist()
        second_rows = dataset_reader.read_table(second).to_pylist()
        first_manifest = dataset_reader.read_manifest(first)
        second_manifest = dataset_reader.read_manifest(second)

        assert first.row_count == second.row_count == 1
        assert first_rows == second_rows
        assert first_rows[0]["close"] == 100.75
        assert first_manifest["rows"] == second_manifest["rows"]
        assert first_manifest["inputs"] == second_manifest["inputs"]
        assert [
            item["identity"]["offset"] for item in first_manifest["rows"][0]["evidence"]
        ] == [2, 5]
        assert first_manifest["inputs"][0]["manifest_sha256"] == (
            published.manifest.manifest_hash
        )
        assert {item["role"] for item in first_manifest["inputs"][0]["objects"]} == {
            "raw",
            "normalized",
        }
    finally:
        bundle.close()


def test_reversed_exact_manifest_selection_is_stable(tmp_path: Path) -> None:
    bundle = configured_coordinator(
        tmp_path, offsets=(2, 5), epochs=("epoch-a", "epoch-b")
    )
    try:
        first = bundle.coordinator.publish_next()
        second = bundle.coordinator.publish_next()
        assert isinstance(first, Published)
        assert isinstance(second, Published)
        builder = DatasetBuilder(
            PublicationReader(bundle.store, bundle.root, limits()),
            bundle.root,
            code_revision="test-revision",
            dependency_lock_sha256="d" * 64,
        )
        forward = builder.build(
            BuildDatasetRequest(
                (first.manifest.manifest_hash, second.manifest.manifest_hash),
                CANDLE_RECIPE_SCHEMA,
            )
        ).dataset
        reversed_order = builder.build(
            BuildDatasetRequest(
                (second.manifest.manifest_hash, first.manifest.manifest_hash),
                CANDLE_RECIPE_SCHEMA,
            )
        ).dataset
        reader = DatasetReader(bundle.root)
        assert forward.row_count == reversed_order.row_count == 1
        assert (
            reader.read_table(forward).to_pylist()
            == reader.read_table(reversed_order).to_pylist()
        )
        assert (
            reader.read_manifest(forward)["rows"]
            == reader.read_manifest(reversed_order)["rows"]
        )
        assert (
            reader.read_manifest(forward)["inputs"]
            == reader.read_manifest(reversed_order)["inputs"]
        )
        assert [
            evidence["identity"]["offset"]
            for evidence in reader.read_manifest(forward)["rows"][0]["evidence"]
        ] == [2, 5]
    finally:
        bundle.close()


def test_unsupported_publication_schema_fails_before_snapshot_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle, published, builder = _fixture(tmp_path)
    try:
        validated = builder._publications.resolve_exact(
            published.manifest.manifest_hash
        )
        body = validated.document.body
        bad_normalized = replace(
            body.objects[1],
            format=SchemaRef(
                "unsupported.normalized", NORMALIZED_PARQUET_SCHEMA.version
            ),
        )
        changed = replace(
            validated,
            document=replace(
                validated.document,
                body=replace(body, objects=(body.objects[0], bad_normalized)),
            ),
        )
        monkeypatch.setattr(builder._publications, "resolve_exact", lambda _: changed)
        with pytest.raises(
            DatasetBuildError, match="Publication object roles disagree"
        ):
            builder.build(
                BuildDatasetRequest(
                    (published.manifest.manifest_hash,), CANDLE_RECIPE_SCHEMA
                )
            )
        assert not (bundle.root.state_dir / "datasets" / "manifests").exists()
    finally:
        bundle.close()


def test_unsupported_normalized_semantic_schema_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle, published, builder = _fixture(tmp_path)
    try:
        validated = builder._publications.resolve_exact(
            published.manifest.manifest_hash
        )
        first = validated.inputs[0]
        changed_value = PublicationInput(
            first.raw,
            replace(
                first.outcome, output_schema=SchemaRef("unknown.candle", Version(1, 0))
            ),
            first.semantics,
        )
        changed = replace(validated, inputs=(changed_value, *validated.inputs[1:]))
        monkeypatch.setattr(builder._publications, "resolve_exact", lambda _: changed)
        with pytest.raises(
            DatasetBuildError, match="Unsupported normalized candle schema"
        ):
            builder.build(
                BuildDatasetRequest(
                    (published.manifest.manifest_hash,), CANDLE_RECIPE_SCHEMA
                )
            )
        assert not (bundle.root.state_dir / "datasets" / "manifests").exists()
    finally:
        bundle.close()


def test_missing_normalized_object_fails_without_dataset_manifest(
    tmp_path: Path,
) -> None:
    bundle, published, builder = _fixture(tmp_path)
    try:
        descriptor = published.document.body.objects[1]
        object_path = (
            bundle.root.state_dir
            / "archive"
            / "objects"
            / "sha256"
            / descriptor.sha256[:2]
            / f"{descriptor.sha256}.parquet"
        )
        object_path.unlink()
        with pytest.raises(DatasetBuildError):
            builder.build(
                BuildDatasetRequest(
                    (published.manifest.manifest_hash,), CANDLE_RECIPE_SCHEMA
                )
            )
        assert not (bundle.root.state_dir / "datasets" / "manifests").exists()
    finally:
        bundle.close()


def test_unsupported_recipe_schema_fails_before_writing(tmp_path: Path) -> None:
    bundle, published, builder = _fixture(tmp_path)
    try:
        with pytest.raises(DatasetBuildError):
            builder.build(
                BuildDatasetRequest(
                    (published.manifest.manifest_hash,),
                    SchemaRef("unsupported.recipe", Version(1, 0)),
                )
            )
        assert not (bundle.root.state_dir / "datasets" / "manifests").exists()
    finally:
        bundle.close()
