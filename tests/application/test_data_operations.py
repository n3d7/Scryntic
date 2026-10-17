"""Reusable imported data workflows keep F18/F19 authority and exact bytes."""

import asyncio
import importlib.util
import json
from collections.abc import Callable
from dataclasses import asdict
from hashlib import sha256
from pathlib import Path
from typing import TypeVar

import pytest

from scryntic.application.operations import ApplicationError, ErrorCode, Operation
from scryntic.configuration.paths import Installation
from scryntic.dataset.service import DatasetService
from scryntic.imports.catalog import ImportCatalog
from scryntic.imports.protocol import ImportError
from tests.dataset.test_imported_recipes import _clock, _publish
from tests.imports.test_catalog import published_inputs
from tests.normalization.helpers import DEFAULT_RECEIPT, installation
from tests.replay.helpers import START, STEP, sources

T = TypeVar("T")


class Context:
    def __init__(self, root: Installation) -> None:
        self.installation = root
        self.code_revision = "f24-fixture"
        self.dependency_lock_sha256 = "d" * 64
        self.exports: dict[str, dict[str, bytes]] = {}

    async def run_io(self, function: Callable[[], T]) -> T:
        return await asyncio.to_thread(function)

    def read_config_json(self, leaf: str, maxbytes: int) -> object:
        data = (self.installation.config_dir / leaf).read_bytes()
        assert len(data) <= maxbytes
        return json.loads(data)

    def export_bundle(self, leaf: str, files: dict[str, bytes]) -> dict[str, object]:
        self.exports[leaf] = files
        return {
            "destination": leaf,
            "sha256": {k: sha256(v).hexdigest() for k, v in files.items()},
        }


def accept(root: Installation, data: bytes, incoming: Path) -> str:
    with ImportCatalog(root) as catalog:
        return catalog.accept(data, incoming, DEFAULT_RECEIPT).manifest_hash


def test_data_workflows_are_available_without_an_interface() -> None:
    assert importlib.util.find_spec("scryntic.application.data_operations") is not None


def test_registry_rejects_coercion_traversal_and_excess_inputs() -> None:
    from scryntic.application.data_operations import WORKFLOWS, DatasetBuildRequest

    workflows = {workflow.operation: workflow for workflow in WORKFLOWS}
    for operation, values in (
        (Operation.DATA_INSPECT, {"manifest_sha256": "../secret"}),
        (Operation.DATASET_INSPECT, {"manifest_sha256": "a" * 64, "limit": "129"}),
        (
            Operation.DATASET_EXPORT,
            {"manifest_sha256": "a" * 64, "destination": "../out"},
        ),
        (Operation.DATASET_BUILD, {"manifests": "a" * 64, "finalized_only": "yes"}),
    ):
        with pytest.raises(ApplicationError) as rejected:
            workflows[operation].request(values)
        assert rejected.value.code is ErrorCode.INVALID
    with pytest.raises((ValueError, TypeError)):
        DatasetBuildRequest(tuple(f"{i:064x}" for i in range(9)))
    with pytest.raises((ValueError, TypeError)):
        DatasetBuildRequest(("a" * 64,), mode="as-observed")


def test_public_hash_resolution_checks_metadata_and_artifacts(tmp_path: Path) -> None:
    from scryntic.application.dto import BuildDatasetRequest
    from scryntic.dataset.schemas import F18_RECIPE_SCHEMA

    data, incoming = published_inputs(tmp_path)
    root = installation(tmp_path / "workstation")
    with ImportCatalog(root) as catalog:
        accepted = catalog.accept(data, incoming, DEFAULT_RECEIPT)
        assert hasattr(catalog, "resolve")
        assert catalog.resolve(accepted.manifest_hash).ref == accepted
        datasets = DatasetService(
            catalog, root, code_revision="fixture", dependency_lock_sha256="d" * 64
        )
        reference = datasets.build(
            BuildDatasetRequest((accepted.manifest_hash,), F18_RECIPE_SCHEMA)
        ).dataset
        assert hasattr(datasets, "resolve")
        assert datasets.resolve(reference.manifest_sha256) == reference
        with pytest.raises(ImportError):
            datasets.resolve("a" * 64)
        path = (
            root.state_dir
            / "datasets/manifests/sha256"
            / reference.manifest_sha256[:2]
            / f"{reference.manifest_sha256}.json"
        )
        path.chmod(0o600)
        path.write_bytes(b"{}")
        with pytest.raises(ImportError):
            datasets.resolve(reference.manifest_sha256)


