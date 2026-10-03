"""Coordinator guards reject unsafe requests and independently verify results."""

import base64
from collections.abc import Iterator
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest

from scryntic.application.dto import BuildDatasetRequest
from scryntic.archive.canonical import canonical_json_bytes
from scryntic.dataset.recipes import RecipePolicy
from scryntic.dataset.schemas import CANDLE_RECIPE_SCHEMA
from scryntic.dataset.service import F18_RECIPE_SCHEMA, DatasetService
from scryntic.domain.dataset import DatasetRef
from scryntic.imports.catalog import ImportCatalog
from scryntic.imports.protocol import ImportError
from tests.dataset.test_recipe_guards import claim
from tests.dataset.test_recipes import _clock
from tests.imports.test_catalog import published_inputs
from tests.normalization.helpers import DEFAULT_RECEIPT, installation


@pytest.fixture
def service(tmp_path: Path) -> Iterator[DatasetService]:
    root = installation(tmp_path / "workstation")
    with ImportCatalog(root) as catalog:
        yield DatasetService(
            catalog, root, code_revision="fixture", dependency_lock_sha256="d" * 64
        )


@pytest.mark.parametrize(
    ("recipe", "policy", "cutoff", "coverage", "message"),
    [
        (CANDLE_RECIPE_SCHEMA, RecipePolicy(lag_steps=1), None, (), "recipe 1.2"),
        (CANDLE_RECIPE_SCHEMA, None, None, (claim(),), "recipe 1.2"),
        (F18_RECIPE_SCHEMA, RecipePolicy(mode="as-observed"), None, (), "cutoff"),
        (
            F18_RECIPE_SCHEMA,
            RecipePolicy(mode="as-observed"),
            DEFAULT_RECEIPT,
            (),
            "cutoff",
        ),
        (F18_RECIPE_SCHEMA, None, _clock(1), (), "Historical"),
        (F18_RECIPE_SCHEMA, None, None, [], "coverage snapshot"),
        (F18_RECIPE_SCHEMA, None, None, (None,), "coverage snapshot"),
        (F18_RECIPE_SCHEMA, None, None, (claim(),) * 4097, "coverage snapshot"),
    ],
)
def test_invalid_request_fails_before_input_access_or_launch(
    service: DatasetService,
    monkeypatch: pytest.MonkeyPatch,
    recipe: Any,
    policy: RecipePolicy | None,
    cutoff: Any,
    coverage: Any,
    message: str,
) -> None:
    def forbidden(*args: Any, **kwargs: Any) -> None:
        pytest.fail("Invalid request reached input access or native launch")

    monkeypatch.setattr(service._catalog, "analytical_inputs", forbidden)
    monkeypatch.setattr(service, "_launch", forbidden)
    request = BuildDatasetRequest(("a" * 64,), recipe, as_observed_cutoff=cutoff)
    with pytest.raises(ValueError, match=message):
        service.build(request, policy=policy, coverage=coverage)