def test_build_inspect_export_preserve_exact_provenance(tmp_path: Path) -> None:
    from scryntic.application.data_operations import (
        DataInspectRequest,
        DatasetBuildRequest,
        DatasetExportRequest,
        DatasetInspectRequest,
    )
    from scryntic.workflows.data import DataWorkflows

    data, incoming = published_inputs(tmp_path)
    root = installation(tmp_path / "workstation")
    accepted = accept(root, data, incoming)
    context = Context(root)
    service = DataWorkflows(context)

    async def run() -> None:
        inspected = json.loads(
            (await service.execute(DataInspectRequest(accepted))).payload
        )
        assert inspected["manifest_sha256"] == accepted
        assert inspected["validated"] is True
        assert len(inspected["objects"]) == 2
        built = await service.execute(DatasetBuildRequest((accepted,)))
        reference = json.loads(built.payload)["dataset"]["manifest_sha256"]
        rows = json.loads(
            (await service.execute(DatasetInspectRequest(reference))).payload
        )["rows"]
        assert rows[0]["close"] == "100.750000000000000000"
        await service.execute(DatasetExportRequest(reference, "fixture-export"))
        artifacts = context.exports["fixture-export"]
        manifest = artifacts["dataset.manifest.json"]
        assert sha256(manifest).hexdigest() == reference
        provenance = json.loads(manifest)
        assert provenance["inputs"][0]["manifest_sha256"] == accepted
        assert (
            sha256(artifacts["dataset.parquet"]).hexdigest()
            == provenance["parquet"]["sha256"]
        )
        assert provenance["environment"]["code_revision"] == "f24-fixture"

    asyncio.run(run())


def test_replay_is_repeatable_and_rejects_retired_pins(tmp_path: Path) -> None:
    from scryntic.application.data_operations import DatasetBuildRequest, ReplayRequest
    from scryntic.workflows.data import DataWorkflows

    data, incoming = _publish(tmp_path, sources())
    root = installation(tmp_path / "workstation")
    context = Context(root)
    service = DataWorkflows(context)
    accepted = accept(root, data, incoming)

    async def run() -> str:
        built = await service.execute(
            DatasetBuildRequest((accepted,), label_horizon_steps=1)
        )
        reference = json.loads(built.payload)["dataset"]["manifest_sha256"]
        request = ReplayRequest(
            reference,
            START,
            START + 4 * STEP,
            START + 8 * STEP,
            START + 12 * STEP,
            coverage="include",
            time_quality="include",
            destination="replay",
        )
        first = await service.execute(request)
        second = await service.execute(request)
        assert first.payload == second.payload
        report = context.exports["replay"]["replay.json"]
        assert json.loads(report)["dataset"]["manifest_sha256"] == reference
        assert json.loads(first.payload)["report_sha256"] == sha256(report).hexdigest()
        return str(reference)

    reference = asyncio.run(run())
    with ImportCatalog(root) as catalog:
        datasets = DatasetService(
            catalog, root, code_revision="fixture", dependency_lock_sha256="d" * 64
        )
        datasets.retire(datasets.resolve(reference))
    request = ReplayRequest(
        reference, START, START + 4 * STEP, START + 8 * STEP, START + 12 * STEP
    )
    operation = service.execute(request)
    with pytest.raises(ApplicationError) as rejected:
        asyncio.run(operation)
    assert rejected.value.code is ErrorCode.UNAVAILABLE