def test_request_limit_prevents_worker_launch(
    service: DatasetService, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("scryntic.dataset.service.MAX_REQUEST_BYTES", 1)
    with pytest.raises(ImportError, match="request limit"):
        service._launch({"protocol": 1}, (), ())


def test_worker_response_must_bind_to_exact_request(
    service: DatasetService, monkeypatch: pytest.MonkeyPatch
) -> None:
    def forged(*args: Any) -> bytes:
        return canonical_json_bytes({"request_sha256": "a" * 64})

    monkeypatch.setattr(service._decoder, "analytical", forged)
    with pytest.raises(ImportError, match="identity mismatch"):
        service._launch({"protocol": 1}, (), ())


@pytest.fixture
def built(
    service: DatasetService, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[DatasetService, DatasetRef, dict[str, Any], dict[str, Any]]:
    data, incoming = published_inputs(tmp_path)
    imported = service._catalog.accept(data, incoming, DEFAULT_RECEIPT)
    captured: dict[str, Any] = {}
    launch = service._launch

    def capture(
        request: dict[str, Any], files: tuple[int, ...], names: tuple[str, ...]
    ) -> dict[str, Any]:
        response = launch(request, files, names)
        captured["request"] = deepcopy(request)
        captured["response"] = deepcopy(response)
        return response

    monkeypatch.setattr(service, "_launch", capture)
    request = BuildDatasetRequest((imported.manifest_hash,), F18_RECIPE_SCHEMA)
    ref = service.build(request).dataset
    return service, ref, captured["request"], captured["response"]


@pytest.mark.parametrize("mutation", ["unknown-field", "invalid-base64", "empty"])
def test_result_artifact_shape_is_closed(
    built: tuple[DatasetService, DatasetRef, dict[str, Any], dict[str, Any]],
    mutation: str,
) -> None:
    service, reference, request, response = built
    if mutation == "unknown-field":
        response["native_object"] = "not admitted"
    elif mutation == "invalid-base64":
        response["parquet_base64"] = "!"
    else:
        response["parquet_base64"] = base64.b64encode(b"").decode()
    with pytest.raises(ImportError, match="result rejected"):
        service._install(response, request)
    assert service.pins(reference)["dataset_manifests"] == [reference.manifest_sha256]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("row_count", True),
        ("row_count", 0),
        ("rows", []),
        ("dataset_schema", {"name": "dataset", "version": {"major": 99}}),
    ],
)
def test_manifest_identity_is_independent_of_worker_claims(
    built: tuple[DatasetService, DatasetRef, dict[str, Any], dict[str, Any]],
    field: str,
    value: object,
) -> None:
    service, reference, _, response = built
    manifest = response["manifest"]
    manifest[field] = value
    with pytest.raises(ValueError, match="identity mismatch"):
        service._validate_manifest(reference, manifest)


@pytest.mark.parametrize("rows", [None, {}, [], [{}, {}]])
def test_inspection_cardinality_is_checked_before_values(
    built: tuple[DatasetService, DatasetRef, dict[str, Any], dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
    rows: object,
) -> None:
    service, reference, _, _ = built

    def forged(*args: Any) -> dict[str, Any]:
        return {"request_sha256": "a" * 64, "rows": rows}

    monkeypatch.setattr(service, "_launch", forged)
    with pytest.raises(ImportError, match="inspection response"):
        service.inspect(reference)


def test_inspection_limit_and_artifact_length_fail_before_launch(
    built: tuple[DatasetService, DatasetRef, dict[str, Any], dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, reference, _, response = built
    with pytest.raises(ValueError, match="row limit"):
        service.inspect(reference, limit=129)
    manifest = response["manifest"]
    manifest["parquet"]["encoded_bytes"] += 1
    monkeypatch.setattr(service, "read_manifest", lambda _: manifest)
    with pytest.raises(ImportError, match="length mismatch"):
        service.inspect(reference)


def test_corrupted_pin_cannot_be_read_recreated_or_retired(
    built: tuple[DatasetService, DatasetRef, dict[str, Any], dict[str, Any]],
) -> None:
    service, reference, _, response = built
    pin_path = service._pins._path(reference)
    pin_path.chmod(0o600)
    pin_path.write_bytes(b"{}")
    pin_path.chmod(0o400)
    with pytest.raises(ImportError, match="pin unavailable"):
        service.pins(reference)
    with pytest.raises(ImportError, match="pin unavailable"):
        service._pins.create(reference, response["manifest"])
    with pytest.raises(ImportError, match="pin unavailable"):
        service.retire(reference)
    assert pin_path.exists()
    assert service.inspect(reference)[0]["close"] == "100.750000000000000000"


def test_missing_manifest_remains_explicit(service: DatasetService) -> None:
    from scryntic.dataset.schemas import EVOLVED_DATASET_SCHEMA

    reference = DatasetRef("a" * 64, EVOLVED_DATASET_SCHEMA, 0)
    with pytest.raises(ImportError, match="manifest rejected"):
        service.read_manifest(reference)