def test_strict_build_preserves_explicit_cutoff_evidence(tmp_path: Path) -> None:
    from scryntic.application.data_operations import (
        DatasetBuildRequest,
        DatasetExportRequest,
    )
    from scryntic.workflows.data import DataWorkflows

    data, incoming = _publish(tmp_path, sources())
    root = installation(tmp_path / "workstation")
    accepted = accept(root, data, incoming)
    context = Context(root)
    cutoff = asdict(_clock(START + 6 * STEP))
    (root.config_dir / "cutoff").write_text(json.dumps(cutoff))
    service = DataWorkflows(context)

    async def run() -> None:
        result = await service.execute(
            DatasetBuildRequest((accepted,), mode="as-observed", cutoff_leaf="cutoff")
        )
        reference = json.loads(result.payload)["dataset"]["manifest_sha256"]
        await service.execute(DatasetExportRequest(reference, "strict"))
        manifest = json.loads(context.exports["strict"]["dataset.manifest.json"])
        assert manifest["as_observed_cutoff"] == cutoff
        assert manifest["row_count"] == 6

    asyncio.run(run())


def test_coverage_capture_is_pinned_without_inventing_completeness(
    tmp_path: Path,
) -> None:
    from scryntic.application.data_operations import (
        DatasetBuildRequest,
        DatasetExportRequest,
    )
    from scryntic.dataset.recipes import CoverageClaim
    from scryntic.workflows.context import WorkflowContext
    from scryntic.workflows.data import DataWorkflows
    from tests.normalization.helpers import INSTRUMENT_ID

    data, incoming = _publish(tmp_path, sources(8))
    root = installation(tmp_path / "workstation")
    accepted = accept(root, data, incoming)
    context = WorkflowContext(root)
    claim = CoverageClaim(
        INSTRUMENT_ID, STEP, START, START + 8 * STEP, "complete", _clock(START - STEP)
    )
    capture = [claim.projection()]
    capture_path = root.config_dir / "coverage"
    capture_path.write_text(json.dumps({"version": 1, "intervals": capture}))
    capture_path.chmod(0o600)
    service = DataWorkflows(context)

    async def run() -> None:
        built = await service.execute(
            DatasetBuildRequest(
                (accepted,), coverage="require-complete", coverage_leaf="coverage"
            )
        )
        reference = json.loads(built.payload)["dataset"]["manifest_sha256"]
        await service.execute(DatasetExportRequest(reference, "covered"))
        manifest = json.loads(
            (root.state_dir / "exports/covered/dataset.manifest.json").read_bytes()
        )
        assert manifest["coverage_claims"] == capture
        expected = sha256(
            json.dumps(capture, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        assert manifest["coverage_sha256"] == expected
        assert json.loads(built.payload)["pins"]["coverage_sha256"] == expected
        assert manifest["row_count"] == 8
        await context.aclose()

    asyncio.run(run())


def test_incomplete_cutoff_capture_fails_closed_without_clock_defaults(
    tmp_path: Path,
) -> None:
    from scryntic.application.data_operations import DatasetBuildRequest
    from scryntic.workflows.data import DataWorkflows

    data, incoming = published_inputs(tmp_path)
    root = installation(tmp_path / "workstation")
    accepted = accept(root, data, incoming)
    context = Context(root)
    (root.config_dir / "cutoff").write_text(json.dumps({"wall_time_ns": START}))
    service = DataWorkflows(context)
    request = DatasetBuildRequest((accepted,), mode="as-observed", cutoff_leaf="cutoff")
    operation = service.execute(request)
    with pytest.raises(ApplicationError) as rejected:
        asyncio.run(operation)
    assert rejected.value.code is ErrorCode.CONFIGURATION
    assert not (root.state_dir / "datasets").exists()


def test_import_inspection_revalidates_objects_after_acceptance(tmp_path: Path) -> None:
    from scryntic.application.data_operations import DataInspectRequest
    from scryntic.imports.protocol import ImportLimits, parse_request
    from scryntic.workflows.data import DataWorkflows

    data, incoming = published_inputs(tmp_path)
    root = installation(tmp_path / "workstation")
    accepted = accept(root, data, incoming)
    descriptor = parse_request(data, ImportLimits()).body.objects[0]
    stored = root.state_dir / "imports/objects" / descriptor.sha256
    stored.chmod(0o600)
    stored.write_bytes(b"secret compromised payload")
    stored.chmod(0o400)
    service = DataWorkflows(Context(root))
    request = DataInspectRequest(accepted)
    operation = service.execute(request)
    with pytest.raises(ApplicationError) as rejected:
        asyncio.run(operation)
    assert rejected.value.code is ErrorCode.UNAVAILABLE
    assert "secret" not in str(rejected.value)
